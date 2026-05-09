#!/usr/bin/env python3
"""
dnse_backfill_adj.py
====================
Pull DNSE backward-adjusted daily prices cho toàn bộ symbols
và backfill vào daily_prices.close_adj + volume (nơi còn NULL).

Chiến lược:
  - Thêm cột close_adj, volume vào daily_prices nếu chưa có
  - Với mỗi symbol có trong DB: fetch DNSE 2012-01-01 → 2025-12-31
  - UPDATE close_adj = DNSE.close (adjusted)
  - UPDATE volume    = DNSE.volume (nếu hiện tại NULL = Oracle rows)
  - MASVN/DNSE rows (2026+): close_adj = close (đã adjusted), volume đã có

Usage:
  python3 scripts/dnse_backfill_adj.py
  python3 scripts/dnse_backfill_adj.py --symbols HPG,VNM,MBB   # test subset
  python3 scripts/dnse_backfill_adj.py --from 2020-01-01       # partial backfill
"""
import os, sys, time, sqlite3, argparse, logging
import requests
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH      = os.path.join(PROJECT_ROOT, "data", "securities_master.db")
DNSE_URL     = "https://services.entrade.com.vn/chart-api/v2/ohlcs/stock"
HEADERS      = {"Origin": "https://entrade.com.vn", "User-Agent": "Mozilla/5.0"}
DELAY_OK     = 0.20   # giây giữa các request thành công
DELAY_ERR    = 1.0    # giây khi lỗi
MAX_RETRY    = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("DNSEBackfill")

def ts(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").timestamp())

# ── DNSE fetch ─────────────────────────────────────────────────
def fetch_dnse(symbol: str, from_date: str, to_date: str) -> list[dict]:
    """Trả về list of {trade_date, close_adj, volume}. Rỗng nếu lỗi."""
    params = {
        "from": ts(from_date), "to": ts(to_date),
        "symbol": symbol, "resolution": "1D",
    }
    for attempt in range(MAX_RETRY):
        try:
            r = requests.get(DNSE_URL, params=params,
                             headers=HEADERS, timeout=15)
            if r.status_code == 200:
                d = r.json()
                if "t" not in d or not d["t"]:
                    return []
                return [
                    {
                        "trade_date": datetime.fromtimestamp(t).strftime("%Y-%m-%d"),
                        "close_adj":  c,
                        "volume":     v,
                    }
                    for t, c, v in zip(d["t"], d["c"], d["v"])
                ]
            elif r.status_code == 400:
                return []   # bond/invalid — không retry
            elif r.status_code == 429:
                log.warning(f"  [{symbol}] Rate limit — đợi 5s")
                time.sleep(5)
            else:
                log.warning(f"  [{symbol}] HTTP {r.status_code}")
                time.sleep(DELAY_ERR)
        except Exception as e:
            log.warning(f"  [{symbol}] attempt {attempt+1}: {e}")
            time.sleep(DELAY_ERR)
    return []

# ── Schema migration ───────────────────────────────────────────
def migrate_schema(con: sqlite3.Connection):
    cols = {r[1] for r in con.execute("PRAGMA table_info(daily_prices)")}
    if "close_adj" not in cols:
        con.execute("ALTER TABLE daily_prices ADD COLUMN close_adj REAL")
        log.info("  Đã thêm cột close_adj")
    if "volume" not in cols:
        con.execute("ALTER TABLE daily_prices ADD COLUMN volume INTEGER")
        log.info("  Đã thêm cột volume")
    # MASVN rows đã có volume từ stock_prices — copy vào daily_prices
    # (đã được INSERT khi build daily_prices, nhưng kiểm tra lại)
    con.commit()

# ── Update một symbol ──────────────────────────────────────────
def update_symbol(con: sqlite3.Connection, symbol: str,
                  from_date: str, to_date: str) -> tuple[int, int]:
    """Trả về (rows_updated_adj, rows_updated_vol)."""
    rows = fetch_dnse(symbol, from_date, to_date)
    if not rows:
        return 0, 0

    adj_updates = []
    vol_updates = []
    for r in rows:
        adj_updates.append((r["close_adj"], symbol, r["trade_date"]))
        vol_updates.append((r["volume"],    symbol, r["trade_date"]))

    # UPDATE close_adj cho TẤT CẢ rows (Oracle + MASVN)
    con.executemany("""
        UPDATE daily_prices
        SET close_adj = ?
        WHERE symbol = ? AND trade_date = ?
    """, adj_updates)
    n_adj = con.total_changes

    # UPDATE volume CHỈ cho Oracle rows (volume IS NULL)
    con.executemany("""
        UPDATE daily_prices
        SET volume = ?
        WHERE symbol = ? AND trade_date = ?
          AND volume IS NULL
    """, vol_updates)
    n_vol = con.total_changes - n_adj

    con.commit()
    return n_adj, n_vol

# ── Main ───────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None,
                    help="Comma-separated subset, e.g. HPG,VNM")
    ap.add_argument("--symbols-file", default=None,
                    help="File chứa danh sách symbols cách nhau bằng dấu phẩy")
    ap.add_argument("--from",    dest="from_date",
                    default="2012-01-01")
    ap.add_argument("--to",      dest="to_date",
                    default="2025-12-31")
    ap.add_argument("--resume",  action="store_true",
                    help="Bỏ qua symbols đã có close_adj")
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-64000")

    migrate_schema(con)

    # Lấy danh sách symbols
    if args.symbols_file:
        with open(args.symbols_file) as f:
            symbols = [s.strip().upper() for s in f.read().split(",") if s.strip()]
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        rows = con.execute("""
            SELECT DISTINCT symbol FROM daily_prices
            WHERE trade_date >= ? AND trade_date <= ?
            ORDER BY symbol
        """, (args.from_date, args.to_date)).fetchall()
        symbols = [r[0] for r in rows]

    # Resume: bỏ qua symbols đã có đủ close_adj
    if args.resume:
        done = {r[0] for r in con.execute("""
            SELECT symbol FROM daily_prices
            WHERE close_adj IS NOT NULL
            GROUP BY symbol
            HAVING COUNT(*) > 100
        """)}
        before = len(symbols)
        symbols = [s for s in symbols if s not in done]
        log.info(f"Resume: bỏ qua {before-len(symbols)} symbols đã xong")

    total    = len(symbols)
    t_start  = time.time()
    ok, fail = 0, 0
    total_adj_rows = 0

    log.info(f"Bắt đầu backfill {total} symbols | "
             f"{args.from_date} → {args.to_date}")
    log.info(f"DB: {DB_PATH}")

    for i, sym in enumerate(symbols, 1):
        n_adj, n_vol = update_symbol(con, sym, args.from_date, args.to_date)
        if n_adj > 0:
            ok += 1
            total_adj_rows += n_adj
        else:
            fail += 1

        # Progress mỗi 50 symbols
        if i % 50 == 0 or i == total:
            elapsed  = time.time() - t_start
            rate     = i / elapsed if elapsed else 0
            eta_min  = (total - i) / rate / 60 if rate else 0
            pct      = i / total * 100
            log.info(
                f"  [{i:>5}/{total}] {pct:>5.1f}%  "
                f"ok={ok} fail={fail}  "
                f"adj_rows={total_adj_rows:,}  "
                f"speed={rate:.1f}sym/s  "
                f"eta={eta_min:.1f}min"
            )

        time.sleep(DELAY_OK)

    # ── Final stats ─────────────────────────────────────────────
    elapsed = (time.time() - t_start) / 60
    log.info(f"\n🏁 Hoàn tất trong {elapsed:.1f} phút")
    log.info(f"   Symbols OK   : {ok:,}")
    log.info(f"   Symbols fail : {fail:,}")
    log.info(f"   Adj rows     : {total_adj_rows:,}")

    # Coverage report
    r = con.execute("""
        SELECT
            source,
            COUNT(*)                                      AS total,
            SUM(CASE WHEN close_adj IS NOT NULL THEN 1 END) AS has_adj,
            SUM(CASE WHEN volume    IS NOT NULL THEN 1 END) AS has_vol
        FROM daily_prices
        GROUP BY source
    """).fetchall()
    log.info("\n  Coverage sau backfill:")
    log.info(f"  {'Source':15s} {'Total':>10s} {'Has_adj':>10s} {'Has_vol':>10s}")
    for row in r:
        log.info(f"  {str(row[0]):15s} {row[1]:>10,} {(row[2] or 0):>10,} {(row[3] or 0):>10,}")

    con.close()

if __name__ == "__main__":
    main()
