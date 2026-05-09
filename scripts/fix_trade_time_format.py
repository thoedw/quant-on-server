#!/usr/bin/env python3
"""
scripts/fix_trade_time_format.py
==============================================
One-time migration: chuẩn hóa trade_time về ISO 8601 (VN local, no TZ suffix).

VẤN ĐỀ PHÁT HIỆN (2026-04-23):
  - Có 2 format song song trong DB:
      * "2026-04-23T14:29:00"  (ISO_T  — từ MASVN WebSocket)
      * "2026-04-23 14:29:00"  (SPACE  — từ DNSE/EOD sync)
  - Cả 2 đều là VN local time (UTC+7), KHÔNG phải UTC.
  - UNIQUE constraint dùng raw string → T ≠ SPACE → có thể duplicate.
  - Nhiều query cũ dùng date(trade_time, '+7 hours') → SAI với VN local time.

GIẢi PHÁP:
  1. Chuẩn hóa TẤT CẢ SPACE → ISO_T   (UPDATE in-place)
  2. Xóa 10 duplicate rows             (DELETE giữ row có buy_vol > 0)
  3. Thêm UNIQUE constraint thực tế    (đã có qua PRIMARY KEY)
  4. Rebuild 2 index dùng date() sai   (đổi thành date(trade_time) đúng)

Cách chạy:
  python3 scripts/fix_trade_time_format.py --dry-run   # preview
  python3 scripts/fix_trade_time_format.py --commit    # apply
"""

import os
import sys
import sqlite3
import logging
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = Path(os.environ.get("SMD_DB_PATH",
               PROJECT_ROOT / "data" / "securities_master.db"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [TimeFix] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def audit(conn: sqlite3.Connection) -> dict:
    """Đếm số rows cần fix."""
    space_count = conn.execute(
        "SELECT COUNT(*) FROM stock_prices WHERE trade_time LIKE '% %'"
    ).fetchone()[0]

    dup_count = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT security_id, interval, replace(trade_time,'T',' ') as t_norm
            FROM stock_prices
            GROUP BY security_id, interval, t_norm
            HAVING COUNT(*) > 1
        )
    """).fetchone()[0]

    return {"space_rows": space_count, "duplicate_groups": dup_count}


def step1_normalize_space_to_T(conn: sqlite3.Connection, commit: bool) -> int:
    """
    Chuẩn hóa: '2026-04-23 14:29:00' → '2026-04-23T14:29:00'
    Chỉ update SPACE format, không đụng ISO_T rows.
    """
    count = conn.execute(
        "SELECT COUNT(*) FROM stock_prices WHERE trade_time LIKE '% %'"
    ).fetchone()[0]
    logger.info(f"STEP 1: {count:,} rows SPACE format cần normalize")

    if count == 0:
        return 0

    if commit:
        # Batch update 10k
        BATCH = 10_000
        total_done = 0
        while True:
            conn.execute("""
                UPDATE stock_prices
                SET trade_time = replace(trade_time, ' ', 'T')
                WHERE rowid IN (
                    SELECT rowid FROM stock_prices
                    WHERE trade_time LIKE '% %'
                    LIMIT ?
                )
            """, (BATCH,))
            changed = conn.total_changes - (conn.total_changes - conn.execute(
                "SELECT changes()"
            ).fetchone()[0])
            # Dùng cách khác: check remaining
            remaining = conn.execute(
                "SELECT COUNT(*) FROM stock_prices WHERE trade_time LIKE '% %'"
            ).fetchone()[0]
            total_done = count - remaining
            logger.info(f"  Progress: {total_done:,}/{count:,} (còn {remaining:,})")
            conn.commit()
            if remaining == 0:
                break
        logger.info(f"✅ STEP 1 done: {count:,} rows normalized")
    else:
        logger.info("  (dry-run) sẽ update bằng: UPDATE ... SET trade_time = replace(trade_time,' ','T')")

    return count


def step2_remove_duplicates(conn: sqlite3.Connection, commit: bool) -> int:
    """
    Xóa duplicate rows bằng pure SQL bulk DELETE (nhanh hơn Python loop).
    Giữ row có buy_vol cao nhất (hoặc rowid nhỏ nhất nếu tie).
    """
    dup_count = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT security_id, interval, replace(trade_time,'T',' ') as t_norm
            FROM stock_prices
            GROUP BY security_id, interval, t_norm
            HAVING COUNT(*) > 1
        )
    """).fetchone()[0]

    logger.info(f"STEP 2: {dup_count:,} nhóm duplicate")

    if dup_count == 0:
        return 0

    if commit:
        # Tìm rowid "winner" mỗi group: max buy_vol, nếu tie thì rowid nhỏ nhất
        # Sau đó DELETE tất cả rowid KHÔNG phải winner.
        conn.execute("""
            DELETE FROM stock_prices
            WHERE rowid NOT IN (
                SELECT winner_rowid FROM (
                    SELECT
                        MIN(CASE WHEN bv_rank=1 THEN rowid END) AS winner_rowid
                    FROM (
                        SELECT
                            rowid,
                            security_id,
                            interval,
                            replace(trade_time,' ','T') AS t_key,
                            COALESCE(buy_vol,0) AS bv,
                            RANK() OVER (
                                PARTITION BY security_id, interval, replace(trade_time,' ','T')
                                ORDER BY COALESCE(buy_vol,0) DESC, rowid ASC
                            ) AS bv_rank
                        FROM stock_prices
                    )
                    GROUP BY security_id, interval, t_key
                )
            )
        """)
        deleted = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        logger.info(f"✅ STEP 2 done: deleted {deleted:,} duplicate rows (SQL bulk)")
    else:
        # Estimate
        total_extra = conn.execute("""
            SELECT SUM(cnt-1) FROM (
                SELECT COUNT(*) as cnt
                FROM stock_prices
                GROUP BY security_id, interval, replace(trade_time,'T',' ')
                HAVING cnt > 1
            )
        """).fetchone()[0] or 0
        logger.info(f"  (dry-run) sẽ DELETE ~{total_extra:,} duplicate rows")
        deleted = 0

    return deleted


def step3_fix_index(conn: sqlite3.Connection, commit: bool):
    """
    Drop và rebuild index idx_sp_interval_date:
    Sai:    date(trade_time)          ← trade_time đã là local VN, không cần offset
    Đúng:   date(trade_time)          ← vẫn đúng nếu trade_time là VN local
    
    Vấn đề thực sự là CÁC QUERY trong code, không phải index.
    Nhưng ta rebuild index để đảm bảo consistency sau khi rename T.
    """
    logger.info("STEP 3: Rebuild indexes (REINDEX)")
    if commit:
        conn.execute("REINDEX idx_sp_interval_date")
        conn.execute("REINDEX idx_stock_prices_security_time")
        conn.execute("REINDEX idx_sp_security_interval_time")
        conn.commit()
        logger.info("✅ STEP 3 done: indexes rebuilt")
    else:
        logger.info("  (dry-run) sẽ REINDEX 3 indexes")


def step4_verify(conn: sqlite3.Connection):
    """
    Xác minh sau migration.
    """
    logger.info("\n=== POST-MIGRATION VERIFICATION ===")

    space_left = conn.execute(
        "SELECT COUNT(*) FROM stock_prices WHERE trade_time LIKE '% %'"
    ).fetchone()[0]
    logger.info(f"  SPACE format còn lại: {space_left} (expected 0)")

    iso_count = conn.execute(
        "SELECT COUNT(*) FROM stock_prices WHERE trade_time LIKE '%T%'"
    ).fetchone()[0]
    logger.info(f"  ISO_T format tổng:    {iso_count:,}")

    dup_left = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT security_id, interval, trade_time
            FROM stock_prices
            GROUP BY security_id, interval, trade_time
            HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    logger.info(f"  Duplicate groups còn: {dup_left} (expected 0)")

    # Sample check
    samples = conn.execute("""
        SELECT s.symbol, sp.trade_time, sp.interval
        FROM stock_prices sp JOIN securities s ON sp.security_id=s.security_id
        WHERE s.symbol IN ('SAB','SHB','HPG') AND sp.interval='1m'
        ORDER BY sp.trade_time DESC LIMIT 6
    """).fetchall()
    logger.info("  Sample trade_time sau fix:")
    for r in samples:
        logger.info(f"    {r[0]:6} [{r[2]}] {r[1]}")

    # Kiểm tra query date() không dùng offset
    cnt_sab = conn.execute("""
        SELECT COUNT(*) FROM stock_prices sp
        JOIN securities s ON sp.security_id=s.security_id
        WHERE s.symbol='SAB' AND sp.interval='1m'
          AND date(sp.trade_time)='2026-04-23'
    """).fetchone()[0]
    logger.info(f"  SAB 1m date('2026-04-23') không offset: {cnt_sab} nến (expected ~220)")

    cnt_sab_wrong = conn.execute("""
        SELECT COUNT(*) FROM stock_prices sp
        JOIN securities s ON sp.security_id=s.security_id
        WHERE s.symbol='SAB' AND sp.interval='1m'
          AND date(sp.trade_time,'+7 hours')='2026-04-23'
    """).fetchone()[0]
    logger.info(f"  SAB 1m date('+7 hours')='2026-04-23': {cnt_sab_wrong} nến (expected 0 sau fix)")

    return space_left == 0 and dup_left == 0


def main():
    parser = argparse.ArgumentParser(
        description="Fix trade_time format: SPACE → ISO_T, remove duplicates"
    )
    parser.add_argument("--commit", action="store_true",
                        help="Ghi vào DB (mặc định: dry-run)")
    parser.add_argument("--skip-normalize", action="store_true")
    parser.add_argument("--skip-dedup", action="store_true")
    parser.add_argument("--skip-reindex", action="store_true")
    args = parser.parse_args()

    mode = "COMMIT" if args.commit else "DRY-RUN"
    logger.info(f"{'='*60}")
    logger.info(f"fix_trade_time_format.py — {mode}")
    logger.info(f"DB: {DB_PATH}")
    logger.info(f"{'='*60}\n")

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # Audit trước
    info = audit(conn)
    logger.info(f"PRE-AUDIT: space_rows={info['space_rows']:,}  dup_groups={info['duplicate_groups']}")
    logger.info("")

    # ⚠️  DEDUP trước, NORMALIZE sau:
    # Nếu normalize trước → UNIQUE constraint fail khi SPACE row bị đổi thành T
    # mà T row đã tồn tại. Phải xóa bản sao SPACE trước rồi mới normalize.
    if not args.skip_dedup:
        step2_remove_duplicates(conn, commit=args.commit)
        logger.info("")

    if not args.skip_normalize:
        step1_normalize_space_to_T(conn, commit=args.commit)
        logger.info("")

    if not args.skip_reindex:
        step3_fix_index(conn, commit=args.commit)
        logger.info("")

    ok = step4_verify(conn)

    if args.commit:
        if ok:
            logger.info("\n✅ Migration hoàn tất! DB đã sạch.")
        else:
            logger.error("\n❌ Còn vấn đề sau migration — kiểm tra lại!")
    else:
        logger.info("\n⚠️  DRY-RUN hoàn tất. Dùng --commit để apply.")

    conn.close()


if __name__ == "__main__":
    main()
