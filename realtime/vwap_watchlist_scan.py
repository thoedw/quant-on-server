#!/usr/bin/env python3
"""
scripts/vwap_watchlist_scan.py
══════════════════════════════════════════════════════════
Batch VWAP + Whale Hunter scan cho toàn bộ watchlist từ DB.

Cách dùng:
  PYTHONPATH=. python3 scripts/vwap_watchlist_scan.py
  PYTHONPATH=. python3 scripts/vwap_watchlist_scan.py --name vip
  PYTHONPATH=. python3 scripts/vwap_watchlist_scan.py --loop            # realtime mode, refresh 60s
  PYTHONPATH=. python3 scripts/vwap_watchlist_scan.py --loop --interval 30  # refresh 30s
  PYTHONPATH=. python3 scripts/vwap_watchlist_scan.py --signals-only    # chỉ in mã có signal
  PYTHONPATH=. python3 scripts/vwap_watchlist_scan.py --top 5            # top N mã delta dương nhất
  PYTHONPATH=. python3 scripts/vwap_watchlist_scan.py --date 2026-04-25  # ngày khác

Alias (trong .zshrc):
  qwvwap              → scan 1 lần (bảng + detail)
  qwvwap-live         → live dashboard (loop 60s, chỉ bảng)
  qwvwap-sig          → chỉ hiện mã có signal
"""

import os, sys, math, re, argparse, time, io, logging, contextlib
from collections import defaultdict
from datetime import datetime, timezone, timedelta


@contextlib.contextmanager
def _silent():
    """Tắt tất cả stdout + logging trong khối compute — tránh cuộn terminal live mode."""
    # 1. Tắt logging toàn bộ
    old_levels = {}
    for name, lgr in logging.Logger.manager.loggerDict.items():
        if isinstance(lgr, logging.Logger):
            old_levels[name] = lgr.level
            lgr.setLevel(logging.CRITICAL + 1)
    root = logging.getLogger()
    old_root = root.level
    root.setLevel(logging.CRITICAL + 1)

    # 2. Redirect stdout → devnull
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, 'w')
    try:
        yield
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout
        root.setLevel(old_root)
        for name, lv in old_levels.items():
            lgr = logging.Logger.manager.loggerDict.get(name)
            if isinstance(lgr, logging.Logger):
                lgr.setLevel(lv)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import sqlite3
from realtime.vwap_engine import VWAPEngine, _session_open_utc
from realtime.watchlist_db import load_watchlist
from scripts.whale_hunter import (
    score_hidden_accumulation, score_vwap_reclaim, score_delta_divergence,
    score_vwap_rejection, score_pvwap_support_test, score_vwap_bounce,
    _compute_vol_surge, WhaleHunter, MIN_SCORE, SIDE_QUALITY_GATE,
)

VN_TZ = timezone(timedelta(hours=7))
DB    = os.getenv("SMD_DB_PATH", os.path.join(PROJECT_ROOT, "data", "securities_master.db"))

ICONS = {
    'HIDDEN_ACCUMULATION': '🐋', 'VWAP_RECLAIM': '🚀',
    'DELTA_DIVERGENCE': '📊',    'VWAP_REJECTION': '🔴',
    'PVWAP_SUPPORT_TEST': '🎯',  'VWAP_BOUNCE': '🔁',
}

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m";  E = "\033[0m"

# ANSI helpers cho in-place rendering
CUR_HOME  = "\033[H"           # về góc trên trái
CUR_SAVE  = "\033[s"           # save cursor pos
CUR_REST  = "\033[u"           # restore cursor pos
EOL_ERASE = "\033[K"           # xoá từ cursor đến cuối dòng
HIDE_CUR  = "\033[?25l"       # ẩn cursor
SHOW_CUR  = "\033[?25h"       # hiện cursor lại

_ANSI_RE = re.compile(r'\033\[[0-9;]*[mK]')  # để đo độ rộng thực

def _visible_len(s: str) -> int:
    """Độ dài hiển thị thực (bỏ ANSI escape codes)."""
    return len(_ANSI_RE.sub('', s))


# ════════════════════════════════════════════════════════════════
# ANALYSIS CORE
# ════════════════════════════════════════════════════════════════

def analyze_symbol(conn, sid, symbol, DATE_VN, session_open, all_snaps, wh, delta_reliable):
    """Phân tích một mã. Trả về dict kết quả hoặc None nếu không có dữ liệu."""
    pvwap_row = conn.execute('''
        SELECT trade_date, vwap, vwap_upper1, vwap_lower1, vwap_upper2, vwap_lower2,
               cum_volume, cum_delta, buy_vol, sell_vol, side_cov_pct, session_open, session_close
        FROM daily_vwap_summary
        WHERE security_id=? AND trade_date < ?
        ORDER BY trade_date DESC LIMIT 5
    ''', (sid, DATE_VN)).fetchall()

    pv         = pvwap_row[0]['vwap']      if pvwap_row else None
    pv_date    = pvwap_row[0]['trade_date'] if pvwap_row else None
    hist_deltas = [(r['trade_date'], r['cum_delta'] or 0) for r in pvwap_row]

    rows = conn.execute('''
        SELECT trade_time, open, high, low, close, volume,
               COALESCE(buy_vol,0) as bv, COALESCE(sell_vol,0) as sv,
               COALESCE(buy_vol,0)-COALESCE(sell_vol,0) as delta
        FROM stock_prices
        WHERE security_id=? AND interval='1m'
          AND trade_time>=? AND date(trade_time)=?
        ORDER BY trade_time
    ''', (sid, session_open, DATE_VN)).fetchall()

    if not rows:
        return None

    last      = rows[-1];  first = rows[0]
    total_vol = sum(r[5] for r in rows) or 1
    total_bv  = sum(r[6] for r in rows)
    total_sv  = sum(r[7] for r in rows)
    total_d   = sum(r[8] for r in rows)
    h_high    = max(r[2] for r in rows)
    h_low     = min(r[3] for r in rows)
    side_cov  = round((total_bv + total_sv) * 100.0 / total_vol, 1)

    cum_pv = cum_v = cum_pv2 = 0.0
    for r in rows:
        p = r[4] or 0.0; v = r[5] or 0
        cum_pv += p * v; cum_v += v; cum_pv2 += p * p * v
    vwap_t = cum_pv / cum_v if cum_v > 0 else last[4]
    std     = math.sqrt(max(0, (cum_pv2 / cum_v) - vwap_t ** 2)) if cum_v > 0 else 0

    close_c = last[4]
    vs_vwap = (close_c - vwap_t) / vwap_t * 100 if vwap_t else 0
    vs_pv   = (close_c - pv) / pv * 100 if pv else None

    # Delta per hour
    hour_buckets = defaultdict(lambda: {'bv': 0, 'sv': 0, 'v': 0})
    for r in rows:
        t_str = str(r[0]).replace('T', ' ')
        nums  = re.findall(r'\d+', t_str)
        h_vn  = int(nums[3]) if len(nums) > 3 else 0
        bk    = f'{h_vn:02d}h'
        hour_buckets[bk]['bv'] += r[6]
        hour_buckets[bk]['sv'] += r[7]
        hour_buckets[bk]['v']  += r[5]

    # VWAP bounces (20 nến cuối)
    bounces = 0; was_below = False
    for r in rows[-20:]:
        c = r[4] or 0.0
        if c <= 0: continue
        if c < vwap_t * 0.998: was_below = True
        elif was_below and c >= vwap_t * 0.998:
            bounces += 1; was_below = False

    # Whale signals
    signals  = []
    snap_obj = all_snaps.get(sid)
    if snap_obj:
        snap = {
            'snapshot_time': snap_obj.snapshot_time,
            'vwap': snap_obj.vwap, 'last_close': snap_obj.last_close,
            'vwap_upper1': snap_obj.vwap_upper1, 'vwap_lower1': snap_obj.vwap_lower1,
            'vwap_upper2': snap_obj.vwap_upper2, 'vwap_lower2': snap_obj.vwap_lower2,
            'cum_volume': snap_obj.cum_volume, 'cum_delta': snap_obj.cum_delta,
        }
        recent   = wh._get_recent_candles(conn, sid, n=12)
        vs_s     = _compute_vol_surge(recent)
        pvwap_wh = wh._get_pvwap(conn, sid)
        prev     = wh._get_prev_vwap(conn, sid, snap['snapshot_time'])

        checks = [
            ('HIDDEN_ACCUMULATION', 'BUY',  *score_hidden_accumulation(snap, recent, vs_s, delta_reliable)),
            ('DELTA_DIVERGENCE',    'BUY',  *score_delta_divergence(snap, recent, vs_s, delta_reliable)),
            ('PVWAP_SUPPORT_TEST',  'BUY',  *score_pvwap_support_test(snap, pvwap_wh, recent, vs_s, delta_reliable)),
            ('VWAP_BOUNCE',         'BUY',  *score_vwap_bounce(snap, recent, vs_s)),
        ]
        s2, d2 = score_vwap_reclaim(snap, prev, vs_s, delta_reliable)
        s4, d4 = score_vwap_rejection(snap, recent, vs_s, delta_reliable)
        if s2 >= MIN_SCORE: checks.append(('VWAP_RECLAIM',  'BUY',  s2, d2))
        if s4 >= MIN_SCORE: checks.append(('VWAP_REJECTION', 'SELL', s4, d4))

        for sig, dir_, sc, det in checks:
            if sc >= MIN_SCORE:
                signals.append((sig, dir_, sc, det))
        signals.sort(key=lambda x: -x[2])

    return {
        'sym': symbol, 'close': close_c, 'open': first[1],
        'high': h_high, 'low': h_low,
        'vwap': vwap_t, 'std': std,
        'pvwap': pv, 'pvwap_date': pv_date,
        'vs_vwap': vs_vwap, 'vs_pv': vs_pv,
        'delta': total_d, 'total_vol': total_vol,
        'buy_vol': total_bv, 'sell_vol': total_sv,
        'side_cov': side_cov, 'ncandles': len(rows),
        'bounces': bounces, 'signals': signals,
        'hist_deltas': hist_deltas,
        'hour_buckets': dict(hour_buckets),
    }


# ════════════════════════════════════════════════════════════════
# PRINT HELPERS
# ════════════════════════════════════════════════════════════════

def _summary_table_lines(results) -> list:
    """Trả về list các dòng cho bảng tóm tắt (không print trực tiếp)."""
    lines = []
    lines.append(f"  {'SYM':<5} {'Close':>6} {'VWAP':>6} {'vsV%':>5}  {'PVWAP':>6} {'pvp%':>6}  {'Delta':>12}  {'Vol(M)':>6}  {'Cov%':>5}  Signals")
    lines.append(f"  {'─'*88}")
    for r in results:
        vs_v_col = f"{G if r['vs_vwap'] >= 0 else R}{r['vs_vwap']:>+4.1f}%{E}"
        vs_p_str = f"{r['vs_pv']:>+5.2f}%" if r['vs_pv'] is not None else "   — "
        d_col    = f"{G if r['delta'] >= 0 else R}{r['delta']:>+12,}{E}"
        pv_str   = f"{r['pvwap']:.2f}" if r['pvwap'] else "  —  "
        sig_str  = "  ".join(f"{ICONS.get(s,'•')}{s[:4]}({sc:.0f})"
                             for s, _, sc, __ in r['signals'][:2]) or "—"
        lines.append(
            f"  {B}{r['sym']:<5}{E} {r['close']:>6.2f} {r['vwap']:>6.2f} "
            f"{vs_v_col}  {pv_str:>6} {vs_p_str}  {d_col}  "
            f"{r['total_vol']/1e6:>6.2f}M  {r['side_cov']:>5.1f}%  {sig_str}"
        )
    return lines


def print_summary_table(results):
    for line in _summary_table_lines(results):
        print(line)


def print_detail(r, show_hours=True):
    pv = r['pvwap']
    print(f"\n  ┌─ {B}{C}{r['sym']}{E} {'─'*50}")
    print(f"  │  OHLC  : {r['open']:.2f} / {r['high']:.2f} / {r['low']:.2f} / {r['close']:.2f}")
    print(f"  │  VWAP  : {r['vwap']:.2f}   ±1σ=[{r['vwap']-r['std']:.2f}, {r['vwap']+r['std']:.2f}]")
    print(f"  │  PVWAP : {pv:.2f} ({r['pvwap_date']})" if pv else "  │  PVWAP : —")
    print(f"  │  Delta : {r['delta']:+,}  buy={r['buy_vol']:,}  sell={r['sell_vol']:,}  side={r['side_cov']}%")
    print(f"  │  Candles: {r['ncandles']}  |  VWAP bounces: {r['bounces']}")
    if r['hist_deltas']:
        parts = [f"{d}: {dlt:+,}" for d, dlt in r['hist_deltas'][:3]]
        print(f"  │  History: " + "  |  ".join(parts))
    if show_hours and r['hour_buckets']:
        print(f"  │  Hourly delta:")
        for bk in sorted(r['hour_buckets']):
            b = r['hour_buckets'][bk]
            d = b['bv'] - b['sv']
            bar  = ('█' if d >= 0 else '░') * min(int(abs(d) / 20000), 20)
            sign = '+' if d >= 0 else '-'
            print(f"  │    {bk}: {b['v']/1e6:.2f}M  {d:>+10,}  {sign}{bar}")
    if r['signals']:
        print(f"  │  🔔 Signals:")
        for sig, dir_, sc, det in r['signals']:
            print(f"  │    {ICONS.get(sig,'•')} {sig:<22} [{dir_:<4}] score={sc:.0f}")
            for k, v in list(det.items())[:3]:
                print(f"  │       {k}: {v}")
    else:
        print(f"  │  ⚪ Không có signal đạt ngưỡng {MIN_SCORE}")
    print(f"  └{'─'*53}")


# ════════════════════════════════════════════════════════════════
# RUN SCAN  (1 lần)
# ════════════════════════════════════════════════════════════════

def run_scan(args):
    """Chạy một vòng scan. Được gọi trực tiếp hoặc từ realtime loop."""
    DATE_VN = args.date or datetime.now(VN_TZ).strftime("%Y-%m-%d")

    is_market = getattr(args, 'market', False)
    if is_market:
        # Load tất cả mã có data intraday hôm nay
        db_open_vn = DATE_VN + 'T09:00:00'
        db_now_vn  = datetime.now(VN_TZ).strftime('%Y-%m-%dT%H:%M:%S')
        _conn = sqlite3.connect(DB); _conn.row_factory = sqlite3.Row
        rows = _conn.execute("""
            SELECT DISTINCT s.symbol
            FROM stock_prices sp JOIN securities s ON s.security_id=sp.security_id
            WHERE sp.interval='1m' AND sp.trade_time >= ? AND sp.trade_time <= ?
            ORDER BY s.symbol
        """, (db_open_vn, db_now_vn)).fetchall()
        _conn.close()
        watchlist = [r['symbol'] for r in rows]
        if not watchlist:
            return [f"❌ Không có dữ liệu intraday hôm nay trong DB"]
    else:
        watchlist = load_watchlist(list_name=args.name, db_path=DB)
        if not watchlist:
            return [f"❌ Watchlist '{args.name}' trống hoặc không tồn tại."]

    is_live = getattr(args, 'loop', False)
    now_vn  = datetime.now(VN_TZ).strftime('%Y-%m-%d %H:%M:%S')
    label_src = 'MARKET 📈' if is_market else f"list='{args.name}'"
    label   = 'LIVE ⚡' if is_live else 'SCAN'

    conn         = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    session_open = _session_open_utc(DATE_VN)

    mkt = conn.execute(
        "SELECT SUM(COALESCE(volume,0)), SUM(COALESCE(buy_vol,0)+COALESCE(sell_vol,0)) "
        "FROM stock_prices WHERE interval='1m' AND date(trade_time)=? AND volume>0",
        (DATE_VN,)
    ).fetchone()
    mkt_cov        = round((mkt[1] or 0) * 100.0 / max(mkt[0] or 1, 1), 1)
    delta_reliable = mkt_cov >= SIDE_QUALITY_GATE

    eng = VWAPEngine(DB)
    _ctx = _silent() if is_live else contextlib.nullcontext()
    with _ctx:
        all_snaps = {s.security_id: s for s in eng.compute_all(top_n=800, date_vn=DATE_VN)}
    wh = WhaleHunter(DB)

    results = []
    for sym in watchlist:
        sec = conn.execute("SELECT security_id FROM securities WHERE symbol=?", (sym,)).fetchone()
        if not sec:
            continue
        res = analyze_symbol(conn, sec[0], sym, DATE_VN, session_open, all_snaps, wh, delta_reliable)
        if res:
            results.append(res)
    conn.close()

    # Filter / sort
    display = results
    if args.signals_only:
        display = [r for r in results if r['signals']]
    if args.top > 0:
        display = sorted(results, key=lambda r: -r['delta'])[:args.top]

    # ── Build output lines buffer ──────────────────────────────────
    lines: list[str] = []
    lines.append(f"{'='*72}")
    lines.append(f"  {B}{C}📋 VWAP WATCHLIST {label} — {now_vn}  ({len(watchlist)} mã, {label_src}){E}")
    lines.append(f"{'='*72}")
    lines.append(f"  Market Side Coverage: {mkt_cov}% | delta_reliable={delta_reliable}")

    if display:
        lines.append("")
        lines.extend(_summary_table_lines(display))
        lines.append(f"  Tổng: {len(display)}/{len(results)} mã")

    # Signal digest
    triggered = [(r['sym'], *sig) for r in results for sig in r['signals']]
    triggered.sort(key=lambda x: -x[3])
    lines.append(f"{'─'*72}")
    lines.append(f"  🔔 SIGNAL DIGEST (score ≥ {MIN_SCORE}):")

    # ── DATA QUALITY GATE ──────────────────────────────────────
    # Signal DELTA-DEPENDENT: HIDDEN_ACCUMULATION, DELTA_DIVERGENCE,
    # VWAP_REJECTION, VWAP_RECLAIM (có delta guard).
    # Signal PRICE-ONLY:      VWAP_BOUNCE, PVWAP_SUPPORT_TEST (không cần delta).
    DELTA_SIGNALS = {'HIDDEN_ACCUMULATION', 'DELTA_DIVERGENCE', 'VWAP_REJECTION', 'VWAP_RECLAIM'}
    data_ok = delta_reliable   # True nếu coverage ≥ 50%

    if not data_ok:
        lines.append(f"  {R}{B}⚠️  DATA QUALITY WARNING: Side Coverage={mkt_cov}% (ngưỡng an toàn ≥50%){E}")
        lines.append(f"  {R}   MASVN có thể bị FROZEN. Các signal dưới đây có độ tin cậy thấp.{E}")
        lines.append(f"  {R}   KHÔNG nên giao dịch dựa trên VWAP_REJECTION/RECLAIM hôm nay.{E}")
        lines.append(f"  {'─'*72}")

    if triggered:
        for sym, sig, dir_, sc, det in triggered:
            d_icon = '⬆️ BUY' if dir_ == 'BUY' else '⬇️ SELL'
            is_delta_dep = sig in DELTA_SIGNALS
            quality_tag  = f" {Y}[LOW-DATA]{E}" if (not data_ok and is_delta_dep) else ""
            lines.append(f"  {ICONS.get(sig,'•')} {B}{sym:<5}{E} {sig:<22} {d_icon:<10} score={sc:.0f}{quality_tag}")
    else:
        lines.append("  ⚪ Không có signal nào kích hoạt")
    lines.append(f"{'='*72}")

    # Chi tiết chỉ in khi không phải live mode
    if not args.no_detail and not is_live:
        lines.append(f"{'─'*72}")
        lines.append(f"  CHI TIẾT TỪNG MÃ:\n")
        for r in display:
            # capture print_detail output bằng StringIO
            import io
            buf = io.StringIO()
            _old = sys.stdout; sys.stdout = buf
            print_detail(r)
            sys.stdout = _old
            lines.extend(buf.getvalue().splitlines())

    return lines


# ════════════════════════════════════════════════════════════════
# IN-PLACE RENDERER (live mode)
# ════════════════════════════════════════════════════════════════

_last_line_count = 0  # số dòng đã render lần trước

def _render_inplace(lines: list[str], cycle: int, interval: int, remaining: int) -> None:
    """In đè toàn bộ `lines` tại chỗ, không cuộn terminal."""
    global _last_line_count

    now_vn = datetime.now(VN_TZ).strftime('%H:%M:%S')
def _render_clear(lines: list, cycle: int, interval: int) -> None:
    """Clear toàn bộ màn hình rồi in lại — đơn giản và chắc chắn không cuộn."""
    now_vn = datetime.now(VN_TZ).strftime('%H:%M:%S')
    sys.stdout.write("\033[2J\033[H")  # clear full screen + go home
    print("\n".join(lines))
    # Countdown trong dòng cuối, ghi đè tại chỗ
    for remaining in range(interval, 0, -1):
        now_vn = datetime.now(VN_TZ).strftime('%H:%M:%S')
        sys.stdout.write(
            f"\r  ⏱  [{now_vn}] Cycle #{cycle} — "
            f"refresh trong {remaining:3d}s  (Ctrl+C để thoát)"
        )
        sys.stdout.flush()
        time.sleep(1)
    print()


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Batch VWAP Watchlist Scanner")
    parser.add_argument("--date",         default=None,  help="Ngày phân tích YYYY-MM-DD (mặc định: hôm nay)")
    parser.add_argument("--name",         default="vip", help="Tên watchlist trong DB (mặc định: vip)")
    parser.add_argument("--market",       action="store_true", help="Quét toàn thị trường (tất cả mã có data hôm nay)")
    parser.add_argument("--signals-only", action="store_true", help="Chỉ in mã có signal kích hoạt")
    parser.add_argument("--top",          type=int, default=0, help="Top N mã delta dương nhất")
    parser.add_argument("--no-detail",    action="store_true", help="Chỉ in bảng tóm tắt")
    parser.add_argument("--loop",         action="store_true", help="Realtime mode: tự refresh liên tục (in-place, không cuộn)")
    parser.add_argument("--interval",     type=int, default=60, help="Giây giữa mỗi refresh (mặc định: 60)")
    args = parser.parse_args()

    if args.loop:
        cycle = 0
        try:
            while True:
                cycle += 1
                lines = run_scan(args)   # build buffer — logger bị chặn bởi _silent()
                _render_clear(lines, cycle, args.interval)
        except KeyboardInterrupt:
            print(f"\n\n  👋 Live mode đã dừng.\n")
    else:
        lines = run_scan(args)
        print("\n".join(lines))


if __name__ == "__main__":
    main()
