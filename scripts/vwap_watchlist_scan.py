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
    score_pt_accumulation, score_pt_dumping,
    _compute_vol_surge, WhaleHunter, MIN_SCORE, SIDE_QUALITY_GATE,
)

VN_TZ = timezone(timedelta(hours=7))
DB    = os.getenv("SMD_DB_PATH", os.path.join(PROJECT_ROOT, "data", "securities_master.db"))

# ════════════════════════════════════════════════════════════════
# SESSION CACHE — giảm SQLite queries cho data static trong ngày
# ════════════════════════════════════════════════════════════════
# Dữ liệu được cache:
#   meta   : symbol → (security_id, exchange)   — không đổi, reset khi restart
#   pvwap  : symbol → pvwap_row list            — đổi khi sang ngày mới
#   wlist  : list_name → [symbols]              — stable trong session
# Dữ liệu KHÔNG cache:
#   stock_prices 1m bars                        — realtime, luôn đọc mới
#   market side coverage                        — realtime
_SCAN_CACHE: dict = {
    'date'  : None,   # trade date đang cache
    'meta'  : {},     # symbol → (security_id, exchange)
    'pvwap' : {},     # symbol → list[Row]
    'wlist' : {},     # list_name → [str]
}

def _cache_check(date_vn: str):
    """Reset pvwap cache khi sang ngày mới. meta và wlist không cần reset."""
    if _SCAN_CACHE['date'] != date_vn:
        _SCAN_CACHE['date'] = date_vn
        _SCAN_CACHE['pvwap'].clear()
        # Không xoá meta/wlist — symbols và security_id không đổi theo ngày

ICONS = {
    'HIDDEN_ACCUMULATION': '🐋', 'VWAP_RECLAIM': '🚀',
    'DELTA_DIVERGENCE': '📊',    'VWAP_REJECTION': '🔴',
    'PVWAP_SUPPORT_TEST': '🎯',  'VWAP_BOUNCE': '🔁',
}

# Tên viết tắt cho cột Signals bảng trái — đủ để nhận biết, vừa cột hẹp
SIG_ABBR = {
    'HIDDEN_ACCUMULATION': 'HIDD_ACCUM',
    'VWAP_RECLAIM':        'VWAP_RECLAIM',
    'DELTA_DIVERGENCE':    'DELTA_DIV',
    'VWAP_REJECTION':      'VWAP_REJECT',
    'PVWAP_SUPPORT_TEST':  'PVWAP_SUPP',
    'VWAP_BOUNCE':         'VWAP_BOUNCE',
    'PT_DUMPING':          'PT_DUMPING',
    'PT_ACCUMULATION':     'PT_ACCUM',
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

# ════════════════════════════════════════════════════════════════
# PT_VOL + NN DATA — Lấy từ stock_prices 1D một lần / ngày
# ════════════════════════════════════════════════════════════════
_PT_NN_CACHE: dict = {}   # DATE_VN → {symbol: {pt_vol, nn_net, nn_buy, nn_sell}}

def _fetch_pt_nn_1d(conn, DATE_VN: str) -> dict:
    """Bulk-fetch pt_vol + avg_pt_price + foreign_buy/sell từ 1D bars cho ngày DATE_VN.
    Trả về dict symbol → {pt_vol, avg_pt_price, nn_buy, nn_sell, nn_net}.
    Cache theo ngày — không refetch trong cùng phiên.
    """
    if DATE_VN in _PT_NN_CACHE:
        return _PT_NN_CACHE[DATE_VN]
    rows = conn.execute("""
        SELECT s.symbol,
               COALESCE(sp.pt_vol, 0)           AS pt_vol,
               COALESCE(sp.avg_pt_price, 0.0)   AS avg_pt_price,
               COALESCE(sp.foreign_buy_vol, 0)  AS nn_buy,
               COALESCE(sp.foreign_sell_vol, 0) AS nn_sell
        FROM stock_prices sp
        JOIN securities s ON s.security_id = sp.security_id
        WHERE sp.interval = '1D'
          AND date(sp.trade_time) = ?
    """, (DATE_VN,)).fetchall()
    result = {}
    for r in rows:
        sym, pt, apt, nb, ns = r[0], r[1] or 0, r[2] or 0.0, r[3] or 0, r[4] or 0
        result[sym] = {
            'pt_vol': pt, 'avg_pt_price': apt,
            'nn_buy': nb, 'nn_sell': ns, 'nn_net': nb - ns,
        }
    _PT_NN_CACHE[DATE_VN] = result
    return result

# ANALYSIS CORE
# ════════════════════════════════════════════════════════════════

def analyze_symbol(conn, sid, symbol, exchange, DATE_VN, session_open, all_snaps, wh, delta_reliable):
    """Phân tích một mã. Trả về dict kết quả hoặc None nếu không có dữ liệu."""
    # pvwap_row: cache cả ngày vì PVWAP luôn lấy từ ngày hôm qua trở về trước
    if symbol not in _SCAN_CACHE['pvwap']:
        _SCAN_CACHE['pvwap'][symbol] = conn.execute('''
            SELECT trade_date, vwap, vwap_upper1, vwap_lower1, vwap_upper2, vwap_lower2,
                   cum_volume, cum_delta, buy_vol, sell_vol, side_cov_pct, session_open, session_close
            FROM daily_vwap_summary
            WHERE security_id=? AND trade_date < ?
            ORDER BY trade_date DESC LIMIT 5
        ''', (sid, DATE_VN)).fetchall()
    pvwap_row = _SCAN_CACHE['pvwap'][symbol]

    pv         = pvwap_row[0]['vwap']      if pvwap_row else None
    pv_date    = pvwap_row[0]['trade_date'] if pvwap_row else None
    hist_deltas = [(r['trade_date'], r['cum_delta'] or 0) for r in pvwap_row]

    # Session cap theo sàn: loại ATC candle (Open=Close=settlement)
    # REVERT: ATC được include vì giá Close = giá đóng cửa chính thức, volume thật.
    # BVC fix cho ATC: dùng close-vs-open direction thay vì Parkinson (H=L=0).

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

    # ── Ceiling / Floor detection ─────────────────────────────────
    # Khi mã kịch trần/sàn, DNSE volume≈0 (không có lệnh khớp)
    # nhưng MASVN ghi cumulative buy_vol lớn → delta vô nghĩa.
    # Candle bị lỗi: vol=0 nhưng (buy+sell) > 500 CP → zero out side.
    cleaned = []
    limit_candles = 0
    for r in rows:
        vol = r[5] or 0
        bv  = r[6] or 0
        sv  = r[7] or 0
        if (bv + sv) > max(vol, 1) * 10:   # MASVN side >> DNSE vol → ceiling/floor
            limit_candles += 1
            cleaned.append((r[0], r[1], r[2], r[3], r[4], vol, 0, 0, 0))
        else:
            cleaned.append(r)
    rows = cleaned
    is_limit = limit_candles > len(rows) * 0.3   # >30% nến bị lỗi → limit stock

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

    # Whale signals — bỏ qua nếu là limit-price stock (delta không đáng tin)
    signals  = []
    snap_obj = all_snaps.get(sid)
    if snap_obj and not is_limit:
        snap = {
            'snapshot_time': snap_obj.snapshot_time,
            'vwap': snap_obj.vwap, 'last_close': snap_obj.last_close,
            'vwap_upper1': snap_obj.vwap_upper1, 'vwap_lower1': snap_obj.vwap_lower1,
            'vwap_upper2': snap_obj.vwap_upper2, 'vwap_lower2': snap_obj.vwap_lower2,
            'cum_volume': snap_obj.cum_volume, 'cum_delta': snap_obj.cum_delta,
            # Put-through — populated later when pt_nn data is merged
            'pt_vol': snap_obj.pt_vol, 'avg_pt_price': snap_obj.avg_pt_price,
            'pt_ratio': snap_obj.pt_ratio,
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
            ('PT_ACCUMULATION',     'BUY',  *score_pt_accumulation(snap, delta_reliable)),
            ('PT_DUMPING',          'SELL', *score_pt_dumping(snap)),
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
        'is_limit': is_limit,          # True nếu mã kịch trần/sàn
        'limit_candles': limit_candles, # Số nến bị lọc
        'pt_vol': 0, 'nn_buy': 0, 'nn_sell': 0, 'nn_net': 0,  # sẽ merge sau
    }


# ════════════════════════════════════════════════════════════════
# INDEX ANALYSIS (đọc từ market_indices / index_vwap_summary)
# ════════════════════════════════════════════════════════════════

MARKET_INDEX_CODES = {'VNINDEX', 'VN30', 'VN100', 'HNX30', 'NOVIN'}


def analyze_index_symbol(conn, symbol: str, DATE_VN: str) -> dict | None:
    """Phân tích chỉ số thị trường từ market_indices + index_vwap_summary.
    Trả về dict cùng cấu trúc với analyze_symbol() để tái dùng display functions.
    """
    rows = conn.execute('''
        SELECT trade_time, open, high, low, close, volume,
               COALESCE(buy_vol,0)  AS bv,
               COALESCE(sell_vol,0) AS sv,
               COALESCE(buy_vol,0)-COALESCE(sell_vol,0) AS delta
        FROM market_indices
        WHERE index_code=? AND interval='1m'
          AND date(trade_time)=?
        ORDER BY trade_time
    ''', (symbol, DATE_VN)).fetchall()

    if not rows:
        return None

    last  = rows[-1];  first = rows[0]
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
    std = math.sqrt(max(0, (cum_pv2 / cum_v) - vwap_t ** 2)) if cum_v > 0 else 0

    close_c = last[4]
    vs_vwap = (close_c - vwap_t) / vwap_t * 100 if vwap_t else 0

    # PVWAP: ngày gần nhất trước DATE_VN từ index_vwap_summary
    pv_row = conn.execute('''
        SELECT trade_date, vwap FROM index_vwap_summary
        WHERE index_code=? AND trade_date < ?
        ORDER BY trade_date DESC LIMIT 1
    ''', (symbol, DATE_VN)).fetchone()
    pv      = pv_row[1] if pv_row else None
    pv_date = pv_row[0] if pv_row else None
    vs_pv   = (close_c - pv) / pv * 100 if pv else None

    # VWAP bounces
    bounces = 0; was_below = False
    for r in rows[-20:]:
        c = r[4] or 0.0
        if c <= 0: continue
        if c < vwap_t * 0.998: was_below = True
        elif was_below and c >= vwap_t * 0.998:
            bounces += 1; was_below = False

    # Hourly delta
    hour_buckets = defaultdict(lambda: {'bv': 0, 'sv': 0, 'v': 0})
    for r in rows:
        t_str = str(r[0]).replace('T', ' ')
        nums  = re.findall(r'\d+', t_str)
        h_vn  = int(nums[3]) if len(nums) > 3 else 0
        bk    = f'{h_vn:02d}h'
        hour_buckets[bk]['bv'] += r[6]
        hour_buckets[bk]['sv'] += r[7]
        hour_buckets[bk]['v']  += r[5]

    return {
        'sym': symbol,        # tên sạch, không có ▼ suffix
        'is_index': True,     # flag phân biệt index vs stock
        'close': close_c, 'open': first[1],
        'high': h_high, 'low': h_low,
        'vwap': vwap_t, 'std': std,
        'pvwap': pv, 'pvwap_date': pv_date,
        'vs_vwap': vs_vwap, 'vs_pv': vs_pv,
        'delta': total_d, 'total_vol': total_vol,
        'buy_vol': total_bv, 'sell_vol': total_sv,
        'side_cov': side_cov, 'ncandles': len(rows),
        'bounces': bounces, 'signals': [],
        'hist_deltas': [(pv_date, 0)] if pv_date else [],
        'hour_buckets': dict(hour_buckets),
    }



def _summary_table_lines(results) -> list:
    """Trả về list các dòng cho bảng tóm tắt — ANSI/emoji-aware column alignment."""
    # ── Visible column widths (ký tự hiển thị, không kể ANSI/emoji padding) ──
    #   sym  cls  vwp  vsv  pvw  pvp  dlt   dv   vol   pt  ptp   nn  cov
    W = (8,   8,   8,   6,   8,   7,   13,   8,   7,   7,   5,   8,   7)
    S = '  '  # separator giữa các cột

    # Header — plain text, no ANSI, widths match W
    cols_h = [
        f"{'SYM':<{W[0]}}",  f"{'Close':>{W[1]}}",  f"{'VWAP':>{W[2]}}",
        f"{'vsV%':>{W[3]}}",  f"{'PVWAP':>{W[4]}}",  f"{'pvp%':>{W[5]}}",
        f"{'Delta':>{W[6]}}",  f"{'Δ/V%':>{W[7]}}",  f"{'Vol(M)':>{W[8]}}",
        f"{'PT(M)':>{W[9]}}",  f"{'PT%':>{W[10]}}",  f"{'NN Net':>{W[11]}}",
        f"{'Cov%':>{W[12]}}",  'Signals',
    ]
    hdr = S.join(cols_h)
    lines = [hdr, '─' * _visual_len(hdr)]

    # Compact signal display: ABBR(score) — readable text, cột hẹp
    def _sig_compact(signals):
        if not signals:
            return '—'
        parts = []
        for s, dir_, sc, __ in signals[:2]:
            abbr = SIG_ABBR.get(s, s[:10])
            clr  = G if dir_ == 'BUY' else R
            parts.append(f"{clr}{abbr}({sc:.0f}){E}")
        return '  '.join(parts)

    for r in results:
        is_idx = r.get('is_index', False)
        is_lim = r.get('is_limit', False)

        pv     = r['pvwap']
        vsv    = r['vs_vwap']
        vsp    = r['vs_pv']
        dlt    = r['delta']
        tv     = max(r['total_vol'], 1)
        dv_pct = dlt / tv * 100
        pt_v   = r.get('pt_vol', 0)
        pt_rt  = r.get('pt_ratio', 0.0)
        avg_pt = r.get('avg_pt_price', 0.0)
        vwap_r = r.get('vwap', 0.0)
        nn_net = r.get('nn_net', 0)

        # SYM — bold, cyan for index rows
        sym_s = _ljust(
            f"{B}{C if is_idx else ''}{r['sym']}{E if not is_idx else E}",
            W[0]
        )

        # Numeric columns — plain, right-aligned (no color needed)
        close_s = f"{r['close']:>{W[1]}.2f}"
        vwap_s  = f"{r['vwap']:>{W[2]}.2f}"

        # vsV% — colored, ANSI-aware pad
        vsv_s = _rjust(f"{G if vsv >= 0 else R}{vsv:>+.1f}%{E}", W[3])

        # PVWAP — plain
        pvwap_s = f"{pv:{W[4]}.2f}" if pv else f"{'—':>{W[4]}}"

        # pvp% — colored or dash; MUST be W[5] visible chars
        if vsp is not None:
            pvp_s = _rjust(f"{G if vsp >= 0 else R}{vsp:>+.2f}%{E}", W[5])
        else:
            pvp_s = f"{'—':>{W[5]}}"

        # Delta — colored + comma-formatted
        if is_lim:
            delta_s = _rjust(f"{Y}N/A{E}", W[6])
        else:
            delta_s = _rjust(f"{G if dlt >= 0 else R}{dlt:>+,}{E}", W[6])

        # Δ/V% — colored
        if is_lim:
            dv_s = _rjust(f"{Y}N/A{E}", W[7])
        else:
            dv_s = _rjust(f"{G if dv_pct >= 0 else R}{dv_pct:>+.1f}%{E}", W[7])

        # Vol(M) — right-aligned, W[8]-1 digits + "M"
        vol_s = f"{r['total_vol']/1e6:>{W[8]-1}.2f}M"

        # PT(M) — right-aligned, W[9]-1 digits + "M", or dash
        if not is_idx and pt_v > 0:
            pt_s = f"{pt_v/1e6:>{W[9]-1}.2f}M"
        else:
            pt_s = f"{'—':>{W[9]}}"

        # PT% — colored or dash; exact W[10] visible chars
        if not is_idx and pt_rt > 0 and avg_pt > 0 and vwap_r > 0:
            pct = pt_rt * 100
            clr = G if avg_pt > vwap_r else R
            ptp_s = _rjust(f"{clr}{pct:.1f}%{E}", W[10])
        else:
            ptp_s = f"{'—':>{W[10]}}"

        # NN Net — colored or dash; exact W[11] visible chars
        if not is_idx and nn_net != 0:
            clr = G if nn_net > 0 else R
            nn_s = _rjust(f"{clr}{nn_net/1e3:>+.0f}K{E}", W[11])
        else:
            nn_s = f"{'—':>{W[11]}}"

        # Cov% — right-aligned, W[12]-1 digits + "%"
        cov_s = f"{r['side_cov']:>{W[12]-1}.1f}%"

        # Signals — compact icon+score, limit width to avoid blowing out columns
        if is_lim:
            sig_s = '⛔TRẦN' if r['close'] >= (pv or r['close']) else '⛔SÀN'
        else:
            sig_s = _sig_compact(r['signals'])

        row = S.join([
            sym_s, close_s, vwap_s, vsv_s, pvwap_s, pvp_s,
            delta_s, dv_s, vol_s, pt_s, ptp_s, nn_s, cov_s,
        ]) + S + sig_s
        lines.append(row)

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
    if r.get('is_limit'):
        print(f"  │  Delta : {Y}⛔ TRẦN/SÀN — delta không đáng tin ({r['limit_candles']} nến bị lọc){E}")
    else:
        print(f"  │  Delta : {r['delta']:+,}  buy={r['buy_vol']:,}  sell={r['sell_vol']:,}  side={r['side_cov']}%")
    # PT Vol + NN
    pt    = r.get('pt_vol', 0)
    apt   = r.get('avg_pt_price', 0.0)
    ptratio = r.get('pt_ratio', 0.0)
    vwap_r  = r.get('vwap', 0.0)
    nn_b  = r.get('nn_buy', 0); nn_s = r.get('nn_sell', 0); nn_n = r.get('nn_net', 0)
    if pt > 0 or nn_b > 0 or nn_s > 0:
        nn_tag = f"{G}+{nn_n/1e3:.0f}K{E}" if nn_n > 0 else (f"{R}{nn_n/1e3:.0f}K{E}" if nn_n < 0 else "—")
        pt_tag = ""
        if pt > 0 and apt > 0 and vwap_r > 0:
            premium = (apt - vwap_r) / vwap_r * 100
            clr = G if apt > vwap_r else R
            pt_tag = f"  avgPT={clr}{apt:.3f}{E} ({clr}{premium:+.2f}% vs VWAP{E})  PT%={ptratio*100:.1f}%"
        print(f"  │  Block : PT={pt/1e3:.0f}K CP{pt_tag}  | NN buy={nn_b/1e3:.0f}K  sell={nn_s/1e3:.0f}K  net={nn_tag}")
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
        # Luôn pin VNINDEX + NOVIN đầu danh sách
        if 'VNINDEX' not in watchlist:
            watchlist = ['VNINDEX'] + watchlist
        if 'NOVIN' not in watchlist:
            idx = watchlist.index('VNINDEX') + 1
            watchlist.insert(idx, 'NOVIN')
        if not watchlist:
            return [f"❌ Không có dữ liệu intraday hôm nay trong DB"], []
    else:
        watchlist = load_watchlist(list_name=args.name, db_path=DB)
        if not watchlist:
            return [f"❌ Watchlist '{args.name}' trống hoặc không tồn tại."], []
        # Pin VNINDEX + NOVIN đầu mọi watchlist
        if 'NOVIN' not in watchlist:
            watchlist = ['NOVIN'] + watchlist
        if 'VNINDEX' not in watchlist:
            watchlist = ['VNINDEX'] + watchlist

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

    # Invalidate pvwap cache nếu sang ngày mới
    _cache_check(DATE_VN)

    results = []
    for sym in watchlist:
        # ─ Chỉ số thị trường: đọc từ market_indices, không cần securities
        if sym.upper() in MARKET_INDEX_CODES:
            res = analyze_index_symbol(conn, sym.upper(), DATE_VN)
        else:
            # Cache security metadata (security_id, exchange) — không đổi trong session
            if sym not in _SCAN_CACHE['meta']:
                sec = conn.execute(
                    "SELECT security_id, exchange FROM securities WHERE symbol=?", (sym,)
                ).fetchone()
                if sec:
                    _SCAN_CACHE['meta'][sym] = (sec[0], sec[1] or 'UNKNOWN')
            cached_meta = _SCAN_CACHE['meta'].get(sym)
            if not cached_meta:
                continue
            sid, exchange = cached_meta
            res = analyze_symbol(
                conn, sid, sym, exchange,
                DATE_VN, session_open, all_snaps, wh, delta_reliable
            )
        if res:
            results.append(res)
    # PT/NN data cập nhật realtime trong phiên → không dùng cache cũ
    _PT_NN_CACHE.pop(DATE_VN, None)
    pt_nn = _fetch_pt_nn_1d(conn, DATE_VN)
    for r in results:
        nn_data = pt_nn.get(r.get('sym', ''), {})
        pt_vol         = nn_data.get('pt_vol', 0)
        r['pt_vol']      = pt_vol
        r['avg_pt_price'] = nn_data.get('avg_pt_price', 0.0)
        r['nn_buy']      = nn_data.get('nn_buy', 0)
        r['nn_sell']     = nn_data.get('nn_sell', 0)
        r['nn_net']      = nn_data.get('nn_net', 0)
        total_all        = r.get('total_vol', 0) + pt_vol
        r['pt_ratio']    = pt_vol / total_all if total_all > 0 else 0.0

    conn.close()

    # Filter / sort
    display = results
    if args.signals_only:
        display = [r for r in results if r['signals']]

    # Sort key
    sort_by = getattr(args, 'sort', 'delta')
    if sort_by == 'abs_delta':
        sort_key = lambda r: -abs(r['delta'])
    elif sort_by == 'symbol':
        sort_key = lambda r: r['sym']
    else:  # 'delta' — default: mã buy pressure mạnh nhất lên đầu
        sort_key = lambda r: -r['delta']

    display = sorted(display, key=sort_key)

    # ── Pin các chỉ số thị trường (VNINDEX...) lên đầu bất kể sort ────────────
    _index_rows = [r for r in display if r.get('is_index')]
    _stock_rows  = [r for r in display if not r.get('is_index')]
    display = _index_rows + _stock_rows

    if args.top > 0:
        # top áp dụng cho stock rows; index rows luôn hiện
        display = _index_rows + _stock_rows[:args.top]

    # ── Signal digest — build cột PHẢI (ASCII table, không emoji) ────
    triggered = [(r['sym'], *sig, r['delta'], r['total_vol']) for r in results for sig in r['signals']]
    triggered.sort(key=lambda x: -x[3])
    DELTA_SIGNALS = {'HIDDEN_ACCUMULATION', 'DELTA_DIVERGENCE', 'VWAP_REJECTION', 'VWAP_RECLAIM'}
    data_ok = delta_reliable

    # Rút gọn tên signal để vừa cột
    SIG_SHORT = {
        'VWAP_BOUNCE':          'VWAP_BOUNCE',
        'VWAP_REJECTION':       'VWAP_REJECT',
        'VWAP_RECLAIM':         'VWAP_RECLAIM',
        'PVWAP_SUPPORT_TEST':   'PVWAP_SUPP',
        'HIDDEN_ACCUMULATION':  'HIDD_ACCUM',
        'DELTA_DIVERGENCE':     'DELTA_DIV',
    }
    # ASCII direction tag (không dùng emoji ⬆️ vì chiếm 2 cols)
    def _dir_tag(d): return f"{G}BUY {E}" if d == 'BUY' else f"{R}SELL{E}"

    # Số signal = --top (khớp cột trái), hoặc tất cả
    sig_limit = args.top if args.top > 0 else len(triggered)
    sig_lines: list[str] = []

    # Header cột phải — căn giống header cột trái
    hdr_sep = '─' * 38
    sig_lines.append(f"  {'SIGNAL DIGEST':^36}  sc >= {MIN_SCORE}")
    sig_lines.append(f"  {hdr_sep}")
    if not data_ok:
        sig_lines.append(f"  {R}{B}⚠️  Coverage={mkt_cov}% [LOW-DATA]{E}")
        sig_lines.append(f"  {R}   MASVN có thể bị FROZEN{E}")
    if triggered:
        for sym, sig, dir_, sc, det, delta, total_vol in triggered[:sig_limit]:
            d_icon = '⬆️' if dir_ == 'BUY' else '⬇️'
            is_delta_dep = sig in DELTA_SIGNALS
            q_tag = f" {Y}[!]{E}" if (not data_ok and is_delta_dep) else ""
            sig_lines.append(
                f"  {ICONS.get(sig,'•')} {B}{sym:<5}{E} "
                f"{d_icon} {sig:<22} sc={sc:.0f}{q_tag}"
            )
        if len(triggered) > sig_limit:
            sig_lines.append(f"  … (+{len(triggered)-sig_limit} mã khác)")
    else:
        sig_lines.append("  ⚪ Không có signal nào kích hoạt")

    # ── Cột TRÁI: VWAP table ─────────────────────────────────────────
    lines: list[str] = []
    lines.append(f"{'='*72}")
    lines.append(f"  {B}{C}📋 VWAP WATCHLIST {label} — {now_vn}  ({len(watchlist)} mã, {label_src}){E}")
    lines.append(f"{'='*72}")
    lines.append(f"  Market Side Coverage: {mkt_cov}% | delta_reliable={delta_reliable}")

    if display:
        lines.append("")
        # Đếm số dòng header trước khi summary table bắt đầu
        # _summary_table_lines có col-header (dòng 0) + separator (dòng 1) + data...
        # Vạch phân cách signal digest căn với dòng separator = left_pre_table + 1
        left_pre_table = len(lines)
        lines.extend(_summary_table_lines(display))
        lines.append(f"  Tổng: {len(display)}/{len(results)} mã")
    else:
        left_pre_table = len(lines)

    # ── Cột PHẢI: Signal Digest ────────────────────────────────────
    # Pad đầu sig_lines để separator thẳng hàng với separator cột trái
    pad_top = left_pre_table       # căn thẳng với col-header cột trái

    DIR_COL = {'BUY': f"{G}BUY {E}", 'SELL': f"{R}SELL{E}"}

    sig_limit = args.top if args.top > 0 else len(triggered)
    sig_lines: list[str] = [''] * pad_top   # pad trống để căn hàng

    # Header + separator thẳng hàng với col-header cột trái
    sig_lines.append(f"  {'SYM':<5}  {'DIR':<4}  {'SIGNAL':<22}  {'SC':>3}  {'Delta':>8}  {'Δ/V%':>6}")
    sig_lines.append(f"  {'─'*55}")

    if not data_ok:
        sig_lines.append(f"  {R}[!] Coverage={mkt_cov}% — LOW DATA{E}")

    if triggered:
        for sym, sig, dir_, sc, det, delta, total_vol in triggered[:sig_limit]:
            q_mark = f"{Y}*{E}" if (not data_ok and sig in DELTA_SIGNALS) else ' '
            dv_pct  = delta / max(total_vol, 1) * 100
            d_abs   = delta / 1_000_000
            dv_col  = f"{G if dv_pct >= 0 else R}{dv_pct:>+6.1f}%{E}"
            d_col   = f"{G if delta >= 0 else R}{d_abs:>+7.2f}M{E}"
            sig_lines.append(
                f"  {B}{sym:<5}{E}  {DIR_COL.get(dir_, dir_)}  {sig:<22}  {sc:>3.0f}{q_mark} {d_col}  {dv_col}"
            )
        if len(triggered) > sig_limit:
            sig_lines.append(f"  ... (+{len(triggered)-sig_limit} more)")
    else:
        sig_lines.append("  -- no signals --")

    # Chi tiết chỉ in khi không phải live mode
    if not args.no_detail and not is_live:
        lines.append(f"{'─'*72}")
        lines.append(f"  CHI TIẾT TỪNG MÃ:\n")
        for r in display:
            import io
            buf = io.StringIO()
            _old = sys.stdout; sys.stdout = buf
            print_detail(r)
            sys.stdout = _old
            lines.extend(buf.getvalue().splitlines())

    return lines, sig_lines



# ════════════════════════════════════════════════════════════════
# IN-PLACE RENDERER (live mode)
# ════════════════════════════════════════════════════════════════

import shutil

import re as _re

def _visual_len(s: str) -> int:
    """
    Tính độ rộng THỰC TẾ trên terminal:
      - Strip ANSI escape codes (màu, bold...)
      - Emoji và CJK ký tự chiếm 2 cột, đếm đúng là 2
      - Variation selector U+FE0F (theo sau emoji) không chiếm cột, bỏ qua
    """
    clean = _re.sub(r'\x1b\[[0-9;]*m', '', s)  # strip ANSI
    w = 0
    for ch in clean:
        cp = ord(ch)
        if cp == 0xFE0F:   # variation selector — 0 width
            pass
        elif cp >= 0x2E80:  # CJK + emoji range → 2 cols
            w += 2
        else:
            w += 1
    return w

def _rjust(s: str, w: int) -> str:
    """Right-justify s to visible width w (ANSI + emoji aware)."""
    return ' ' * max(0, w - _visual_len(s)) + s

def _ljust(s: str, w: int) -> str:
    """Left-justify s to visible width w (ANSI + emoji aware)."""
    return s + ' ' * max(0, w - _visual_len(s))

# Keep old name as alias for compatibility
_strip_ansi = lambda s: _re.sub(r'\x1b\[[0-9;]*m', '', s)

def _render_clear(left: list, right: list, cycle: int, interval: int) -> None:
    """
    Ghép 2 cột side-by-side rồi in đè tại chỗ, không cuộn terminal.

    Dùng escape CHA (\033[{N}G) để cột phải LUÔN bắt đầu tại cột N,
    bất kể cột trái dài bao nhiêu — không bao giờ bị đẩy dòng.
    """
    term    = shutil.get_terminal_size(fallback=(160, 45))
    term_w  = term.columns
    term_h  = term.lines
    max_rows = max(5, term_h - 3)

    col_w   = term_w * 3 // 5     # cột trái chiếm 60% màn hình
    right_x = col_w + 3           # cột phải bắt đầu tại 60%+3

    left_vis  = left[:max_rows]
    right_vis = right[:max_rows]
    n_rows    = max(len(left_vis), len(right_vis))

    sys.stdout.write("\033[2J\033[H")   # clear screen + cursor về (1,1)
    for i in range(n_rows):
        l_raw = left_vis[i]  if i < len(left_vis)  else ""
        r_raw = right_vis[i] if i < len(right_vis) else ""
        # In cột trái, sau đó dùng CHA để nhảy đến đúng cột right_x
        # → cột phải không bao giờ bị lệch dù cột trái tràn
        sys.stdout.write(l_raw + f"\033[{right_x}G" + r_raw + "\n")

    sys.stdout.flush()

    # Countdown đếm ngược
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
# STATIC SIDE-BY-SIDE PRINTER  (non-live mode)
# ════════════════════════════════════════════════════════════════

def _print_side_by_side(left: list, right: list) -> None:
    """
    In hai bảng cạnh nhau trên cùng terminal — không cần loop.
    Dùng escape CHA (\\033[{N}G) để cột phải bắt đầu tại cột cố định,
    bất kể cột trái rộng bao nhiêu (cùng cơ chế với _render_clear).
    """
    term_w  = shutil.get_terminal_size(fallback=(160, 45)).columns
    # right_x: bắt đầu cột phải tại max visible width của cột trái + 4 chars gap
    # Dùng header (dòng đầu, không có ANSI màu) để đo ổn định hơn
    left_w  = max((_visual_len(l) for l in left), default=80)
    right_x = min(left_w + 4, term_w - 30)   # không đẩy cột phải ra ngoài màn hình

    n = max(len(left), len(right))
    out = []
    for i in range(n):
        l = left[i]  if i < len(left)  else ''
        r = right[i] if i < len(right) else ''
        out.append(l + f"\033[{right_x}G" + r)
    sys.stdout.write('\n'.join(out) + '\n')
    sys.stdout.flush()


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Batch VWAP Watchlist Scanner")
    parser.add_argument("--date",         default=None,  help="Ngày phân tích YYYY-MM-DD (mặc định: hôm nay)")
    parser.add_argument("--name",         default="vip", help="Tên watchlist trong DB (mặc định: vip)")
    parser.add_argument("--market",       action="store_true", help="Quét toàn thị trường (tất cả mã có data hôm nay)")
    parser.add_argument("--signals-only", action="store_true", help="Chỉ in mã có signal kích hoạt")
    parser.add_argument("--top",          type=int, default=0, help="Top N mã sau khi sort")
    parser.add_argument("--sort",         default="delta",
                        choices=["delta", "abs_delta", "symbol"],
                        help="Sắp xếp: delta (mua mạnh nhất lên đầu), abs_delta (biến động lớn nhất), symbol (A-Z). Mặc định: delta")
    parser.add_argument("--no-detail",    action="store_true", help="Chỉ in bảng tóm tắt")
    parser.add_argument("--loop",         action="store_true", help="Realtime mode: tự refresh liên tục (in-place, không cuộn)")
    parser.add_argument("--interval",     type=int, default=60, help="Giây giữa mỗi refresh (mặc định: 60)")
    args = parser.parse_args()

    if args.loop:
        cycle = 0
        try:
            while True:
                cycle += 1
                left, right = run_scan(args)
                _render_clear(left, right, cycle, args.interval)
        except KeyboardInterrupt:
            print(f"\n\n  👋 Live mode đã dừng.\n")
    else:
        left, right = run_scan(args)
        _print_side_by_side(left, right)


if __name__ == "__main__":
    main()
