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

def _rjust(s: str, w: int) -> str:
    """Right-justify ANSI string to visible width w."""
    return ' ' * max(0, w - _visible_len(s)) + s

def _ljust(s: str, w: int) -> str:
    """Left-justify ANSI string to visible width w."""
    return s + ' ' * max(0, w - _visible_len(s))


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

    # pt_vol / pt_ratio from intraday VWAP snapshot (populated by VWAPEngine)
    pt_vol   = snap_obj.pt_vol   if snap_obj else 0
    pt_ratio = snap_obj.pt_ratio if snap_obj else 0.0

    return {
        'sym': symbol, 'close': close_c, 'open': first[1],
        'high': h_high, 'low': h_low,
        'vwap': vwap_t, 'std': std,
        'pvwap': pv, 'pvwap_date': pv_date,
        'vs_vwap': vs_vwap, 'vs_pv': vs_pv,
        'delta': total_d, 'total_vol': total_vol,
        'dv_pct': total_d / total_vol * 100 if total_vol else 0.0,
        'buy_vol': total_bv, 'sell_vol': total_sv,
        'pt_vol': pt_vol, 'pt_ratio': pt_ratio,
        'side_cov': side_cov, 'ncandles': len(rows),
        'bounces': bounces, 'signals': signals,
        'hist_deltas': hist_deltas,
        'hour_buckets': dict(hour_buckets),
    }


# ════════════════════════════════════════════════════════════════
# PRINT HELPERS
# ════════════════════════════════════════════════════════════════

def _summary_table_lines(results) -> list:
    """Trả về list các dòng cho bảng tóm tắt — ANSI-aware alignment."""
    # ── Column visible widths ──────────────────────────────────
    #  sym  close  vwap  vsv  pvwap  pvp  delta   dv   vol   pt   ptp  cov
    W  = (5,   6,    6,   6,   6,    7,   11,    7,   7,   6,    5,   5)
    S  = '  '  # col separator

    def hrow(*labels):
        cols = [f"{lb:>{w}}" for lb, w in zip(labels, W)]
        return S.join(cols)

    hdr = hrow('SYM', 'Close', 'VWAP', 'vsV%', 'PVWAP', 'pvp%',
               'Delta', 'Δ/V%', 'Vol(M)', 'PT(M)', 'PT%', 'Cov%') + S + 'Signals'
    # left-justify SYM header
    hdr = f"{'SYM':<{W[0]}}" + hdr[W[0]:]
    sep = '─' * _visible_len(hdr)

    lines = [hdr, sep]

    for r in results:
        pv    = r['pvwap']
        vsv   = r['vs_vwap']
        vsp   = r['vs_pv']
        dlt   = r['delta']
        dv    = r.get('dv_pct', dlt / r['total_vol'] * 100 if r['total_vol'] else 0)
        pt_v  = r.get('pt_vol', 0)
        pt_rt = r.get('pt_ratio', 0.0)

        sym_s   = _ljust(f"{B}{r['sym']}{E}", W[0])
        close_s = f"{r['close']:>{W[1]}.2f}"
        vwap_s  = f"{r['vwap']:>{W[2]}.2f}"
        vsv_s   = _rjust(f"{G if vsv >= 0 else R}{vsv:>+.1f}%{E}", W[3])
        pvwap_s = f"{pv:>{W[4]}.2f}" if pv else f"{'—':>{W[4]}}"
        pvp_s   = (
            _rjust(f"{G if vsp >= 0 else R}{vsp:>+.2f}%{E}", W[5])
            if vsp is not None else f"{'—':>{W[5]}}"
        )
        dlt_s   = _rjust(f"{G if dlt >= 0 else R}{dlt:>+,}{E}", W[6])
        dv_s    = _rjust(f"{G if dv  >= 0 else R}{dv:>+.1f}%{E}", W[7])
        vol_s   = f"{r['total_vol']/1e6:>{W[8]-1}.2f}M"
        pt_s    = (f"{pt_v/1e6:>{W[9]-1}.2f}M" if pt_v > 0 else f"{'—':>{W[9]}}")
        ptp_s   = (f"{pt_rt*100:>{W[10]-1}.1f}%" if pt_v > 0 else f"{'—':>{W[10]}}")
        cov_s   = f"{r['side_cov']:>{W[11]-1}.1f}%"
        sig_s   = ('  '.join(
            f"{ICONS.get(s,'•')}{s[:4]}({sc:.0f})"
            for s, _, sc, __ in r['signals'][:2]
        ) or '—')

        row = S.join([sym_s, close_s, vwap_s, vsv_s, pvwap_s, pvp_s,
                      dlt_s, dv_s, vol_s, pt_s, ptp_s, cov_s]) + S + sig_s
        lines.append(row)

    return lines


def print_summary_table(results):
    for line in _summary_table_lines(results):
        print(line)


def _signal_panel_lines(triggered: list, data_ok: bool) -> list:
    """Right-side signal digest panel — aligned, compact."""
    # triggered: list of (sym, sig, dir_, sc, det, delta, dv_pct)
    DELTA_SIGNALS = {'HIDDEN_ACCUMULATION', 'DELTA_DIVERGENCE', 'VWAP_REJECTION', 'VWAP_RECLAIM'}
    S  = '  '
    #       sym  dir  signal  sc  delta  dv
    W  = (5,   4,   22,    3,   8,    7)

    hdr = S.join([
        f"{'SYM':<{W[0]}}",
        f"{'DIR':<{W[1]}}",
        f"{'SIGNAL':<{W[2]}}",
        f"{'SC':>{W[3]}}",
        f"{'Delta':>{W[4]}}",
        f"{'Δ/V%':>{W[5]}}",
    ])
    lines = [f"{B}{hdr}{E}", '─' * _visible_len(hdr)]

    if not triggered:
        lines.append('  ⚪ No signals')
        return lines

    for sym, sig, dir_, sc, det, delta, dv_pct in triggered:
        low_data = (not data_ok) and (sig in DELTA_SIGNALS)
        dir_c    = G if dir_ == 'BUY' else R
        dlt_c    = G if delta >= 0 else R
        dv_c     = G if dv_pct >= 0 else R

        sym_s  = _ljust(f"{B}{sym}{E}", W[0])
        dir_s  = _ljust(f"{dir_c}{dir_}{E}", W[1])
        sig_s  = _ljust(sig, W[2])
        sc_s   = _rjust(f"{sc:.0f}", W[3])
        dlt_s  = _rjust(f"{dlt_c}{delta/1e6:>+.2f}M{E}", W[4])
        dv_s   = _rjust(f"{dv_c}{dv_pct:>+.1f}%{E}", W[5])
        tag    = f" {Y}[!]{E}" if low_data else ''

        lines.append(S.join([sym_s, dir_s, sig_s, sc_s, dlt_s, dv_s]) + tag)

    return lines


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
    import shutil
    DELTA_SIGNALS = {'HIDDEN_ACCUMULATION', 'DELTA_DIVERGENCE', 'VWAP_REJECTION', 'VWAP_RECLAIM'}
    data_ok  = delta_reliable
    term_w   = shutil.get_terminal_size((160, 40)).columns

    # triggered: (sym, sig, dir_, sc, det, delta, dv_pct)
    triggered = [
        (r['sym'], sig, dir_, sc, det, r['delta'], r.get('dv_pct', 0.0))
        for r in results for sig, dir_, sc, det in r['signals']
    ]
    triggered.sort(key=lambda x: -x[3])

    # ── Header ────────────────────────────────────────────────
    lines: list[str] = []
    hdr_title = f"  {B}{C}📋 VWAP WATCHLIST {label} — {now_vn}  ({len(watchlist)} mã, {label_src}){E}"
    lines.append('═' * term_w)
    lines.append(hdr_title)
    lines.append('═' * term_w)
    lines.append(f"  Market Side Coverage: {mkt_cov}% | delta_reliable={delta_reliable}")

    if not data_ok:
        lines.append(f"  {R}{B}⚠  Side Coverage={mkt_cov}% (<50%) — delta signals độ tin cậy thấp{E}")

    if display:
        # ── Left panel: main table ─────────────────────────────
        left = _summary_table_lines(display)
        left_w = max((_visible_len(l) for l in left), default=0)

        # ── Right panel: signal digest ─────────────────────────
        right = _signal_panel_lines(triggered, data_ok)
        right_w = max((_visible_len(l) for l in right), default=0)

        GAP = 4  # chars between panels
        if left_w + GAP + right_w <= term_w and triggered:
            # Side-by-side
            nrows = max(len(left), len(right))
            lines.append('')
            for i in range(nrows):
                lpart = left[i]  if i < len(left)  else ''
                rpart = right[i] if i < len(right) else ''
                pad   = ' ' * (left_w - _visible_len(lpart) + GAP)
                lines.append(lpart + pad + rpart)
        else:
            # Stacked
            lines.append('')
            lines.extend(left)
            if triggered:
                lines.append('')
                lines.append(f"  {B}🔔 SIGNAL DIGEST (score ≥ {MIN_SCORE}):{E}")
                lines.extend(right)

        lines.append(f"  Tổng: {len(display)}/{len(results)} mã")
    elif not triggered:
        lines.append("  ⚪ Không có signal nào kích hoạt")

    lines.append('═' * term_w)

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
