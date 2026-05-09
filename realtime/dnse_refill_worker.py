#!/usr/bin/env python3
"""
realtime/dnse_refill_worker.py
════════════════════════════════════════════════════════
Tiến trình nền (worker) chạy tự động mỗi 5 phút để lấy dữ liệu
OHLCV (Open, High, Low, Close, Volume) 1 phút từ DNSE (Public API)
→ Sau đó tự động phân loại buy/sell bằng BVC (Bulk Volume Classification)
  cho các nến chưa có side data từ MASVN.

Hybrid Side Strategy:
  - Nến đã có MASVN native (buy_vol>0): giữ nguyên (˜95% chính xác)
  - Nến chưa có MASVN (buy_vol=0): BVC fill ngậy (˜87% chính xác)
  ⇒ Kết quả: 1544 mã đều có side data (thay vì chỉ 150 Tier-1 MASVN)

Cách chạy dưới nền:
  pm2 start realtime/dnse_refill_worker.py --name dnse_refiller --interpreter python3
"""

import os
import sys
import math
import time
import sqlite3
import requests
import asyncio
import logging
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from realtime.watchlist_db import load_watchlist

# ── Config ──────────────────────────────────────────────────────
WATCHLIST_LIST_NAME  = os.getenv("WATCHLIST_NAME", "vip")
REFILL_INTERVAL      = 180   # seconds = 3 phút (watchlist VIP)
MARKET_INTERVAL      = 300   # seconds = 5 phút (toàn thị trường)
VN_TZ                = timezone(timedelta(hours=7))
DB_PATH              = os.getenv("SMD_DB_PATH", os.path.join(PROJECT_ROOT, "data", "securities_master.db"))
DNSE_URL             = "https://services.entrade.com.vn/chart-api/v2/ohlcs/stock"

# BVC Config
BVC_ENABLED         = True   # Tắt nếu muốn chạy thuần OHLCV-only
_SQRT_2LN2          = math.sqrt(2.0 * math.log(2.0))  # ≈ 1.1774

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [DNSERefill] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(PROJECT_ROOT, "dnse_refill.log"))
    ]
)
logger = logging.getLogger(__name__)


def fetch_dnse_1m(symbol: str, t_from: int, t_to: int) -> list:
    """Lấy dữ liệu OHLCV 1 phút từ DNSE. Trả về list of dicts."""
    try:
        r = requests.get(DNSE_URL, params={
            "from": t_from,
            "to": t_to,
            "symbol": symbol,
            "resolution": "1"
        }, timeout=10)
        if r.ok:
            d = r.json()
            ts_list = d.get("t") or []
            o_list  = d.get("o") or []
            h_list  = d.get("h") or []
            l_list  = d.get("l") or []
            c_list  = d.get("c") or []
            v_list  = d.get("v") or []
            
            records = []
            for i in range(len(ts_list)):
                if v_list[i]:  # Chỉ lấy nến có volume
                    records.append({
                        "ts": ts_list[i],
                        "o": o_list[i],
                        "h": h_list[i],
                        "l": l_list[i],
                        "c": c_list[i],
                        "v": int(v_list[i])
                    })
            return records
    except Exception as e:
        logger.error(f"Lỗi lấy dữ liệu DNSE cho {symbol}: {e}")
    return []


# ════════════════════════════════════════════════════════
# BVC CORE (inline, không phụ thuộc scipy)
# ════════════════════════════════════════════════════════

def _norm_cdf(z: float) -> float:
    """Approximation of Φ(z) = P(X ≤ z) for X ~ N(0,1)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _bvc(open_: float, high: float, low: float,
         close: float, volume: int) -> tuple[int, int]:
    """
    Bulk Volume Classification (Easley, López de Prado, O'Hara 2016).
    ← được gọi với mỗi 1m bar sau khi DNSE write, khi buy_vol = 0.

    Parkinson sigma: σ = (H - L) / (2√ln2)
    Z-score        : z = (ΔP) / σ
    P(buy)         : Φ(z)

    Trường hợp đặc biệt — ATC / Doji (H ≈ L):
      Khi range gần bằng 0, không có thông tin directional.
      Gán 50/50 (delta=0) thay vì bias về buy, vì:
        - Nến ATC (14:45): khớp lệnh định kỳ, không có aggressor
        - Doji trong phiên: 2 bên cân bằng, delta_p=0 không cho thêm info
    """
    if volume <= 0:
        return 0, 0
    rng = high - low
    if rng < 1e-6:
        # Không có range → không có directional signal → 50/50 neutral
        buy_vol = volume // 2
        return buy_vol, volume - buy_vol
    delta_p = close - open_
    sigma = rng / _SQRT_2LN2
    z = max(-4.0, min(4.0, delta_p / sigma))
    p_buy    = _norm_cdf(z)
    buy_vol  = int(round(volume * p_buy))
    sell_vol = volume - buy_vol
    return max(0, buy_vol), max(0, sell_vol)



def upsert_to_db(conn: sqlite3.Connection, sec_id: int, records: list) -> int:
    """
    Upsert dữ liệu OHLCV vào stock_prices.

    Hybrid Side Strategy:
      1. Upsert OHLCV (không đụng buy_vol/sell_vol nếu đã có MASVN data)
      2. Với mỗi bar mới insert: nếu buy_vol vẫn = 0 sau upsert
         → áp BVC để ước tính buy/sell ngay lập tức.
    """
    if not records:
        return 0

    # Bước 1: Upsert OHLCV, không đụng side data đã có
    upsert_sql = """
        INSERT INTO stock_prices (security_id, interval, trade_time, open, high, low, close, volume)
        VALUES (?, '1m', ?, ?, ?, ?, ?, ?)
        ON CONFLICT (security_id, interval, trade_time) DO UPDATE SET
            open   = excluded.open,
            high   = excluded.high,
            low    = excluded.low,
            close  = excluded.close,
            volume = excluded.volume
    """
    batch = []
    for r in records:
        dt_vn = datetime.fromtimestamp(r["ts"], tz=VN_TZ)
        time_str = dt_vn.strftime("%Y-%m-%dT%H:%M:00")
        batch.append((sec_id, time_str, r["o"], r["h"], r["l"], r["c"], r["v"]))

    try:
        conn.executemany(upsert_sql, batch)
        conn.commit()
    except Exception as e:
        logger.error(f"Lỗi upsert OHLCV: {e}")
        conn.rollback()
        return 0

    if not BVC_ENABLED:
        return len(batch)

    # Bước 2: BVC fill cho các bar chưa có side data
    # Chỉ UPDATE những bar có buy_vol IS NULL hoặc = 0 → giữ nguyên MASVN data
    time_strs = [b[1] for b in batch]
    placeholders = ",".join("?" * len(time_strs))
    need_bvc = conn.execute(
        f"""
        SELECT rowid, open, high, low, close, volume
        FROM stock_prices
        WHERE security_id = ?
          AND interval = '1m'
          AND trade_time IN ({placeholders})
          AND (
               ((buy_vol IS NULL OR buy_vol = 0) AND (sell_vol IS NULL OR sell_vol = 0))
               OR (COALESCE(buy_vol, 0) + COALESCE(sell_vol, 0) > volume * 3)
          )
          AND volume > 0
        """,
        [sec_id] + time_strs,
    ).fetchall()

    if not need_bvc:
        return len(batch)

    bvc_updates = []
    for rowid, o, h, l, c, v in need_bvc:
        if None in (o, h, l, c) or v <= 0:
            continue
        buy_vol, sell_vol = _bvc(float(o), float(h), float(l), float(c), int(v))
        delta = buy_vol - sell_vol
        bvc_updates.append((buy_vol, sell_vol, delta, rowid))

    if bvc_updates:
        conn.executemany(
            "UPDATE stock_prices SET buy_vol=?, sell_vol=?, delta=? WHERE rowid=?",
            bvc_updates,
        )
        conn.commit()
        logger.debug(
            f"  BVC fill: {len(bvc_updates)} bars "
            f"(sec_id={sec_id}, từ {time_strs[0]} đến {time_strs[-1]})"
        )

    return len(batch)



async def refill_loop():
    logger.info("═" * 60)
    logger.info(f"🎯 Bắt đầu tiến trình DNSE OHLCV Refiller (watchlist={WATCHLIST_LIST_NAME}, {REFILL_INTERVAL//60} phút/lần)")
    logger.info(f"DB Path : {DB_PATH}")
    logger.info(f"Target  : Watchlist '{WATCHLIST_LIST_NAME}'")
    logger.info("═" * 60)

    while True:
        try:
            now_vn = datetime.now(VN_TZ)
            # Chỉ chạy trong giờ hoặc gần phiên (từ 08:50 đến 15:30)
            # Nhưng để an toàn cứ để chạy cả ngày hoặc filter tùy ý. 
            # Cứ chạy liên tục cũng không sao vì API miễn phí và query nhẹ.
            
            # Lấy data của ngày hôm nay
            date_vn = now_vn.strftime("%Y-%m-%d")
            utc_open = datetime.strptime(date_vn + " 02:00:00", "%Y-%m-%d %H:%M:%S")
            t_from = int(utc_open.replace(tzinfo=timezone.utc).timestamp())
            t_to   = int(now_vn.timestamp())
            
            conn = sqlite3.connect(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            
            watchlist = load_watchlist(list_name=WATCHLIST_LIST_NAME, db_path=DB_PATH)
            if not watchlist:
                logger.warning(f"Watchlist '{WATCHLIST_LIST_NAME}' trống. Chờ chu kỳ sau.")
                conn.close()
                await asyncio.sleep(REFILL_INTERVAL)
                continue

            # Lấy ID chứng khoán
            sec_map = {}
            rows = conn.execute("SELECT symbol, security_id FROM securities").fetchall()
            for r in rows:
                sec_map[r[0]] = r[1]
                
            total_upserted = 0
            for sym in watchlist:
                sec_id = sec_map.get(sym)
                if not sec_id:
                    continue
                    
                records = fetch_dnse_1m(sym, t_from, t_to)
                if records:
                    updated = upsert_to_db(conn, sec_id, records)
                    total_upserted += updated
                await asyncio.sleep(0.2)  # Rate limit nhẹ

            conn.close()
            logger.info(f"✅ Hoàn tất chu kỳ refill. Đã upsert/check {total_upserted} nến cho {len(watchlist)} mã.")
            
        except Exception as e:
            logger.error(f"Lỗi trong chu kỳ refill: {e}")
            
        await asyncio.sleep(REFILL_INTERVAL)


async def refill_market_loop():
    logger.info("═" * 60)
    logger.info(f"🌍 Bắt đầu tiến trình DNSE Market Refiller ({MARKET_INTERVAL//60} phút/lần, giờ giao dịch)")
    logger.info("═" * 60)

    while True:
        try:
            now_vn = datetime.now(VN_TZ)

            # Chỉ chạy trong giờ giao dịch 09:00–15:30 VN
            if not (9 <= now_vn.hour < 16):
                logger.debug(f"Market loop: ngoài giờ giao dịch ({now_vn.strftime('%H:%M')}), bỏ qua")
                await asyncio.sleep(3600)
                continue

            date_vn = now_vn.strftime("%Y-%m-%d")
            utc_open = datetime.strptime(date_vn + " 02:00:00", "%Y-%m-%d %H:%M:%S")
            t_from = int(utc_open.replace(tzinfo=timezone.utc).timestamp())
            t_to   = int(now_vn.timestamp())

            conn = sqlite3.connect(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")

            # Fix: tất cả mã trong DB đều có exchange='UNKNOWN'
            # Dùng asset_type='EQUITY' AND is_active=1 thay thế
            rows = conn.execute(
                "SELECT symbol, security_id FROM securities "
                "WHERE asset_type='EQUITY' AND is_active=1"
            ).fetchall()
            sec_map = {r[0]: r[1] for r in rows}

            logger.info(f"Refill toàn thị trường: {len(sec_map)} mã EQUITY...")
            total_upserted = 0
            bvc_filled = 0

            for sym, sec_id in sec_map.items():
                records = fetch_dnse_1m(sym, t_from, t_to)
                if records:
                    updated = upsert_to_db(conn, sec_id, records)
                    total_upserted += updated
                await asyncio.sleep(0.1)   # Rate limit: 10 req/s an toàn

            conn.close()
            logger.info(
                f"✅ Hoàn tất refill thị trường: {total_upserted} nến upserted "
                f"(BVC fill inline cho bars buy_vol=0)"
            )

        except Exception as e:
            logger.error(f"Lỗi trong chu kỳ refill thị trường: {e}")

        await asyncio.sleep(MARKET_INTERVAL)  # 5 phút


async def main():
    await asyncio.gather(
        refill_loop(),
        refill_market_loop()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Dừng Refiller.")
