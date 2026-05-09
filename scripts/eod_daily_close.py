#!/usr/bin/env python3
"""
scripts/eod_daily_close.py
EOD Daily Close — Đồng bộ OHLCV cuối ngày từ DNSE cho 7 Timeframes, đối soát chất lượng Ticks Intraday.
Chạy: 15:45 ICT (T2-T6) via cron.

Quy tắc cốt lõi:
  - DNSE là nguồn sự thật (Open/High/Low/Close/Volume) → GHI ĐÈ stock_prices.
  - buy_vol, sell_vol, delta từ Intraday Engine → TUYỆT ĐỐI KHÔNG ĐẮP LÊN.
  - UPCOM vol_ok_threshold = 90% (thanh khoản thưa, mỗi tick quan trọng hơn).
  - HOSE/HNX vol_ok_threshold = 80%.
"""

import os
import sys
import sqlite3
import asyncio
import aiohttp
import logging
import argparse
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from securities_master.database import DatabaseManager
from securities_master.extractors.async_dnse_extractor import AsyncDNSEExtractor
from securities_master.loaders.sqlite_loader import SQLiteLoader
from scripts.ptvol_imputer import run_impute as impute_pt_vol
from scripts.vwap_qc import run_qc as run_vwap_qc
from scripts.bvc_imputer import run_bvc_imputer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ============================================================
# CẤU HÌNH (CONFIG)
# ============================================================
INTERVALS      = ['1m', '5m', '15m', '30m', '1H', '1D', '1W']
CONCURRENCY    = 120          # Semaphore: requests song song tối đa
TIMEOUT_SEC    = 20           # Timeout mỗi request

OHLC_DIFF_PCT  = 5.0          # % lệch OHLC tối đa được coi là OK
VOL_OK_HOSE    = 80.0         # % Volume bắt tối thiểu (HOSE / HNX)
VOL_OK_UPCOM   = 90.0         # % Volume bắt tối thiểu (UPCOM, thưa hơn nên ngưỡng cao hơn)

# Redis — đọc buy_vol 1D từ Engine cache
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# ============================================================
# MARKET INDICES — DDL
# ============================================================
MARKET_INDICES = ['VNINDEX', 'VN30', 'VN100', 'HNX30']
INDEX_API_URL  = 'https://services.entrade.com.vn/chart-api/v2/ohlcs/index'

DDL_MARKET_INDICES = """
CREATE TABLE IF NOT EXISTS market_indices (
    index_code  TEXT    NOT NULL,
    interval    TEXT    NOT NULL,
    trade_time  TEXT    NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      INTEGER DEFAULT 0,
    buy_vol     INTEGER DEFAULT 0,
    sell_vol    INTEGER DEFAULT 0,
    delta       INTEGER DEFAULT 0,
    is_ato      INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (index_code, interval, trade_time)
);
CREATE INDEX IF NOT EXISTS idx_mi_code_interval ON market_indices(index_code, interval);
CREATE INDEX IF NOT EXISTS idx_mi_time          ON market_indices(trade_time);
"""

DDL_INDEX_VWAP = """
CREATE TABLE IF NOT EXISTS index_vwap_summary (
    index_code   TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    vwap         REAL,
    vwap_std     REAL,
    vwap_upper1  REAL,
    vwap_lower1  REAL,
    vwap_upper2  REAL,
    vwap_lower2  REAL,
    cum_volume   INTEGER DEFAULT 0,
    cum_delta    INTEGER DEFAULT 0,
    buy_vol      INTEGER DEFAULT 0,
    sell_vol     INTEGER DEFAULT 0,
    session_open  REAL,
    session_close REAL,
    PRIMARY KEY (index_code, trade_date)
);
"""

# SQL Schema cho bảng Quality Log
DDL_QUALITY_LOG = """
CREATE TABLE IF NOT EXISTS price_quality_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    interval        TEXT    NOT NULL,
    trade_time      TEXT    NOT NULL,
    eng_open        REAL    DEFAULT 0,
    eng_high        REAL    DEFAULT 0,
    eng_low         REAL    DEFAULT 0,
    eng_close       REAL    DEFAULT 0,
    eng_vol         INTEGER DEFAULT 0,
    eng_buy_vol     INTEGER DEFAULT 0,
    eng_sell_vol    INTEGER DEFAULT 0,
    eng_delta       INTEGER DEFAULT 0,
    eod_open        REAL    DEFAULT 0,
    eod_high        REAL    DEFAULT 0,
    eod_low         REAL    DEFAULT 0,
    eod_close       REAL    DEFAULT 0,
    eod_vol         INTEGER DEFAULT 0,
    vol_capture_pct  REAL,   -- eng_vol / eod_vol * 100 (intra vs EOD)
    side_coverage_pct REAL,  -- (buy_vol + sell_vol) / eng_vol * 100 (classified vs total)
    ohlc_max_diff   REAL,
    gap_reason      TEXT,
    status          TEXT    NOT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_date, symbol, interval, trade_time)
);
CREATE INDEX IF NOT EXISTS idx_pql_date_status ON price_quality_log(run_date, status);
CREATE INDEX IF NOT EXISTS idx_pql_symbol ON price_quality_log(symbol, run_date);
"""

DDL_QUALITY_VIEW = """
CREATE VIEW IF NOT EXISTS v_engine_quality AS
SELECT
    run_date, interval, gap_reason,
    COUNT(*)                                              AS total_candles,
    SUM(CASE WHEN status='OK'         THEN 1 ELSE 0 END) AS ok_count,
    SUM(CASE WHEN status='MISSING'    THEN 1 ELSE 0 END) AS missing_count,
    SUM(CASE WHEN status='LOW_VOL'    THEN 1 ELSE 0 END) AS low_vol_count,
    SUM(CASE WHEN status='OHLC_DIFF'  THEN 1 ELSE 0 END) AS ohlc_diff_count,
    -- Tầng 1: eng_vol vs eod_vol (intra capture bao nhiêu % real volume)
    ROUND(AVG(CASE WHEN vol_capture_pct IS NOT NULL
                   THEN vol_capture_pct END), 1)         AS avg_vol_capture_pct,
    -- Tầng 2: (buy+sell) vs eng_vol (bao nhiêu % đã được phân loại side)
    ROUND(AVG(CASE WHEN side_coverage_pct IS NOT NULL
                   THEN side_coverage_pct END), 1)       AS avg_side_coverage_pct,
    COUNT(DISTINCT symbol)                               AS affected_symbols
FROM price_quality_log
GROUP BY run_date, interval, gap_reason
ORDER BY run_date DESC, missing_count DESC;
"""

# ============================================================
# DATABASE HELPERS
# ============================================================
def ensure_quality_schema(conn: sqlite3.Connection):
    for stmt in DDL_QUALITY_LOG.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)

    # Migration: thêm cột side_coverage_pct nếu DB cũ chưa có
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(price_quality_log)")}
    if 'side_coverage_pct' not in existing_cols:
        conn.execute("ALTER TABLE price_quality_log ADD COLUMN side_coverage_pct REAL")
        logger.info("🔧 Migration: đã thêm cột side_coverage_pct vào price_quality_log")

    # Drop và recreate view (schema view thay đổi)
    conn.execute("DROP VIEW IF EXISTS v_engine_quality")
    try:
        conn.execute(DDL_QUALITY_VIEW.strip())
    except Exception as e:
        logger.warning(f"⚠️ Không thể tạo view v_engine_quality: {e}")

    conn.commit()

def get_symbols(conn: sqlite3.Connection, filter_symbols=None) -> list:
    """Trả về [(symbol, security_id, exchange)] cho các mã EQUITY."""
    if filter_symbols:
        placeholders = ",".join("?" * len(filter_symbols))
        rows = conn.execute(f"""
            SELECT symbol, security_id, exchange FROM securities
            WHERE asset_type='EQUITY' AND symbol IN ({placeholders})
            ORDER BY symbol
        """, filter_symbols).fetchall()
    else:
        rows = conn.execute("""
            SELECT symbol, security_id, exchange FROM securities
            WHERE asset_type='EQUITY' ORDER BY symbol
        """).fetchall()
    return rows

def snapshot_engine_data(conn: sqlite3.Connection, today_str: str) -> dict:
    """
    Chụp lại toàn bộ nến hôm nay của Intraday Engine từ stock_prices.
    Key: (security_id, interval, trade_time_str)
    """
    rows = conn.execute("""
        SELECT security_id, interval, trade_time,
               open, high, low, close, volume,
               buy_vol, sell_vol, delta
        FROM stock_prices
        WHERE date(trade_time) = ?
    """, (today_str,)).fetchall()

    snap = {}
    for r in rows:
        # Normalize timestamp: đảm bảo key dùng 'T' separator (isoformat) nhất quán
        tt_normalized = str(r[2]).replace(' ', 'T')  # '2026-04-17 09:00:00' → '2026-04-17T09:00:00'
        key = (r[0], r[1], tt_normalized)
        snap[key] = {
            'open': r[3], 'high': r[4], 'low': r[5], 'close': r[6],
            'volume': r[7], 'buy_vol': r[8], 'sell_vol': r[9], 'delta': r[10]
        }
    logger.info(f"📸 Engine Snapshot: {len(snap)} nến hôm nay tìm thấy trong DB.")
    return snap

def upsert_dnse_records(conn: sqlite3.Connection, records: list, dry_run: bool):
    """
    Ghi đè OHLCV từ DNSE vào stock_prices.
    KHÔNG được chạm vào buy_vol, sell_vol, delta.
    """
    if dry_run:
        return
    conn.executemany("""
        INSERT INTO stock_prices
            (security_id, interval, trade_time, open, high, low, close, volume,
             buy_vol, sell_vol, delta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0)
        ON CONFLICT(security_id, interval, trade_time) DO UPDATE SET
            open   = excluded.open,
            high   = excluded.high,
            low    = excluded.low,
            close  = excluded.close,
            volume = excluded.volume
        -- buy_vol, sell_vol, delta: KHÔNG thay đổi (giữ nguyên Engine data)
    """, [
        (r.security_id, r.interval,
         # Normalize sang ISO 8601 T-format để khớp UNIQUE key của engine
         # (Python sqlite3 adapter tự convert datetime → space-format nếu không gọi isoformat)
         r.trade_time.isoformat() if hasattr(r.trade_time, 'isoformat') else str(r.trade_time).replace(' ', 'T'),
         r.open, r.high, r.low, r.close, r.volume)
        for r in records
    ])
    conn.commit()


def restore_1d_buysell_from_redis(
    conn: sqlite3.Connection,
    today_str: str,
    sec_map: dict,
    dry_run: bool,
) -> int:
    """
    Khôi phục buy_vol/sell_vol/delta cho nến 1D hôm nay từ Redis.

    Vấn đề: Engine chỉ flush 1D candle vào DB khi kỳ mới bắt đầu
    (sáng hôm sau). Khi eod_daily_close chạy lúc 15:45, nến 1D chưa
    có trong DB → DNSE upsert tạo row mới với buy_vol=0.

    Giải pháp: Engine sync 1D vào Redis mỗi 10 giây qua key:
      intra:open_candle:{SYMBOL}:1D
    Function này đọc Redis SAU khi DNSE upsert và UPDATE lại buy_vol.
    """
    try:
        import redis as redis_lib
        r = redis_lib.Redis(
            host=REDIS_HOST, port=REDIS_PORT,
            decode_responses=True, socket_connect_timeout=2
        )
        r.ping()
    except Exception as e:
        logger.warning(f"⚠️  Redis không khả dụng — bỏ qua restore buy_vol 1D: {e}")
        return 0

    keys = r.keys("intra:open_candle:*:1D")
    if not keys:
        logger.warning("⚠️  Redis: Không tìm thấy open_candle 1D nào")
        return 0

    updates = []
    stale   = 0
    for key in keys:
        # key = "intra:open_candle:{SYMBOL}:1D"
        parts = key.split(":")
        if len(parts) < 4:
            continue
        symbol = parts[2]
        sec_id = sec_map.get(symbol)
        if not sec_id:
            continue

        data = r.hgetall(key)
        if not data:
            continue

        # Chỉ restore nếu period_start là hôm nay
        period_start = data.get("period_start_str", "")
        if not period_start.startswith(today_str):
            stale += 1
            continue

        buy_vol  = int(data.get("buy_vol",  0) or 0)
        sell_vol = int(data.get("sell_vol", 0) or 0)
        delta    = int(data.get("delta",    0) or 0)

        if buy_vol == 0 and sell_vol == 0:
            continue  # Không có gì để restore

        updates.append((buy_vol, sell_vol, delta, sec_id, today_str))

    logger.info(
        f"📊 Redis 1D restore: {len(updates)} mã có buy/sell vol "
        f"({stale} stale bỏ qua, dry_run={dry_run})"
    )

    if not updates or dry_run:
        return len(updates)

    conn.executemany("""
        UPDATE stock_prices
        SET    buy_vol  = ?,
               sell_vol = ?,
               delta    = ?
        WHERE  security_id = ?
          AND  interval    = '1D'
          AND  date(trade_time) = ?
    """, updates)
    conn.commit()
    logger.info(f"✅ Đã khôi phục buy_vol 1D cho {len(updates)} mã từ Redis")
    return len(updates)

# ============================================================
# FIX #1: IMPUTE BUY_VOL / SELL_VOL CHO TF ≥ 5m TỪ DỮ LIỆU 1m
# ============================================================

TF_MINUTES = {'5m': 5, '15m': 15, '30m': 30, '1H': 60}

def impute_buyvol_from_1m(conn: sqlite3.Connection, today_str: str, dry_run: bool) -> int:
    """
    Sau khi DNSE upsert chạy xong, các nến TF ≥ 5m/1D đều có buy_vol=0
    vì Engine chưa flush open candles vào DB lúc EOD chạy (15:45).

    Fix: Aggregate buy_vol/sell_vol từ nến 1m lên từng TF.
      period_start(5m)  = floor(trade_time, 5m)
      period_start(1D)  = date + ' 09:00:00'

    Chỉ UPDATE rows đã tồn tại (do DNSE tạo) — không INSERT thêm.
    Trả về số rows được update.
    """
    # Kéo 1m data có buy/sell
    rows_1m = conn.execute("""
        SELECT security_id, trade_time,
               COALESCE(buy_vol,  0) AS buy_vol,
               COALESCE(sell_vol, 0) AS sell_vol
        FROM stock_prices
        WHERE interval='1m' AND date(trade_time)=?
          AND (buy_vol > 0 OR sell_vol > 0)
    """, (today_str,)).fetchall()

    if not rows_1m:
        logger.warning("impute_buyvol: Không có dữ liệu 1m có buy/sell — bỏ qua")
        return 0

    # Build aggregation: (security_id, tf, period_start) → [buy, sell]
    from collections import defaultdict
    agg: dict = defaultdict(lambda: [0, 0])

    for r in rows_1m:
        sid = r[0]
        tt  = str(r[1]).replace('T', ' ')
        bv  = r[2]; sv = r[3]
        try:
            dt = datetime.strptime(tt[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

        # TF phút
        for tf, mins in TF_MINUTES.items():
            floored_min = (dt.minute // mins) * mins
            ps = dt.replace(minute=floored_min, second=0).strftime("%Y-%m-%dT%H:%M:%S")  # T-format
            agg[(sid, tf, ps)][0] += bv
            agg[(sid, tf, ps)][1] += sv

        # 1D: period_start = 09:00:00 ngày đó
        ps_1d = dt.strftime("%Y-%m-%d") + "T09:00:00"  # T-format
        agg[(sid, '1D', ps_1d)][0] += bv
        agg[(sid, '1D', ps_1d)][1] += sv

    if dry_run:
        logger.info(f"[dry-run] impute_buyvol: {len(agg)} aggregation keys (không ghi DB)")
        return len(agg)

    # UPDATE theo từng TF — chỉ rows đã tồn tại
    updated_total = 0
    updates_by_tf: dict = defaultdict(list)
    for (sid, tf, ps), (bv, sv) in agg.items():
        if bv == 0 and sv == 0:
            continue
        updates_by_tf[tf].append((bv, sv, bv - sv, sid, ps))

    for tf, batch in updates_by_tf.items():
        cur = conn.executemany("""
            UPDATE stock_prices
            SET buy_vol  = ?,
                sell_vol = ?,
                delta    = ?
            WHERE security_id = ?
              AND interval    = ?
              AND trade_time  = ?
        """, [(b[0], b[1], b[2], b[3], tf, b[4]) for b in batch])
        updated_total += cur.rowcount
        logger.info(f"  impute [{tf}] updated {cur.rowcount:,} / {len(batch):,} rows")

    conn.commit()
    logger.info(f"✅ impute_buyvol completed: {updated_total:,} rows updated")
    return updated_total


# ============================================================
# FIX #2: TÍNH DAILY_VWAP_SUMMARY SAU KHI EOD UPSERT
# ============================================================

def compute_daily_vwap_summary(conn: sqlite3.Connection, today_str: str, dry_run: bool) -> int:
    """
    Tính lại daily_vwap_summary từ nến 1m (sau khi impute buy_vol xong).
    Gọi cuối EOD để cập nhật anchored VWAP ngày hôm nay vào bảng tổng kết.
    Trả về số rows upserted.
    """
    import math
    from collections import defaultdict

    rows = conn.execute("""
        SELECT security_id, trade_time, open, close, volume,
               COALESCE(buy_vol,  0) AS buy_vol,
               COALESCE(sell_vol, 0) AS sell_vol
        FROM stock_prices
        WHERE interval='1m' AND date(trade_time)=?
          AND volume > 0
        ORDER BY security_id, trade_time
    """, (today_str,)).fetchall()

    if not rows:
        logger.warning("compute_daily_vwap_summary: Không có dữ liệu 1m")
        return 0

    grouped: dict = defaultdict(list)
    for r in rows:
        grouped[r[0]].append(r)

    inserts = []
    for sid, candles in grouped.items():
        if len(candles) < 3:
            continue
        cum_pv = cum_v = cum_pv2 = 0.0
        cum_buy = cum_sell = 0
        first, last = candles[0], candles[-1]
        for c in candles:
            p = c[3] or 0.0
            v = c[4] or 0
            cum_pv  += p * v
            cum_v   += v
            cum_pv2 += p * p * v
            cum_buy  += c[5] or 0
            cum_sell += c[6] or 0
        if cum_v == 0:
            continue
        vwap = cum_pv / cum_v
        var  = max(0.0, (cum_pv2 / cum_v) - vwap ** 2)
        std  = math.sqrt(var)
        side_vol = cum_buy + cum_sell
        side_cov = round(side_vol * 100.0 / max(int(cum_v), 1), 1)
        inserts.append((
            sid, today_str,
            round(vwap, 4), round(std, 4),
            round(vwap + std,   4), round(vwap - std,   4),
            round(vwap + 2*std, 4), round(vwap - 2*std, 4),
            int(cum_v), cum_buy - cum_sell,
            cum_buy, cum_sell, side_cov,
            first[2],   # session_open (open của nến 1m đầu tiên)
            last[3],    # session_close (close của nến 1m cuối cùng)
        ))

    if dry_run:
        logger.info(f"[dry-run] compute_daily_vwap_summary: {len(inserts)} rows (không ghi DB)")
        return len(inserts)

    conn.executemany("""
        INSERT OR REPLACE INTO daily_vwap_summary
            (security_id, trade_date, vwap, vwap_std,
             vwap_upper1, vwap_lower1, vwap_upper2, vwap_lower2,
             cum_volume, cum_delta, buy_vol, sell_vol, side_cov_pct,
             session_open, session_close)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, inserts)
    conn.commit()
    logger.info(f"✅ compute_daily_vwap_summary: upserted {len(inserts)} rows")
    return len(inserts)


def compute_ohlc_diff(eng: dict, eod) -> float:
    """Tính % lệch OHLC lớn nhất giữa engine vs DNSE."""
    diffs = []
    for field in ['open', 'high', 'low', 'close']:
        e_val = eng.get(field, 0) or 0
        d_val = getattr(eod, field, 0) or 0
        if d_val > 0:
            diffs.append(abs(e_val - d_val) / d_val * 100)
    return round(max(diffs), 2) if diffs else 0.0

def determine_vol_threshold(exchange: str) -> float:
    """Ngưỡng Volume tuỳ theo sàn giao dịch."""
    if exchange and 'UPCOM' in exchange.upper():
        return VOL_OK_UPCOM
    return VOL_OK_HOSE

def build_quality_log_entry(
    run_date, symbol, interval, trade_time_str,
    eng_row: Optional[dict], eod_record, vol_threshold: float
) -> dict:
    """Phân tích chất lượng 1 nến và trả về dict để INSERT vào price_quality_log."""
    eod_vol      = eod_record.volume if eod_record else 0
    eng_vol      = (eng_row.get('volume') or 0) if eng_row else 0
    eng_buy_vol  = (eng_row or {}).get('buy_vol',  0) or 0
    eng_sell_vol = (eng_row or {}).get('sell_vol', 0) or 0
    side_vol     = eng_buy_vol + eng_sell_vol

    # Tầng 1: intra vs EOD (intra bắt được bao nhiêu % vol thực)
    vol_capture_pct   = round(eng_vol  / eod_vol  * 100, 1) if eod_vol  > 0 else None
    # Tầng 2: (buy+sell) vs intra (bao nhiêu % đã được phân loại side)
    side_coverage_pct = round(side_vol / eng_vol  * 100, 1) if eng_vol  > 0 else None

    ohlc_diff = compute_ohlc_diff(eng_row or {}, eod_record) if eng_row and eod_record else None

    if eng_row is None:
        status = 'MISSING'
        gap_reason = 'ENGINE_DOWN'
    elif vol_capture_pct is not None and vol_capture_pct < vol_threshold:
        status = 'LOW_VOL'
        gap_reason = 'MQTT_DROP'
    elif ohlc_diff is not None and ohlc_diff > OHLC_DIFF_PCT:
        status = 'OHLC_DIFF'
        gap_reason = 'PARSE_ERR'
    else:
        status = 'OK'
        gap_reason = 'OK'

    return {
        'run_date': run_date,
        'symbol': symbol,
        'interval': interval,
        'trade_time': trade_time_str,
        'eng_open':    (eng_row or {}).get('open', 0),
        'eng_high':    (eng_row or {}).get('high', 0),
        'eng_low':     (eng_row or {}).get('low', 0),
        'eng_close':   (eng_row or {}).get('close', 0),
        'eng_vol':         eng_vol,
        'eng_buy_vol':     eng_buy_vol,
        'eng_sell_vol':    eng_sell_vol,
        'eng_delta':   (eng_row or {}).get('delta', 0),
        'eod_open':    eod_record.open  if eod_record else 0,
        'eod_high':    eod_record.high  if eod_record else 0,
        'eod_low':     eod_record.low   if eod_record else 0,
        'eod_close':   eod_record.close if eod_record else 0,
        'eod_vol':         eod_vol,
        'vol_capture_pct':  vol_capture_pct,
        'side_coverage_pct': side_coverage_pct,
        'ohlc_max_diff':    ohlc_diff,
        'gap_reason':  gap_reason,
        'status':      status,
    }

def batch_insert_quality_log(conn: sqlite3.Connection, entries: list, dry_run: bool):
    if dry_run or not entries:
        return
    conn.executemany("""
        INSERT OR REPLACE INTO price_quality_log
            (run_date, symbol, interval, trade_time,
             eng_open, eng_high, eng_low, eng_close, eng_vol,
             eng_buy_vol, eng_sell_vol, eng_delta,
             eod_open, eod_high, eod_low, eod_close, eod_vol,
             vol_capture_pct, side_coverage_pct, ohlc_max_diff, gap_reason, status)
        VALUES
            (:run_date, :symbol, :interval, :trade_time,
             :eng_open, :eng_high, :eng_low, :eng_close, :eng_vol,
             :eng_buy_vol, :eng_sell_vol, :eng_delta,
             :eod_open, :eod_high, :eod_low, :eod_close, :eod_vol,
             :vol_capture_pct, :side_coverage_pct, :ohlc_max_diff, :gap_reason, :status)
    """, entries)
    conn.commit()

# ============================================================
# MARKET INDICES — SCHEMA + SYNC FETCH
# ============================================================

def ensure_index_schema(conn: sqlite3.Connection) -> None:
    """Tạo bảng market_indices + index_vwap_summary nếu chưa có; migrate cột mới."""
    for stmt in DDL_MARKET_INDICES.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    for stmt in DDL_INDEX_VWAP.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    # Migration: thêm cột mới nếu DB cũ chưa có
    existing = {r[1] for r in conn.execute("PRAGMA table_info(market_indices)")}
    for col, defn in [
        ('buy_vol',  'INTEGER DEFAULT 0'),
        ('sell_vol', 'INTEGER DEFAULT 0'),
        ('delta',    'INTEGER DEFAULT 0'),
        ('is_ato',   'INTEGER DEFAULT 0'),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE market_indices ADD COLUMN {col} {defn}")
            logger.info(f"🔧 Migration: added market_indices.{col}")
    conn.commit()


def qc_market_indices(
    conn: sqlite3.Connection,
    today_str: str,
) -> dict:
    """
    Kiểm tra chất lượng dữ liệu market_indices:
      1. OHLC validity    : H ≥ max(O,C) ≥ min(O,C) ≥ L
      2. Stale detection  : nến 1m gần nhất đã quá 5 phút (trong giờ GD)
      3. Anomaly volume   : vol > 5× rolling median
      4. Gap intraday     : lỗ hổng 1m trong giờ GD hôm nay

    Returns: {
      'ohlc_errors': N, 'stale': [code,...], 'anomalies': N,
      'gap_1m': {code: missing_count}, 'grade': 'A'/'B'/'C'/'F'
    }
    """
    from datetime import timezone as _tz, timedelta as _td
    import statistics

    VN_TZ_  = _tz(_td(hours=7))
    now_vn  = datetime.now(VN_TZ_)
    now_str = now_vn.strftime('%H:%M')

    # Giờ giao dịch: 09:15-11:30, 13:00-14:45
    in_session = (
        ('09:15' <= now_str <= '11:30') or
        ('13:00' <= now_str <= '14:45')
    )

    result = {
        'ohlc_errors': 0, 'stale': [], 'anomalies': 0,
        'gap_1m': {}, 'grade': 'A',
    }

    # 1. OHLC validity (kiểm tra toàn bộ, không chỉ hôm nay)
    bad_ohlc = conn.execute("""
        SELECT COUNT(*) FROM market_indices
        WHERE date(trade_time) = ?
          AND (
            high < open OR high < close OR
            low  > open OR low  > close OR
            high < low  OR open <= 0
          )
    """, (today_str,)).fetchone()[0]
    result['ohlc_errors'] = bad_ohlc
    if bad_ohlc:
        logger.warning(f"  ⚠️  Index QC: {bad_ohlc} OHLC errors ngày {today_str}")

    # 2. Stale detection (chỉ kiểm khi trong giờ GD)
    if in_session:
        cutoff = (now_vn - _td(minutes=5)).replace(tzinfo=None)
        cutoff_str = cutoff.strftime('%Y-%m-%dT%H:%M:%S')
        for code in MARKET_INDICES:
            last = conn.execute("""
                SELECT MAX(trade_time) FROM market_indices
                WHERE index_code = ? AND interval = '1m'
                  AND date(trade_time) = ?
            """, (code, today_str)).fetchone()[0]
            if not last or last < cutoff_str:
                result['stale'].append(code)
                logger.warning(f"  ⚠️  Index STALE: {code} 1m — last={last or 'N/A'}")

    # 3. Anomaly volume (>5× median trong ngày hôm nay)
    for code in MARKET_INDICES:
        vols = [r[0] for r in conn.execute("""
            SELECT volume FROM market_indices
            WHERE index_code = ? AND interval = '1m'
              AND date(trade_time) = ? AND volume > 0
        """, (code, today_str)).fetchall()]
        if len(vols) >= 5:
            med  = statistics.median(vols)
            threshold = med * 5
            spikes = [v for v in vols if v > threshold]
            result['anomalies'] += len(spikes)
            if spikes:
                logger.warning(
                    f"  ⚠️  Volume anomaly {code}/1m: {len(spikes)} spike(s) "
                    f"(max={max(spikes):,} vs med={med:,.0f})"
                )

    # 4. Gap intraday 1m hôm nay (bỏ qua nếu ngoài giờ GD)
    if in_session:
        for code in MARKET_INDICES:
            actual = conn.execute("""
                SELECT COUNT(*) FROM market_indices
                WHERE index_code = ? AND interval = '1m'
                  AND date(trade_time) = ?
            """, (code, today_str)).fetchone()[0]
            # Kỳ vọng: ~225 nến/ngày — <150 là bất thường
            if actual < 150:
                result['gap_1m'][code] = actual
                logger.warning(
                    f"  ⚠️  1m gap {code}: chỉ có {actual} nến hôm nay (min kỳ vọng ~150)"
                )

    # Overall grade
    issues = (
        result['ohlc_errors'] > 0 or
        len(result['stale']) >= 2 or
        result['anomalies'] >= 5 or
        len(result['gap_1m']) >= 2
    )
    result['grade'] = 'F' if issues else ('B' if (result['stale'] or result['anomalies']) else 'A')

    icon = '✅' if result['grade'] == 'A' else ('🟡' if result['grade'] == 'B' else '🔴')
    logger.info(
        f"  {icon} Index QC [{today_str}]: "
        f"OHLC_err={result['ohlc_errors']} stale={len(result['stale'])} "
        f"anomaly={result['anomalies']} gap1m={len(result['gap_1m'])} "
        f"grade={result['grade']}"
    )
    return result


def fetch_index_ohlcv(
    index_code: str,
    resolution: str,
    from_ts: int,
    to_ts: int,
    interval_label: str,
) -> list:
    """
    Gọi DNSE chart-api/v2/ohlcs/index, trả về list dict
    [{trade_time, open, high, low, close, volume}, ...]
    """
    import requests as _req
    hdrs = {
        'Origin':  'https://entrade.com.vn',
        'Referer': 'https://entrade.com.vn/',
        'User-Agent': 'Mozilla/5.0',
    }
    params = {
        'symbol':     index_code,
        'resolution': resolution,
        'from':       from_ts,
        'to':         to_ts,
    }
    try:
        r = _req.get(INDEX_API_URL, params=params, headers=hdrs, timeout=15)
        r.raise_for_status()
        d = r.json()
        t_arr = d.get('t', [])
        if not t_arr:
            return []
        _VN_TZ = timezone(timedelta(hours=7))

        # Session bounds cho intraday candles (1m/5m/15m/30m/1H)
        # HOSE (VNINDEX/VN30/VN100): 09:15-14:30 continuous, ATC 14:30-14:45 → cap 14:30
        # HNX  (HNX30):              09:00-14:45 continuous, ATC 14:45       → cap 14:46
        # EOD (1D/1W) không có intraday time → bỏ qua filter này
        _is_intraday = interval_label not in ('1D', '1W')
        _HOSE_CODES  = {'VNINDEX', 'VN30', 'VN100'}
        _SESSION_START = (9, 0)
        # HOSE đóng lúc 14:30 (ATC bắt đầu) → loại ATC khỏi VWAP
        # HNX30 đóng lúc 14:45 → cho phép đến 14:46
        _session_end = (14, 30) if index_code in _HOSE_CODES else (14, 46)

        records = []
        for i, ts in enumerate(t_arr):
            # Chuyển Unix epoch → ICT (UTC+7) tường minh, không dùng local system TZ
            dt_vn = datetime.fromtimestamp(ts, tz=_VN_TZ).replace(tzinfo=None)

            # Lọc candle ngoài session giao dịch (ATC, pre-market, post-market)
            if _is_intraday:
                hm = (dt_vn.hour, dt_vn.minute)
                if hm < _SESSION_START or hm >= _session_end:
                    logger.debug(
                        f"  [index filter] {index_code} skip out-of-session: "
                        f"{dt_vn.strftime('%H:%M')}"
                    )
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
    except Exception as exc:
        logger.warning(f"  [index fetch] {index_code}/{interval_label}: {exc}")
        return []


def classify_index_bvc(conn: sqlite3.Connection, today_str: str, dry_run: bool) -> int:
    """
    Áp dụng BVC (Easley-López de Prado-O'Hara) cho tất cả nến 1m của market_indices.

    Quy tắc ATO: nến đầu phiên 09:15 thường là khớp lệnh mở cửa →
    volume lớn, H≈L → BVC tự động gán 50/50 (bar_range < 1e-6).
    Không cần lọc riêng — thuật toán tự xử lý đúng.

    Returns: số bars được classify.
    """
    import math as _math

    def _norm_cdf(z):
        return 0.5 * (1.0 + _math.erf(z / _math.sqrt(2.0)))

    _SQRT_2LN2 = _math.sqrt(2.0 * _math.log(2.0))

    def _bvc(o, h, l, c, v):
        if v <= 0:
            return 0, 0
        r = h - l
        dp = c - o
        if r < 1e-6:
            # H≈L: tick rule thay vì Parkinson
            # delta_p=0 → ATO/doji thật → 50/50
            # delta_p>0 → ATC giá tăng → BUY 100%
            # delta_p<0 → ATC giá giảm → SELL 100%
            if abs(dp) < 1e-6:
                bv = v // 2; return bv, v - bv
            return (v, 0) if dp > 0 else (0, v)
        z  = max(-4.0, min(4.0, dp / (r / _SQRT_2LN2)))
        bv = int(round(v * _norm_cdf(z)))
        return max(0, bv), max(0, v - bv)

    rows = conn.execute("""
        SELECT rowid, index_code, trade_time, open, high, low, close, volume
        FROM market_indices
        WHERE interval = '1m' AND volume > 0
          AND date(trade_time) = ?
          AND (buy_vol = 0 AND sell_vol = 0)
    """, (today_str,)).fetchall()

    if not rows:
        return 0

    updates = []
    for rowid, code, tt, o, h, l, c, v in rows:
        if None in (o, h, l, c):
            continue
        bv, sv = _bvc(float(o), float(h), float(l), float(c), int(v))
        delta  = bv - sv
        # ATO = nến đầu phiên 09:15 có volume >> median
        is_ato = 1 if tt[11:16] in ('09:15', '09:16') else 0
        updates.append((bv, sv, delta, is_ato, rowid))

    if not dry_run and updates:
        conn.executemany("""
            UPDATE market_indices
            SET buy_vol=?, sell_vol=?, delta=?, is_ato=?
            WHERE rowid=?
        """, updates)
        conn.commit()
        logger.info(f"  ✅ BVC index: {len(updates):,} bars classified (ngày {today_str})")
    return len(updates)


def compute_index_vwap(conn: sqlite3.Connection, today_str: str, dry_run: bool) -> int:
    """
    Tính VWAP ngày cho từng chỉ số từ nến 1m.
    Công thức: VWAP = Σ(close × volume) / Σ(volume)
    Bands: ±1σ, ±2σ (Parkinson variance).
    Upsert vào index_vwap_summary.
    Returns: số rows upserted.
    """
    import math as _math

    rows = conn.execute("""
        SELECT index_code, trade_time, open, close, volume, buy_vol, sell_vol
        FROM market_indices
        WHERE interval = '1m' AND volume > 0
          AND date(trade_time) = ?
        ORDER BY index_code, trade_time
    """, (today_str,)).fetchall()

    if not rows:
        return 0

    from collections import defaultdict
    grouped = defaultdict(list)
    for r in rows:
        grouped[r[0]].append(r)

    inserts = []
    for code, candles in grouped.items():
        if len(candles) < 3:
            continue
        cum_pv = cum_v = cum_pv2 = 0.0
        cum_buy = cum_sell = 0
        for _, tt, o, c, v, bv, sv in candles:
            cum_pv   += c * v
            cum_v    += v
            cum_pv2  += c * c * v
            cum_buy  += (bv or 0)
            cum_sell += (sv or 0)
        if cum_v == 0:
            continue
        vwap = cum_pv / cum_v
        var  = max(0.0, (cum_pv2 / cum_v) - vwap ** 2)
        std  = _math.sqrt(var)
        inserts.append((
            code, today_str,
            round(vwap, 4), round(std, 4),
            round(vwap + std,   4), round(vwap - std,   4),
            round(vwap + 2*std, 4), round(vwap - 2*std, 4),
            int(cum_v), cum_buy - cum_sell,
            cum_buy, cum_sell,
            candles[0][2],   # session_open
            candles[-1][3],  # session_close
        ))

    if not inserts:
        return 0

    if not dry_run:
        conn.executemany("""
            INSERT OR REPLACE INTO index_vwap_summary
                (index_code, trade_date, vwap, vwap_std,
                 vwap_upper1, vwap_lower1, vwap_upper2, vwap_lower2,
                 cum_volume, cum_delta, buy_vol, sell_vol,
                 session_open, session_close)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, inserts)
        conn.commit()
        for code, dt, vwap, std, *_ in inserts:
            logger.info(
                f"  📐 Index VWAP {code} [{dt}]: "
                f"VWAP={vwap:.2f} ±{std:.2f}"
            )
    return len(inserts)


def sync_market_indices(
    conn: sqlite3.Connection,
    today_str: str,
    dry_run: bool,
    backfill_days: Optional[int] = None,
) -> dict:
    """
    Fetch và upsert market_indices cho 4 chỉ số × 7 TF.

    - Intraday TF (1m/5m/15m/30m/1H): lấy today_str
    - EOD TF (1D/1W): lấy lookback tùy backfill_days hoặc mặc định 7 ngày

    Returns: {'fetched': N, 'upserted': N}
    """
    ensure_index_schema(conn)

    VN_TZ_ = timezone(timedelta(hours=7))
    now_ts  = int(datetime.now(VN_TZ_).timestamp())

    # Mặc định: intraday lấy hôm nay; EOD/weekly lấy 30 ngày (để bắt thiếu sót)
    days_intraday = 1
    days_daily    = backfill_days if backfill_days else 30
    days_weekly   = backfill_days * 7 if backfill_days else 365

    TF_CONFIG = {
        '1m':  ('1',   days_intraday),
        '5m':  ('5',   days_intraday * 2),
        '15m': ('15',  days_intraday * 3),
        '30m': ('30',  days_intraday * 3),
        '1H':  ('1H',  days_intraday * 5),
        '1D':  ('1D',  days_daily),
        '1W':  ('1W',  days_weekly),
    }

    total_fetched = 0
    total_upserted = 0

    for idx_code in MARKET_INDICES:
        for tf_label, (resolution, lookback_days) in TF_CONFIG.items():
            from_ts = int(
                (datetime.now(VN_TZ_) - timedelta(days=lookback_days)).timestamp()
            )
            records = fetch_index_ohlcv(
                idx_code, resolution, from_ts, now_ts, tf_label
            )
            total_fetched += len(records)

            if not records or dry_run:
                continue

            conn.executemany("""
                INSERT INTO market_indices
                    (index_code, interval, trade_time, open, high, low, close, volume)
                VALUES (:index_code, :interval, :trade_time,
                        :open, :high, :low, :close, :volume)
                ON CONFLICT(index_code, interval, trade_time) DO UPDATE SET
                    open   = excluded.open,
                    high   = excluded.high,
                    low    = excluded.low,
                    close  = excluded.close,
                    volume = excluded.volume
            """, [
                {**rec, 'index_code': idx_code, 'interval': tf_label}
                for rec in records
            ])
            total_upserted += len(records)

    if not dry_run:
        conn.commit()

    # QC sau khi upsert
    if not dry_run:
        qc_market_indices(conn, today_str)

    # BVC: phân loại buy/sell volume cho 1m bars
    n_bvc = classify_index_bvc(conn, today_str, dry_run)
    if n_bvc:
        logger.info(f"  🧬 Index BVC: {n_bvc:,} bars")

    # VWAP: tính VWAP ngày từ 1m bars (sau khi có buy_vol)
    n_vwap = compute_index_vwap(conn, today_str, dry_run)
    if n_vwap:
        logger.info(f"  📐 Index VWAP: {n_vwap} indices computed")

    return {'fetched': total_fetched, 'upserted': total_upserted, 'bvc': n_bvc, 'vwap': n_vwap}


# ============================================================
# ASYNC FETCH (tái dụng AsyncDNSEExtractor)
# ============================================================
async def fetch_all_today(symbols_info: list, today_str: str, dry_run: bool) -> dict:
    """
    Fetch 7 TF × N mã cho ngày hôm nay song song.
    Trả về dict: {security_id: {interval: [PriceRecord, ...]}}
    """
    start_dt = f"{today_str} 00:00:00"
    end_dt   = f"{today_str} 23:59:59"

    sem = asyncio.Semaphore(CONCURRENCY)
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SEC)

    results = {}  # {security_id: {interval: [...]}}

    async def _fetch(session, extractor, symbol, sec_id, interval):
        try:
            records = await extractor.fetch_ohlcv(symbol, sec_id, start_dt, end_dt, interval)
            return (sec_id, interval, records)
        except Exception as e:
            logger.warning(f"[{symbol}-{interval}] Lỗi: {e}")
            return (sec_id, interval, [])

    async with aiohttp.ClientSession(timeout=timeout) as session:
        extractor = AsyncDNSEExtractor(session, sem)
        tasks = [
            _fetch(session, extractor, sym, sid, iv)
            for (sym, sid, _exch) in symbols_info
            for iv in INTERVALS
        ]
        total = len(tasks)
        logger.info(f"🚀 Đang tải {total} requests ({len(symbols_info)} mã × {len(INTERVALS)} TF) song song...")
        done = 0
        for coro in asyncio.as_completed(tasks):
            sec_id, interval, records = await coro
            done += 1
            if done % 500 == 0 or done == total:
                logger.info(f"   Tiến độ: {done}/{total} ({done*100//total}%)")
            if records:
                if sec_id not in results:
                    results[sec_id] = {}
                results[sec_id][interval] = records

    return results

# ============================================================
# REPORT
# ============================================================
def print_quality_report(conn: sqlite3.Connection, run_date: str):
    rows = conn.execute("""
        SELECT status, gap_reason, COUNT(*),
               ROUND(AVG(vol_capture_pct),1),
               ROUND(AVG(side_coverage_pct),1)
        FROM price_quality_log
        WHERE run_date = ? AND interval = '1m'
        GROUP BY status, gap_reason
        ORDER BY COUNT(*) DESC
    """, (run_date,)).fetchall()

    logger.info("=" * 70)
    logger.info(f"📊 BÁO CÁO ĐỐI CHIẾU VOLUME — Ngày {run_date} [interval=1m]")
    logger.info(f"  {'Status':<12} {'Gap':<14} {'Nến':>6} {'Vol%':>8} {'Side%':>8}")
    logger.info(f"  {'─'*52}")
    for r in rows:
        logger.info(
            f"  {r[0]:<12} {r[1]:<14} {r[2]:>6,} "
            f"{str(r[3] or '-'):>8} {str(r[4] or '-'):>8}"
        )

    # ── Tóm tắt 3 tầng đối chiếu ──────────────────────────────
    agg = conn.execute("""
        SELECT
            COUNT(*)                             AS total,
            SUM(eng_vol)                         AS sum_eng_vol,
            SUM(eng_buy_vol + eng_sell_vol)      AS sum_classified,
            SUM(eod_vol)                         AS sum_eod_vol,
            ROUND(AVG(vol_capture_pct),  1)      AS avg_vol_cap,
            ROUND(AVG(side_coverage_pct),1)      AS avg_side_cov
        FROM price_quality_log
        WHERE run_date = ? AND interval = '1m' AND eng_vol > 0
    """, (run_date,)).fetchone()

    if agg and agg[0]:
        eng_vol_m   = (agg[1] or 0) / 1_000_000
        classified_m= (agg[2] or 0) / 1_000_000
        eod_vol_m   = (agg[3] or 0) / 1_000_000
        leftover_m  = eng_vol_m - classified_m
        logger.info("")
        logger.info("📐 ĐỐI CHIẾU 3 TẦNG (tổng 1m toàn thị trường):")
        logger.info(f"  Tầng 1 — EOD vol (chuẩn):      {eod_vol_m:>10.2f}M CP  (tổng từ nến 1m)")
        logger.info(f"  Tầng 2 — Intra eng_vol:         {eng_vol_m:>10.2f}M CP  ({agg[4]}% capture)")
        logger.info(f"  Tầng 3 — buy_vol + sell_vol:    {classified_m:>10.2f}M CP  ({agg[5]}% of intra)")
        logger.info(f"           NEUTRAL (chưa phân loại):{leftover_m:>9.2f}M CP")
        logger.info("")
        if agg[5] and agg[5] >= 90:
            logger.info("  ✅ Side coverage XUẤT SẮC (≥90%) — MASVN đang hoạt động tốt")
        elif agg[5] and agg[5] >= 70:
            logger.info("  🟡 Side coverage ĐẠT YÊU CẦU (70-90%) — Một số tick NEUTRAL")
        else:
            logger.info("  🔴 Side coverage THẤP (<70%) — Kiểm tra MASVN có chạy không?")

    # ── Top 10 mã bị miss nặng nhất ───────────────────────────
    worst = conn.execute("""
        SELECT symbol, interval,
               ROUND(AVG(vol_capture_pct),   1) AS avg_cap,
               ROUND(AVG(side_coverage_pct), 1) AS avg_side,
               COUNT(*) AS n_candles
        FROM price_quality_log
        WHERE run_date = ? AND status IN ('MISSING', 'LOW_VOL')
        GROUP BY symbol, interval
        ORDER BY avg_cap ASC LIMIT 10
    """, (run_date,)).fetchall()

    if worst:
        logger.info("🔴 Top 10 mã / TF bị rụng Ticks nhiều nhất hôm nay:")
        for w in worst:
            logger.info(f"  {w[0]:6s} [{w[1]:4s}] vol_cap={w[2]}%  side_cov={w[3]}%  ({w[4]} nến)")
    logger.info("=" * 70)

# ============================================================
# MAIN
# ============================================================
async def main(filter_symbols=None, dry_run=False, date_override=None,
               skip_indices=False, backfill_days=None, indices_only=False):
    db_path = os.getenv("SMD_DB_PATH", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "securities_master.db"
    ))

    today_str = date_override if date_override else datetime.now().strftime("%Y-%m-%d")

    # ── Chế độ nhẹ: chỉ sync indices, bỏ qua toàn bộ stock ETL ───────────────
    if indices_only:
        logger.info(f"📈 INDICES-ONLY MODE — {today_str}")
        conn = sqlite3.connect(db_path)
        idx_stats = sync_market_indices(
            conn, today_str=today_str, dry_run=dry_run, backfill_days=backfill_days
        )
        logger.info(
            f"✅ Indices sync done: fetched={idx_stats['fetched']:,} "
            f"upserted={idx_stats['upserted']:,}"
        )
        conn.close()
        return
    run_date  = today_str
    t_start   = datetime.now()

    logger.info("=" * 60)
    logger.info(f"📅 EOD DAILY CLOSE — {today_str}")
    logger.info(f"   Intervals: {INTERVALS}")
    logger.info(f"   Vol OK: HOSE/HNX≥{VOL_OK_HOSE}% | UPCOM≥{VOL_OK_UPCOM}%")
    logger.info(f"   OHLC Diff OK: ≤{OHLC_DIFF_PCT}%")
    logger.info(f"   Dry-Run: {dry_run}")
    logger.info("=" * 60)

    conn = sqlite3.connect(db_path, detect_types=0)
    ensure_quality_schema(conn)

    symbols_info = get_symbols(conn, filter_symbols)
    if not symbols_info:
        logger.error("Không tìm thấy mã chứng khoán nào!")
        return
    logger.info(f"📦 Tổng {len(symbols_info)} mã cần xử lý.")

    # PHASE 0: Snapshot engine data TRƯỚC khi ghi đè
    engine_snap = snapshot_engine_data(conn, today_str)

    # PHASE 1 (P0): Tải toàn bộ từ DNSE
    eod_data = await fetch_all_today(symbols_info, today_str, dry_run)
    logger.info(f"✅ DNSE trả về dữ liệu cho {len(eod_data)}/{len(symbols_info)} mã.")

    # Build lookup: symbol -> exchange để xác định ngưỡng Vol
    exchange_map = {sid: exch for (_, sid, exch) in symbols_info}
    symbol_map   = {sid: sym  for (sym, sid, _) in symbols_info}

    # PHASE 1: Upsert + Phase 2: Quality Log
    total_upserted = 0
    all_quality_entries = []

    for (sym, sec_id, exch) in symbols_info:
        vol_threshold = determine_vol_threshold(exch)
        eod_intervals = eod_data.get(sec_id, {})

        for interval in INTERVALS:
            records = eod_intervals.get(interval, [])

            # Upsert vào stock_prices
            if records:
                upsert_dnse_records(conn, records, dry_run)
                total_upserted += len(records)

            # Quality check từng nến
            for rec in records:
                trade_time_str = rec.trade_time.isoformat()
                key = (sec_id, interval, trade_time_str)
                eng_row = engine_snap.get(key)

                entry = build_quality_log_entry(
                    run_date, sym, interval, trade_time_str,
                    eng_row, rec, vol_threshold
                )
                all_quality_entries.append(entry)

            # Nến của engine nhưng KHÔNG có trong DNSE (EXTRA)
            for (eid, eiv, ett), eng_data in engine_snap.items():
                if eid == sec_id and eiv == interval:
                    # ett đã là isoformat (T separator) từ normalize trên
                    rec_times = {r.trade_time.isoformat() for r in records}
                    if ett not in rec_times:
                        all_quality_entries.append(build_quality_log_entry(
                            run_date, sym, interval, ett,
                            eng_data, None, vol_threshold
                        ))

    # Insert Quality Log
    batch_insert_quality_log(conn, all_quality_entries, dry_run)

    # PHASE 2.5: Khôi phục buy_vol 1D từ Redis (graceful fallback)
    sym_to_id = {sym: sid for (sym, sid, _) in symbols_info}
    n_restored = restore_1d_buysell_from_redis(conn, today_str, sym_to_id, dry_run)
    logger.info(f"📊 1D buy_vol Redis restore: {n_restored} mã")

    # PHASE 2.6: Impute buy_vol từ 1m → TF ≥ 5m/1D
    # Fix cốt lõi: Engine không flush open candles kịp trước 15:45
    # → Aggregate trực tiếp từ 1m data đã có trong DB
    logger.info("🔧 PHASE 2.6: Impute buy_vol từ 1m → 5m/15m/30m/1H/1D...")
    n_imputed = impute_buyvol_from_1m(conn, today_str, dry_run)
    logger.info(f"📊 Imputed {n_imputed:,} rows buy_vol/sell_vol")

    # PHASE 2.65: BVC Auto-Fill — vá side data còn thiếu sau DNSE upsert
    # Chạy sau khi DNSE đã upsert đủ 1m bars và Phase 2.6 đã aggregate.
    # Chỉ fill các bars có buy_vol=0 (giữ nguyên MASVN native data).
    # Bỏ qua nếu side_cov đã ≥ 90% (MASVN hoạt động tốt).
    logger.info("🧬 PHASE 2.65: BVC Auto-Fill (Bulk Volume Classification)...")
    if not dry_run:
        try:
            # Kiểm tra side coverage trước khi chạy
            cov_row = conn.execute("""
                SELECT ROUND(
                    SUM(COALESCE(buy_vol,0)+COALESCE(sell_vol,0))*100.0
                    / NULLIF(SUM(volume),0), 1
                )
                FROM stock_prices
                WHERE interval='1m' AND volume>0
                  AND date(trade_time) = ?
            """, (today_str,)).fetchone()
            side_cov = cov_row[0] if cov_row and cov_row[0] else 0.0

            if side_cov >= 90.0:
                logger.info(
                    f"  ✅ Side coverage đã đạt {side_cov}% — bỏ qua BVC fill"
                )
            else:
                logger.info(
                    f"  ⚠️  Side coverage = {side_cov}% (< 90%) — bắt đầu BVC fill..."
                )
                bvc_stats = run_bvc_imputer(
                    conn,
                    date_filter = today_str,
                    commit      = True,
                    batch_size  = 10_000,
                )
                logger.info(
                    f"  🧬 BVC imputed {bvc_stats['imputed']:,} bars "
                    f"(skip={bvc_stats['skipped']}, "
                    f"elapsed={bvc_stats['elapsed_sec']}s)"
                )
                # Kiểm tra coverage sau khi fill
                cov_after = conn.execute("""
                    SELECT ROUND(
                        SUM(COALESCE(buy_vol,0)+COALESCE(sell_vol,0))*100.0
                        / NULLIF(SUM(volume),0), 1
                    )
                    FROM stock_prices
                    WHERE interval='1m' AND volume>0
                      AND date(trade_time) = ?
                """, (today_str,)).fetchone()
                new_cov = cov_after[0] if cov_after and cov_after[0] else 0.0
                logger.info(
                    f"  📈 Side coverage: {side_cov}% → {new_cov}% sau BVC fill"
                )
        except Exception as e:
            logger.warning(f"  ⚠️  BVC Phase 2.65 lỗi (không chẹn EOD): {e}")
    else:
        logger.info("    [DRY-RUN] Bỏ qua Phase 2.65")

    # PHASE 2.8: Tính pt_vol từ DNSE → pt_vol = DNSE_1D_total - SUM(1m bars)
    # DNSE 1D volume = nm_vol + pt_vol (total chính thức từ sàn)
    # nm_vol = SUM(nến 1m) → pt_vol = hiệu số
    logger.info("📈 PHASE 2.8: Tính pt_vol (thỏa thuận) từ DNSE 1D vs 1m bars...")
    if not dry_run:
        filter_syms = [sym for (sym, _, _) in symbols_info]
        pt_stats = impute_pt_vol(
            date_vn=today_str,
            filter_syms=filter_syms,
            dry_run=False,
            quiet=False,
        )
        logger.info(
            f"📊 pt_vol: fetched={pt_stats['fetched']} updated={pt_stats['updated']} "
            f"total_pt_vol={pt_stats['total_pt_vol']/1e6:.2f}M"
        )
    else:
        logger.info("    [DRY-RUN] Bỏ qua Phase 2.8")

    # PHASE 2.7: Tính lại daily_vwap_summary
    logger.info("📐 PHASE 2.7: Tính daily_vwap_summary...")

    n_vwap = compute_daily_vwap_summary(conn, today_str, dry_run)
    logger.info(f"📊 daily_vwap_summary: {n_vwap} rows upserted")

    # PHASE 2.9: VWAP QC — so sánh daily_vwap_summary vs TradingView Screener
    logger.info("🔍 PHASE 2.9: VWAP QC vs TradingView Screener...")
    if not dry_run:
        try:
            qc_syms = [sym for (sym, _, _) in symbols_info]
            qc_stats = run_vwap_qc(
                trade_date   = today_str,
                filter_syms  = qc_syms,
                quiet        = False,
            )
            if qc_stats['error'] > 0:
                logger.warning(
                    f"⚠️  VWAP QC: {qc_stats['error']} mã gap > 3% — kiểm tra pt_vol hoặc 1m data"
                )
            else:
                logger.info(
                    f"✅ VWAP QC: {qc_stats['ok']} OK | {qc_stats['warn']} WARN | 0 ERROR"
                )
        except Exception as e:
            logger.warning(f"VWAP QC skipped (ERR): {e}")
    else:
        logger.info("    [DRY-RUN] Bỏ qua Phase 2.9")

    # PHASE 3: Report
    duration = (datetime.now() - t_start).total_seconds()
    logger.info(f"\n✅ HOÀN TẤT. Tổng {total_upserted:,} nến upserted. Thời gian: {duration:.1f}s")
    if not dry_run:
        print_quality_report(conn, run_date)

    # PHASE 4: Market Indices Sync (VNINDEX, VN30, VN100, HNX30 × 7 TF)
    if not skip_indices:
        logger.info("\n📈 PHASE 4: Đồng bộ Market Indices (VNINDEX/VN30/VN100/HNX30 × 7 TF)...")
        idx_stats = sync_market_indices(
            conn,
            today_str   = today_str,
            dry_run     = dry_run,
            backfill_days = backfill_days,
        )
        logger.info(
            f"   Indices: fetched={idx_stats['fetched']:,} | upserted={idx_stats['upserted']:,}"
            + (" [DRY-RUN]" if dry_run else "")
        )
    else:
        logger.info("\n📈 PHASE 4: Bỏ qua Market Indices (--skip-indices)")

    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EOD Daily Close — Đồng bộ giá cuối ngày 7 Timeframes")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Danh sách mã test (VD: VCB,HPG,TCB). Mặc định: toàn bộ DB")
    parser.add_argument("--dry-run", action="store_true",
                        help="Test: chạy đọc, không ghi DB, không insert log")
    parser.add_argument("--date", type=str, default=None,
                        help="Ngày cần xử lý (YYYY-MM-DD). Mặc định: hôm nay")
    parser.add_argument("--skip-indices", action="store_true",
                        help="Bỏ qua PHASE 4 — không đồng bộ market indices")
    parser.add_argument("--backfill-indices", type=int, default=None, metavar="DAYS",
                        help="Số ngày backfill cho indices 1D/1W (VD: 365 = 1 năm). Mặc định: 30")
    parser.add_argument("--indices-only", action="store_true",
                        help="Chế độ nhẹ: chỉ sync market indices, bỏ qua stock ETL. "
                             "Dùng cho cron intraday (mỗi 1–5 phút trong giờ GD).")
    args = parser.parse_args()

    filter_syms = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    asyncio.run(main(
        filter_symbols = filter_syms,
        dry_run        = args.dry_run,
        date_override  = args.date,
        skip_indices   = args.skip_indices,
        backfill_days  = args.backfill_indices,
        indices_only   = args.indices_only,
    ))
