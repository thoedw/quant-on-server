#!/usr/bin/env python3
"""
daily_bvc_imputer.py
====================
Áp dụng BVC (Bulk Volume Classification) cho bảng daily_prices
để ước tính buy_vol cho các rows chưa có side data (DNSE + Oracle_HQ).

BVC formula (Easley, López de Prado, O'Hara 2016):
    σ = (high - low) / (2 * sqrt(ln(2)))   ← Parkinson volatility
    z = (close - open) / max(σ, ε)          ← direction z-score
    p_buy = Φ(z)                             ← Normal CDF → [0,1]
    buy_vol = round(volume × p_buy)

Áp dụng:
    - DNSE rows (2012–2025): có OHLCV từ Oracle, volume từ DNSE → BVC ước tính
    - Oracle_HQ rows (pre-2012): có OHLCV, không có volume → BVC khi volume từ DNSE đã fill
    - MASVN rows (2026+): đã có real buy_vol → SKIP

Usage:
    python3 scripts/daily_bvc_imputer.py              # dry-run (in thống kê)
    python3 scripts/daily_bvc_imputer.py --commit     # ghi DB
    python3 scripts/daily_bvc_imputer.py --source DNSE --commit
    python3 scripts/daily_bvc_imputer.py --symbols HPG,VNM --commit
"""
import os, sys, math, sqlite3, time, argparse, logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH      = PROJECT_ROOT / "data" / "securities_master.db"
BATCH_SIZE   = 50_000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("DailyBVC")

# ── BVC Core ──────────────────────────────────────────────────────
_SQRT_2LN2 = math.sqrt(2.0 * math.log(2.0))   # ≈ 1.1774

def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

def bvc_classify(open_: float, high: float, low: float,
                 close: float, volume: int) -> tuple[int, int]:
    """
    Trả về (buy_vol, sell_vol) ước tính từ daily OHLCV.
    """
    if not volume or volume <= 0:
        return 0, 0
    if None in (open_, high, low, close):
        return 0, 0

    bar_range = high - low
    delta_p   = close - open_

    # Parkinson volatility (ε nhỏ để tránh chia 0)
    epsilon = max(bar_range, abs(delta_p) * 0.01, 1e-9)
    sigma   = epsilon / _SQRT_2LN2

    z     = delta_p / sigma
    p_buy = _norm_cdf(z)

    buy_vol  = round(volume * p_buy)
    sell_vol = volume - buy_vol
    return buy_vol, sell_vol

# ── Main impute ────────────────────────────────────────────────────
def run(commit: bool, source_filter: str | None, symbols: list[str] | None):
    con = sqlite3.connect(str(DB_PATH), timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-128000")

    # ── Thêm cột buy_vol_method nếu chưa có ──────────────────────
    cols = {r[1] for r in con.execute("PRAGMA table_info(daily_prices)")}
    if "buy_vol_method" not in cols:
        con.execute(
            "ALTER TABLE daily_prices ADD COLUMN buy_vol_method TEXT"
        )
        con.commit()
        log.info("Đã thêm cột buy_vol_method")

    # ── Build WHERE clause ────────────────────────────────────────
    conditions = [
        "buy_vol IS NULL",          # chưa có side data
        "volume > 0",               # có volume
        "open IS NOT NULL",         # có OHLC để tính BVC
        "close IS NOT NULL",
        "source != 'MASVN'",        # MASVN đã có real data
    ]
    params: list = []

    if source_filter:
        conditions.append("source = ?")
        params.append(source_filter)

    if symbols:
        placeholders = ",".join("?" * len(symbols))
        conditions.append(f"symbol IN ({placeholders})")
        params.extend(symbols)

    where = " AND ".join(conditions)

    # ── Đếm tổng ─────────────────────────────────────────────────
    total = con.execute(
        f"SELECT COUNT(*) FROM daily_prices WHERE {where}", params
    ).fetchone()[0]
    log.info(f"Rows cần BVC impute: {total:,}")
    if total == 0:
        log.info("Không có rows nào cần impute → exit")
        con.close()
        return

    # ── Fetch tất cả keys cần update (1 lần, tránh OFFSET drift) ──
    log.info("Fetching keys cần impute...")
    all_rows = con.execute(f"""
        SELECT symbol, trade_date,
               open, high, low, close, volume
        FROM daily_prices
        WHERE {where}
        ORDER BY trade_date, symbol
    """, params).fetchall()

    total     = len(all_rows)
    log.info(f"Đã lấy {total:,} rows vào memory")

    processed = 0
    p_buy_sum = 0.0
    t_start   = time.time()

    for batch_start in range(0, total, BATCH_SIZE):
        batch = all_rows[batch_start : batch_start + BATCH_SIZE]

        updates = []
        for sym, dt, op, hi, lo, cl, vol in batch:
            bv, sv = bvc_classify(op, hi, lo, cl, vol)
            p_buy_sum += bv / max(vol, 1)
            updates.append((bv, sym, dt))

        if commit:
            con.executemany("""
                UPDATE daily_prices
                SET buy_vol        = ?,
                    buy_vol_method = 'BVC_daily'
                WHERE symbol = ? AND trade_date = ?
            """, updates)
            con.commit()

        processed += len(batch)
        elapsed    = time.time() - t_start
        rate       = processed / elapsed if elapsed else 0
        eta        = (total - processed) / rate / 60 if rate else 0
        log.info(
            f"  {processed:>9,}/{total:,}  ({processed/total*100:.1f}%)  "
            f"speed={rate:,.0f}r/s  eta={eta:.1f}min"
        )

    # ── Stats ─────────────────────────────────────────────────────
    elapsed = (time.time() - t_start) / 60
    avg_p   = p_buy_sum / max(processed, 1)
    log.info(f"\n{'─'*58}")
    log.info(f"{'DRY-RUN' if not commit else '✅ COMMIT'} hoàn tất trong {elapsed:.1f} phút")
    log.info(f"  Processed  : {processed:,} rows")
    log.info(f"  Avg buy%   : {avg_p*100:.1f}% (expected ~50% neutral market)")

    # ── Coverage sau khi impute ───────────────────────────────────
    log.info(f"\n=== Coverage daily_prices ===")
    for row in con.execute("""
        SELECT source,
               COUNT(*)                                               AS total,
               COALESCE(SUM(CASE WHEN buy_vol IS NOT NULL THEN 1 END),0)   AS has_bv,
               COALESCE(SUM(CASE WHEN buy_vol_method='BVC_daily' THEN 1 END),0) AS bvc_est,
               COALESCE(ROUND(SUM(CASE WHEN buy_vol IS NOT NULL THEN 1.0 END)
                     / COUNT(*) * 100, 1), 0.0)                       AS pct
        FROM daily_prices GROUP BY source ORDER BY MIN(trade_date)
    """).fetchall():
        log.info(
            f"  {str(row[0]):12s}  total={row[1]:>9,}  "
            f"has_bv={row[2]:>9,}  bvc_est={row[3]:>9,}  pct={row[4]}%"
        )

    # ── Sample verify ─────────────────────────────────────────────
    log.info("\nSample HPG (5 ngày):")
    log.info(f"  {'Date':12s} {'Close':>8s} {'Volume':>12s} "
             f"{'buy_vol':>12s} {'buy%':>7s}  Method")
    for r in con.execute("""
        SELECT trade_date, close, volume, buy_vol, buy_vol_method
        FROM daily_prices
        WHERE symbol='HPG' AND buy_vol IS NOT NULL
          AND source IN ('DNSE','Oracle_HQ')
        ORDER BY trade_date DESC LIMIT 5
    """).fetchall():
        bp = r[3] / r[2] * 100 if r[2] else 0
        log.info(f"  {r[0]:12s} {r[1]:>8.2f} {r[2]:>12,} "
                 f"{r[3]:>12,} {bp:>6.1f}%  {r[4]}")

    con.close()

# ── Entry point ────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="BVC impute for daily_prices")
    ap.add_argument("--commit",  action="store_true",
                    help="Ghi DB (mặc định là dry-run)")
    ap.add_argument("--source",  default=None,
                    choices=["DNSE", "Oracle_HQ"],
                    help="Chỉ impute một source cụ thể")
    ap.add_argument("--symbols", default=None,
                    help="Subset symbols, VD: HPG,VNM")
    args = ap.parse_args()

    symbols = ([s.strip().upper() for s in args.symbols.split(",")]
               if args.symbols else None)

    if not args.commit:
        log.info("=== DRY-RUN mode (không ghi DB) — thêm --commit để ghi ===")

    run(commit=args.commit, source_filter=args.source, symbols=symbols)

if __name__ == "__main__":
    main()
