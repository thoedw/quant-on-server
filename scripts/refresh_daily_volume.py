"""
scripts/refresh_daily_volume.py
Cập nhật bảng ref_prices (dùng cho Price Board Dashboard) từ stock_prices 1D.

THAY ĐỔI KIẾN TRÚC (2026-04-16):
  - TRƯỚC: Gọi DNSE API trực tiếp (fetch nến 1m để tổng hợp volume) → lãng phí 1 vòng API.
  - SAU:   Đọc thẳng từ stock_prices (interval='1D') vừa được eod_daily_close.py cập nhật.
  - Kết quả: Không còn gọi DNSE 2 lần trong cùng 1 khung 15:45, tiết kiệm tài nguyên.

Chạy sau eod_daily_close.py (hoặc sau khi biết stock_prices 1D đã có dữ liệu hôm nay).
"""
import sqlite3
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / 'data' / 'securities_master.db'

def refresh_from_stock_prices(conn: sqlite3.Connection, today_str: str) -> int:
    """
    Đọc nến 1D của ngày hôm nay từ stock_prices và upsert vào ref_prices.
    Trả về số mã được cập nhật.
    """
    rows = conn.execute("""
        SELECT s.symbol, sp.open, sp.high, sp.low, sp.close, sp.volume
        FROM stock_prices sp
        JOIN securities s ON sp.security_id = s.security_id
        WHERE sp.interval = '1D'
          AND date(sp.trade_time) = ?
          AND s.asset_type = 'EQUITY'
        ORDER BY s.symbol
    """, (today_str,)).fetchall()

    if not rows:
        logger.warning(f"Không tìm thấy nến 1D ngày {today_str} trong stock_prices.")
        logger.warning("Hãy chắc chắn eod_daily_close.py đã chạy xong trước.")
        return 0

    # Upsert vào ref_prices
    conn.executemany("""
        INSERT INTO ref_prices
            (symbol, ref_price, ceil_price, floor_price,
             last_price, high_price, low_price, total_vol, trade_val, fetched_at)
        VALUES (?, 0, 0, 0, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(symbol) DO UPDATE SET
            last_price = excluded.last_price,
            high_price = excluded.high_price,
            low_price  = excluded.low_price,
            total_vol  = excluded.total_vol,
            trade_val  = excluded.trade_val,
            fetched_at = excluded.fetched_at
    """, [
        (
            symbol,
            round(close, 2),                            # last_price = close
            round(high, 2),
            round(low, 2),
            int(volume),
            round(volume * close / 1e6, 2),             # trade_val (tỷ VND ước tính)
        )
        for (symbol, open_, high, low, close, volume) in rows
    ])
    conn.commit()
    return len(rows)


def print_top_volume(conn: sqlite3.Connection, n: int = 10):
    sample = conn.execute("""
        SELECT symbol, total_vol, last_price, high_price, low_price, trade_val
        FROM ref_prices
        WHERE total_vol > 0
        ORDER BY total_vol DESC
        LIMIT ?
    """, (n,)).fetchall()

    logger.info(f"\n📊 Top {n} Volume hôm nay:")
    logger.info(f"  {'Symbol':6s} | {'Volume':>14s} | {'Close':>8s} | {'High':>8s} | {'Low':>8s} | {'Val(tỷ)':>9s}")
    logger.info(f"  {'-'*65}")
    for r in sample:
        logger.info(
            f"  {r[0]:6s} | {r[1]:>14,.0f} | {r[2]:>8.2f} | {r[3]:>8.2f} | {r[4]:>8.2f} | {r[5]:>9,.0f}"
        )


def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    t0 = datetime.now()

    logger.info("=" * 60)
    logger.info(f"🔄 REFRESH DAILY VOLUME (from stock_prices) — {today_str}")
    logger.info("=" * 60)

    conn = sqlite3.connect(DB_PATH, detect_types=0)

    count = refresh_from_stock_prices(conn, today_str)

    elapsed = (datetime.now() - t0).total_seconds()
    logger.info(f"✅ Cập nhật {count} mã vào ref_prices. Thời gian: {elapsed:.2f}s")

    if count > 0:
        print_top_volume(conn)

    conn.close()


if __name__ == "__main__":
    main()
