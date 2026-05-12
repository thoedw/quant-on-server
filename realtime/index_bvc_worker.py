#!/usr/bin/env python3
"""
realtime/index_bvc_worker.py
════════════════════════════════════════════════════════
Worker cập nhật OHLCV 1m + BVC cho VNIndex/VN30 mỗi 60 giây.

Pipeline mỗi cycle:
  1. Gọi DNSE chart-api → lấy toàn bộ nến 1m từ 09:00 VN đến now
  2. UPSERT vào market_indices (reset buy_vol/sell_vol=0 để BVC re-classify)
  3. Chạy BVC (Easley-López de Prado-O'Hara) → fill buy_vol/sell_vol/delta

Kết quả: cột Delta/Cov% của VNINDEX, VN30 trong bảng theo dõi cập nhật realtime.

Cách chạy:
  pm2 start realtime/index_bvc_worker.py --name index_bvc --interpreter python3
"""

import os
import sys
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
POLL_INTERVAL = 60          # giây giữa mỗi cycle
MARKET_OPEN   = (9,  0)     # Bắt đầu fetch từ 09:00
MARKET_CLOSE  = (15, 5)     # Thêm 5 phút sau đóng cửa để lấy nến cuối

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [IndexBVC] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


# ── BVC (Easley-López de Prado-O'Hara) ─────────────────────────

def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

_SQRT_2LN2 = math.sqrt(2.0 * math.log(2.0))


def _bvc(o: float, h: float, l: float, c: float, v: int):
    """
    Phân loại volume nến 1m thành buy_vol / sell_vol theo BVC.

    Trường hợp H≈L (ATO 9:15, doji, ATC):
      |Δclose| ≈ 0  → 50/50 (khớp lệnh mở, không rõ chiều)
      Δclose > 0    → 100% BUY
      Δclose < 0    → 100% SELL
    Trường hợp thông thường:
      z  = Δclose / (range / √(2·ln2))   ← chuẩn hóa Parkinson
      bv = round(volume × Φ(z))          ← Φ = CDF chuẩn tắc
    """
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


# ── Data fetch ──────────────────────────────────────────────────

def fetch_1m(symbol: str, date_vn: str) -> list:
    """Fetch toàn bộ nến 1m trong ngày từ DNSE chart API."""
    base    = datetime.strptime(date_vn, '%Y-%m-%d').replace(tzinfo=VN_TZ)
    from_ts = int(base.replace(hour=9, minute=0).timestamp())
    to_ts   = int(datetime.now(VN_TZ).timestamp()) + 300
    try:
        resp = requests.get(
            INDEX_API_URL,
            params={
                'symbol':     symbol,
                'resolution': '1',
                'from':       from_ts,
                'to':         to_ts,
            },
            headers={
                'Origin':     'https://entrade.com.vn',
                'Referer':    'https://entrade.com.vn/',
                'User-Agent': 'Mozilla/5.0',
            },
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
            # Lọc pre-market và post-ATC
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


# ── DB write ────────────────────────────────────────────────────

def upsert_and_classify(conn: sqlite3.Connection, symbol: str, bars: list) -> int:
    """
    UPSERT bars vào market_indices rồi chạy BVC.

    Reset buy_vol/sell_vol=0 khi upsert để BVC luôn re-classify,
    kể cả nến cuối đang hình thành (close thay đổi mỗi phút).
    """
    if not bars:
        return 0

    conn.executemany("""
        INSERT INTO market_indices
            (index_code, interval, trade_time,
             open, high, low, close, volume,
             buy_vol, sell_vol, delta, is_ato)
        VALUES (?, '1m', ?, ?, ?, ?, ?, ?, 0, 0, 0, 0)
        ON CONFLICT(index_code, interval, trade_time) DO UPDATE SET
            open=excluded.open, high=excluded.high,
            low=excluded.low,   close=excluded.close,
            volume=excluded.volume,
            buy_vol=0, sell_vol=0, delta=0
    """, [
        (symbol, b['trade_time'], b['open'], b['high'],
         b['low'], b['close'], b['volume'])
        for b in bars
    ])

    # BVC classify tất cả bars vừa được reset
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
            UPDATE market_indices
            SET buy_vol=?, sell_vol=?, delta=?, is_ato=?
            WHERE rowid=?
        """, updates)

    conn.commit()
    return len(bars)


# ── Main loop ───────────────────────────────────────────────────

def is_market_hours() -> bool:
    t = datetime.now(VN_TZ)
    hm = (t.hour, t.minute)
    return MARKET_OPEN <= hm <= MARKET_CLOSE


def run_once(conn: sqlite3.Connection, date_vn: str) -> None:
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


def main():
    logger.info(f'Index BVC Worker started — symbols={SYMBOLS} interval={POLL_INTERVAL}s')
    conn = sqlite3.connect(DB_PATH, detect_types=0, check_same_thread=False)
    try:
        while True:
            if is_market_hours():
                date_vn = datetime.now(VN_TZ).strftime('%Y-%m-%d')
                try:
                    run_once(conn, date_vn)
                except Exception as e:
                    logger.error(f'run_once error: {e}', exc_info=True)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info('Dừng worker.')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
