#!/usr/bin/env python3
"""
scripts/daily_vwap_builder.py
==============================================
Xây dựng bảng daily_vwap_summary từ nến 1m intraday.

Mục đích:
  - Tính VWAP cuối ngày (anchored 9:15 VN) cho mỗi mã mỗi ngày
  - Lưu Cum Delta, buy_vol, sell_vol → phân tích dòng tiền đa phiên
  - PVWAP anchor cho Swing Whale Hunter signals:
      🎯 PVWAP_SUPPORT_TEST: giá test PVWAP hôm qua và bật lên (BUY)
      🔴 PVWAP_BREAKDOWN:    giá đột phá xuống dưới PVWAP với vol (SELL)

Cách chạy:
  python3 scripts/daily_vwap_builder.py              # Hôm nay
  python3 scripts/daily_vwap_builder.py --backfill   # Backfill toàn bộ
  python3 scripts/daily_vwap_builder.py --date 2026-04-21
  python3 scripts/daily_vwap_builder.py --backfill --since 2026-04-01
"""

import os
import sys
import math
import sqlite3
import logging
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

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
    format="%(asctime)s [%(levelname)s] [DailyVWAP] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# SCHEMA
# ============================================================

DDL_TABLE = """
CREATE TABLE IF NOT EXISTS daily_vwap_summary (
    security_id   INTEGER NOT NULL,
    trade_date    TEXT    NOT NULL,   -- YYYY-MM-DD (múi giờ VN+7)
    vwap          REAL    NOT NULL,   -- VWAP phiên (anchored 9:15)
    vwap_std      REAL    NOT NULL,   -- Volume-weighted σ
    vwap_upper1   REAL    NOT NULL,   -- VWAP + 1σ
    vwap_lower1   REAL    NOT NULL,   -- VWAP - 1σ
    vwap_upper2   REAL    NOT NULL,   -- VWAP + 2σ
    vwap_lower2   REAL    NOT NULL,   -- VWAP - 2σ
    cum_volume    INTEGER NOT NULL,   -- Tổng volume phiên
    cum_delta     INTEGER NOT NULL,   -- Tổng (buy_vol - sell_vol)
    buy_vol       INTEGER DEFAULT 0,  -- Tổng khối lượng mua chủ động
    sell_vol      INTEGER DEFAULT 0,  -- Tổng khối lượng bán chủ động
    side_cov_pct  REAL    DEFAULT 0,  -- (buy+sell)/volume × 100
    session_open  REAL    DEFAULT 0,  -- Giá nến 1m đầu tiên lúc 9:15
    session_close REAL    DEFAULT 0,  -- Giá nến 1m cuối cùng
    PRIMARY KEY (security_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_dvs_date ON daily_vwap_summary(trade_date);
CREATE INDEX IF NOT EXISTS idx_dvs_sid  ON daily_vwap_summary(security_id, trade_date);
"""


def ensure_schema(conn: sqlite3.Connection):
    for stmt in DDL_TABLE.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
    logger.info("✅ Schema daily_vwap_summary OK")


# ============================================================
# CORE COMPUTATION
# ============================================================

def _session_open_utc(date_vn: str) -> str:
    """9:15 VN → UTC ISO string để query trade_time trong DB."""
    dt_vn = datetime.strptime(date_vn, "%Y-%m-%d").replace(
        hour=9, minute=15, tzinfo=VN_TZ
    )
    return dt_vn.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def compute_for_date(conn: sqlite3.Connection, date_vn: str) -> int:
    """
    Tính VWAP summary cho tất cả mã có dữ liệu 1m ngày date_vn.
    Trả về số mã đã tính.
    """
    session_open = _session_open_utc(date_vn)  # lower bound (kept for reference)

    # Kéo toàn bộ nến 1m, kể cả nến ATC.
    # ATC candle: Open=giá cuối continuous trading, Close=giá đóng cửa chính thức (ATC).
    # H=L=Close (single-price auction) → BVC Parkinson = 50/50 (sai).
    # Fix đúng: bất đầu từ classify_bvc, ATC sẽ dùng hướng close-vs-open thay vì Parkinson.
    # Không loại ATC vì: (1) là volume thật, (2) giá Close = giá đóng cửa chính thức.
    rows = conn.execute("""
        SELECT sp.security_id, sp.trade_time, sp.open, sp.close, sp.volume,
               COALESCE(sp.buy_vol, 0)  AS bv,
               COALESCE(sp.sell_vol, 0) AS sv
        FROM   stock_prices sp
        JOIN   securities   s  ON s.security_id = sp.security_id
        WHERE  sp.interval  = '1m'
          AND  date(sp.trade_time) = ?
          AND  sp.volume > 0
        ORDER  BY sp.security_id, sp.trade_time
    """, (date_vn,)).fetchall()

    if not rows:
        logger.warning(f"  {date_vn}: Không có dữ liệu 1m")
        return 0

    # Nhóm theo security_id
    grouped: dict[int, list] = defaultdict(list)
    for r in rows:
        grouped[r[0]].append(r)

    records = []
    for sid, candles in grouped.items():
        if len(candles) < 3:  # Tối thiểu 3 nến (tránh mã bị halt)
            continue

        cum_pv = cum_vol = cum_delta = cum_pv2 = 0.0
        buy_vol = sell_vol = 0

        for c in candles:
            p  = c[3] or 0.0
            v  = c[4] or 0
            bv = c[5] or 0
            sv = c[6] or 0

            cum_pv    += p * v
            cum_vol   += v
            cum_delta += bv - sv
            cum_pv2   += p * p * v
            buy_vol   += bv
            sell_vol  += sv

        if cum_vol == 0:
            continue

        vwap     = cum_pv / cum_vol
        variance = max(0.0, (cum_pv2 / cum_vol) - vwap ** 2)
        std      = math.sqrt(variance)
        side_cov = round((buy_vol + sell_vol) * 100.0 / cum_vol, 1)

        records.append((
            sid, date_vn,
            round(vwap, 4),
            round(std, 4),
            round(vwap + std, 4),
            round(vwap - std, 4),
            round(vwap + 2 * std, 4),
            round(vwap - 2 * std, 4),
            int(cum_vol),
            int(cum_delta),
            int(buy_vol),
            int(sell_vol),
            side_cov,
            candles[0][3]  or 0.0,   # session_open  (open của nến đầu tiên)
            candles[-1][3] or 0.0,   # session_close (close của nến cuối)
        ))

    if records:
        conn.executemany("""
            INSERT INTO daily_vwap_summary
                (security_id, trade_date,
                 vwap, vwap_std,
                 vwap_upper1, vwap_lower1, vwap_upper2, vwap_lower2,
                 cum_volume, cum_delta,
                 buy_vol, sell_vol, side_cov_pct,
                 session_open, session_close)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(security_id, trade_date) DO UPDATE SET
                vwap          = excluded.vwap,
                vwap_std      = excluded.vwap_std,
                vwap_upper1   = excluded.vwap_upper1,
                vwap_lower1   = excluded.vwap_lower1,
                vwap_upper2   = excluded.vwap_upper2,
                vwap_lower2   = excluded.vwap_lower2,
                cum_volume    = excluded.cum_volume,
                cum_delta     = excluded.cum_delta,
                buy_vol       = excluded.buy_vol,
                sell_vol      = excluded.sell_vol,
                side_cov_pct  = excluded.side_cov_pct,
                session_open  = excluded.session_open,
                session_close = excluded.session_close
        """, records)
        conn.commit()

    logger.info(f"  {date_vn}: {len(records)} mã ✅")
    return len(records)


def get_available_dates(conn: sqlite3.Connection, since: str = None) -> list[str]:
    """Lấy danh sách ngày có dữ liệu 1m trong DB."""
    q = """
        SELECT DISTINCT date(trade_time) AS d
        FROM   stock_prices
        WHERE  interval = '1m' AND volume > 0
    """
    params = []
    if since:
        q += " AND date(trade_time) >= ?"
        params.append(since)
    q += " ORDER BY d"
    return [r[0] for r in conn.execute(q, params).fetchall()]


def print_summary(conn: sqlite3.Connection):
    """In nhanh thống kê bảng daily_vwap_summary."""
    row = conn.execute("""
        SELECT COUNT(DISTINCT trade_date) AS n_days,
               COUNT(*) AS total_rows,
               MIN(trade_date), MAX(trade_date),
               ROUND(AVG(side_cov_pct), 1)
        FROM daily_vwap_summary
    """).fetchone()
    if row and row[0]:
        logger.info(f"📊 daily_vwap_summary: {row[0]} ngày | {row[1]:,} mã-ngày | "
                    f"{row[2]} → {row[3]} | avg side_cov={row[4]}%")


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Xây dựng bảng daily_vwap_summary")
    parser.add_argument("--date",     type=str, help="Tính 1 ngày cụ thể (YYYY-MM-DD)")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill toàn bộ lịch sử 1m")
    parser.add_argument("--since",    type=str, default=None,
                        help="Kết hợp --backfill, từ ngày nào (YYYY-MM-DD)")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    ensure_schema(conn)

    if args.date:
        dates = [args.date]
    elif args.backfill:
        dates = get_available_dates(conn, since=args.since)
        logger.info(f"Backfill {len(dates)} ngày "
                    f"({dates[0] if dates else '?'} → {dates[-1] if dates else '?'})")
    else:
        today = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        dates = [today]
        logger.info(f"Chạy cho hôm nay: {today}")

    total = 0
    for d in dates:
        total += compute_for_date(conn, d)

    logger.info(f"\n✅ Hoàn tất. {total} mã-ngày đã upsert vào daily_vwap_summary.")
    print_summary(conn)
    conn.close()


if __name__ == "__main__":
    main()
