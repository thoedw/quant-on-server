"""
price_gap_filler.py — Pre-Quant Data Quality Gate

Vai trò: Cán bộ QC kiểm soát chất lượng dữ liệu giá trước khi chạy bất kỳ
tiến trình quant interday nào.

Chiến lược:
1. DETECT: Tìm ngày giao dịch bị thiếu data trong DB cho từng interval
2. VALIDATE: Phân biệt gap thật vs ngày nghỉ (dùng inference từ DB)
3. FILL: Tự động gọi DNSE async để lấp đầy gap
4. REPORT: Báo cáo coverage chi tiết
5. GATE: Exit 0 nếu OK, exit 1 nếu còn gap không fill được

Giới hạn DNSE thực tế (đã kiểm chứng):
  1m:        Chỉ năm hiện tại
  5m/15m/30m: Không có lịch sử (chỉ realtime intra)
  1H:        Từ ~2023
  1D:        Từ 2015
  1W:        Từ 2024

Trading Calendar: Infer từ DB (ngày nào >= 3 blue-chip có 1D OHLCV = trading day)

Usage:
  python3 scripts/price_gap_filler.py                  (detect + auto-fill + report)
  python3 scripts/price_gap_filler.py --check-only     (chỉ report, không fill)
  python3 scripts/price_gap_filler.py --interval 1D    (chỉ check 1 interval)
  python3 scripts/price_gap_filler.py --lookback 30    (kiểm tra 30 ngày gần nhất)
  python3 scripts/price_gap_filler.py --symbols VNM,HPG (chỉ check subset mã)
"""

import os
import sys
import asyncio
import aiohttp
import sqlite3
import argparse
import logging
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from securities_master.extractors.async_dnse_extractor import AsyncDNSEExtractor
from securities_master.loaders.sqlite_loader import SQLiteLoader

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("GapFiller")

# ─── Config ───────────────────────────────────────────────────────────────────
CONCURRENCY = 30  # requests song song (tăng nếu DNSE chưa chặn)

# Số ngày không có data → đánh dấu INACTIVE (delisted/suspended)
INACTIVE_CUTOFF_DAYS = 60

# Các interval DNSE có thể gap-fill
FILLABLE_INTERVALS = {
    "1H": "2023-01-01",   # DNSE có từ 2023
    "1D": "2015-01-01",   # DNSE có từ 2015
    "1W": "2024-01-01",   # DNSE có từ 2024
    "1m": None,           # Chỉ fill ngày hôm nay (year-only)
}

# Blue-chip mã dùng để infer trading calendar
CALENDAR_ANCHORS = ["VNM", "VCB", "HPG", "FPT", "MWG", "TCB", "VHM", "BID", "CTG", "VIC"]
MIN_ANCHORS = 3  # Ít nhất 3 mã có data = ngày giao dịch

# ─── Data Models ─────────────────────────────────────────────────────────────
@dataclass
class GapInfo:
    symbol: str
    security_id: int
    interval: str
    missing_date: str   # yyyy-mm-dd


@dataclass
class FillResult:
    filled: int = 0        # Số lượng gap đã fill thành công
    filled_rows: int = 0   # Số nến đã ghi vào DB
    failed: int = 0        # Lỗi API/network thực sự
    skipped: int = 0       # DNSE không có data (không thể kiểm soát)
    details: list = field(default_factory=list)


# ─── Active Symbol Management ───────────────────────────────────────────────

def auto_mark_inactive(conn: sqlite3.Connection) -> tuple[int, int]:
    """
    Tự động đánh dấu is_active=0 cho 2 nhóm mã "chết" để bỏ qua khi fill gap:
      1. Mã hoàn toàn không có bất kỳ 1D row nào trong INACTIVE_CUTOFF_DAYS ngày.
      2. Mã có 1D row nhưng volume = 0 liên tục (tạm ngưng / bị halt giao dịch).

    Phục hồi is_active=1 nếu mã quay trở lại có volume thực sự.
    Returns: (marked_inactive, recovered_active)
    """
    # Đảm bảo cột tồn tại (idempotent migration)
    try:
        conn.execute("ALTER TABLE securities ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        conn.commit()
        logger.info("📋 Đã thêm cột is_active vào schema")
    except sqlite3.OperationalError:
        pass  # Đã tồn tại

    before = conn.execute(
        "SELECT COUNT(*) FROM securities WHERE asset_type='EQUITY' AND is_active=0"
    ).fetchone()[0]

    # Nhóm 1: không có row 1D nào trong X ngày (delisted hoàn toàn)
    conn.execute(f"""
        UPDATE securities
        SET is_active = 0
        WHERE asset_type = 'EQUITY'
          AND security_id NOT IN (
              SELECT DISTINCT security_id FROM stock_prices
              WHERE interval = '1D'
                AND date(trade_time) >= date('now', '-{INACTIVE_CUTOFF_DAYS} days')
          )
    """)

    # Nhóm 2: có row 1D nhưng volume = 0 liên tục → tạm ngưng / bị halt
    conn.execute(f"""
        UPDATE securities
        SET is_active = 0
        WHERE asset_type = 'EQUITY'
          AND security_id IN (
              SELECT security_id FROM stock_prices
              WHERE interval = '1D'
                AND date(trade_time) >= date('now', '-{INACTIVE_CUTOFF_DAYS} days')
              GROUP BY security_id
              HAVING SUM(volume) = 0
          )
    """)

    after_dead = conn.execute(
        "SELECT COUNT(*) FROM securities WHERE asset_type='EQUITY' AND is_active=0"
    ).fetchone()[0]
    marked = after_dead - before

    # Phục hồi: mã quay lại có volume thực sự trong 30 ngày
    conn.execute("""
        UPDATE securities
        SET is_active = 1
        WHERE asset_type = 'EQUITY'
          AND is_active = 0
          AND security_id IN (
              SELECT security_id FROM stock_prices
              WHERE interval = '1D'
                AND date(trade_time) >= date('now', '-30 days')
              GROUP BY security_id
              HAVING SUM(volume) > 0
          )
    """)
    after_recovery = conn.execute(
        "SELECT COUNT(*) FROM securities WHERE asset_type='EQUITY' AND is_active=0"
    ).fetchone()[0]
    recovered = after_dead - after_recovery

    conn.commit()
    return marked, recovered


# ─── Trading Calendar ─────────────────────────────────────────────────────────
def build_trading_calendar(conn: sqlite3.Connection, from_date: str, to_date: str) -> set[str]:
    """
    Infer ngày giao dịch từ DB: ngày nào >= MIN_ANCHORS mã blue-chip có 1D data.
    Chính xác 100%, không phụ thuộc API ngoài, tự cập nhật mỗi ngày.
    """
    placeholders = ",".join("?" * len(CALENDAR_ANCHORS))
    rows = conn.execute(f"""
        SELECT date(sp.trade_time) AS dt,
               COUNT(DISTINCT sp.security_id) AS n
        FROM stock_prices sp
        JOIN securities s ON sp.security_id = s.security_id
        WHERE sp.interval = '1D'
          AND s.symbol IN ({placeholders})
          AND sp.volume > 0
          AND date(sp.trade_time) BETWEEN ? AND ?
        GROUP BY dt
        HAVING n >= ?
        ORDER BY dt
    """, CALENDAR_ANCHORS + [from_date, to_date, MIN_ANCHORS]).fetchall()

    calendar = {r[0] for r in rows}
    logger.info(f"📅 Trading calendar: {len(calendar)} ngày GD trong {from_date} → {to_date}")
    return calendar


def get_expected_trading_days(calendar: set[str], from_date: str, to_date: str) -> list[str]:
    """
    Trả về danh sách ngày giao dịch kỳ vọng trong khoảng.
    Dùng calendar làm nguồn sự thật, loại thứ 7/CN bổ sung.
    """
    result = []
    current = datetime.strptime(from_date, "%Y-%m-%d").date()
    end     = datetime.strptime(to_date,   "%Y-%m-%d").date()
    while current <= end:
        ds = current.strftime("%Y-%m-%d")
        # Ngày trong calendar HOẶC ngày thường (Mon-Fri) nếu calendar chưa có data tương lai
        if ds in calendar or current.weekday() < 5:
            if ds in calendar:
                result.append(ds)
        current += timedelta(days=1)
    return result


# ─── Gap Detection ────────────────────────────────────────────────────────────
def detect_gaps_for_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    security_id: int,
    interval: str,
    trading_days: list[str],
) -> list[GapInfo]:
    """
    Tìm ngày giao dịch có trong calendar nhưng vắng trong DB cho symbol+interval.
    """
    rows = conn.execute("""
        SELECT date(trade_time) AS dt
        FROM stock_prices
        WHERE security_id = ? AND interval = ?
          AND date(trade_time) BETWEEN ? AND ?
    """, (security_id, interval, trading_days[0], trading_days[-1])).fetchall()

    existing = {r[0] for r in rows}
    return [
        GapInfo(symbol=symbol, security_id=security_id,
                interval=interval, missing_date=d)
        for d in trading_days
        if d not in existing
    ]


def detect_all_gaps(
    conn: sqlite3.Connection,
    symbols_info: list[tuple],   # [(symbol, security_id, exchange)]
    intervals: list[str],
    lookback_days: int,
) -> list[GapInfo]:
    """Quét toàn bộ mã × interval để tìm gap."""
    today = date.today()
    from_date_map = {}
    for iv in intervals:
        effective_start = FILLABLE_INTERVALS.get(iv)
        if effective_start:
            from_d = max(
                datetime.strptime(effective_start, "%Y-%m-%d").date(),
                today - timedelta(days=lookback_days)
            )
        else:
            from_d = today - timedelta(days=lookback_days)
        from_date_map[iv] = from_d.strftime("%Y-%m-%d")

    to_date = today.strftime("%Y-%m-%d")

    # Build calendar chung theo range rộng nhất
    global_from = min(from_date_map.values())
    calendar = build_trading_calendar(conn, global_from, to_date)

    all_gaps: list[GapInfo] = []
    for (sym, sec_id, _) in symbols_info:
        for iv in intervals:
            from_date = from_date_map[iv]
            trading_days = [d for d in sorted(calendar) if d >= from_date and d <= to_date]
            if not trading_days:
                continue
            gaps = detect_gaps_for_symbol(conn, sym, sec_id, iv, trading_days)
            all_gaps.extend(gaps)

    return all_gaps


# ─── Gap Fill ────────────────────────────────────────────────────────────────
async def fill_one_gap(
    gap: GapInfo,
    extractor: AsyncDNSEExtractor,
    loader: SQLiteLoader,
) -> dict:
    """Fetch DNSE và upsert vào DB cho 1 gap cụ thể."""
    start_str = gap.missing_date + " 00:00:00"
    end_str   = gap.missing_date + " 23:59:59"
    try:
        records = await extractor.fetch_ohlcv(
            gap.symbol, gap.security_id,
            start_str, end_str,
            gap.interval
        )
        if not records:
            return {"gap": gap, "status": "no_data", "count": 0}

        loader.load_prices(records)
        return {"gap": gap, "status": "filled", "count": len(records)}

    except Exception as e:
        logger.warning(f"[{gap.symbol}-{gap.interval}-{gap.missing_date}] Fill lỗi: {e}")
        return {"gap": gap, "status": "failed", "error": str(e), "count": 0}

async def fill_all_gaps(gaps: list[GapInfo], db_path: str) -> FillResult:
    """Fill tất cả gaps song song."""
    if not gaps:
        return FillResult()

    result = FillResult()
    sem = asyncio.Semaphore(CONCURRENCY)

    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ttl_dns_cache=300)
    session_timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(connector=connector, timeout=session_timeout) as session:
        from securities_master.database import DatabaseManager
        extractor = AsyncDNSEExtractor(session, sem)
        db_manager = DatabaseManager(db_path)
        loader = SQLiteLoader(db_manager)
        _conn = db_manager.get_connection()

        tasks = [fill_one_gap(g, extractor, loader) for g in gaps]

        # Progress tracking
        done = 0
        total = len(tasks)
        import time
        t0 = time.time()
        for coro in asyncio.as_completed(tasks):
            r = await coro
            done += 1
            if r["status"] == "filled":
                result.filled += 1
                result.filled_rows += r["count"]
            elif r["status"] == "no_data":
                result.skipped += 1
            else:
                result.failed += 1
                result.details.append(r)

            # Log tiến độ mỗi 500 gap (bao gồm cả skipped)
            if done % 500 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                logger.info(
                    f"   Fill tiến độ: {done:,}/{total:,} ({done*100//total}%) | "
                    f"✅{result.filled} ⏭{result.skipped} ❌{result.failed} | "
                    f"ETA: {eta:.0f}s"
                )

        _conn.commit()
        _conn.close()

    return result


# ─── Report ───────────────────────────────────────────────────────────────────
def print_gap_report(gaps: list[GapInfo], result: FillResult, check_only: bool):
    """In báo cáo tổng hợp."""
    from collections import Counter
    gap_by_iv = Counter(g.interval for g in gaps)

    logger.info("=" * 65)
    logger.info("🔍 DATA QUALITY GATE — Báo cáo Gap")
    logger.info(f"   Tổng gap phát hiện: {len(gaps):,}")
    for iv, cnt in sorted(gap_by_iv.items()):
        logger.info(f"   {iv:5s}: {cnt:,} gaps")

    if not check_only:
        total_attempt = result.filled_rows + result.skipped + result.failed
        logger.info(f"\n   ✅ Filled: {result.filled:,} gap → {result.filled_rows:,} nến")
        logger.info(f"   ⏭️  Skipped: {result.skipped:,} (DNSE không có data — không thể fill)")
        logger.info(f"   ❌ Failed: {result.failed:,} (Lỗi API/network cần kiểm tra)")

    if gaps:
        # Top mã bị gap nhiều nhất
        gap_by_sym = Counter(g.symbol for g in gaps)
        worst = gap_by_sym.most_common(5)
        logger.info(f"\n   🔴 Top 5 mã gap nhiều nhất:")
        for sym, cnt in worst:
            logger.info(f"      {sym}: {cnt} ngày thiếu (có thể do thổi khoản thấp)")
    else:
        logger.info("\n   ✅ Không phát hiện gap nào — Data sạch!")

    logger.info("=" * 65)


# ─── Market Index Gap Detection + Fill ────────────────────────────────────────

INDEX_CODES    = ['VNINDEX', 'VN30', 'VN100', 'HNX30']
INDEX_API_GF   = 'https://services.entrade.com.vn/chart-api/v2/ohlcs/index'
INDEX_TF_RES   = {'1m':'1','5m':'5','15m':'15','30m':'30','1H':'1H','1D':'1D','1W':'1W'}

# Lookback tối đa cho từng TF khi detect gap (giới hạn DNSE API)
INDEX_TF_LOOKBACK = {
    '1m':  3,      # chỉ fill 3 ngày gần nhất
    '5m':  20,
    '15m': 60,
    '30m': 120,
    '1H':  365,
    '1D':  365 * 3,
    '1W':  365 * 5,
}


def detect_index_gaps(
    conn: sqlite3.Connection,
    calendar: set[str],
    lookback_days: int,
) -> list[dict]:
    """
    Tìm ngày giao dịch thiếu trong bảng market_indices.
    So sánh trading calendar vs dữ liệu thực tế trong DB.
    Returns: list of {'code', 'tf', 'date'}
    """
    # Bảng có tồn tại không?
    tbl = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='market_indices'"
    ).fetchone()
    if not tbl:
        logger.info("  [index gap] Bảng market_indices chưa có — bỏ qua")
        return []

    today_str = date.today().strftime('%Y-%m-%d')
    gaps = []

    for code in INDEX_CODES:
        for tf, max_lb in INDEX_TF_LOOKBACK.items():
            effective_lb = min(lookback_days, max_lb)
            from_date = (
                date.today() - timedelta(days=effective_lb)
            ).strftime('%Y-%m-%d')

            existing = {
                r[0] for r in conn.execute("""
                    SELECT date(trade_time) FROM market_indices
                    WHERE index_code = ? AND interval = ?
                      AND date(trade_time) BETWEEN ? AND ?
                """, (code, tf, from_date, today_str)).fetchall()
            }

            # Ngày kỳ vọng từ calendar
            expected = {d for d in calendar if from_date <= d <= today_str}
            for d in sorted(expected - existing):
                gaps.append({'code': code, 'tf': tf, 'date': d})

    return gaps


def _fill_one_index_gap(code: str, tf: str, missing_date: str) -> list[dict]:
    """
    Fetch 1 ngày thiếu cho 1 index/TF từ DNSE.
    Returns: list of record dicts (rỗng nếu lỗi hoặc DNSE không có data).
    """
    import requests as _req
    from datetime import timezone as _tz, timedelta as _td

    res = INDEX_TF_RES.get(tf)
    if not res:
        return []

    from_dt = datetime.strptime(missing_date, '%Y-%m-%d')
    # Offset UTC: VN = UTC+7, DNSE API dùng UTC epoch
    from_ts = int((from_dt - _td(hours=7)).replace(tzinfo=_tz.utc).timestamp())
    to_ts   = from_ts + 86400 + 3600  # +1 ngày +1h buffer

    hdrs   = {'Origin': 'https://entrade.com.vn', 'User-Agent': 'Mozilla/5.0'}
    params = {'symbol': code, 'resolution': res, 'from': from_ts, 'to': to_ts}

    try:
        r = _req.get(INDEX_API_GF, params=params, headers=hdrs, timeout=12)
        if r.status_code != 200:
            return []
        d = r.json()
        t_arr = d.get('t', [])
        if not t_arr:
            return []

        records = []
        for i, ts in enumerate(t_arr):
            dt_vn = (
                datetime.fromtimestamp(ts, tz=_tz.utc) + _td(hours=7)
            ).replace(tzinfo=None)
            records.append({
                'index_code': code,
                'interval':   tf,
                'trade_time': dt_vn.strftime('%Y-%m-%dT%H:%M:%S'),
                'open':  float(d['o'][i]),
                'high':  float(d['h'][i]),
                'low':   float(d['l'][i]),
                'close': float(d['c'][i]),
                'volume': int(d['v'][i]) if d.get('v') else 0,
            })
        return records
    except Exception as exc:
        logger.warning(f"  [index gap] {code}/{tf}/{missing_date}: {exc}")
        return []


def fill_all_index_gaps(
    gaps: list[dict],
    conn: sqlite3.Connection,
    check_only: bool = False,
) -> dict:
    """
    Fill tất cả index gaps (đồng bộ).
    Vì chỉ có 4 × 7 = 28 combinations, không cần async.
    Returns: {'detected': N, 'filled': N, 'rows': N, 'skipped': N, 'failed': N}
    """
    stats = {'detected': len(gaps), 'filled': 0, 'rows': 0, 'skipped': 0, 'failed': 0}
    if not gaps or check_only:
        return stats

    for g in gaps:
        records = _fill_one_index_gap(g['code'], g['tf'], g['date'])
        if not records:
            stats['skipped'] += 1
            continue
        try:
            conn.executemany("""
                INSERT INTO market_indices
                    (index_code, interval, trade_time, open, high, low, close, volume)
                VALUES (:index_code, :interval, :trade_time,
                        :open, :high, :low, :close, :volume)
                ON CONFLICT(index_code, interval, trade_time) DO UPDATE SET
                    open   = excluded.open, high  = excluded.high,
                    low    = excluded.low,  close = excluded.close,
                    volume = excluded.volume
            """, records)
            conn.commit()
            stats['filled'] += 1
            stats['rows']   += len(records)
        except Exception as exc:
            logger.warning(f"  [index gap upsert] {g}: {exc}")
            stats['failed'] += 1

    return stats


def print_index_gap_report(stats: dict, check_only: bool):
    """In báo cáo index gap fill."""
    if stats['detected'] == 0:
        logger.info("  ✅ [Index] Không phát hiện gap — market_indices sạch!")
        return
    logger.info(f"  🔍 [Index] {stats['detected']} gaps phát hiện")
    if not check_only:
        logger.info(f"      ✅ Filled: {stats['filled']} gap ({stats['rows']} rows)")
        logger.info(f"      ⏭️  Skipped: {stats['skipped']} (DNSE no data)")
        logger.info(f"      ❌ Failed : {stats['failed']}")


# ─── Pre-Quant Gate ──────────────────────────────────────────────────────────
def pre_quant_check(
    conn: sqlite3.Connection,
    intervals: list[str] = ["1D"],
    lookback_days: int = 5,
) -> bool:
    """
    Quick check: DB có đủ data không?
    Sử dụng embed trong whale_hunter / vwap / backtest.
    Returns True nếu data OK, False nếu có gap cần fix.
    """
    today = date.today()
    from_date = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")
    calendar = build_trading_calendar(conn, from_date, to_date)

    if not calendar:
        logger.warning("⚠️ Không xây được Trading Calendar — DB thiếu 1D data!")
        return False

    last_trading_day = max(calendar)
    for iv in intervals:
        rows = conn.execute("""
            SELECT COUNT(DISTINCT security_id)
            FROM stock_prices
            WHERE interval = ? AND date(trade_time) = ?
        """, (iv, last_trading_day)).fetchone()
        n = rows[0] if rows else 0
        if n < 100:  # Ít hơn 100 mã = có vấn đề
            logger.warning(f"⚠️ [{iv}] Ngày {last_trading_day}: chỉ có {n} mã — có thể đang gap!")
            return False

    return True


# ─── Main ────────────────────────────────────────────────────────────────────
async def main(
    check_only: bool = False,
    intervals: list[str] = None,
    lookback_days: int = 30,
    filter_symbols: list[str] = None,
):
    db_path = os.getenv("SMD_DB_PATH", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "securities_master.db"
    ))

    conn = sqlite3.connect(db_path)
    t_start = datetime.now()

    logger.info("=" * 65)
    logger.info("🔍 PRICE DATA QUALITY GATE — Khởi động")
    logger.info(f"   Mode: {'Check Only' if check_only else 'Auto Fill'}")
    logger.info(f"   Lookback: {lookback_days} ngày")
    logger.info(f"   Intervals: {intervals}")
    logger.info("=" * 65)

    # Auto-mark inactive symbols trước (tránh quét gap vô ích)
    marked, recovered = auto_mark_inactive(conn)
    if marked > 0 or recovered > 0:
        logger.info(f"📋 Cập nhật trạng thái: {marked} mã → INACTIVE | {recovered} mã → ACTIVE trở lại")

    # Lấy danh sách mã (CHỈ mã ACTIVE)
    if filter_symbols:
        pl = ",".join("?" * len(filter_symbols))
        symbols_info = conn.execute(f"""
            SELECT symbol, security_id, exchange FROM securities
            WHERE asset_type='EQUITY'
              AND COALESCE(is_active, 1) = 1
              AND symbol IN ({pl})
            ORDER BY symbol
        """, filter_symbols).fetchall()
    else:
        symbols_info = conn.execute("""
            SELECT symbol, security_id, exchange FROM securities
            WHERE asset_type='EQUITY'
              AND COALESCE(is_active, 1) = 1
            ORDER BY symbol
        """).fetchall()

    # Lấy số mã inactive để hiển thị
    inactive_count = conn.execute("""
        SELECT COUNT(*) FROM securities
        WHERE asset_type='EQUITY' AND COALESCE(is_active, 1) = 0
    """).fetchone()[0]

    logger.info(f"📦 Kiểm tra {len(symbols_info):,} mã ACTIVE × {len(intervals)} intervals"
                f" (bỏ qua {inactive_count} mã INACTIVE/Delisted)")

    # Detect gaps
    gaps = detect_all_gaps(conn, symbols_info, intervals, lookback_days)
    conn.close()

    logger.info(f"🔍 Phát hiện {len(gaps):,} gap cần xử lý")

    # Fill (nếu không phải check-only)
    result = FillResult()
    if not check_only and gaps:
        # Lọc chỉ những interval có thể fill
        fillable_gaps = [g for g in gaps if g.interval in FILLABLE_INTERVALS]
        unfillable = [g for g in gaps if g.interval not in FILLABLE_INTERVALS]

        if unfillable:
            logger.warning(f"⚠️ {len(unfillable)} gap ở interval không fill được (5m/15m/30m lịch sử)")

        if fillable_gaps:
            logger.info(f"🔧 Bắt đầu fill {len(fillable_gaps):,} gap...")
            result = await fill_all_gaps(fillable_gaps, db_path)

    # Report stocks
    print_gap_report(gaps, result, check_only)

    # ── Index Gap Fill ────────────────────────────────────────────
    conn2 = sqlite3.connect(db_path)
    logger.info("\n🔍 INDEX GAP CHECK — market_indices")
    index_gaps = detect_index_gaps(conn2, calendar, lookback_days)
    idx_stats  = fill_all_index_gaps(index_gaps, conn2, check_only)
    print_index_gap_report(idx_stats, check_only)
    conn2.close()

    duration = (datetime.now() - t_start).total_seconds()
    logger.info(f"⏱️  Tổng thời gian: {duration:.1f}s")

    # Gate: chỉ fail khi có lỗi API thực sự (network/timeout).
    # Skipped (DNSE không có data) = bình thường với mã thanh khoản thấp.
    # check-only không fail gate (chỉ report).
    if result.failed > 0:
        logger.error(f"❌ Gate FAIL — {result.failed} lỗi API khi fill. Kiểm tra kết nối DNSE!")
        sys.exit(1)
    elif check_only and len(gaps) > 0:
        logger.warning(
            f"⚠️ Gate CHECK-ONLY: Phát hiện {len(gaps):,} gap. "
            f"Chạy `quant-check` để auto-fill."
        )
        sys.exit(0)   # Exit 0 vì đây chỉ là cảnh báo, không phải lỗi cần chặn pipeline
    else:
        filled_pct = (
            f"{result.filled}/{result.filled + result.skipped} gaps filled"
            if not check_only else "chưa fill"
        )
        logger.info(f"✅ Gate PASS — {filled_pct}. Sẵn sàng chạy quant!")
        sys.exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Price Data Quality Gate — Pre-Quant Data Check & Auto Fill"
    )
    parser.add_argument("--check-only", action="store_true",
                        help="Chỉ report gap, không fill")
    parser.add_argument("--interval", type=str, default=None,
                        help="Interval cụ thể (1D/1H/1W). Mặc định: 1D,1H,1W")
    parser.add_argument("--lookback", type=int, default=30,
                        help="Số ngày nhìn lại (mặc định: 30)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Subset mã kiểm tra (VD: VNM,HPG). Mặc định: tất cả")
    args = parser.parse_args()

    # Xác định intervals cần check
    if args.interval:
        intervals = [args.interval.strip()]
    else:
        # Default: chỉ check các interval có thể fill được từ lịch sử
        intervals = ["1D", "1H", "1W"]

    filter_syms = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    asyncio.run(main(
        check_only=args.check_only,
        intervals=intervals,
        lookback_days=args.lookback,
        filter_symbols=filter_syms,
    ))
