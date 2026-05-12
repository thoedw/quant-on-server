#!/usr/bin/env python3
"""
realtime/index_bvc_worker.py
════════════════════════════════════════════════════════
Worker cập nhật OHLCV 1m + BVC cho VNINDEX/VN30, và tính noVININDEX mỗi 60 giây.

Pipeline mỗi cycle:
  1. Fetch 1m OHLCV từ DNSE chart API → UPSERT market_indices, BVC classify
  2. Tính noVININDEX: loại trừ đóng góp điểm của VIN group (VIC, VHM, VRE)

noVININDEX formula:
  w_i = issueShare_i × ref_price_i / Σ_HOSE(issueShare_j × ref_price_j)
  VIN_drag_t   = Σ_VIN[w_i × (p_i_t/p_i_open − 1)]
  noVIN_return = (VNINDEX_t/VNINDEX_open − 1 − VIN_drag_t) / (1 − W_VIN)
  noVININDEX_t = VNINDEX_open × (1 + noVIN_return)

Cách chạy:
  pm2 start realtime/index_bvc_worker.py --name index_bvc --interpreter python3
"""

import os
import sys
import json
import math
import time
import sqlite3
import logging
import requests
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# ── Cấu hình ───────────────────────────────────────────────────
VN_TZ         = timezone(timedelta(hours=7))
DB_PATH       = os.path.join(PROJECT_ROOT, 'data', 'securities_master.db')
INDEX_API_URL = 'https://services.entrade.com.vn/chart-api/v2/ohlcs/index'
SYMBOLS       = ['VNINDEX', 'VN30']
POLL_INTERVAL = 60
MARKET_OPEN   = (9,  0)
MARKET_CLOSE  = (15, 5)

# noVININDEX — cổ phiếu hệ sinh thái Vingroup niêm yết HOSE
VIN_GROUP = ['VIC', 'VHM', 'VRE']

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [IndexBVC] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


# ── BVC (Easley-López de Prado-O'Hara) ─────────────────────────

def _norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

_SQRT_2LN2 = math.sqrt(2.0 * math.log(2.0))


def _bvc(o, h, l, c, v):
    """BVC: phân loại volume thành buy/sell."""
    if v <= 0:
        return 0, 0
    r  = h - l
    dp = c - o
    if r < 1e-6:
        if abs(dp) < 1e-6:
            bv = v // 2
            return bv, v - bv
        return (v, 0) if dp > 0 else (0, v)
    z  = max(-4.0, min(4.0, dp / (r / _SQRT_2LN2)))
    bv = max(0, int(round(v * _norm_cdf(z))))
    return bv, max(0, v - bv)


# ── VIN weights ─────────────────────────────────────────────────

def load_vin_weights(conn):
    """
    Tính trọng số VIN group trong VNINDEX từ issueShare × ref_price.

    Công thức: w_i = issueShare_i × ref_price_i / Σ_HOSE(issueShare_j × ref_price_j)

    Nguồn: financial_reports (VietCap, Q4/2025) + ref_prices (cập nhật hàng ngày).
    Chỉ cần chạy 1 lần/ngày vì issueShare thay đổi rất ít (tối đa vài lần/năm).

    Returns: (weights_dict, W_VIN)
      weights_dict : {symbol: weight} cho các mã trong VIN_GROUP
      W_VIN        : tổng trọng số VIN group (float 0–1)
    """
    rows = conn.execute("""
        SELECT s.symbol,
               json_extract(fr.data, '$.issueShare') AS shares,
               rp.ref_price
        FROM financial_reports fr
        JOIN securities s  ON s.security_id  = fr.security_id
        JOIN ref_prices rp ON rp.symbol       = s.symbol
        WHERE fr.report_type = 'ratio'
          AND s.exchange     = 'HOSE'
          AND s.is_active    = 1
          AND json_extract(fr.data, '$.issueShare') > 0
          AND rp.ref_price > 0
        GROUP BY s.security_id
        HAVING fr.year = MAX(fr.year)
    """).fetchall()

    if not rows:
        logger.error('load_vin_weights: không có dữ liệu issueShare trong DB')
        return {}, 0.0

    total_cap = sum(float(r[1]) * float(r[2]) for r in rows)
    if total_cap <= 0:
        return {}, 0.0

    all_weights = {r[0]: float(r[1]) * float(r[2]) / total_cap for r in rows}
    vin_weights = {sym: all_weights.get(sym, 0.0) for sym in VIN_GROUP}
    W_VIN       = sum(vin_weights.values())

    logger.info(
        'VIN weights: ' +
        ', '.join(f'{s}={vin_weights[s]*100:.2f}%' for s in VIN_GROUP) +
        f' | W_VIN={W_VIN*100:.2f}%'
    )
    return vin_weights, W_VIN


# ── noVININDEX ──────────────────────────────────────────────────

def _get_session_open(conn, date_vn, index_code='VNINDEX'):
    """Lấy giá close của nến 1m đầu tiên trong ngày (09:15 = ATO close)."""
    row = conn.execute("""
        SELECT close FROM market_indices
        WHERE index_code=? AND interval='1m' AND date(trade_time)=?
        ORDER BY trade_time ASC LIMIT 1
    """, (index_code, date_vn)).fetchone()
    return row[0] if row else None


def _get_latest_close(conn, date_vn, index_code=None, symbol=None):
    """Lấy giá close 1m gần nhất trong ngày."""
    if index_code:
        row = conn.execute("""
            SELECT close FROM market_indices
            WHERE index_code=? AND interval='1m' AND date(trade_time)=?
            ORDER BY trade_time DESC LIMIT 1
        """, (index_code, date_vn)).fetchone()
    else:
        row = conn.execute("""
            SELECT sp.close FROM stock_prices sp
            JOIN securities s ON s.security_id = sp.security_id
            WHERE s.symbol=? AND sp.interval='1m' AND date(sp.trade_time)=?
            ORDER BY sp.trade_time DESC LIMIT 1
        """, (symbol, date_vn)).fetchone()
    return row[0] if row else None


def _get_stock_open(conn, date_vn, symbol):
    """Lấy giá close của nến 1m đầu tiên (09:15) của cổ phiếu."""
    row = conn.execute("""
        SELECT sp.close FROM stock_prices sp
        JOIN securities s ON s.security_id = sp.security_id
        WHERE s.symbol=? AND sp.interval='1m' AND date(sp.trade_time)=?
        ORDER BY sp.trade_time ASC LIMIT 1
    """, (symbol, date_vn)).fetchone()
    return row[0] if row else None


def compute_novin(conn, date_vn, vin_weights, W_VIN):
    """
    Tính toàn bộ chuỗi 1m noVININDEX cho ngày date_vn rồi upsert vào market_indices.

    Với mỗi nến 1m của VNINDEX:
      VIN_drag_t   = Σ_VIN[w_i × (p_i_t/p_i_open − 1)]
      noVIN_return = (VNINDEX_t/VNINDEX_open − 1 − VIN_drag_t) / (1 − W_VIN)
      noVININDEX_t = VNINDEX_open × (1 + noVIN_return)

    Nến cuối đang hình thành được tính lại mỗi cycle.
    Volume = 1 (constant) để VWAP trong bảng theo dõi tính được.
    """
    if not vin_weights or W_VIN <= 0 or W_VIN >= 1:
        return 0

    # Lấy toàn bộ nến 1m VNINDEX hôm nay
    vni_bars = conn.execute("""
        SELECT trade_time, close FROM market_indices
        WHERE index_code='VNINDEX' AND interval='1m' AND date(trade_time)=?
        ORDER BY trade_time ASC
    """, (date_vn,)).fetchall()

    if not vni_bars:
        return 0

    vni_open = vni_bars[0][1]  # close của nến đầu tiên 09:15

    # Lấy open price của từng VIN stock (nến 09:15)
    vin_opens = {}
    for sym in VIN_GROUP:
        p = _get_stock_open(conn, date_vn, sym)
        if p and p > 0:
            vin_opens[sym] = p

    if not vin_opens:
        logger.warning('compute_novin: không có giá open VIN stocks')
        return 0

    # Lấy close 1m của VIN stocks theo từng time slot
    # (Dùng 1m close gần nhất trước/bằng trade_time của VNINDEX bar)
    vin_closes = {}
    for sym in VIN_GROUP:
        rows = conn.execute("""
            SELECT sp.trade_time, sp.close FROM stock_prices sp
            JOIN securities s ON s.security_id = sp.security_id
            WHERE s.symbol=? AND sp.interval='1m' AND date(sp.trade_time)=?
            ORDER BY sp.trade_time ASC
        """, (sym, date_vn)).fetchall()
        vin_closes[sym] = {r[0]: r[1] for r in rows}

    # Tính noVININDEX cho từng nến VNINDEX
    novin_bars = []
    prev_novin = vni_open  # giá trị noVININDEX nến trước

    for i, (tt, vni_close) in enumerate(vni_bars):
        if not vni_close or vni_open <= 0:
            continue

        # VIN drag tại thời điểm tt
        drag = 0.0
        for sym in VIN_GROUP:
            p_open = vin_opens.get(sym)
            if not p_open:
                continue
            # Lấy close VIN gần nhất ≤ tt
            sym_bars = vin_closes.get(sym, {})
            p_last = None
            for t_bar in sorted(sym_bars.keys(), reverse=True):
                if t_bar <= tt:
                    p_last = sym_bars[t_bar]
                    break
            if p_last and p_last > 0:
                r_i = p_last / p_open - 1.0
                drag += vin_weights.get(sym, 0.0) * r_i

        vni_return  = vni_close / vni_open - 1.0
        novin_return = (vni_return - drag) / (1.0 - W_VIN)
        novin_close  = round(vni_open * (1.0 + novin_return), 2)

        # OHLC: dùng close của nến trước làm open nến hiện tại
        novin_open = prev_novin if i > 0 else novin_close
        novin_bars.append((
            tt,
            novin_open,                          # open
            max(novin_open, novin_close),        # high
            min(novin_open, novin_close),        # low
            novin_close,                         # close
        ))
        prev_novin = novin_close

    if not novin_bars:
        return 0

    conn.executemany("""
        INSERT INTO market_indices
            (index_code, interval, trade_time, open, high, low, close,
             volume, buy_vol, sell_vol, delta, is_ato)
        VALUES ('NOVIN', '1m', ?, ?, ?, ?, ?, 1, 0, 0, 0, 0)
        ON CONFLICT(index_code, interval, trade_time) DO UPDATE SET
            open=excluded.open, high=excluded.high,
            low=excluded.low,   close=excluded.close
    """, novin_bars)
    conn.commit()

    novin_last   = novin_bars[-1][4]
    novin_return_total = novin_last / vni_open - 1.0
    vni_return_total   = vni_bars[-1][1] / vni_open - 1.0
    logger.info(
        f'NOVIN: {len(novin_bars)} nến | '
        f'close={novin_last:.2f} ({novin_return_total:+.3%}) '
        f'vs VNINDEX={vni_bars[-1][1]:.2f} ({vni_return_total:+.3%}) '
        f'| VIN drag={novin_return_total - vni_return_total / (1-W_VIN) + novin_return_total:.3%}'
    )
    return len(novin_bars)


# ── VNINDEX/VN30 fetch + BVC ─────────────────────────────────────

def fetch_1m(symbol, date_vn):
    """Fetch toàn bộ nến 1m trong ngày từ DNSE chart API."""
    base    = datetime.strptime(date_vn, '%Y-%m-%d').replace(tzinfo=VN_TZ)
    from_ts = int(base.replace(hour=9, minute=0).timestamp())
    to_ts   = int(datetime.now(VN_TZ).timestamp()) + 300
    try:
        resp = requests.get(
            INDEX_API_URL,
            params={'symbol': symbol, 'resolution': '1',
                    'from': from_ts, 'to': to_ts},
            headers={'Origin': 'https://entrade.com.vn',
                     'Referer': 'https://entrade.com.vn/',
                     'User-Agent': 'Mozilla/5.0'},
            timeout=15,
        )
        resp.raise_for_status()
        d     = resp.json()
        t_arr = d.get('t', [])
        if not t_arr:
            return []
        records = []
        for i, ts in enumerate(t_arr):
            dt_vn = datetime.fromtimestamp(ts, tz=VN_TZ).replace(tzinfo=None)
            hm    = (dt_vn.hour, dt_vn.minute)
            if hm < (9, 0) or hm > (14, 45):
                continue
            records.append({
                'trade_time': dt_vn.strftime('%Y-%m-%dT%H:%M:%S'),
                'open':   float(d['o'][i]),
                'high':   float(d['h'][i]),
                'low':    float(d['l'][i]),
                'close':  float(d['c'][i]),
                'volume': int(d['v'][i]) if d.get('v') else 0,
            })
        return records
    except Exception as e:
        logger.warning(f'fetch_1m {symbol}: {e}')
        return []


def upsert_and_classify(conn, symbol, bars):
    """UPSERT bars vào market_indices rồi chạy BVC."""
    if not bars:
        return 0
    conn.executemany("""
        INSERT INTO market_indices
            (index_code, interval, trade_time,
             open, high, low, close, volume, buy_vol, sell_vol, delta, is_ato)
        VALUES (?, '1m', ?, ?, ?, ?, ?, ?, 0, 0, 0, 0)
        ON CONFLICT(index_code, interval, trade_time) DO UPDATE SET
            open=excluded.open, high=excluded.high,
            low=excluded.low,   close=excluded.close,
            volume=excluded.volume,
            buy_vol=0, sell_vol=0, delta=0
    """, [(symbol, b['trade_time'], b['open'], b['high'],
           b['low'], b['close'], b['volume']) for b in bars])

    rows = conn.execute("""
        SELECT rowid, open, high, low, close, volume, trade_time
        FROM market_indices
        WHERE index_code=? AND interval='1m'
          AND buy_vol=0 AND sell_vol=0 AND volume > 0
    """, (symbol,)).fetchall()

    updates = []
    for rowid, o, h, l, c, v, tt in rows:
        if None in (o, h, l, c):
            continue
        bv, sv = _bvc(float(o), float(h), float(l), float(c), int(v))
        is_ato = 1 if tt[11:16] in ('09:15', '09:16') else 0
        updates.append((bv, sv, bv - sv, is_ato, rowid))

    if updates:
        conn.executemany("""
            UPDATE market_indices SET buy_vol=?, sell_vol=?, delta=?, is_ato=?
            WHERE rowid=?
        """, updates)
    conn.commit()
    return len(bars)


# ── Main loop ───────────────────────────────────────────────────

def is_market_hours():
    t  = datetime.now(VN_TZ)
    hm = (t.hour, t.minute)
    return MARKET_OPEN <= hm <= MARKET_CLOSE


def run_once(conn, date_vn, vin_weights, W_VIN):
    # 1. VNINDEX + VN30: fetch + BVC
    for sym in SYMBOLS:
        bars = fetch_1m(sym, date_vn)
        n    = upsert_and_classify(conn, sym, bars)
        if n:
            row = conn.execute("""
                SELECT SUM(delta), SUM(buy_vol), SUM(sell_vol), COUNT(*)
                FROM market_indices
                WHERE index_code=? AND interval='1m' AND date(trade_time)=?
            """, (sym, date_vn)).fetchone()
            cum_d, cum_b, cum_s, nbar = row or (0, 0, 0, 0)
            sign = '+' if (cum_d or 0) >= 0 else ''
            logger.info(
                f'{sym}: {nbar} nến | Δ={sign}{cum_d:,} '
                f'| buy={cum_b:,} sell={cum_s:,}'
            )

    # 2. noVININDEX
    compute_novin(conn, date_vn, vin_weights, W_VIN)


def main():
    logger.info(
        f'Index BVC Worker started — OHLCV:{SYMBOLS} + noVIN:{VIN_GROUP} '
        f'interval={POLL_INTERVAL}s'
    )
    conn = sqlite3.connect(DB_PATH, detect_types=0, check_same_thread=False)

    # Load VIN weights một lần lúc khởi động, refresh mỗi ngày mới
    vin_weights, W_VIN = load_vin_weights(conn)
    current_date       = datetime.now(VN_TZ).strftime('%Y-%m-%d')

    try:
        while True:
            if is_market_hours():
                date_vn = datetime.now(VN_TZ).strftime('%Y-%m-%d')

                # Refresh weights khi sang ngày mới (ref_prices cập nhật overnight)
                if date_vn != current_date:
                    vin_weights, W_VIN = load_vin_weights(conn)
                    current_date       = date_vn

                try:
                    run_once(conn, date_vn, vin_weights, W_VIN)
                except Exception as e:
                    logger.error(f'run_once error: {e}', exc_info=True)

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info('Dừng worker.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
