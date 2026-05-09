#!/usr/bin/env python3
"""
oracle_pull_hq.py
=================
Pull historical data từ Oracle HQ (172.16.21.40:1521/vietnam)
về SQLite local.

Tables pulled:
  - SSI01H00  → hq_daily_ohlc    (11.3M rows, 2009→2026, daily OHLC)
  - TSO01H10  → hq_trade_ticks   (3.2M rows, 2025→2026, matched trades)

Usage:
  python3 scripts/oracle_pull_hq.py [--table ohlc|ticks|all] [--since 20240101]
"""
import os, sys, time, argparse, logging, sqlite3
from datetime import datetime

try:
    import oracledb
except ImportError:
    print("Thiếu oracledb: pip install oracledb")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────
ORACLE_DSN  = "172.16.21.40:1521/vietnam"
ORACLE_USER = "vn"
ORACLE_PASS = "vn"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH     = os.path.join(PROJECT_ROOT, "data", "securities_master.db")
BATCH_SIZE  = 50_000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("OraclePull")

# ── SQLite schema ─────────────────────────────────────────────
DDL_OHLC = """
CREATE TABLE IF NOT EXISTS hq_daily_ohlc (
    symbol      TEXT    NOT NULL,
    trade_date  TEXT    NOT NULL,   -- YYYY-MM-DD
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    avg_price   REAL,               -- AVG_PRI (VWAP-like)
    ceiling     REAL,               -- MAX_PRI
    floor_price REAL,               -- DN_PRI
    exchange    TEXT,               -- STK_MKT_TP (HNX/HOSE/UPCOM)
    stk_type    TEXT,               -- STK_TP
    PRIMARY KEY (symbol, trade_date)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_hq_ohlc_date   ON hq_daily_ohlc(trade_date);
CREATE INDEX IF NOT EXISTS idx_hq_ohlc_symbol ON hq_daily_ohlc(symbol);
"""

DDL_TICKS = """
CREATE TABLE IF NOT EXISTS hq_trade_ticks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    trade_date  TEXT    NOT NULL,   -- YYYY-MM-DD
    trade_time  TEXT    NOT NULL,   -- HH:MM:SS
    price       REAL    NOT NULL,
    qty         INTEGER NOT NULL,
    branch      TEXT                -- BNH_CD
);
CREATE INDEX IF NOT EXISTS idx_hq_ticks_sym_dt
    ON hq_trade_ticks(symbol, trade_date, trade_time);
"""

def init_sqlite(con):
    """Tạo tables nếu chưa có."""
    for ddl in DDL_OHLC.strip().split(";"):
        ddl = ddl.strip()
        if ddl: con.execute(ddl)
    for ddl in DDL_TICKS.strip().split(";"):
        ddl = ddl.strip()
        if ddl: con.execute(ddl)
    con.commit()
    log.info("SQLite schema ready")

def fmt_date(yyyymmdd: str) -> str:
    """20091231 → 2009-12-31"""
    d = str(yyyymmdd)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"

# ── Pull SSI01H00 → hq_daily_ohlc ────────────────────────────
def pull_ohlc(ora_cur, sqlite_con, since: str = "20090101"):
    log.info(f"=== PULL SSI01H00 (daily OHLC) từ {since} ===")

    # Đếm tổng
    ora_cur.execute(f"SELECT COUNT(*) FROM SSI01H00 WHERE DT >= :since", {"since": since})
    total = ora_cur.fetchone()[0]
    log.info(f"  Tổng rows cần pull: {total:,}")

    # Lấy offset cuối đã pull
    row = sqlite_con.execute(
        "SELECT MAX(trade_date) FROM hq_daily_ohlc"
    ).fetchone()[0]
    resume_from = row.replace("-","") if row else since
    if resume_from > since:
        log.info(f"  Resume từ {resume_from} (đã có data đến {row})")
        since = resume_from

    # Query Oracle — batch theo date range
    ora_cur.execute(f"""
        SELECT STK_CD, DT,
               STRT_PRI, HIGH_PRI, LOW_PRI, CLS_PRI, AVG_PRI,
               MAX_PRI, DN_PRI,
               STK_MKT_TP, STK_TP
        FROM SSI01H00
        WHERE DT >= :since
        ORDER BY DT, STK_CD
    """, {"since": since})

    inserted = 0
    t0 = time.time()
    while True:
        rows = ora_cur.fetchmany(BATCH_SIZE)
        if not rows: break

        records = []
        for r in rows:
            stk, dt, op, hi, lo, cl, avg, ceil_, fl, mkt, stp = r
            records.append((
                str(stk).strip(),
                fmt_date(str(dt)),
                float(op)   if op   else None,
                float(hi)   if hi   else None,
                float(lo)   if lo   else None,
                float(cl)   if cl   else None,
                float(avg)  if avg  else None,
                float(ceil_) if ceil_ else None,
                float(fl)   if fl   else None,
                str(mkt).strip() if mkt else None,
                str(stp).strip() if stp else None,
            ))

        sqlite_con.executemany("""
            INSERT OR REPLACE INTO hq_daily_ohlc
            (symbol, trade_date, open, high, low, close, avg_price,
             ceiling, floor_price, exchange, stk_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, records)
        sqlite_con.commit()

        inserted += len(records)
        elapsed = time.time() - t0
        rate = inserted / elapsed if elapsed else 0
        pct = inserted / total * 100 if total else 0
        log.info(f"  OHLC: {inserted:>9,} / {total:,}  ({pct:.1f}%)  "
                 f"speed={rate:.0f}r/s  eta={((total-inserted)/rate/60):.1f}min")

    log.info(f"✅ OHLC done: {inserted:,} rows in {(time.time()-t0)/60:.1f}min")

# ── Pull TSO01H10 → hq_trade_ticks ───────────────────────────
def pull_ticks(ora_cur, sqlite_con, since: str = "20250101"):
    log.info(f"=== PULL TSO01H10 (trade ticks) từ {since} ===")

    ora_cur.execute(
        "SELECT COUNT(*) FROM TSO01H10 WHERE STK_ORD_DT >= :since AND DEL_YN='N'",
        {"since": since}
    )
    total = ora_cur.fetchone()[0]
    log.info(f"  Tổng rows: {total:,}")

    # Resume
    row = sqlite_con.execute(
        "SELECT MAX(trade_date||'T'||trade_time) FROM hq_trade_ticks"
    ).fetchone()[0]
    if row:
        resume_dt = row[:10].replace("-","")
        log.info(f"  Resume từ {resume_dt}")
        since = resume_dt

    ora_cur.execute(f"""
        SELECT STK_CD, STK_ORD_DT, MTH_TIME,
               MTH_PRI, MTH_QTY, BNH_CD
        FROM TSO01H10
        WHERE STK_ORD_DT >= :since AND DEL_YN='N'
        ORDER BY STK_ORD_DT, MTH_TIME, STK_CD
    """, {"since": since})

    inserted = 0
    t0 = time.time()
    while True:
        rows = ora_cur.fetchmany(BATCH_SIZE)
        if not rows: break

        records = []
        for r in rows:
            stk, dt, tm, pri, qty, bnh = r
            records.append((
                str(stk).strip(),
                fmt_date(str(dt)),
                str(tm).strip() if tm else "00:00:00",
                float(pri) if pri else 0,
                int(qty)   if qty else 0,
                str(bnh).strip() if bnh else None,
            ))

        sqlite_con.executemany("""
            INSERT INTO hq_trade_ticks
            (symbol, trade_date, trade_time, price, qty, branch)
            VALUES (?,?,?,?,?,?)
        """, records)
        sqlite_con.commit()

        inserted += len(records)
        elapsed = time.time() - t0
        rate = inserted / elapsed if elapsed else 0
        log.info(f"  Ticks: {inserted:>7,} / {total:,}  "
                 f"speed={rate:.0f}r/s")

    log.info(f"✅ Ticks done: {inserted:,} rows in {(time.time()-t0)/60:.1f}min")

# ── Main ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Pull Oracle HQ → SQLite")
    ap.add_argument("--table",  choices=["ohlc","ticks","all"], default="all")
    ap.add_argument("--since",  default=None,
                    help="Start date YYYYMMDD (default: ohlc=20090101, ticks=20250101)")
    args = ap.parse_args()

    log.info("Kết nối Oracle HQ...")
    ora_con = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN)
    ora_cur = ora_con.cursor()
    ora_cur.arraysize = BATCH_SIZE
    log.info("✅ Oracle connected")

    sqlite_con = sqlite3.connect(DB_PATH, timeout=30)
    sqlite_con.execute("PRAGMA journal_mode=WAL")
    sqlite_con.execute("PRAGMA synchronous=NORMAL")
    sqlite_con.execute("PRAGMA cache_size=-64000")  # 64MB cache
    init_sqlite(sqlite_con)
    log.info(f"✅ SQLite connected: {DB_PATH}")

    t_start = time.time()

    if args.table in ("ohlc", "all"):
        since = args.since or "20090101"
        pull_ohlc(ora_cur, sqlite_con, since)

    if args.table in ("ticks", "all"):
        since = args.since or "20250101"
        pull_ticks(ora_cur, sqlite_con, since)

    total_min = (time.time() - t_start) / 60
    log.info(f"🏁 Tất cả xong trong {total_min:.1f} phút")

    # Final stats
    for tbl in ["hq_daily_ohlc", "hq_trade_ticks"]:
        cnt = sqlite_con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        log.info(f"  {tbl}: {cnt:,} rows")

    ora_con.close()
    sqlite_con.close()

if __name__ == "__main__":
    main()
