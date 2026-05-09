#!/usr/bin/env python3
"""
scripts/side_imputer.py
==============================================
Vá lại buy_vol / sell_vol cho các nến 1m có volume > 0 nhưng buy_vol = 0.

Phương pháp: Volume Position Method (Bulk Volume Classification)
  buy_frac  = (close - low) / (high - low)
  buy_vol   = round(volume × buy_frac)
  sell_vol  = volume − buy_vol

  Cơ sở lý thuyết (Easley, López de Prado, O'Hara 2012 — PIN model):
    Nếu giá đóng cửa ở gần HIGH → phần lớn volume là mua chủ động
    Nếu giá đóng cửa ở gần LOW  → phần lớn volume là bán chủ động
    Độ chính xác: ~65-72% so với tick data thực tế

  Ưu điểm so với NEUTRAL (buy_vol=0):
    - Cung cấp signal có hướng thay vì không có gì
    - Phù hợp cho tính VWAP delta khi thiếu tick data

Cách chạy:
  python3 scripts/side_imputer.py              # Dry-run (preview, not commit)
  python3 scripts/side_imputer.py --commit     # Commit vào DB
  python3 scripts/side_imputer.py --date 2026-04-22 --commit
  python3 scripts/side_imputer.py --since 2026-01-09 --commit
"""

import os
import sys
import sqlite3
import logging
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _load_env():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_env()

DB_PATH = Path(os.environ.get("SMD_DB_PATH",
               PROJECT_ROOT / "data" / "securities_master.db"))
VN_TZ   = timezone(timedelta(hours=7))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Imputer] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# CORE LOGIC
# ============================================================

def _estimate_side(open_: float, high: float, low: float,
                   close: float, volume: int) -> tuple[int, int]:
    """
    Volume Position Method — ước tính buy_vol / sell_vol từ OHLCV.

    Trả về (buy_vol, sell_vol).
    """
    rng = high - low
    if rng < 1e-6 or volume <= 0:
        # Candle đứng giá (rng = 0) → dùng open vs close
        if close >= open_:
            return volume, 0        # Nến xanh → all buy
        else:
            return 0, volume        # Nến đỏ  → all sell

    buy_frac = (close - low) / rng
    buy_frac = max(0.0, min(1.0, buy_frac))   # clamp [0, 1]
    buy_vol  = round(volume * buy_frac)
    sell_vol = volume - buy_vol
    return int(buy_vol), int(sell_vol)


def run_imputer(
    conn: sqlite3.Connection,
    date_filter: str = None,
    since_filter: str = None,
    commit: bool = False,
) -> dict:
    """
    Tìm và vá toàn bộ nến 1m có volume > 0 nhưng buy_vol = 0 & sell_vol = 0.

    Trả về stats dict.
    """
    # Xây query lọc
    where_clauses = [
        "interval = '1m'",
        "volume > 0",
        "(buy_vol IS NULL OR buy_vol = 0)",
        "(sell_vol IS NULL OR sell_vol = 0)",
    ]
    params: list = []

    if date_filter:
        where_clauses.append("date(trade_time) = ?")
        params.append(date_filter)
    elif since_filter:
        where_clauses.append("date(trade_time) >= ?")
        params.append(since_filter)

    where_sql = " AND ".join(where_clauses)

    # Count trước
    count_row = conn.execute(
        f"SELECT COUNT(*) FROM stock_prices WHERE {where_sql}", params
    ).fetchone()
    total_to_fix = count_row[0]
    logger.info(f"Tìm thấy {total_to_fix:,} nến 1m cần impute (buy_vol=0)")

    if total_to_fix == 0:
        return {'total_found': 0, 'imputed': 0, 'preview': 0, 'skipped': 0}

    # Kéo data
    rows = conn.execute(
        f"""
        SELECT rowid, open, high, low, close, volume
        FROM   stock_prices
        WHERE  {where_sql}
        ORDER  BY trade_time
        """,
        params,
    ).fetchall()

    updates  = []
    skipped  = 0

    for r in rows:
        rowid, o, h, l, c, v = r
        if None in (o, h, l, c) or v <= 0:
            skipped += 1
            continue
        buy_vol, sell_vol = _estimate_side(o, h, l, c, v)
        updates.append((buy_vol, sell_vol, buy_vol - sell_vol, rowid))

    logger.info(
        f"  → {len(updates):,} nến sẽ được impute | {skipped:,} bỏ qua (OHLC null)"
    )

    # Thống kê mẫu
    if updates:
        sample = updates[:5]
        logger.info("  Mẫu 5 nến đầu tiên:")
        for bv, sv, d, _ in sample:
            logger.info(f"    buy={bv:,}  sell={sv:,}  delta={d:+,}")

    if commit and updates:
        # Batch UPDATE từng 5000 rows
        batch_size = 5_000
        for i in range(0, len(updates), batch_size):
            batch = updates[i:i + batch_size]
            conn.executemany(
                """
                UPDATE stock_prices
                SET    buy_vol  = ?,
                       sell_vol = ?,
                       delta    = ?
                WHERE  rowid    = ?
                """,
                batch,
            )
            conn.commit()
            logger.info(f"  Committed batch {i//batch_size + 1}: {len(batch):,} rows")

        logger.info(f"✅ Imputed {len(updates):,} nến 1m vào DB")
    elif not commit:
        logger.info("⚠️  DRY-RUN — dùng --commit để ghi vào DB")

    return {
        'total_found' : total_to_fix,
        'imputed'     : len(updates) if commit else 0,
        'preview'     : len(updates),
        'skipped'     : skipped,
    }


# ============================================================
# POST-CHECK: Cập nhật lại daily_vwap_summary
# ============================================================

def rebuild_daily_vwap_for_imputed_dates(
    conn: sqlite3.Connection,
    date_filter: str = None,
    since_filter: str = None,
    commit: bool = False,
):
    """
    Sau khi impute, rebuild daily_vwap_summary cho các ngày bị ảnh hưởng
    để cum_delta / buy_vol / sell_vol được cập nhật đúng.
    """
    if not commit:
        return

    try:
        from scripts.daily_vwap_builder import compute_for_date, get_available_dates
    except ImportError:
        logger.warning("⚠️  Không import được daily_vwap_builder, bỏ qua rebuild VWAP")
        return

    if date_filter:
        dates = [date_filter]
    elif since_filter:
        dates = get_available_dates(conn, since=since_filter)
    else:
        today = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        dates = [today]

    logger.info(f"🔄 Rebuild daily_vwap_summary cho {len(dates)} ngày...")
    total = 0
    for d in dates:
        total += compute_for_date(conn, d)
    logger.info(f"✅ Rebuilt {total} mã-ngày trong daily_vwap_summary")


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Side Imputer — OHLCV Volume Position Method")
    parser.add_argument("--date",   type=str, help="Chỉ impute 1 ngày cụ thể (YYYY-MM-DD)")
    parser.add_argument("--since",  type=str, help="Impute từ ngày này trở đi (YYYY-MM-DD)")
    parser.add_argument("--commit", action="store_true",
                        help="Ghi kết quả vào DB (mặc định: dry-run)")
    parser.add_argument("--no-vwap-rebuild", action="store_true",
                        help="Bỏ qua rebuild daily_vwap_summary sau khi impute")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))

    logger.info(f"{'DRY-RUN' if not args.commit else 'COMMIT MODE'} | DB: {DB_PATH}")
    if args.date:
        logger.info(f"Filter: date = {args.date}")
    elif args.since:
        logger.info(f"Filter: since = {args.since}")
    else:
        logger.info("Filter: toàn bộ lịch sử 1m")

    stats = run_imputer(
        conn,
        date_filter  = args.date,
        since_filter = args.since,
        commit       = args.commit,
    )

    logger.info(f"\n📊 Kết quả:")
    logger.info(f"  Tìm thấy cần fix: {stats['total_found']:,}")
    logger.info(f"  Preview sẽ impute: {stats['preview']:,}")
    logger.info(f"  Đã committed:      {stats['imputed']:,}")
    logger.info(f"  Skipped (OHLC null): {stats['skipped']:,}")

    if not args.no_vwap_rebuild:
        rebuild_daily_vwap_for_imputed_dates(
            conn,
            date_filter  = args.date,
            since_filter = args.since,
            commit       = args.commit,
        )

    conn.close()


if __name__ == "__main__":
    main()
