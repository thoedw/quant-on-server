"""
realtime/watchlist_masvn_worker.py
════════════════════════════════════════════════════════
Dedicated MASVN WebSocket Worker cho Watchlist ưu tiên cao.

Mục tiêu:
  • Đảm bảo buy/sell/delta CHẤT LƯỢNG CAO cho danh sách watchlist
  • Chạy ĐỘC LẬP với Intraday Engine — không cạnh tranh resource
  • Ghi trực tiếp vào SQLite (update buy_vol/sell_vol/delta chính xác)
  • Heartbeat + QC báo cáo mỗi 60 giây
  • Watchlist được đọc từ DB (bảng watchlists) — không hardcode
  • Hot-reload watchlist mỗi 5 phút — thêm/bớt mã không cần restart

Kiến trúc:
  WatchlistMASVNWorker
    ├── 2 WebSocket connections đến MASVN (Active-Active)
    ├── Dedup bằng (symbol, price, vol, window_2s)
    ├── SQLite upsert buy_vol/sell_vol khi candle flush mỗi 30s
    ├── Hot-reload watchlist từ DB mỗi WATCHLIST_RELOAD_INTERVAL giây
    └── QC báo cáo side_coverage % theo từng mã

Cách chạy:
  cd ~/quant && source venv_py11/bin/activate
  PYTHONPATH=. python3 realtime/watchlist_masvn_worker.py

Quản lý watchlist:
  python3 scripts/watchlist_manager.py list
  python3 scripts/watchlist_manager.py add TCB VCB
  python3 scripts/watchlist_manager.py remove PSI
"""

import os
import sys
import asyncio
import logging
import msgpack
import sqlite3
import websockets
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from realtime.watchlist_db import load_watchlist

# ── Config ──────────────────────────────────────────────────────
WATCHLIST_LIST_NAME      = os.getenv("WATCHLIST_NAME", "vip")
WATCHLIST_RELOAD_INTERVAL = 300  # giây — hot-reload watchlist từ DB mỗi 5 phút

DB_PATH         = os.getenv("SMD_DB_PATH", os.path.join(PROJECT_ROOT, "data", "securities_master.db"))
MASVN_URI       = "wss://mastrade.masvn.com/ws/"
HANDSHAKE_MSG   = {"e": ["#handshake", {"authToken": None}, 1]}

RECONNECT_BASE  = 3    # giây
RECONNECT_CAP   = 60
HANDSHAKE_TMO   = 8    # giây
FLUSH_INTERVAL  = 30   # giây — flush candle delta counter vào SQLite
HEARTBEAT_EVERY = 60   # giây — in ra QC report

VN_TZ = timezone(timedelta(hours=7))

# ── Logging ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [WLWorker] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_ROOT, "watchlist_worker.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# DEDUP CACHE  (fingerprint window 2 giây)
# ════════════════════════════════════════════════════════════════

class DedupCache:
    """
    Loại bỏ tick trùng giữa 2 worker chạy song song.
    Fingerprint = (symbol, price_int, vol) trong cửa sổ 2 giây.
    """
    WINDOW_MS = 2000  # 2 giây

    def __init__(self):
        self._cache: Dict[Tuple, int] = {}  # fingerprint → last_ts_ms

    def is_duplicate(self, symbol: str, price: float, vol: int, ts: datetime) -> bool:
        ts_ms = int(ts.timestamp() * 1000)
        window = ts_ms // self.WINDOW_MS          # bucket 2s
        fp = (symbol, int(price * 10), vol, window)

        if fp in self._cache:
            return True
        self._cache[fp] = ts_ms
        # Dọn cache cũ hơn 10s
        cutoff_window = (ts_ms - 10_000) // self.WINDOW_MS
        self._cache = {k: v for k, v in self._cache.items() if k[3] >= cutoff_window}
        return False


# ════════════════════════════════════════════════════════════════
# CANDLE DELTA ACCUMULATOR  (in-memory per 1m period)
# ════════════════════════════════════════════════════════════════

class CandleDeltaAccum:
    """
    Gom buy_vol/sell_vol theo từng nến 1m (per symbol).
    Flush vào SQLite mỗi FLUSH_INTERVAL giây.
    """

    def __init__(self):
        # { symbol: { period_start_str: {"buy": int, "sell": int} } }
        self._data: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"buy": 0, "sell": 0}))
        self._lock = asyncio.Lock()

    def ingest(self, symbol: str, vol: int, side: str, ts: datetime):
        period = ts.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:00")
        if side == "BUY":
            self._data[symbol][period]["buy"] += vol
        elif side == "SELL":
            self._data[symbol][period]["sell"] += vol

    async def flush(self, sec_map: Dict[str, int]) -> Dict[str, dict]:
        """
        Trả về snapshot current candles và xóa các period đã đóng.
        Trả về: { symbol: { period: {buy, sell} }  }
        """
        async with self._lock:
            now_period = datetime.now(VN_TZ).replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:00")
            snapshot = {}
            for sym, periods in self._data.items():
                snapshot[sym] = {}
                for period, counts in periods.items():
                    if period < now_period:  # period đã đóng → flush
                        snapshot[sym][period] = dict(counts)
            # Xóa period đã flush
            for sym in snapshot:
                for period in snapshot[sym]:
                    del self._data[sym][period]
            return snapshot


# ════════════════════════════════════════════════════════════════
# SQLITE UPDATER  (chỉ update buy_vol/sell_vol/delta)
# ════════════════════════════════════════════════════════════════

class SQLiteUpdater:
    """
    Update buy_vol/sell_vol/delta cho stock_prices rows đã tồn tại.
    KHÔNG tạo row mới — chỉ làm giàu dữ liệu đã có từ intraday_engine.
    """

    SQL_UPDATE = """
        UPDATE stock_prices
        SET buy_vol  = ?,
            sell_vol = ?,
            delta    = ?
        WHERE security_id = ?
          AND interval    = '1m'
          AND trade_time  IN (?, ?)
    """
    # Trade_time có thể là 'T' hoặc ' ' separator → thử cả hai

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._sec_map: Dict[str, int] = {}

    def open(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        rows = self.conn.execute(
            "SELECT symbol, security_id FROM securities WHERE asset_type='EQUITY'"
        ).fetchall()
        self._sec_map = {r[0]: r[1] for r in rows}
        logger.info(f"✅ SQLiteUpdater: loaded {len(self._sec_map)} symbol mappings")

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def update_batch(self, snapshot: Dict[str, Dict[str, dict]]) -> int:
        """
        Batch update buy_vol/sell_vol cho tất cả (symbol, period) trong snapshot.
        Trả về số row đã update.
        """
        if not snapshot or not self.conn:
            return 0

        updated = 0
        for sym, periods in snapshot.items():
            sec_id = self._sec_map.get(sym)
            if not sec_id:
                continue
            for period_str, counts in periods.items():
                buy  = counts["buy"]
                sell = counts["sell"]
                # trade_time có thể là T-sep hoặc space-sep
                t_sep   = period_str                     # "2026-04-28T10:15:00"
                sp_sep  = period_str.replace("T", " ")  # "2026-04-28 10:15:00"
                try:
                    cur = self.conn.execute("""
                        UPDATE stock_prices
                        SET buy_vol  = ?,
                            sell_vol = ?,
                            delta    = ?
                        WHERE security_id = ?
                          AND interval    = '1m'
                          AND trade_time IN (?, ?)
                    """, (buy, sell, buy - sell, sec_id, t_sep, sp_sep))
                    updated += cur.rowcount
                except Exception as e:
                    logger.warning(f"  update lỗi {sym} {period_str}: {e}")

        self.conn.commit()
        return updated

    @property
    def sec_map(self) -> Dict[str, int]:
        return self._sec_map


# ════════════════════════════════════════════════════════════════
# MASVN WEBSOCKET WORKER  (1 kết nối / 1 instance)
# ════════════════════════════════════════════════════════════════

class WatchlistMASVNSingleWorker:
    """
    Một kết nối MASVN WebSocket duy nhất, tự hồi sinh khi rớt.
    Subscribe chỉ WATCHLIST symbols.
    Truyền tick đã parse về on_tick callback.
    """

    def __init__(self, worker_id: str, symbols: List[str], on_tick: Callable):
        self.worker_id   = worker_id
        self.symbols     = symbols
        self.on_tick     = on_tick
        self._running    = False
        self._task: Optional[asyncio.Task] = None
        self.is_connected = False
        self.ticks_total  = 0
        self.ticks_by_sym: Dict[str, int] = defaultdict(int)
        self.reconnect_count = 0

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(
            self._reconnect_loop(), name=f"wl-worker-{self.worker_id}"
        )
        logger.info(f"🟡 Worker [{self.worker_id}] khởi động — {len(self.symbols)} mã watchlist")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"🔴 Worker [{self.worker_id}] đã dừng")

    async def _reconnect_loop(self):
        backoff = RECONNECT_BASE
        while self._running:
            try:
                await self._connect_and_listen()
                logger.warning(f"⚠️  [{self.worker_id}] kết nối đóng. Reconnect sau {backoff}s...")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"⚠️  [{self.worker_id}] lỗi: {type(e).__name__}: {e}. Reconnect sau {backoff}s...")

            if not self._running:
                break
            self.is_connected = False
            self.reconnect_count += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_CAP)

    async def _connect_and_listen(self):
        async with websockets.connect(
            MASVN_URI,
            ping_interval=None,
            max_size=None,
            close_timeout=10,
            open_timeout=15,
        ) as ws:
            # Handshake
            await ws.send(msgpack.packb(HANDSHAKE_MSG))
            raw = await asyncio.wait_for(ws.recv(), timeout=HANDSHAKE_TMO)
            if not isinstance(raw, bytes):
                raise ConnectionError("Handshake nhận string thay vì bytes")
            res = msgpack.unpackb(raw, strict_map_key=False)
            if not (isinstance(res, dict) and "r" in res):
                raise ConnectionError(f"Handshake thất bại: {res}")
            logger.info(f"✅ [{self.worker_id}] kết nối MASVN thành công!")

            # Subscribe watchlist
            cid = 2
            for sym in self.symbols:
                msg = {"e": ["#subscribe", {"channel": f"market.quote.{sym}"}, cid]}
                cid += 1
                await ws.send(msgpack.packb(msg))
            await asyncio.sleep(0.1)
            logger.info(f"📡 [{self.worker_id}] đã subscribe {len(self.symbols)} mã watchlist: {self.symbols}")
            self.is_connected = True

            # Listen loop
            async for msg in ws:
                if not self._running:
                    break
                if isinstance(msg, str):
                    await ws.send("")   # application-level pong
                    continue
                if isinstance(msg, bytes):
                    try:
                        data = msgpack.unpackb(msg, strict_map_key=False)
                    except Exception:
                        continue
                    if not isinstance(data, dict) or "r" in data:
                        continue
                    if "p" in data and isinstance(data["p"], list) and len(data["p"]) >= 2:
                        channel = data["p"][0]
                        payload = data["p"][1]
                        if isinstance(channel, str) and channel.startswith("market.quote."):
                            self._process_quote(payload)

        self.is_connected = False

    def _process_quote(self, data: dict):
        if not data:
            return
        try:
            symbol    = data.get("s")
            price_raw = data.get("c", 0)
            vol       = data.get("mv", 0)
            side      = data.get("mb", "NEUTRAL")

            if not symbol or not price_raw or not vol:
                return
            if symbol not in self.symbols:
                return

            price = round(price_raw / 1000.0, 2)
            ti    = data.get("ti")
            ts    = datetime.fromtimestamp(ti / 1000.0) if ti else datetime.now()

            self.on_tick(symbol, price, int(vol), ts, self.worker_id, side)
            self.ticks_total            += 1
            self.ticks_by_sym[symbol]   += 1

        except Exception as e:
            logger.debug(f"[{self.worker_id}] parse lỗi: {e}")


# ════════════════════════════════════════════════════════════════
# WATCHLIST WORKER MANAGER  (2× Active-Active + QC + SQLite flush)
# ════════════════════════════════════════════════════════════════

class WatchlistWorkerManager:
    """
    Điều phối 2 WatchlistMASVNSingleWorker (Active-Active) cho watchlist.
    Tích hợp:
      - DedupCache: loại trùng cross-worker
      - CandleDeltaAccum: gom buy/sell theo 1m candle
      - SQLiteUpdater: flush delta vào DB mỗi 30s
      - Heartbeat QC: báo cáo side_coverage mỗi 60s
      - Hot-reload watchlist từ DB mỗi WATCHLIST_RELOAD_INTERVAL giây
    """

    NUM_WORKERS = 2

    def __init__(self, list_name: str, db_path: str):
        self.list_name  = list_name
        self.db_path    = db_path
        self.symbols: List[str] = []

        self.dedup   = DedupCache()
        self.accum   = CandleDeltaAccum()
        self.updater = SQLiteUpdater(db_path)
        self.workers: List[WatchlistMASVNSingleWorker] = []

        # QC counters
        self._tick_total   = 0
        self._dedup_skip   = 0
        self._side_counts: Dict[str, Dict[str, int]] = {}

    def _on_tick(self, symbol: str, price: float, vol: int, ts: datetime, source: str, side: str):
        """Callback từ mỗi worker — dedup → accum → QC."""
        if symbol not in self.symbols:
            return
        if self.dedup.is_duplicate(symbol, price, vol, ts):
            self._dedup_skip += 1
            return

        self._tick_total += 1
        side_norm = side.upper() if side in ("BUY", "SELL") else "NEUTRAL"

        # Gom vào candle accumulator
        self.accum.ingest(symbol, vol, side_norm, ts)

        # QC counters
        if symbol not in self._side_counts:
            self._side_counts[symbol] = {"buy": 0, "sell": 0, "neutral": 0, "total": 0}
        sc = self._side_counts[symbol]
        sc["total"] += vol
        if side_norm == "BUY":     sc["buy"]     += vol
        elif side_norm == "SELL":  sc["sell"]    += vol
        else:                      sc["neutral"]  += vol

    def _reload_symbols_from_db(self) -> bool:
        """Reload watchlist từ DB. Trả về True nếu có thay đổi."""
        new_symbols = load_watchlist(list_name=self.list_name, db_path=self.db_path)
        if not new_symbols:
            logger.warning("hot-reload: DB trả về rỗng — giữ nguyên list cũ")
            return False
        if set(new_symbols) == set(self.symbols):
            return False
        added   = set(new_symbols) - set(self.symbols)
        removed = set(self.symbols) - set(new_symbols)
        self.symbols = new_symbols
        # Cập nhật subscribers của mỗi worker
        for w in self.workers:
            w.symbols = new_symbols
        if added:   logger.info(f"🔄 Watchlist reload: +{list(added)}")
        if removed: logger.info(f"🔄 Watchlist reload: -{list(removed)}")
        return True

    async def start(self):
        # Load watchlist lần đầu từ DB
        self.symbols = load_watchlist(list_name=self.list_name, db_path=self.db_path)
        if not self.symbols:
            logger.error("❌ Watchlist trống — kiểm tra DB bảng watchlists")
            return

        self.updater.open()
        logger.info(
            f"🚀 WatchlistWorkerManager: khởi động {self.NUM_WORKERS} workers "
            f"cho {len(self.symbols)} mã (list='{self.list_name}')"
        )
        logger.info(f"   Watchlist: {self.symbols}")

        for i in range(self.NUM_WORKERS):
            self.workers.append(
                WatchlistMASVNSingleWorker(
                    worker_id=f"WL-{i+1}",
                    symbols=self.symbols,
                    on_tick=self._on_tick,
                )
            )

        await asyncio.gather(*[w.start() for w in self.workers])

        # Background tasks
        asyncio.create_task(self._flush_loop(),          name="wl-flush-loop")
        asyncio.create_task(self._heartbeat_loop(),      name="wl-heartbeat-loop")
        asyncio.create_task(self._watchlist_reload_loop(), name="wl-reload-loop")

    async def stop(self):
        logger.info("🛑 WatchlistWorkerManager: dừng...")
        await asyncio.gather(*[w.stop() for w in self.workers])
        self.updater.close()

    # ── Watchlist Reload Loop ────────────────────────────────────

    async def _watchlist_reload_loop(self):
        """Mỗi WATCHLIST_RELOAD_INTERVAL giây: kiểm tra DB có thay đổi không."""
        while True:
            await asyncio.sleep(WATCHLIST_RELOAD_INTERVAL)
            try:
                changed = self._reload_symbols_from_db()
                if changed:
                    # Reconnect workers để subscribe lại với list mới
                    logger.info("🔄 Watchlist đã thay đổi — reconnect workers để re-subscribe...")
                    for w in self.workers:
                        if w._ws:
                            try:
                                await w._ws.close()
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"Watchlist reload loop lỗi: {e}")

    # ── Flush Loop ───────────────────────────────────────────────

    async def _flush_loop(self):
        """Mỗi FLUSH_INTERVAL giây: flush candle delta → SQLite."""
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            try:
                snapshot = await self.accum.flush(self.updater.sec_map)
                if snapshot:
                    updated = self.updater.update_batch(snapshot)
                    total_periods = sum(len(p) for p in snapshot.values())
                    logger.info(
                        f"💾 Flush: {total_periods} candle periods → {updated} rows updated in SQLite"
                    )
            except Exception as e:
                logger.error(f"Flush loop lỗi: {e}")

    # ── Heartbeat & QC ───────────────────────────────────────────

    async def _heartbeat_loop(self):
        """Mỗi HEARTBEAT_EVERY giây: in QC report."""
        while True:
            await asyncio.sleep(HEARTBEAT_EVERY)
            try:
                self._print_qc_report()
            except Exception as e:
                logger.error(f"Heartbeat lỗi: {e}")

    def _print_qc_report(self):
        now_vn = datetime.now(VN_TZ).strftime("%H:%M:%S")
        w_status = " | ".join(
            f"{w.worker_id}={'✅' if w.is_connected else '❌'}(ticks={w.ticks_total}, rc={w.reconnect_count})"
            for w in self.workers
        )
        logger.info(f"━━━━━━ WATCHLIST QC REPORT [{now_vn}] (list='{self.list_name}') ━━━━━━")
        logger.info(f"  Workers  : {w_status}")
        logger.info(f"  Ticks    : total={self._tick_total} | dedup_skip={self._dedup_skip}")
        logger.info(f"  {'SYM':<6} {'Vol(K)':>8} {'Buy%':>6} {'Sell%':>6} {'Neutral%':>9} {'SideCov%':>9}")
        logger.info(f"  {'─'*52}")
        for sym in self.symbols:
            sc = self._side_counts.get(sym, {})
            total = sc.get("total", 0)
            buy   = sc.get("buy", 0)
            sell  = sc.get("sell", 0)
            neu   = sc.get("neutral", 0)
            if total > 0:
                buy_pct  = buy  * 100.0 / total
                sell_pct = sell * 100.0 / total
                neu_pct  = neu  * 100.0 / total
                cov_pct  = (buy + sell) * 100.0 / total
                health = "✅" if cov_pct >= 80 else ("⚠️" if cov_pct >= 50 else "❌")
            else:
                buy_pct = sell_pct = neu_pct = cov_pct = 0.0
                health = "⏳"
            logger.info(
                f"  {sym:<6} {total/1000:>8.1f}K {buy_pct:>5.1f}% {sell_pct:>6.1f}% "
                f"{neu_pct:>8.1f}%  {cov_pct:>7.1f}% {health}"
            )
        logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


# ════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ════════════════════════════════════════════════════════════════

async def main():
    logger.info("═" * 60)
    logger.info("  🎯 WATCHLIST MASVN WORKER — khởi động")
    logger.info(f"  DB: {DB_PATH}")
    logger.info(f"  Watchlist list_name: '{WATCHLIST_LIST_NAME}'")
    logger.info(f"  Hot-reload mỗi: {WATCHLIST_RELOAD_INTERVAL}s")
    logger.info("═" * 60)

    manager = WatchlistWorkerManager(
        list_name = WATCHLIST_LIST_NAME,
        db_path   = DB_PATH,
    )

    try:
        await manager.start()
        # Chạy mãi — PM2 sẽ restart khi process die
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info("⌨️  Keyboard interrupt — đang dừng worker...")
    finally:
        await manager.stop()
        logger.info("👋 Worker đã dừng hẳn.")


if __name__ == "__main__":
    asyncio.run(main())
