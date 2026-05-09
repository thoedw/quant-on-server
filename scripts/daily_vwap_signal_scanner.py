#!/usr/bin/env python3
"""
scripts/daily_vwap_signal_scanner.py
══════════════════════════════════════════════════════════
Quét toàn thị trường — Daily VWAP Signals.

Signals:
  VWAP_RECLAIM  prev_close < prev_vwap  AND  close >= vwap
                Giá vượt lên trên VWAP sau ít nhất 1 ngày nằm dưới.
                Tín hiệu: tổ chức hấp thụ xong, bắt đầu push.

  BAND_TIGHT    vwap_std / vwap < 0.5%
                Dải biến động VWAP co hẹp bất thường (coiling).
                Thường xảy ra trước breakout mạnh.

Chạy:
  python3 scripts/daily_vwap_signal_scanner.py
  python3 scripts/daily_vwap_signal_scanner.py --signal reclaim
  python3 scripts/daily_vwap_signal_scanner.py --signal tight
  python3 scripts/daily_vwap_signal_scanner.py --date 2026-05-04
  python3 scripts/daily_vwap_signal_scanner.py --top 20 --min-vol 500000
"""

import os, sys, sqlite3, argparse, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH  = PROJECT_ROOT / "data" / "securities_master.db"
VN_TZ    = timezone(timedelta(hours=7))

MIN_VOL        = 500_000    # CP/ngày tối thiểu
MIN_SIDE_COV   = 70.0
TIGHT_THRESH   = 0.005      # vwap_std/vwap < 0.5% = tight
TIGHT_EXTREME  = 0.003      # < 0.3% = rất tight
TIGHT_ULTRA    = 0.002      # < 0.2% = coiling cực
NEAR_VWAP_PCT  = 0.003      # close trong 0.3% của VWAP

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────

def _fmt_delta(v: int) -> str:
    sign = "+" if v >= 0 else "-"
    av = abs(v)
    if av >= 1_000_000: return f"{sign}{av/1_000_000:.2f}M"
    if av >= 1_000:     return f"{sign}{av/1_000:.0f}K"
    return f"{sign}{av}"

def _pad_ansi(colored: str, plain: str, width: int) -> str:
    return colored + " " * max(0, width - len(plain))


# ──────────────────────────────────────────────────────────────
# DB QUERIES
# ──────────────────────────────────────────────────────────────

def resolve_date(conn: sqlite3.Connection, requested: str | None) -> str:
    today = datetime.now(VN_TZ).strftime("%Y-%m-%d")
    target = requested or today
    row = conn.execute(
        "SELECT COUNT(*) FROM daily_vwap_summary WHERE trade_date=?", (target,)
    ).fetchone()
    if row and row[0] > 0:
        return target
    fb = conn.execute(
        "SELECT MAX(trade_date) FROM daily_vwap_summary WHERE trade_date<=?", (today,)
    ).fetchone()
    if fb and fb[0]:
        if not requested:
            logger.warning(f"📅 Hôm nay ({target}) chưa có dữ liệu → fallback: {fb[0]}")
        return fb[0]
    return target


def load_today(conn: sqlite3.Connection, trade_date: str,
               min_vol: int = MIN_VOL) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.symbol, s.exchange,
               dv.vwap, dv.vwap_std,
               dv.vwap_upper1, dv.vwap_lower1,
               dv.vwap_upper2, dv.vwap_lower2,
               dv.session_open  AS open,
               dv.session_close AS close,
               dv.cum_volume    AS volume,
               dv.cum_delta,
               COALESCE(dv.side_cov_pct, 0.0) AS side_cov
        FROM daily_vwap_summary dv
        JOIN securities s ON s.security_id = dv.security_id
        WHERE dv.trade_date = ?
          AND dv.cum_volume >= ?
          AND dv.vwap IS NOT NULL
          AND dv.session_close > 0
          AND dv.vwap_std IS NOT NULL
        ORDER BY s.symbol
    """, (trade_date, min_vol)).fetchall()
    return [dict(r) for r in rows]


def load_prev(conn: sqlite3.Connection, symbol: str, trade_date: str,
              lookback: int = 10) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT dv.trade_date,
               dv.vwap, dv.vwap_std,
               dv.session_close AS close,
               dv.cum_volume    AS volume,
               dv.cum_delta
        FROM daily_vwap_summary dv
        JOIN securities s ON s.security_id = dv.security_id
        WHERE s.symbol = ? AND dv.trade_date < ?
        ORDER BY dv.trade_date DESC
        LIMIT ?
    """, (symbol, trade_date, lookback)).fetchall()
    return [dict(r) for r in reversed(rows)]


# ──────────────────────────────────────────────────────────────
# SCORERS
# ──────────────────────────────────────────────────────────────

def score_vwap_reclaim(row: dict, history: list[dict]) -> tuple[float, dict]:
    close = row["close"]
    vwap  = row["vwap"]
    vol   = row["volume"]
    delta = row["cum_delta"] or 0
    side  = row["side_cov"]

    # Điều kiện cốt lõi: hôm nay >= VWAP
    if close < vwap:
        return 0.0, {}

    # Cần ít nhất 1 ngày trước để kiểm tra
    if not history:
        return 0.0, {}

    prev = history[-1]
    prev_close = prev["close"] or 0
    prev_vwap  = prev["vwap"]  or 0

    # Ngày trước phải DƯỚI VWAP
    if not (prev_close > 0 and prev_vwap > 0 and prev_close < prev_vwap):
        return 0.0, {}

    score = 40.0
    details = {
        "signal":     "VWAP_RECLAIM",
        "close_vwap": f"{close:.2f} ≥ {vwap:.2f} ({(close/vwap-1)*100:+.2f}%)",
        "prev_gap":   f"prev {prev_close:.2f} < {prev_vwap:.2f} ({(prev_close/prev_vwap-1)*100:+.2f}%)",
    }

    # Đếm số ngày liên tiếp dưới VWAP trước khi reclaim
    days_below = 0
    for h in reversed(history):
        hc = h["close"] or 0
        hv = h["vwap"]  or 0
        if hc > 0 and hv > 0 and hc < hv:
            days_below += 1
        else:
            break
    details["days_below"] = days_below
    if days_below >= 3: score += 10
    if days_below >= 5: score += 5

    # Delta hôm nay
    delta_ratio = delta / max(vol, 1)
    details["delta_ratio"] = f"{delta_ratio*100:.1f}%"
    details["cum_delta"]   = _fmt_delta(delta)
    if delta > 0:
        score += 15
        if delta_ratio >= 0.10: score += 10
        elif delta_ratio >= 0.05: score += 5

    # Volume surge vs avg5
    if history:
        avg5 = sum(h["volume"] or 0 for h in history[-5:]) / max(len(history[-5:]), 1)
        if avg5 > 0:
            surge = vol / avg5
            details["vol_surge"] = f"{surge:.1f}×"
            if   surge >= 2.0: score += 15
            elif surge >= 1.2: score += 10

    # Reclaim từ vùng lower1 (discount sâu hơn)
    if prev_close < (row["vwap_lower1"] or vwap):
        score += 5
        details["reclaim_from"] = "lower1"

    # Side coverage penalty
    if side < MIN_SIDE_COV:
        score -= 15
        details["side_warn"] = f"{side:.0f}% < {MIN_SIDE_COV}%"
    else:
        details["side_cov"] = f"{side:.1f}%"

    return min(score, 100.0), details


def score_band_tight(row: dict, history: list[dict]) -> tuple[float, dict]:
    close    = row["close"]
    vwap     = row["vwap"]
    vwap_std = row["vwap_std"] or 0
    vol      = row["volume"]
    delta    = row["cum_delta"] or 0
    side     = row["side_cov"]

    if vwap <= 0 or vwap_std <= 0:
        return 0.0, {}

    tight_ratio = vwap_std / vwap

    # Ngưỡng cơ bản: < 0.5%
    if tight_ratio >= TIGHT_THRESH:
        return 0.0, {}

    score = 40.0
    details = {
        "signal":       "BAND_TIGHT",
        "tight_ratio":  f"{tight_ratio*100:.3f}%",
        "vwap_std":     f"{vwap_std:.4f}",
        "band_width":   f"{(row['vwap_upper1'] or vwap) - (row['vwap_lower1'] or vwap):.3f}",
    }

    # Mức độ chặt
    if   tight_ratio < TIGHT_ULTRA:   score += 25; details["tightness"] = "ULTRA ⚡"
    elif tight_ratio < TIGHT_EXTREME: score += 15; details["tightness"] = "EXTREME"
    else:                             score += 5;  details["tightness"] = "TIGHT"

    # Close gần VWAP (coiling sát nút)
    gap_pct = abs(close - vwap) / vwap
    details["gap_to_vwap"] = f"{gap_pct*100:.3f}%"
    if gap_pct < NEAR_VWAP_PCT:
        score += 10
        details["near_vwap"] = True

    # Delta dương trong khi band tight = tích lũy thầm lặng
    delta_ratio = delta / max(vol, 1)
    details["cum_delta"]   = _fmt_delta(delta)
    details["delta_ratio"] = f"{delta_ratio*100:.1f}%"
    if delta > 0:
        score += 10
        if delta_ratio >= 0.05: score += 5

    # Volume xu hướng giảm (classic coiling pattern)
    if len(history) >= 3:
        vols = [h["volume"] or 0 for h in history[-3:]]
        if vols[0] > 0 and vols[-1] < vols[0]:
            vol_decline = (vols[0] - vols[-1]) / vols[0]
            details["vol_declining"] = f"{vol_decline*100:.0f}%↓"
            if vol_decline > 0.3: score += 10

    # Số ngày tight liên tiếp (trend confirmation)
    streak_tight = 0
    for h in reversed(history):
        hs = h.get("vwap_std") or 0
        hv = h.get("vwap")    or 0
        if hv > 0 and hs > 0 and (hs/hv) < TIGHT_THRESH:
            streak_tight += 1
        else:
            break
    details["tight_streak"] = streak_tight
    if streak_tight >= 3: score += 10

    # Side coverage penalty
    if side < MIN_SIDE_COV:
        score -= 15
        details["side_warn"] = f"{side:.0f}%"
    else:
        details["side_cov"] = f"{side:.1f}%"

    return min(score, 100.0), details


# ──────────────────────────────────────────────────────────────
# SCAN
# ──────────────────────────────────────────────────────────────

def run_scan(trade_date: str, signal_filter: str = "all",
             min_score: float = 55, top_n: int = 0,
             min_vol: int = MIN_VOL) -> tuple[list[dict], list[dict], str]:
    conn = sqlite3.connect(str(DB_PATH))
    trade_date = resolve_date(conn, trade_date if trade_date != datetime.now(VN_TZ).strftime("%Y-%m-%d") else None)

    logger.info(f"🔍 Quét Daily VWAP Signals — {trade_date}")
    rows = load_today(conn, trade_date, min_vol=min_vol)
    logger.info(f"  Tổng mã có dữ liệu: {len(rows):,}")

    reclaims, tights = [], []

    for row in rows:
        history = load_prev(conn, row["symbol"], trade_date)

        if signal_filter in ("all", "reclaim"):
            sc, det = score_vwap_reclaim(row, history)
            if sc >= min_score:
                reclaims.append({**row, "score": sc, "details": det, "trade_date": trade_date})

        if signal_filter in ("all", "tight"):
            sc, det = score_band_tight(row, history)
            if sc >= min_score:
                tights.append({**row, "score": sc, "details": det, "trade_date": trade_date})

    conn.close()

    reclaims.sort(key=lambda x: (-x["score"], -(x["cum_delta"] or 0)))
    tights.sort(key=lambda x:   (-x["score"], -abs(x.get("vwap_std", 1) / max(x.get("vwap", 1), 1))))

    if top_n > 0:
        reclaims = reclaims[:top_n]
        tights   = tights[:top_n]

    return reclaims, tights, trade_date


# ──────────────────────────────────────────────────────────────
# OUTPUT
# ──────────────────────────────────────────────────────────────

def _print_signal_table(results: list[dict], title: str, cols: list[tuple]):
    """Generic table printer cho một nhóm signal."""
    G = "\033[92m"; Y = "\033[93m"; B = "\033[94m"
    E = "\033[0m";  W = "\033[1m";  HDR = "\033[1;97m"

    col_names = [c[0] for c in cols]
    widths    = [c[1] for c in cols]

    header = "  " + "  ".join(f"{HDR}{n:>{w}}{E}" for n, w in zip(col_names, widths))
    sep    = f"  {HDR}{'─' * (sum(widths) + 2*len(widths))}{E}"

    print(f"\n  {W}⚡ {title}  [{len(results)} mã]{E}")
    print(sep)
    print(header)
    print(sep)

    for r in results:
        d          = r["details"]
        close      = r["close"]
        vwap       = r["vwap"]
        vol        = r["volume"]
        delta      = r["cum_delta"] or 0
        vs_vwap    = (close / vwap - 1) * 100
        delta_r    = delta / max(vol, 1) * 100
        vol_surge  = d.get("vol_surge", "—")
        score_c    = G if r["score"] >= 75 else Y

        sig = d.get("signal", "")

        if sig == "VWAP_RECLAIM":
            days_b  = d.get("days_below", 0)
            plain_d = f"{days_b}d↓"
            db_str  = (f"{G}{W}{plain_d}{E}" if days_b >= 5
                       else f"{G}{plain_d}{E}" if days_b >= 3
                       else plain_d)
            row_str = (
                f"  {B}{W}{r['symbol']:<6}{E}  "
                f"{score_c}{W}{r['score']:>5.0f}{E}  "
                f"{close:>7.2f}  "
                f"{G}{vs_vwap:>+7.2f}%{E}  "
                f"{G}{delta_r:>+6.1f}%{E}  "
                f"{G}{_fmt_delta(delta):>9}{E}  "
                f"{_pad_ansi(db_str, plain_d, 6)}  "
                f"{vol_surge:>8}  "
                f"{r['exchange']:<5}"
            )
        else:  # BAND_TIGHT
            tight_r = (r["vwap_std"] or 0) / max(vwap, 1) * 100
            tightness = d.get("tightness", "")
            tight_c = G if tight_r < 0.2 else (G if tight_r < 0.3 else Y)
            ts = d.get("tight_streak", 0)
            plain_ts = f"{ts}d"
            ts_str = (f"{G}{W}{plain_ts}{E}" if ts >= 5
                      else f"{G}{plain_ts}{E}" if ts >= 3
                      else plain_ts)
            row_str = (
                f"  {B}{W}{r['symbol']:<6}{E}  "
                f"{score_c}{W}{r['score']:>5.0f}{E}  "
                f"{close:>7.2f}  "
                f"{tight_c}{tight_r:>6.3f}%{E}  "
                f"{G}{delta_r:>+6.1f}%{E}  "
                f"{G}{_fmt_delta(delta):>9}{E}  "
                f"{_pad_ansi(ts_str, plain_ts, 5)}  "
                f"{tightness:<10}  "
                f"{r['exchange']:<5}"
            )

        print(row_str)

    print(sep)


def print_all(reclaims: list[dict], tights: list[dict], trade_date: str):
    W = "\033[1m"; E = "\033[0m"
    print()
    print(f"{W}{'═'*76}{E}")
    print(f"{W}  ⚡ DAILY VWAP SIGNALS — {trade_date}{E}")
    print(f"{W}{'═'*76}{E}")

    if reclaims:
        _print_signal_table(
            reclaims,
            "VWAP RECLAIM — Giá vượt lên trên VWAP",
            [("Mã",6),("Score",5),("Giá",7),("vs VWAP",8),
             ("Δ/Vol",7),("Delta",9),("Days↓",6),("VolSurge",8),("EX",5)]
        )
    else:
        print("\n  (Không có mã VWAP_RECLAIM)")

    if tights:
        _print_signal_table(
            tights,
            "BAND TIGHT — Dải VWAP đang coiling",
            [("Mã",6),("Score",5),("Giá",7),("Tight%",7),
             ("Δ/Vol",7),("Delta",9),("Streak",5),("Tightness",10),("EX",5)]
        )
    else:
        print("\n  (Không có mã BAND_TIGHT)")

    print(f"\n  RECLAIM: {len(reclaims)} mã  |  TIGHT: {len(tights)} mã")
    print(f"{W}{'═'*76}{E}\n")


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Daily VWAP Signal Scanner")
    parser.add_argument("--date",      default=None,
                        help="Ngày (YYYY-MM-DD), mặc định: ngày có dữ liệu gần nhất")
    parser.add_argument("--signal",    default="all",
                        choices=["all", "reclaim", "tight"],
                        help="Lọc signal (default: all)")
    parser.add_argument("--min-score", type=float, default=55,
                        help="Score tối thiểu (default: 55)")
    parser.add_argument("--top",       type=int,   default=0,
                        help="Chỉ hiện top N mã mỗi signal")
    parser.add_argument("--min-vol",   type=int,   default=MIN_VOL,
                        help=f"Volume tối thiểu CP (default: {MIN_VOL:,})")
    args = parser.parse_args()

    requested = args.date
    reclaims, tights, trade_date = run_scan(
        requested or datetime.now(VN_TZ).strftime("%Y-%m-%d"),
        signal_filter=args.signal,
        min_score=args.min_score,
        top_n=args.top,
        min_vol=args.min_vol,
    )

    if not reclaims and not tights:
        logger.info("⚠️  Không tìm thấy signal nào")
        return

    print_all(reclaims, tights, trade_date)


if __name__ == "__main__":
    main()
