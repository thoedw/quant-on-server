"""
Intraday Engine - Unified Realtime Price Aggregation
Phiên bản thống nhất: Thu tick → Nặn nến 7 khung → Ghi SQLite
Thay thế hoàn toàn cặp Red-Lightning + Aggregator

Architecture:
  Playwright Browser → DNSE WS → ParseTick → CandleAccumulator → SQLiteWriter
  
Data Flow:
  stockinfo topics  → price snapshot mỗi mã
  boardevent topics → lệnh khớp thật (price + volume) per market
  
Multi-timeframe: 1m, 5m, 15m, 30m, 1H, 1D, 1W
"""
import os
import sys
import time
import json
import struct
import logging
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
from securities_master.database import DatabaseManager
from securities_master.models import PriceRecord
from securities_master.loaders.sqlite_loader import SQLiteLoader

load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [IntraEngine] %(message)s',
    handlers=[
        logging.FileHandler("intraday_engine.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# CONFIG
# ============================================================
DNSE_BOARD_URL = "https://banggia.dnse.com.vn"
DB_PATH = os.getenv("SMD_DB_PATH", os.path.join(PROJECT_ROOT, "data", "securities_master.db"))

TIMEFRAMES = ['1m', '5m', '15m', '30m', '1H', '1D', '1W']

# Bước thời gian cho mỗi khung (phút)
TF_MINUTES = {
    '1m':  1,
    '5m':  5,
    '15m': 15,
    '30m': 30,
    '1H':  60,
    '1D':  480,   # Cả ngày (08:00 - 16:00 VN time)
    '1W':  2400,  # Cả tuần
}

INTRADAY_OPEN_HOUR  = 9  # 09:00 VN
INTRADAY_CLOSE_HOUR = 15  # 15:30 VN
INTRADAY_CLOSE_MIN  = 30

# Topics để đăng ký với DNSE (market-level boardevent có volume thật)
BOARD_MARKETS = [
    ('HSX', 'EQ'),   # HoSE equities
    ('HNX', 'EQ'),   # HNX equities
    ('UPX', 'UPX'),  # UPCOM
    ('DVX', 'FIO'),  # Phái sinh
]

FLUSH_INTERVAL_SECONDS = 30  # Batch write mỗi 30 giây
REDIS_SYNC_INTERVAL_SECONDS = 10 # Sync open candles mỗi 10 giây

# Redis config (graceful — engine vẫn chạy nếu Redis down)
REDIS_HOST = os.getenv('REDIS_HOST', '127.0.0.1')
REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))
REDIS_TICK_KEY = 'last_ticks'       # HASH: { symbol → unix_timestamp }
REDIS_COUNTER_KEY = 'engine:tick_total'


# ============================================================
# TICK RULE CLASSIFIER — Lee-Ready v2
# ============================================================

class TickClassifier:
    """
    Phân loại lệnh khớp theo Lee-Ready (1991).

    Thuật toán:
      UP-TICK   (price > prev)  → BUY  (bên mua chủ động)
      DOWN-TICK (price < prev)  → SELL (bên bán chủ động)
      ZERO-TICK (price == prev) → Kế thừa hướng tick trước (Zero-Tick Rule)
      ZERO-TICK + chưa có lịch sử  → NEUTRAL (tick đầu tiên của phiên)

    Độ chính xác: ~75-80% so với dữ liệu buy/sell thực tế.
    Nếu có Bid/Ask mid-quote sẽ cải thiện lên ~85%+.

    Confidence tạg:
      'NATIVE'    — side do MASVN cung cấp trực tiếp (masvn_mb field)
      'LEE_READY' — do thuật toán Tick Test suy luận
      'NEUTRAL'   — tick đầu tiên, chưa có reference price
    """
    def __init__(self):
        self._last_price: dict[str, float] = {}   # symbol → last price
        self._last_side:  dict[str, str]   = {}   # symbol → 'BUY'|'SELL'

        # Stats
        self.native_count    = 0
        self.lee_ready_count = 0
        self.neutral_count   = 0

    def classify(self, symbol: str, price: float,
                 native_side: str = None) -> tuple[str, str]:
        """
        Trả về (side, confidence) trong đó:
          side       : 'BUY' | 'SELL' | 'NEUTRAL'
          confidence : 'NATIVE' | 'LEE_READY' | 'NEUTRAL'

        Parameters
        ----------
        native_side : str | None
            Nếu MASVN đã có side ('BUY'/'SELL') → dùng trực tiếp (NATIVE).
            Nếu None hoặc 'NEUTRAL' → áp dụng Lee-Ready.
        """
        # NATIVE path: MASVN cấp dữ liệu thật
        if native_side in ('BUY', 'SELL'):
            self._last_price[symbol] = price
            self._last_side[symbol]  = native_side
            self.native_count       += 1
            return native_side, 'NATIVE'

        # LEE-READY path: suy luận từ hướng giá
        prev = self._last_price.get(symbol)
        self._last_price[symbol] = price

        if prev is None:
            # Tick đầu tiên của phiên — chưa có context
            self._last_side[symbol]  = 'NEUTRAL'
            self.neutral_count      += 1
            return 'NEUTRAL', 'NEUTRAL'

        if price > prev:
            side = 'BUY'
        elif price < prev:
            side = 'SELL'
        else:
            # Zero-tick: kế thừa hướng trước (Zero-Tick Rule)
            side = self._last_side.get(symbol, 'NEUTRAL')

        if side in ('BUY', 'SELL'):
            self._last_side[symbol]  = side
            self.lee_ready_count    += 1
            return side, 'LEE_READY'

        self.neutral_count += 1
        return 'NEUTRAL', 'NEUTRAL'

    @property
    def side_quality_pct(self) -> float:
        """Tỷ lệ ticks có side hợp lệ (BUY/SELL)."""
        total = self.native_count + self.lee_ready_count + self.neutral_count
        return round((self.native_count + self.lee_ready_count) * 100.0 / max(total, 1), 1)

    def get_stats(self) -> dict:
        total = self.native_count + self.lee_ready_count + self.neutral_count
        return {
            'native'    : self.native_count,
            'lee_ready' : self.lee_ready_count,
            'neutral'   : self.neutral_count,
            'total'     : total,
            'side_pct'  : self.side_quality_pct,
        }


# ============================================================
# VOLUME TRACKER (Cumulative → Delta)
# ============================================================

class VolumeTracker:
    """Tính volume thực per-tick từ cum_vol (cộng dồn) của stockinfo.
    delta_vol = cum_vol_now - cum_vol_prev
    Reset về 0 khi cum_vol giảm (sang phiên mới).
    """
    def __init__(self):
        self._last: dict[str, int] = {}  # symbol -> last cum_vol

    def delta(self, symbol: str, cum_vol: int) -> int:
        prev = self._last.get(symbol)
        self._last[symbol] = cum_vol
        if prev is None or cum_vol < prev:
            return 0   # Tick đầu tiên hoặc sang phiên mới
        return cum_vol - prev  # Volume khanced cho 1 tick này


# ============================================================
# CANDLE ACCUMULATOR
# ============================================================

class CandleState:
    __slots__ = ['open', 'high', 'low', 'close', 'volume',
                 'buy_vol', 'sell_vol', 'delta',
                 'period_start', 'tick_count']

    def __init__(self, price: float, volume: int, side: str, period_start: datetime):
        self.open         = price
        self.high         = price
        self.low          = price
        self.close        = price
        self.volume       = volume
        self.period_start = period_start
        self.tick_count   = 1
        # Volume Delta (Order Flow)
        self.buy_vol  = volume if side == 'BUY'  else 0
        self.sell_vol = volume if side == 'SELL' else 0
        self.delta    = self.buy_vol - self.sell_vol

    def _cap_side_to_volume(self):
        """
        GUARD: Đảm bảo buy_vol + sell_vol <= volume.

        Root cause của bug: MASVN replay duplicate ticks sau reconnect →
        buy_vol tích lũy 2 lần trong cùng 1 candle, trong khi volume từ
        DNSE chỉ ghi đúng 1 lần. Proportional scaling giữ nguyên tỷ lệ
        buy/sell, chỉ scale xuống nếu vượt ngưỡng.
        """
        total_side = self.buy_vol + self.sell_vol
        if total_side > self.volume and total_side > 0:
            ratio         = self.volume / total_side
            self.buy_vol  = int(self.buy_vol  * ratio)
            self.sell_vol = int(self.sell_vol * ratio)
            self.delta    = self.buy_vol - self.sell_vol

    def update(self, price: float, volume: int, side: str):
        if price > self.high: self.high = price
        if price < self.low:  self.low  = price
        self.close   = price
        self.volume += volume
        self.tick_count += 1
        # Cập nhật Volume Delta
        if side == 'BUY':
            self.buy_vol  += volume
        elif side == 'SELL':
            self.sell_vol += volume
        self.delta = self.buy_vol - self.sell_vol
        # GUARD: chặn overflow do duplicate ticks từ MASVN reconnect
        self._cap_side_to_volume()

    def update_side(self, volume: int, old_side: str, new_side: str):
        """
        Vá lại side classification khi MASVN confirm sau DNSE.

        Hoàn tác buy_vol/sell_vol ghi bởi old_side,
        rồi cộng lại theo new_side (mb thật từ MASVN).

        Không thay đổi OHLCV hay volume tổng — chỉ tái phân bổ
        giữa buy_vol và sell_vol.
        """
        # Hoàn tác old_side
        if old_side == 'BUY':
            self.buy_vol  = max(0, self.buy_vol  - volume)
        elif old_side == 'SELL':
            self.sell_vol = max(0, self.sell_vol - volume)
        # Ghi lại new_side
        if new_side == 'BUY':
            self.buy_vol  += volume
        elif new_side == 'SELL':
            self.sell_vol += volume
        self.delta = self.buy_vol - self.sell_vol
        # GUARD: reclassify có thể inflate side nếu old_side=NEUTRAL
        self._cap_side_to_volume()


def get_period_start(dt: datetime, tf: str) -> datetime:
    """Trả về thời điểm bắt đầu của period chứa dt theo timeframe tf"""
    if tf == '1m':
        return dt.replace(second=0, microsecond=0)
    if tf == '5m':
        m = (dt.minute // 5) * 5
        return dt.replace(minute=m, second=0, microsecond=0)
    if tf == '15m':
        m = (dt.minute // 15) * 15
        return dt.replace(minute=m, second=0, microsecond=0)
    if tf == '30m':
        m = (dt.minute // 30) * 30
        return dt.replace(minute=m, second=0, microsecond=0)
    if tf == '1H':
        return dt.replace(minute=0, second=0, microsecond=0)
    if tf == '1D':
        return dt.replace(hour=9, minute=0, second=0, microsecond=0)
    if tf == '1W':
        # Thứ Hai đầu tuần
        monday = dt - timedelta(days=dt.weekday())
        return monday.replace(hour=9, minute=0, second=0, microsecond=0)
    return dt.replace(second=0, microsecond=0)


class CandleAccumulator:
    """In-memory OHLCV accumulator cho tất cả mã, tất cả timeframes"""

    def __init__(self):
        # state[symbol][tf] = CandleState
        self.state: dict[str, dict[str, CandleState]] = defaultdict(dict)
        # Pending candles chờ flush vào DB
        self.pending: list[tuple[str, str, CandleState]] = []  # (symbol, tf, state)
        self._lock = asyncio.Lock()

    def warm_start(self, preloaded: dict, today_ts: datetime):
        """
        Pre-seed accumulator với buy/sell đã flush trước đó trong ngày.
        Gọi 1 lần ngay sau khi khởi động, trước khi nhận tick đầu tiên.
        """
        for (symbol, interval), data in preloaded.items():
            period_start = datetime.fromisoformat(str(data['period_start_str']).replace(' ', 'T'))
            # Chỉ warm-start nếu period còn đang mở (cùng kỳ với hôm nay)
            if period_start.date() != today_ts.date():
                continue
            cs = CandleState.__new__(CandleState)
            cs.open       = data['open']
            cs.high       = data['high']
            cs.low        = data['low']
            cs.close      = data['close']
            cs.volume     = data['volume']
            cs.buy_vol    = data['buy_vol']
            cs.sell_vol   = data['sell_vol']
            cs.delta      = data['delta']
            cs.period_start = period_start
            self.state[symbol][interval] = cs
        logger.info(f"🔥 Warm-Start nạp {len(preloaded)} khối 1D/1H vào Accumulator")

    async def ingest(self, symbol: str, price: float, volume: int, side: str, ts: datetime = None):
        """
        Nhận 1 tick và cập nhật tất cả timeframes.

        side: 'BUY' | 'SELL' | 'NEUTRAL'
          - MASVN: side thật từ mb field (~95% chính xác)
          - DNSE:  side suy luận từ Tick Rule (~70-75%)
          - NEUTRAL: tick đầu tiên, không tính vào buy/sell
        """
        if price <= 0:
            return
        if ts is None:
            ts = datetime.now()

        async with self._lock:
            sym_state = self.state[symbol]

            for tf in TIMEFRAMES:
                period_start = get_period_start(ts, tf)

                if tf not in sym_state:
                    sym_state[tf] = CandleState(price, volume, side, period_start)
                else:
                    cur = sym_state[tf]
                    if period_start > cur.period_start:
                        # Kỳ mới → flush candle cũ
                        self.pending.append((symbol, tf, cur))
                        sym_state[tf] = CandleState(price, volume, side, period_start)
                    else:
                        cur.update(price, volume, side)

    async def update_side(self, symbol: str, ts: datetime,
                          volume: int, old_side: str, new_side: str):
        """
        Vá lại side của tick đã được ingest (MASVN arrive sau DNSE).

        Chỉ vá candle đang MỞ (chưa flush ra DB).
        Nếu candle đã flush → bỏ qua (chấp nhận được vì MASVN
        thường đến trong vòng 50-200ms, trước chu kỳ flush 30s).

        Parameters
        ----------
        ts       : timestamp của tick cần vá (để xác định đúng timeframe)
        volume   : khối lượng cần tái phân bổ
        old_side : side đã ghi (từ Tick Rule của DNSE)
        new_side : side thật (từ mb của MASVN)
        """
        if old_side == new_side:
            return  # Không cần vá
        async with self._lock:
            sym_state = self.state.get(symbol)
            if not sym_state:
                return
            for tf in TIMEFRAMES:
                cur = sym_state.get(tf)
                if cur is None:
                    continue
                # Chỉ vá nếu tick nằm trong period đang mở
                period_start = get_period_start(ts, tf)
                if period_start == cur.period_start:
                    cur.update_side(volume, old_side, new_side)

    async def flush_all(self) -> list[tuple[str, str, CandleState]]:
        """Lấy hết pending candles để ghi DB"""
        async with self._lock:
            result = self.pending.copy()
            self.pending.clear()
            return result

    async def get_open_candles(self, intervals=('1D', '1H')) -> list[tuple[str, str, CandleState]]:
        """Lấy danh sách các Open candles (đang tính toán) để cache"""
        async with self._lock:
            result = []
            for symbol, sym_state in self.state.items():
                for tf, cur in sym_state.items():
                    if tf in intervals:
                        result.append((symbol, tf, cur))
            return result



    async def force_flush_current(self, ts: datetime = None) -> list[tuple[str, str, CandleState]]:
        """Force flush candle hiện tại (dùng khi đóng cửa)"""
        if ts is None:
            ts = datetime.now()
        async with self._lock:
            result = []
            for symbol, sym_state in self.state.items():
                for tf, cur in sym_state.items():
                    result.append((symbol, tf, cur))
            return result


# ============================================================
# PUT-THROUGH TRACKER
# ============================================================

class PutThroughTracker:
    """
    Lưu trữ in-memory snapshot put-through (thỏa thuận) lũy kế hôm nay per symbol.

    Được cập nhật qua callback on_putthrough của DNSEProvider mỗi khi có lệnh
    thỏa thuận mới từ exchange. Background task _pt_flush_worker flush dữ liệu
    này xuống cột pt_vol của stock_prices[interval='1D'] mỗi 60 giây.

    Lưu ý: avg_pt_price là giá bình quân lũy kế theo KRX — KHÔNG phải VWAP
    của khớp lệnh liên tục. Hai khái niệm này tách biệt trong dự án.
    """
    def __init__(self):
        self._data: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def update(self, symbol: str, pt_vol: int, avg_pt_price: float,
                     pt_val_tỷ: float, pt_count: int):
        async with self._lock:
            self._data[symbol] = {
                'pt_vol':       pt_vol,
                'avg_pt_price': avg_pt_price,
                'pt_val_tỷ':    pt_val_tỷ,
                'pt_count':     pt_count,
            }

    async def snapshot(self) -> dict[str, dict]:
        async with self._lock:
            return dict(self._data)

    def reset(self):
        self._data.clear()


# ============================================================
# FOREIGN TRADING TRACKER
# ============================================================

class ForeignTradingTracker:
    """
    Lưu trữ in-memory snapshot giao dịch nước ngoài lũy kế hôm nay per symbol.

    Nhận cập nhật từ DNSEProvider qua topic trading_result_of_foreign_investor.
    Tổng hợp cả G1 (khớp lệnh liên tục) và G4 (thỏa thuận) để có con số toàn phiên.

    Background task _foreign_flush_worker flush vào cột foreign_buy_vol/foreign_sell_vol
    của stock_prices[interval='1D'] mỗi 60 giây.

    Nguồn dữ liệu: KRX broadcast — tích lũy từ đầu phiên, cập nhật real-time.
    """
    def __init__(self):
        # _data[symbol] = {'G1': {...}, 'G4': {...}}
        self._data: dict[str, dict[str, dict]] = {}
        self._lock = asyncio.Lock()

    async def update(self, symbol: str, board: str,
                     buy_vol: int, buy_val: float,
                     sell_vol: int, sell_val: float):
        async with self._lock:
            if symbol not in self._data:
                self._data[symbol] = {}
            self._data[symbol][board] = {
                'buy_vol':  buy_vol,
                'buy_val':  buy_val,
                'sell_vol': sell_vol,
                'sell_val': sell_val,
            }

    async def snapshot(self) -> dict[str, dict]:
        """
        Returns {symbol: {buy_vol, sell_vol, net_vol}} tổng hợp G1+G4.
        """
        async with self._lock:
            result = {}
            for sym, boards in self._data.items():
                total_buy  = sum(b['buy_vol']  for b in boards.values())
                total_sell = sum(b['sell_vol'] for b in boards.values())
                result[sym] = {
                    'buy_vol':  total_buy,
                    'sell_vol': total_sell,
                    'net_vol':  total_buy - total_sell,
                }
            return result

    def reset(self):
        self._data.clear()


# ============================================================
# SQLITE WRITER
# ============================================================

class SQLiteWriter:
    def __init__(self, db_path: str):
        self.db = DatabaseManager(db_path)
        # Bật WAL Mode và Tối ưu hoá SQLite để chịu I/O cao Ticks liên tục
        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA busy_timeout=5000;")
        cur.execute("PRAGMA cache_size=-64000;") # 64MB cache
        
        # Cache security_id mapping
        cur.execute("SELECT symbol, security_id FROM securities WHERE asset_type='EQUITY'")
        self.sec_map = {row['symbol']: row['security_id'] for row in cur.fetchall()}
        logger.info(f"✅ SQLiteWriter: loaded {len(self.sec_map)} symbol mappings (WAL mode ON)")

    # ON CONFLICT DO UPDATE thay vì INSERT OR REPLACE để giữ nguyên pt_vol
    # đã được write_pt_vol() ghi vào từ put-through tracker.
    SQL_UPSERT = """
        INSERT INTO stock_prices
            (security_id, interval, trade_time, open, high, low, close, volume, buy_vol, sell_vol, delta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(security_id, interval, trade_time) DO UPDATE SET
            open     = excluded.open,
            high     = excluded.high,
            low      = excluded.low,
            close    = excluded.close,
            volume   = excluded.volume,
            buy_vol  = excluded.buy_vol,
            sell_vol = excluded.sell_vol,
            delta    = excluded.delta
    """

    def write(self, candles: list[tuple[str, str, CandleState]]) -> int:
        """Ghi batch candles vào SQLite với buy_vol/sell_vol/delta."""
        rows = []
        for symbol, tf, state in candles:
            sec_id = self.sec_map.get(symbol)
            if not sec_id:
                continue

            # ── SAFETY NET: final cap trước khi ghi DB ──────────────────
            # Phòng trường hợp CandleState bị bypass guard (warm_start,
            # force_flush_current không qua update())
            bv  = state.buy_vol
            sv  = state.sell_vol
            vol = state.volume
            total_side = bv + sv
            if total_side > vol and total_side > 0:
                ratio = vol / total_side
                bv = int(bv * ratio)
                sv = vol - bv
            delta = bv - sv

            rows.append((
                sec_id,
                tf,
                state.period_start.isoformat(),
                round(state.open,  2),
                round(state.high,  2),
                round(state.low,   2),
                round(state.close, 2),
                vol,
                bv,
                sv,
                delta,
            ))

        if not rows:
            return 0

        conn = self.db.get_connection()
        conn.executemany(self.SQL_UPSERT, rows)
        conn.commit()
        return len(rows)

    def load_today_buysell(self, today_str: str) -> dict:
        """
        Warm-Start: Load buy_vol/sell_vol/volume hôm nay từ DB.
        Mục đích: khi restart giữa ngày, accumulator tiếp tục từ giá trị đã flush,
        tránh mất buy/sell tích lũy nửa ngày trước.

        Chỉ warm-start các interval dài (1D, 1H) vì 1m/5m được flush rất thường xuyên.
        Returns: {(symbol, interval): {buy_vol, sell_vol, volume, open, high, low, close, period_start}}
        """
        WARM_INTERVALS = ('1D', '1H')  # Chỉ các interval dài có nguy cơ mất data khi restart
        placeholders = ','.join('?' * len(WARM_INTERVALS))
        conn = self.db.get_connection()
        rows = conn.execute(f"""
            SELECT s.symbol, sp.interval, sp.trade_time,
                   sp.open, sp.high, sp.low, sp.close, sp.volume,
                   sp.buy_vol, sp.sell_vol, sp.delta
            FROM stock_prices sp
            JOIN securities s ON sp.security_id = s.security_id
            WHERE sp.interval IN ({placeholders})
              AND date(sp.trade_time) = ?
              AND (sp.buy_vol > 0 OR sp.sell_vol > 0)
        """, list(WARM_INTERVALS) + [today_str]).fetchall()

        state = {}
        for r in rows:
            key = (r[0], r[1])  # (symbol, interval)
            state[key] = {
                'buy_vol':  r[8], 'sell_vol': r[9], 'delta': r[10],
                'volume':   r[7], 'open':    r[3],
                'high':     r[4], 'low':     r[5], 'close':   r[6],
                'period_start_str': r[2],
            }
        logger.info(f"🔥 Warm-Start: load {len(state)} khối 1D/1H đã flush hôm nay")
        return state

    def write_pt_vol(self, pt_data: dict[str, int], trade_date: str) -> int:
        """
        Ghi pt_vol vào stock_prices[interval='1D'] cho từng mã.

        Dùng UPSERT với trade_time = {trade_date}T09:00:00 (session open của 1D candle).
        INSERT nếu row chưa tồn tại (1D chưa flush trong ngày), UPDATE nếu đã có.
        ON CONFLICT chỉ set pt_vol — không ghi đè OHLCV đã flush bởi CandleAccumulator.

        Parameters
        ----------
        pt_data    : {symbol: pt_vol_shares}
        trade_date : 'YYYY-MM-DD'
        """
        session_open = f"{trade_date}T09:00:00"
        rows = [
            (self.sec_map[sym], session_open, pt_vol)
            for sym, pt_vol in pt_data.items()
            if sym in self.sec_map and pt_vol > 0
        ]
        if not rows:
            return 0
        conn = self.db.get_connection()
        cur = conn.executemany("""
            INSERT INTO stock_prices
                (security_id, interval, trade_time,
                 open, high, low, close, volume, buy_vol, sell_vol, delta, pt_vol)
            VALUES (?, '1D', ?, 0, 0, 0, 0, 0, 0, 0, 0, ?)
            ON CONFLICT(security_id, interval, trade_time) DO UPDATE SET
                pt_vol = excluded.pt_vol
        """, rows)
        conn.commit()
        return cur.rowcount

    def write_foreign_vol(self, foreign_data: dict[str, dict], trade_date: str) -> int:
        """
        Ghi foreign_buy_vol / foreign_sell_vol vào stock_prices[interval='1D'] cho từng mã.

        Dùng UPSERT với trade_time = {trade_date}T09:00:00 (session open).
        ON CONFLICT chỉ set foreign columns — không ghi đè OHLCV.

        Parameters
        ----------
        foreign_data : {symbol: {buy_vol, sell_vol, net_vol}}
        trade_date   : 'YYYY-MM-DD'
        """
        session_open = f"{trade_date}T09:00:00"
        rows = [
            (self.sec_map[sym], session_open, d['buy_vol'], d['sell_vol'])
            for sym, d in foreign_data.items()
            if sym in self.sec_map and (d['buy_vol'] > 0 or d['sell_vol'] > 0)
        ]
        if not rows:
            return 0
        conn = self.db.get_connection()
        cur = conn.executemany("""
            INSERT INTO stock_prices
                (security_id, interval, trade_time,
                 open, high, low, close, volume, buy_vol, sell_vol, delta,
                 foreign_buy_vol, foreign_sell_vol)
            VALUES (?, '1D', ?, 0, 0, 0, 0, 0, 0, 0, 0, ?, ?)
            ON CONFLICT(security_id, interval, trade_time) DO UPDATE SET
                foreign_buy_vol  = excluded.foreign_buy_vol,
                foreign_sell_vol = excluded.foreign_sell_vol
        """, rows)
        conn.commit()
        return cur.rowcount


# ============================================================
# INTRADAY ENGINE (Main)
# ============================================================

from realtime.feed_provider import FeedProvider
from realtime.dnse_provider import DNSEProvider
from realtime.masvn_worker import MASVNManager
from realtime.tick_router import TickRouter

class RedisTickTracker:
    """
    Ghi timestamp tick gần nhất per-symbol vào Redis HASH.
    Price Board đọc hash này để hiện ● indicator.
    Graceful: tự tắt nếu Redis không kết nối được.
    """
    def __init__(self):
        self._r = None
        self._enabled = False
        self._batch: dict[str, str] = {}   # buffer gom để HSET 1 lần/giây
        self._lock = asyncio.Lock()

    async def connect(self):
        try:
            import redis.asyncio as aioredis
            self._r = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT,
                                     socket_connect_timeout=2, decode_responses=True)
            await self._r.ping()
            self._enabled = True
            logger.info(f"✅ RedisTickTracker kết nối {REDIS_HOST}:{REDIS_PORT}")
        except Exception as e:
            logger.warning(f"⚠️  Redis không khả dụng: {e} — live indicators bị tắt")
            self._enabled = False

    def track(self, symbol: str):
        """Gọi mỗi khi có tick — thread-safe vì chỉ ghi vào dict"""
        if not self._enabled:
            return
        self._batch[symbol] = str(int(time.time()))

    async def flush_batch_loop(self):
        """Background: flush batch lên Redis mỗi 1 giây"""
        while True:
            await asyncio.sleep(1)
            if not self._enabled or not self._batch:
                continue
            try:
                async with self._lock:
                    batch = self._batch.copy()
                    self._batch.clear()
                pipe = self._r.pipeline()
                pipe.hset(REDIS_TICK_KEY, mapping=batch)
                pipe.incrby(REDIS_COUNTER_KEY, len(batch))
                await pipe.execute()
            except Exception as e:
                logger.debug(f"Redis flush lỗi: {e}")
                self._enabled = False   # vô hiệu hoá khi mất kết nối


class RedisCandleCache:
    """
    Cache open candles (1D/1H) to Redis.
    Prevent data loss on crash before a flush.
    """
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self._enabled = False
        self._r = None

    async def connect(self):
        try:
            import redis.asyncio as aioredis
            self._r = aioredis.Redis(host=self.host, port=self.port,
                                     socket_connect_timeout=2, decode_responses=True)
            await self._r.ping()
            self._enabled = True
            logger.info(f"✅ RedisCandleCache kết nối {self.host}:{self.port}")
        except Exception as e:
            logger.warning(f"⚠️ Redis không khả dụng cho Candle Cache: {e}")
            self._enabled = False

    async def sync_open_candles(self, candles: list[tuple[str, str, CandleState]]):
        if not self._enabled or not candles: return
        pipe = self._r.pipeline()
        for sym, tf, state in candles:
            if tf not in ('1D', '1H'): continue
            key = f"intra:open_candle:{sym}:{tf}"
            mapping = {
                'buy_vol': state.buy_vol,
                'sell_vol': state.sell_vol,
                'volume': state.volume,
                'open': state.open,
                'high': state.high,
                'low': state.low,
                'close': state.close,
                'delta': state.delta,
                'period_start_str': state.period_start.isoformat()
            }
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, 30 * 3600) # 30 hours
        try:
            await pipe.execute()
        except:
            pass

    async def delete_flushed_candles(self, candles: list[tuple[str, str, CandleState]]):
        if not self._enabled or not candles: return
        keys_to_delete = [
            f"intra:open_candle:{sym}:{tf}" 
            for sym, tf, _ in candles if tf in ('1D', '1H')
        ]
        if keys_to_delete:
            try:
                await self._r.delete(*keys_to_delete)
            except:
                pass

    async def load_open_candles(self, today_ts: datetime) -> dict:
        """Returns format matching load_today_buysell"""
        state_dict = {}
        if not self._enabled: return state_dict
        try:
            keys = await self._r.keys("intra:open_candle:*")
            if not keys: return state_dict
            for k in keys:
                _, _, sym, tf = k.split(':')
                obj = await self._r.hgetall(k)
                if not obj: continue
                # validate period
                try:
                    p_start = datetime.fromisoformat(str(obj['period_start_str']).replace(' ', 'T'))
                    if p_start.date() != today_ts.date():
                        continue
                except:
                    continue

                state_dict[(sym, tf)] = {
                    'buy_vol': int(obj.get('buy_vol', 0)),
                    'sell_vol': int(obj.get('sell_vol', 0)),
                    'volume': int(obj.get('volume', 0)),
                    'open': float(obj.get('open', 0)),
                    'high': float(obj.get('high', 0)),
                    'low': float(obj.get('low', 0)),
                    'close': float(obj.get('close', 0)),
                    'delta': int(obj.get('delta', 0)),
                    'period_start_str': obj.get('period_start_str')
                }
            if state_dict:
                logger.info(f"🔥 Redis: Load {len(state_dict)} open 1D/1H candles từ LẦN CRASH TRƯỚC!")
        except Exception as e:
             logger.warning(f"⚠️ Lỗi load từ Redis Cache: {e}")
        return state_dict

class IntradayEngine:
    def __init__(self):
        self.accumulator      = CandleAccumulator()
        self.writer           = SQLiteWriter(DB_PATH)
        self.classifier       = TickClassifier()
        self.vol_tracker      = VolumeTracker()
        self.pt_tracker       = PutThroughTracker()
        self.foreign_tracker  = ForeignTradingTracker()
        self.redis_tracker    = RedisTickTracker()
        self.redis_cache   = RedisCandleCache(REDIS_HOST, REDIS_PORT)

        # Thiết lập Router và phân vùng deduplication
        self.router = TickRouter(
            self.accumulator,
            self.classifier,
            self.redis_tracker
        )

        self.providers: list[FeedProvider] = []
        self.masvn_manager: MASVNManager = None   # Khởi tạo sau khi có symbols
        self.flush_count   = 0
        self._running      = True

    # ── Helpers ─────────────────────────────────────────────────

    def _load_tier1_symbols(self, conn, top_n: int = 150) -> list:
        """
        Xếp hạng mã chứng khoán theo thanh khoản trung bình 20 phiên gần nhất.
        Trả về top_n mã có thanh khoản cao nhất — đây là Tier 1.
        """
        rows = conn.execute("""
            SELECT s.symbol, AVG(sp.volume) AS avg_vol
            FROM stock_prices sp
            JOIN securities s ON sp.security_id = s.security_id
            WHERE sp.interval = '1D'
              AND sp.trade_time >= date('now', '-20 days')
              AND sp.volume > 0
            GROUP BY s.symbol
            ORDER BY avg_vol DESC
            LIMIT ?
        """, (top_n,)).fetchall()

        tier1 = [r[0] for r in rows]
        logger.info(
            f"🔥 Tier-1 Symbols: Top {len(tier1)} mã thanh khoản cao nhất "
            f"(avg_vol range: {int(rows[-1][1]):,} – {int(rows[0][1]):,} CP)"
        )
        return tier1

    async def _flush_worker(self):
        """Background task: flush pending candles vào SQLite mỗi 30 giây"""
        while self._running:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            try:
                pending = await self.accumulator.flush_all()
                if pending:
                    written = await asyncio.to_thread(self.writer.write, pending)
                    self.flush_count += written
                    # Nhóm theo timeframe để log
                    tf_counts = defaultdict(int)
                    for _, tf, _ in pending:
                        tf_counts[tf] += 1
                    logger.info(f"💾 Flush: {written} nến → SQLite. {dict(tf_counts)}. Tổng: {self.flush_count}")
                    
                    # Xoá khỏi Redis những candles 1D/1H đã đóng
                    await self.redis_cache.delete_flushed_candles(pending)
            except Exception as e:
                logger.error(f"Lỗi flush SQLite: {e}")

    async def _redis_sync_worker(self):
        """Background task: sync open candles lên Redis mỗi 10 giây"""
        while self._running:
            await asyncio.sleep(REDIS_SYNC_INTERVAL_SECONDS)
            try:
                open_candles = await self.accumulator.get_open_candles()
                await self.redis_cache.sync_open_candles(open_candles)
            except Exception as e:
                logger.error(f"Lỗi Redis Sync: {e}")

    async def _pt_flush_worker(self):
        """
        Background task: ghi pt_vol (khối lượng thỏa thuận) vào DB mỗi 60 giây.

        Nguồn dữ liệu: PutThroughTracker nhận từ DNSEProvider qua topic
        stockinfo/v1/roundlotputthrough — dữ liệu trực tiếp từ exchange KRX,
        chính xác hơn phương pháp residual của ptvol_imputer.py.
        """
        while self._running:
            await asyncio.sleep(60)
            try:
                snapshot = await self.pt_tracker.snapshot()
                if not snapshot:
                    continue
                trade_date = datetime.now().strftime('%Y-%m-%d')
                pt_data = {sym: d['pt_vol'] for sym, d in snapshot.items()}
                written = await asyncio.to_thread(
                    self.writer.write_pt_vol, pt_data, trade_date
                )
                if written:
                    logger.info(
                        f"📊 pt_vol flush: {written} mã | "
                        + ", ".join(
                            f"{s}={d['pt_vol']//10000:.0f}万"
                            for s, d in list(snapshot.items())[:5]
                        )
                    )
                else:
                    logger.warning(f"📊 pt_vol flush: 0 rows written (tracker có {len(snapshot)} mã)")
            except Exception as e:
                logger.error(f"Lỗi pt_vol flush: {e}")

    async def _foreign_flush_worker(self):
        """
        Background task: ghi foreign_buy_vol/foreign_sell_vol vào DB mỗi 60 giây.

        Nguồn: KRX topic trading_result_of_foreign_investor — tích lũy từ đầu phiên.
        Tổng hợp G1 (khớp lệnh liên tục) + G4 (thỏa thuận) trước khi ghi.
        """
        while self._running:
            await asyncio.sleep(60)
            try:
                snapshot = await self.foreign_tracker.snapshot()
                if not snapshot:
                    continue
                trade_date = datetime.now().strftime('%Y-%m-%d')
                written = await asyncio.to_thread(
                    self.writer.write_foreign_vol, snapshot, trade_date
                )
                if written:
                    top5 = sorted(snapshot.items(),
                                  key=lambda kv: abs(kv[1]['net_vol']), reverse=True)[:5]
                    logger.debug(
                        f"🌏 foreign flush: {written} mã | "
                        + ", ".join(
                            f"{s} net={d['net_vol']//1000:.0f}K"
                            for s, d in top5
                        )
                    )
            except Exception as e:
                logger.error(f"Lỗi foreign_vol flush: {e}")

    async def run(self):
        """Main loop: Khởi động hệ thống Multi-Vendor Pricing Engine"""
        logger.info("📦 Truy vấn SQLite để kéo toàn bộ danh sách EQUITY symbols...")
        try:
            conn = self.writer.db.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT symbol FROM securities WHERE asset_type='EQUITY'")
            all_symbols = [row['symbol'] for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"Lỗi đọc Database: {e}")
            return
            
        if not all_symbols:
            logger.error("❌ Không tìm thấy mã nào trong DB.")
            return
        logger.info(f"📊 Tìm thấy {len(all_symbols)} mã chứng khoán.")

        # Định cấu hình DNSE Provider
        loop = asyncio.get_running_loop()
        dnse = DNSEProvider(self.vol_tracker)
        dnse.on_tick = self.router.route_tick
        dnse.on_putthrough = lambda sym, data: asyncio.run_coroutine_threadsafe(
            self.pt_tracker.update(
                sym,
                data['pt_vol'],
                data['avg_pt_price'],
                data['pt_val_tỷ'],
                data['pt_count'],
            ),
            loop,
        )
        dnse.on_foreign_tick = lambda sym, board, data: asyncio.run_coroutine_threadsafe(
            self.foreign_tracker.update(
                sym, board,
                data['buy_vol'], data['buy_val'],
                data['sell_vol'], data['sell_val'],
            ),
            loop,
        )
        self.providers.append(dnse)

        # Định cấu hình MASVN Manager (2 workers Active-Active cho Tier 1)
        tier1 = self._load_tier1_symbols(conn, top_n=150)
        self.masvn_manager = MASVNManager(
            tier1_symbols=tier1,
            on_tick=self.router.route_tick,
        )

        await self.redis_tracker.connect()
        await self.redis_cache.connect()

        # 2.5 Warm-Start: khôi phục buy/sell (Redis ưu tiên, SQLite fallback)
        today_ts = datetime.now()
        today_str = today_ts.strftime("%Y-%m-%d")
        
        preloaded = await self.redis_cache.load_open_candles(today_ts)
        if not preloaded:
            logger.info("ℹ️ Redis không có open candles, fallback đọc từ SQLite...")
            preloaded = await asyncio.to_thread(self.writer.load_today_buysell, today_str)
            
        if preloaded:
            self.accumulator.warm_start(preloaded, today_ts)

        # 1. Connect toàn bộ providers song song
        logger.info(f"🚀 Đang khởi động {len(self.providers)} Feed Providers song song...")
        connect_results = await asyncio.gather(*[p.connect() for p in self.providers])
        
        # 2. Subscribe các mã
        for i, provider in enumerate(self.providers):
            if connect_results[i]:
                await provider.subscribe(all_symbols)

        # 2.5 Khởi động MASVN Manager (2 workers Active-Active cho Tier 1)
        # BUG FIX: start() bị thiếu → workers không bao giờ chạy → side coverage = 0%
        await self.masvn_manager.start()

        # 3. Start background loops
        flush_task          = asyncio.ensure_future(self._flush_worker())
        redis_sync_task     = asyncio.ensure_future(self._redis_sync_worker())
        redis_flush_task    = asyncio.ensure_future(self.redis_tracker.flush_batch_loop())
        router_cleanup_task = asyncio.ensure_future(self.router.dedup_cleanup_loop())
        pt_flush_task       = asyncio.ensure_future(self._pt_flush_worker())
        foreign_flush_task  = asyncio.ensure_future(self._foreign_flush_worker())
        
        # 5. Heartbeat Monitor Loop
        try:
            while self._running:
                await asyncio.sleep(30)
                tot_syms  = len(self.accumulator.state)
                stats     = self.router.get_stats()
                override  = stats['masvn_side_override']
                native    = stats['masvn_side_native']
                dedup     = stats['dedup_hits']
                wk_dedup  = stats['masvn_worker_dedup']

                # Trạng thái từng MASVN worker
                w_status = ", ".join(
                    f"{w['worker_id']}={'OK' if w['is_connected'] else 'DOWN'}({w['reconnect_count']}x)"
                    for w in self.masvn_manager.get_all_stats()
                )
                logger.info(
                    f"💓 Heartbeat | "
                    f"BUY={stats['buy_rate']}% | "
                    f"ticks={stats['total_ticks']} | "
                    f"DNSE={stats['dnse_accepted']} MASVN={stats['masvn_accepted']} | "
                    f"native={native} override={override} w_dedup={wk_dedup} dedup={dedup} | "
                    f"candles={self.flush_count} | "
                    f"workers=[{w_status}]"
                )
        finally:
            flush_task.cancel()
            redis_sync_task.cancel()
            redis_flush_task.cancel()
            router_cleanup_task.cancel()
            pt_flush_task.cancel()
            foreign_flush_task.cancel()

            logger.info("Dừng MASVN Manager...")
            await self.masvn_manager.stop()

            logger.info("Ngắt kết nối các DNSE Providers...")
            for p in self.providers:
                if p.is_connected:
                    await p.disconnect()



        # Force flush khi tắt
        logger.info("🛑 Đang flush nến cuối trước khi tắt...")
        final = await self.accumulator.force_flush_current()
        if final:
            written = await asyncio.to_thread(self.writer.write, final)
            logger.info(f"Đã flush {written} nến cuối.")


# ============================================================
# ENTRY POINT
# ============================================================

import traceback

async def main():
    engine = IntradayEngine()
    try:
        await engine.run()
    except KeyboardInterrupt:
        engine._running = False
        logger.info("Ngắt bởi người dùng (Ctrl+C).")
    except Exception as exc:
        # Bắt toàn bộ crash không xử lý → log rõ ràng để giải thích PM2 restart
        logger.critical(
            f"CRASH UNHANDLED: {type(exc).__name__}: {exc}\n"
            + traceback.format_exc()
        )
        engine._running = False
        raise   # để PM2 ghi restart rã vào log


if __name__ == '__main__':
    import sys
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        sys.exit(1)   # PM2 cần exit code ≠ 0 để restart đúng cách
