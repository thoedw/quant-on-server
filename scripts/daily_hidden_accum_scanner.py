#!/usr/bin/env python3
"""
scripts/daily_hidden_accum_scanner.py
══════════════════════════════════════════════════════════
Quét toàn thị trường — Hidden Accumulation trên Daily TF.

Định nghĩa Hidden Accumulation (Daily):
  - Giá đóng cửa HÔM NAY ≤ VWAP ngày (hàng ở vùng discount)
  - Net Delta ngày (buy_vol - sell_vol) > 0  (tích lũy thầm lặng)
  - Delta/Volume > ngưỡng tối thiểu (có conviction)
  - Side Coverage ≥ 80% (dữ liệu đáng tin)
  - Volume ngày đủ lớn (lọc mã kém thanh khoản)

Scoring:
  Base 40  : close < VWAP + net_delta > 0
  +20      : delta/vol > 5% (conviction trung bình)
  +15      : delta/vol > 10% (conviction cao)
  +15      : close trong 0.5% của VWAP (test đỉnh)
  +10      : volume ngày > avg 5 ngày × 1.2 (surge nhẹ)
  +15      : volume ngày > avg 5 ngày × 2.0 (surge mạnh)
  +10      : streak ≥ 3 ngày liên tiếp HA
  -20      : side_cov < 80% (dữ liệu kém tin cậy)

Chạy:
  python3 scripts/daily_hidden_accum_scanner.py
  python3 scripts/daily_hidden_accum_scanner.py --min-score 60
  python3 scripts/daily_hidden_accum_scanner.py --top 20
  python3 scripts/daily_hidden_accum_scanner.py --date 2026-05-04
"""

import os
import sys
import sqlite3
import argparse
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Config ──────────────────────────────────────────────────────
DB_PATH         = PROJECT_ROOT / "data" / "securities_master.db"
VN_TZ           = timezone(timedelta(hours=7))

MIN_SCORE       = 60     # Ngưỡng tối thiểu để hiển thị
MIN_VOL_DAY     = 500_000   # Tối thiểu 500k CP/ngày
MIN_SIDE_COV    = 70.0      # % side coverage tối thiểu
DELTA_RATIO_MED = 0.05      # 5%  → conviction trung bình
DELTA_RATIO_HI  = 0.10      # 10% → conviction cao
VOL_SURGE_MILD  = 1.2       # vol > 1.2× avg5 → nhẹ
VOL_SURGE_STR   = 2.0       # vol > 2.0× avg5 → mạnh
STREAK_MIN      = 3         # Số ngày HA liên tiếp tối thiểu để +điểm
NEAR_VWAP_PCT   = 0.005     # 0.5% gần VWAP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# DATABASE QUERIES
# ══════════════════════════════════════════════════════════════

def load_daily_vwap(conn: sqlite3.Connection, trade_date: str) -> list[dict]:
    """Lấy toàn bộ daily_vwap_summary của ngày trade_date."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT
            s.symbol,
            dv.trade_date,
            dv.vwap,
            dv.session_close  AS close,
            dv.cum_volume     AS volume,
            dv.cum_delta,
            COALESCE(dv.side_cov_pct, 0.0) AS side_cov,
            s.exchange,
            COALESCE(sp.pt_vol, 0)           AS pt_vol,
            COALESCE(sp.foreign_buy_vol, 0)  AS nn_buy,
            COALESCE(sp.foreign_sell_vol, 0) AS nn_sell
        FROM daily_vwap_summary dv
        JOIN securities s ON s.security_id = dv.security_id
        LEFT JOIN stock_prices sp ON sp.security_id = dv.security_id
          AND sp.interval = '1D' AND date(sp.trade_time) = dv.trade_date
        WHERE dv.trade_date = ?
          AND dv.cum_volume >= ?
          AND dv.vwap IS NOT NULL
          AND dv.session_close IS NOT NULL
          AND dv.session_close > 0
        ORDER BY s.symbol
    """, (trade_date, MIN_VOL_DAY)).fetchall()
    return [dict(r) for r in rows]


def load_history(conn: sqlite3.Connection, symbol: str, trade_date: str, lookback: int = 10) -> list[dict]:
    """Lấy N ngày gần nhất trước trade_date để tính vol avg và streak."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT dv.trade_date,
               dv.vwap,
               dv.session_close  AS close,
               dv.cum_volume     AS volume,
               dv.cum_delta,
               COALESCE(dv.side_cov_pct, 0.0) AS side_cov
        FROM daily_vwap_summary dv
        JOIN securities s ON s.security_id = dv.security_id
        WHERE s.symbol = ?
          AND dv.trade_date < ?
        ORDER BY dv.trade_date DESC
        LIMIT ?
    """, (symbol, trade_date, lookback)).fetchall()
    return [dict(r) for r in reversed(rows)]  # chronological order


# ══════════════════════════════════════════════════════════════
# SCORER
# ══════════════════════════════════════════════════════════════

def score_hidden_accum(row: dict, history: list[dict]) -> tuple[float, dict]:
    """
    Tính điểm Hidden Accumulation cho 1 mã.
    Returns: (score, details_dict)
    """
    sym       = row["symbol"]
    close     = row["close"]
    vwap      = row["vwap"]
    volume    = row["volume"]
    cum_delta = row["cum_delta"] or 0
    side_cov  = row["side_cov"] or 0.0

    details = {}

    # ── Điều kiện cốt lõi ────────────────────────────────────
    if close >= vwap:
        return 0.0, {}   # Giá trên VWAP → không phải accumulation dưới vùng
    if cum_delta <= 0:
        return 0.0, {}   # Delta âm → bán ròng, không tích lũy
    if volume < MIN_VOL_DAY:
        return 0.0, {}

    score = 40.0
    details["close_vs_vwap"] = f"{close:.2f} < {vwap:.2f} ({(close/vwap-1)*100:+.2f}%)"
    details["cum_delta"]     = f"+{cum_delta:,}"

    # ── Side Coverage penalty ─────────────────────────────────
    if side_cov < MIN_SIDE_COV:
        score -= 20
        details["side_cov_warn"] = f"{side_cov:.1f}% < {MIN_SIDE_COV}% ⚠️"
    else:
        details["side_cov"] = f"{side_cov:.1f}%"

    # ── Delta / Volume ratio (Conviction) ────────────────────
    delta_ratio = cum_delta / max(volume, 1)
    details["delta_ratio"] = f"{delta_ratio*100:.1f}%"
    if   delta_ratio >= DELTA_RATIO_HI:  score += 15
    elif delta_ratio >= DELTA_RATIO_MED: score += 20
    # < 5% không cộng thêm nhưng cũng không trừ (base score đã thấp)

    # ── Gần VWAP (đang test vùng cân bằng) ──────────────────
    gap_pct = (vwap - close) / vwap
    details["gap_to_vwap"] = f"{gap_pct*100:.2f}%"
    if gap_pct <= NEAR_VWAP_PCT:
        score += 15
        details["near_vwap"] = True

    # ── Volume Surge so với avg 5 ngày ───────────────────────
    if history:
        avg5_vol = sum(h["volume"] or 0 for h in history[-5:]) / max(len(history[-5:]), 1)
        if avg5_vol > 0:
            vol_surge = volume / avg5_vol
            details["vol_surge"] = f"{vol_surge:.1f}×"
            if   vol_surge >= VOL_SURGE_STR:  score += 15
            elif vol_surge >= VOL_SURGE_MILD:  score += 10
        else:
            details["vol_surge"] = "N/A"

    # ── Streak: Số ngày HA liên tiếp trước đó ────────────────
    streak = 0
    for h in reversed(history):
        if (h["close"] or 0) < (h["vwap"] or 0) and (h["cum_delta"] or 0) > 0:
            streak += 1
        else:
            break
    details["streak_days"] = streak
    if streak >= STREAK_MIN:
        score += 10

    return min(score, 100.0), details


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def resolve_trade_date(conn: sqlite3.Connection, requested: str | None) -> str:
    """
    Nếu không truyền --date (hoặc ngày hôm nay chưa có dữ liệu VWAP),
    tự động fallback về ngày gần nhất có dữ liệu trong daily_vwap_summary.
    """
    today = datetime.now(VN_TZ).strftime("%Y-%m-%d")
    target = requested or today

    # Kiểm tra ngày target có dữ liệu không
    row = conn.execute(
        "SELECT COUNT(*) FROM daily_vwap_summary WHERE trade_date = ?", (target,)
    ).fetchone()

    if row and row[0] > 0:
        return target

    # Fallback: lấy ngày mới nhất có dữ liệu
    fallback = conn.execute(
        "SELECT MAX(trade_date) FROM daily_vwap_summary WHERE trade_date <= ?", (today,)
    ).fetchone()

    if fallback and fallback[0]:
        if not requested:  # chỉ thông báo khi auto-detect (không phải user truyền vào)
            logger.warning(
                f"📅 Hôm nay ({target}) chưa có dữ liệu VWAP (đang giờ giao dịch?). "
                f"→ Auto-fallback: {fallback[0]}"
            )
        else:
            logger.warning(f"⚠️  Ngày {target} không có dữ liệu VWAP → fallback: {fallback[0]}")
        return fallback[0]

    return target  # trả về nguyên, để run_scan báo lỗi rõ ràng hơn


def run_scan(trade_date: str, min_score: float = MIN_SCORE, top_n: int = 0) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))

    # Auto-resolve ngày có dữ liệu (fix lỗi 0 mã khi chạy trong giờ giao dịch)
    trade_date = resolve_trade_date(conn, trade_date if trade_date != datetime.now(VN_TZ).strftime("%Y-%m-%d") else None)

    logger.info(f"🔍 Quét Hidden Accumulation Daily — {trade_date}")
    rows = load_daily_vwap(conn, trade_date)
    logger.info(f"  Tổng mã có dữ liệu VWAP: {len(rows):,}")

    results = []
    for row in rows:
        history = load_history(conn, row["symbol"], trade_date)
        score, details = score_hidden_accum(row, history)
        if score >= min_score:
            results.append({
                "symbol":     row["symbol"],
                "exchange":   row["exchange"],
                "close":      row["close"],
                "vwap":       row["vwap"],
                "volume":     row["volume"],
                "cum_delta":  row["cum_delta"] or 0,
                "side_cov":   row["side_cov"],
                "score":      score,
                "details":    details,
                "trade_date": trade_date,   # resolved date
                "pt_vol":     row["pt_vol"]  if "pt_vol"  in row.keys() else 0,
                "nn_buy":     row["nn_buy"]  if "nn_buy"  in row.keys() else 0,
                "nn_sell":    row["nn_sell"] if "nn_sell" in row.keys() else 0,
                "nn_net":     (row["nn_buy"] - row["nn_sell"]) if "nn_buy" in row.keys() else 0,
            })

    conn.close()

    # Sort: score desc → cum_delta desc
    results.sort(key=lambda x: (-x["score"], -x["cum_delta"]))
    if top_n > 0:
        results = results[:top_n]

    return results


def _pad_ansi(colored: str, plain: str, width: int) -> str:
    """Pad một chuỗi có ANSI codes đến đúng visual width.
    colored = chuỗi có ANSI, plain = text thực (không màu) để đo độ dài.
    """
    return colored + " " * max(0, width - len(plain))


def _print_top5_table(results: list[dict]):
    """In chi tiết top N mã dạng bảng ngang."""
    G = "\033[92m"; Y = "\033[93m"; B = "\033[94m"; E = "\033[0m"
    W = "\033[1m";  HDR = "\033[1;97m"

    n = len(results)
    print(f"\n  {W}📋 CHI TIẾT TOP {n}:{E}\n")

    # Header
    SEP = f"  {HDR}{'─'*88}{E}"
    HDR_ROW = (
        f"  {HDR}{'Mã':<6} {'Score':>5}  {'Giá':>7}  {'vs VWAP':>8}  "
        f"{'Δ/Vol':>7}  {'Delta':>9}  {'Side Cov':>8}  {'Vol Surge':>9}  "
        f"{'Streak':<8} {'Near':>5}{E}"
    )
    print(SEP)
    print(HDR_ROW)
    print(SEP)

    for r in results:
        d            = r["details"]
        delta_ratio  = r["cum_delta"] / max(r["volume"], 1) * 100
        vs_vwap_pct  = (r["close"] / r["vwap"] - 1) * 100
        streak       = d.get("streak_days", 0)
        vol_surge    = d.get("vol_surge", "-")
        side_cov     = r["side_cov"]
        near         = "✅" if d.get("near_vwap") else "—"

        # Streak: tính plain text để pad đúng visual width
        if streak >= 10:
            plain_streak = f"{streak}d ★"
            streak_str   = f"{G}{W}{plain_streak}{E}"
        elif streak >= 5:
            plain_streak = f"{streak}d ★"
            streak_str   = f"{G}{plain_streak}{E}"
        elif streak >= 3:
            plain_streak = f"{streak}d"
            streak_str   = f"{G}{plain_streak}{E}"
        else:
            plain_streak = f"{streak}d"
            streak_str   = plain_streak

        score_c = G if r["score"] >= 80 else Y
        cov_c   = G if side_cov >= 90 else Y

        print(
            f"  {B}{W}{r['symbol']:<6}{E} "
            f"{score_c}{W}{r['score']:>5.0f}{E}  "
            f"{r['close']:>7.2f}  "
            f"{vs_vwap_pct:>+7.2f}%  "
            f"{G}{delta_ratio:>+6.1f}%{E}  "
            f"{G}{_fmt_delta(r['cum_delta']):>9}{E}  "
            f"{cov_c}{side_cov:>7.1f}%{E}  "
            f"{vol_surge:>9}  "
            f"{_pad_ansi(streak_str, plain_streak, 8)} "
            f"{near:>5}"
        )

    print(SEP)
    print()


def _fmt_delta(v: int) -> str:
    """Format delta dạng +145K / +1.07M."""
    sign = "+" if v >= 0 else "-"
    av = abs(v)
    if av >= 1_000_000:
        return f"{sign}{av/1_000_000:.2f}M"
    if av >= 1_000:
        return f"{sign}{av/1_000:.0f}K"
    return f"{sign}{av}"


def _print_tier(results: list[dict], title: str):
    G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[94m"; E = "\033[0m"; W = "\033[1m"
    HDR  = "\033[1;97m"   # bright white bold cho header

    COL = f"  {HDR}{'Mã':<6} {'Giá':>7}  {'vs VWAP':>8}  {'Δ/Vol':>7}  {'Delta':>9}  {'PT(K)':>7}  {'NN Net':>8}  {'Streak':<9} {'Vol Surge':>9}  {'Score':>5}{E}"
    SEP = f"  {HDR}{'─'*74}{E}"

    print(f"\n  {W}🏆 {title}{E}")
    print(SEP)
    print(COL)
    print(SEP)

    for r in results:
        delta_ratio = r["cum_delta"] / max(r["volume"], 1) * 100
        streak      = r["details"].get("streak_days", 0)
        vol_surge   = r["details"].get("vol_surge", "-")
        vs_vwap_pct = (r["close"] / r["vwap"] - 1) * 100

        # Streak: tính plain text để pad đúng visual width
        if streak >= 10:
            plain_streak = f"{streak}d ★"
            streak_str   = f"{G}{W}{plain_streak}{E}"
        elif streak >= 5:
            plain_streak = f"{streak}d ★"
            streak_str   = f"{G}{plain_streak}{E}"
        elif streak >= 3:
            plain_streak = f"{streak}d"
            streak_str   = f"{G}{plain_streak}{E}"
        else:
            plain_streak = f"{streak}d"
            streak_str   = plain_streak

        # Score color
        score_c = G if r["score"] >= 80 else Y

        pt_k   = r.get('pt_vol', 0) / 1e3
        nn_net = r.get('nn_net', 0)
        nn_c   = G if nn_net > 0 else (R if nn_net < 0 else '')
        nn_str = f"{nn_c}{nn_net/1e3:>+6.0f}K{E}" if nn_net != 0 else "      — "
        pt_str = f"{pt_k:>6.0f}K" if pt_k > 0 else "      — "
        print(
            f"  {B}{W}{r['symbol']:<6}{E} "
            f"{r['close']:>7.2f}  "
            f"{vs_vwap_pct:>+7.2f}%  "
            f"{G}{delta_ratio:>+6.1f}%{E}  "
            f"{G}{_fmt_delta(r['cum_delta']):>9}{E}  "
            f"{pt_str}  {nn_str}  "
            f"{_pad_ansi(streak_str, plain_streak, 9)}"
            f"{vol_surge:>9}  "
            f"{score_c}{W}{r['score']:>5.0f}{E}"
        )

    print(SEP)


def print_results(results: list[dict], trade_date: str):
    W = "\033[1m"; E = "\033[0m"

    print()
    print(f"{W}{'═'*76}{E}")
    print(f"{W}  🐋 HIDDEN ACCUMULATION — DAILY SCAN — {trade_date}{E}")
    print(f"{W}{'═'*76}{E}")

    # Group: Score ≥ 80 = Tín hiệu mạnh, 60–79 = Đang theo dõi
    strong = [r for r in results if r["score"] >= 80]
    watch  = [r for r in results if 60 <= r["score"] < 80]

    if strong:
        _print_tier(strong, f"Top — Score ≥ 80 (Tín hiệu mạnh)  [{len(strong)} mã]")
    if watch:
        _print_tier(watch,  f"Theo dõi — Score 60–79             [{len(watch)} mã]")

    print(f"\n  Tổng {len(results)} mã | Strong: {len(strong)} | Watch: {len(watch)}")
    print(f"{W}{'═'*76}{E}\n")


def main():
    parser = argparse.ArgumentParser(description="Daily Hidden Accumulation Scanner")
    parser.add_argument("--date",      default=None,  help="Ngày phân tích (YYYY-MM-DD), mặc định: ngày gần nhất có dữ liệu")
    parser.add_argument("--min-score", type=float, default=MIN_SCORE, help=f"Score tối thiểu (default: {MIN_SCORE})")
    parser.add_argument("--top",       type=int,   default=0,  help="Chỉ hiện top N mã")
    parser.add_argument("--sort",      default="score", choices=["score", "delta", "symbol"],
                        help="Sort: score (default), delta, symbol")
    args = parser.parse_args()

    requested_date = args.date
    results = run_scan(
        requested_date or datetime.now(VN_TZ).strftime("%Y-%m-%d"),
        min_score=args.min_score,
        top_n=args.top,
    )

    if not results:
        logger.info(f"⚠️  Không tìm thấy mã nào đạt ngưỡng score ≥ {args.min_score}")
        return

    # Re-sort theo --sort flag
    if args.sort == "delta":
        results.sort(key=lambda x: -x["cum_delta"])
    elif args.sort == "symbol":
        results.sort(key=lambda x: x["symbol"])
    # default "score" đã sort trong run_scan

    actual_date = results[0]["trade_date"] if results else (requested_date or datetime.now(VN_TZ).strftime("%Y-%m-%d"))
    print_results(results, actual_date)

    # Chi tiết top 5 — dạng bảng
    _print_top5_table(results[:5])


if __name__ == "__main__":
    main()
