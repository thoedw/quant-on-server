#!/usr/bin/env python3
"""
scripts/intraday_qc.py
══════════════════════════════════════════════════════════════════
Kiểm Soát Chất Lượng Dữ liệu Intraday — Daily QC Report

Nguồn sự thật: EOD vol từ DNSE (bảng stock_prices, interval='1D')
Đối tượng kiểm tra: buy_vol, sell_vol, delta trong nến 1m của engine
Chạy: sau eod_daily_close.py, lý tưởng là 16:00 ICT T2-T6

Thuật toán:
  Với mỗi symbol:
    vol_capture  = SUM(eng_vol_1m) / eod_vol_1D * 100
    (dùng eod_vol_1D thay vì vol per-candle để tránh lệch vi phân)

    side_coverage = SUM(buy_vol + sell_vol) / SUM(eng_vol) * 100

  Grade hệ A/B/C/F theo tiêu chí kép.

Output:
  • Ghi vào bảng daily_quality_summary (thêm nếu chưa có)
  • In báo cáo console
  • Gửi Telegram nếu có cấu hình
"""

import os
import sys
import sqlite3
import logging
import argparse
import httpx
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("SMD_DB_PATH", os.path.join(ROOT, "data", "securities_master.db"))
TG_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ═══════════════════════════════════════════════════════════════
# NGƯỠNG QUALITY GRADE
# ═══════════════════════════════════════════════════════════════
# vol_capture: SUM nến 1m engine / EOD total vol - phải ~100%
# side_coverage: (buy+sell) / eng_vol - đo MASVN hoạt động tốt ko

GRADE_RULES = [
    # (grade, label,  min_vol_cap, min_side_cov, max_missing_pct)
    ('A', '🟢 Xuất sắc',  90.0, 85.0,  5.0),
    ('B', '🟡 Đạt',       75.0, 65.0, 15.0),
    ('C', '🟠 Kém',       50.0, 40.0, 30.0),
    ('F', '🔴 Thất bại',    0.0,  0.0, 100.0),
]


def grade_symbol(vol_cap: float, side_cov: float, missing_pct: float) -> tuple[str, str]:
    """Trả về (grade, label) theo tiêu chí kép."""
    for g, label, min_vc, min_sc, max_mp in GRADE_RULES:
        if vol_cap >= min_vc and side_cov >= min_sc and missing_pct <= max_mp:
            return g, label
    return 'F', '🔴 Thất bại'


# ═══════════════════════════════════════════════════════════════
# DDL
# ═══════════════════════════════════════════════════════════════
DDL_SUMMARY = """
CREATE TABLE IF NOT EXISTS daily_quality_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    -- Tầng 1: Volume capture (engine 1m vs EOD 1D)
    eod_vol_1d      INTEGER DEFAULT 0,   -- DNSE EOD vol cả ngày (nguồn sự thật)
    eng_vol_sum     INTEGER DEFAULT 0,   -- Tổng eng_vol từ nến 1m
    vol_capture_pct REAL,                -- eng_vol_sum / eod_vol_1d * 100
    -- Tầng 2: Side coverage (MASVN quality)
    buy_vol_sum     INTEGER DEFAULT 0,   -- Tổng buy_vol từ engine
    sell_vol_sum    INTEGER DEFAULT 0,   -- Tổng sell_vol từ engine
    side_vol_sum    INTEGER DEFAULT 0,   -- buy+sell
    neutral_vol     INTEGER DEFAULT 0,   -- eng_vol - side_vol (NEUTRAL/unclassified)
    side_coverage_pct REAL,              -- side_vol / eng_vol * 100
    -- Tầng 3: Candle completeness
    total_candles   INTEGER DEFAULT 0,   -- Số nến 1m kỳ vọng trong phiên (~229)
    missing_candles INTEGER DEFAULT 0,   -- Nến ENGINE_DOWN
    low_vol_candles INTEGER DEFAULT 0,   -- Nến MQTT_DROP
    missing_pct     REAL,               -- missing / total * 100
    -- Tổng hợp
    grade           TEXT,               -- A/B/C/F
    grade_label     TEXT,               -- 🟢 Xuất sắc / ...
    -- Metadata
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trade_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_dqs_date  ON daily_quality_summary(trade_date);
CREATE INDEX IF NOT EXISTS idx_dqs_grade ON daily_quality_summary(trade_date, grade);
"""


def ensure_schema(conn: sqlite3.Connection):
    for stmt in DDL_SUMMARY.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()


# ═══════════════════════════════════════════════════════════════
# COMPUTE PER-SYMBOL QUALITY
# ═══════════════════════════════════════════════════════════════

def compute_quality(conn: sqlite3.Connection, trade_date: str) -> list[dict]:
    """
    Tính chất lượng cho từng symbol trong ngày trade_date.

    Logic:
      - Lấy EOD vol từ stock_prices (interval='1D', date=trade_date) — nguồn sự thật DNSE
      - Lấy SUM eng_vol, eng_buy_vol, eng_sell_vol từ price_quality_log (interval='1m')
      - Dùng eod_vol_1D làm mẫu số → vol_capture_pct đo mức độ chụp tick so với thực tế
    """
    # ── Bước 1: EOD vol cả ngày từ DNSE ──────────────────────
    # QUAN TRỌNG: dùng volume từ nến 1D của DNSE — đây là VOL THỰC TẾA CẢ NGÀY
    # của từng mã, không phải per-candle offset.
    eod_vols = {}
    rows_eod = conn.execute("""
        SELECT s.symbol, sp.volume
        FROM stock_prices sp
        JOIN securities s ON sp.security_id = s.security_id
        WHERE sp.interval = '1D'
          AND date(sp.trade_time) = ?
          AND sp.volume > 0
    """, (trade_date,)).fetchall()
    for sym, vol in rows_eod:
        eod_vols[sym] = vol

    if not eod_vols:
        logger.warning(f"⚠️  Không có dữ liệu EOD 1D cho ngày {trade_date}. Chạy eod_daily_close.py trước!")
        return []

    # ── Bước 2: Thống kê engine từ price_quality_log ────────────
    eng_rows = conn.execute("""
        SELECT
            symbol,
            SUM(eng_vol)                    AS sum_eng_vol,
            SUM(eng_buy_vol)                AS sum_buy,
            SUM(eng_sell_vol)               AS sum_sell,
            COUNT(*)                        AS total_candles,
            SUM(CASE WHEN status='MISSING'  THEN 1 ELSE 0 END) AS missing_cnt,
            SUM(CASE WHEN status='LOW_VOL'  THEN 1 ELSE 0 END) AS low_vol_cnt
        FROM price_quality_log
        WHERE interval = '1m'
          AND run_date  = ?
        GROUP BY symbol
    """, (trade_date,)).fetchall()

    if not eng_rows:
        logger.warning(f"⚠️  Không có dữ liệu price_quality_log cho ngày {trade_date}.")
        return []

    # ── Bước 3: Kết hợp và tính grade ──────────────────────────
    results = []
    for row in eng_rows:
        sym, eng_vol, buy_vol, sell_vol, total, missing, low_vol = row

        eod_vol  = eod_vols.get(sym, 0)
        side_vol = (buy_vol or 0) + (sell_vol or 0)
        neutral  = max(0, (eng_vol or 0) - side_vol)

        # Tầng 1: vol_capture — dùng EOD 1D làm mẫu số (nguồn sự thật DNSE)
        # Mức bình thường: 80-100% (không thể > 100% vì DNSE là chủ)
        # < 80%: Engine bị miss ticks (MQTT drop, sleep mode, ...)
        vol_cap  = round(eng_vol / eod_vol * 100, 1)   if (eod_vol or 0) > 0 else None
        # Tầng 2: side_coverage
        side_cov = round(side_vol / eng_vol * 100, 1)  if eng_vol  > 0 else None
        # Tầng 3: missing
        miss_pct = round(missing / total * 100, 1)     if total    > 0 else None

        # Grade
        g, label = grade_symbol(
            vol_cap  or 0.0,
            side_cov or 0.0,
            miss_pct or 0.0,
        )

        results.append({
            'trade_date'      : trade_date,
            'symbol'          : sym,
            'eod_vol_1d'      : eod_vol,
            'eng_vol_sum'     : eng_vol or 0,
            'vol_capture_pct' : vol_cap,
            'buy_vol_sum'     : buy_vol  or 0,
            'sell_vol_sum'    : sell_vol or 0,
            'side_vol_sum'    : side_vol,
            'neutral_vol'     : neutral,
            'side_coverage_pct': side_cov,
            'total_candles'   : total,
            'missing_candles' : missing,
            'low_vol_candles' : low_vol,
            'missing_pct'     : miss_pct,
            'grade'           : g,
            'grade_label'     : label,
        })

    return results


def upsert_summary(conn: sqlite3.Connection, rows: list[dict]):
    """Ghi kết quả vào daily_quality_summary."""
    conn.executemany("""
        INSERT OR REPLACE INTO daily_quality_summary (
            trade_date, symbol,
            eod_vol_1d, eng_vol_sum, vol_capture_pct,
            buy_vol_sum, sell_vol_sum, side_vol_sum, neutral_vol, side_coverage_pct,
            total_candles, missing_candles, low_vol_candles, missing_pct,
            grade, grade_label
        ) VALUES (
            :trade_date, :symbol,
            :eod_vol_1d, :eng_vol_sum, :vol_capture_pct,
            :buy_vol_sum, :sell_vol_sum, :side_vol_sum, :neutral_vol, :side_coverage_pct,
            :total_candles, :missing_candles, :low_vol_candles, :missing_pct,
            :grade, :grade_label
        )
    """, rows)
    conn.commit()


# ═══════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════

def build_report(conn: sqlite3.Connection, trade_date: str) -> str:
    """Tạo báo cáo tổng hợp dạng text."""
    # Tổng hợp theo grade
    grade_agg = conn.execute("""
        SELECT grade, grade_label, COUNT(*) as n,
               ROUND(AVG(vol_capture_pct),1)  as avg_vc,
               ROUND(AVG(side_coverage_pct),1) as avg_sc,
               ROUND(AVG(missing_pct),1)        as avg_miss
        FROM daily_quality_summary
        WHERE trade_date = ?
        GROUP BY grade, grade_label
        ORDER BY grade ASC
    """, (trade_date,)).fetchall()

    # Tổng toàn thị trường
    mkt = conn.execute("""
        SELECT
            COUNT(*) as total_syms,
            CAST(SUM(eod_vol_1d)   AS REAL) / 1e6 as eod_M,
            CAST(SUM(eng_vol_sum)  AS REAL) / 1e6 as eng_M,
            CAST(SUM(buy_vol_sum)  AS REAL) / 1e6 as buy_M,
            CAST(SUM(sell_vol_sum) AS REAL) / 1e6 as sell_M,
            CAST(SUM(neutral_vol)  AS REAL) / 1e6 as neutral_M,
            ROUND(
                SUM(eng_vol_sum) * 100.0 / NULLIF(SUM(eod_vol_1d), 0)
            , 1) as mkt_vol_cap,
            ROUND(
                SUM(side_vol_sum) * 100.0 / NULLIF(SUM(eng_vol_sum), 0)
            , 1) as mkt_side_cov
        FROM daily_quality_summary WHERE trade_date = ?
    """, (trade_date,)).fetchone()

    # Top 10 mã tệ nhất (grade F + C, sort by side_coverage ASC)
    worst = conn.execute("""
        SELECT symbol, grade_label, vol_capture_pct, side_coverage_pct,
               missing_pct, eod_vol_1d
        FROM daily_quality_summary
        WHERE trade_date = ? AND grade IN ('F','C')
        ORDER BY eod_vol_1d DESC, side_coverage_pct ASC
        LIMIT 10
    """, (trade_date,)).fetchall()

    lines = []
    lines.append(f"📊 BÁO CÁO CHẤT LƯỢNG INTRADAY — {trade_date}")
    lines.append("━" * 48)

    # Tổng quan thị trường
    if mkt and mkt[0]:
        lines.append(f"\n🌐 TỔNG QUAN THỊ TRƯỜNG ({mkt[0]:,} mã)")
        lines.append(f"  EOD vol (chuẩn):   {mkt[1]:>8.1f}M CP")
        lines.append(f"  Engine vol (1m):   {mkt[2]:>8.1f}M CP  ({mkt[6]}% capture)")
        lines.append(f"  ├── Buy vol:        {mkt[3]:>8.1f}M CP")
        lines.append(f"  ├── Sell vol:       {mkt[4]:>8.1f}M CP")
        lines.append(f"  └── NEUTRAL:        {mkt[5]:>8.1f}M CP  ({mkt[7]}% side coverage)")

        # Đánh giá tổng thể
        if mkt[7] and mkt[7] >= 85:
            lines.append(f"\n✅ Side Coverage XUẤT SẮC ({mkt[7]}%) — MASVN hoạt động tốt cả ngày!")
        elif mkt[7] and mkt[7] >= 65:
            lines.append(f"\n🟡 Side Coverage ĐẠT ({mkt[7]}%) — Có gián đoạn nhỏ trong phiên.")
        else:
            lines.append(f"\n🔴 Side Coverage THẤP ({mkt[7]}%) — Kiểm tra MASVN workers ngay!")

    # Phân bố grade
    lines.append(f"\n📊 PHÂN BỐ CHẤT LƯỢNG MÃ:")
    for g, label, n, avg_vc, avg_sc, avg_miss in grade_agg:
        lines.append(f"  {label}  ({g}): {n:>4} mã  |  VolCap={avg_vc}%  SideCov={avg_sc}%  Miss={avg_miss}%")

    # Top mã tệ nhất có thanh khoản
    if worst:
        lines.append(f"\n⚠️ TOP MÃ THANH KHOẢN CAO BỊ KÉM CHẤT LƯỢNG:")
        for sym, label, vc, sc, mp, evol in worst:
            evol_m = (evol or 0) / 1_000_000
            lines.append(f"  {sym:6s} {label} | Vol={evol_m:.1f}M | Cap={vc}% | Side={sc}% | Miss={mp}%")

    lines.append("\n" + "━" * 48)
    return "\n".join(lines)


def send_telegram(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        parts = [text[i:i+4096] for i in range(0, len(text), 4096)]
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        for part in parts:
            httpx.post(url, json={
                "chat_id": TG_CHAT_ID,
                "text": part,
                "parse_mode": "HTML",
            }, timeout=15)
    except Exception as e:
        logger.error(f"Lỗi Telegram: {e}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Intraday QC Report")
    parser.add_argument('--date',     type=str, default=None,
                        help="Ngày cần kiểm tra (YYYY-MM-DD). Mặc định: hôm nay")
    parser.add_argument('--no-telegram', action='store_true',
                        help="Không gửi Telegram")
    parser.add_argument('--dry-run',  action='store_true',
                        help="Tính toán nhưng không ghi DB")
    args = parser.parse_args()

    trade_date = args.date or date.today().strftime("%Y-%m-%d")
    logger.info(f"🔍 QC Intraday — Ngày: {trade_date}")
    logger.info(f"   DB: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH, detect_types=0)
    ensure_schema(conn)

    # Tính chất lượng
    rows = compute_quality(conn, trade_date)
    if not rows:
        logger.error("Không có dữ liệu để QC. Đảm bảo đã chạy eod_daily_close.py!")
        conn.close()
        return

    logger.info(f"📦 Đã tính QC cho {len(rows)} mã.")

    # Thống kê nhanh
    grades = {}
    for r in rows:
        grades[r['grade']] = grades.get(r['grade'], 0) + 1
    logger.info(f"   Phân bố grade: {grades}")

    # Ghi DB
    if not args.dry_run:
        upsert_summary(conn, rows)
        logger.info(f"✅ Đã ghi vào daily_quality_summary.")

    # Report
    report = build_report(conn, trade_date)
    print("\n" + report)

    # Telegram
    if not args.no_telegram:
        send_telegram(report)
        logger.info("📱 Đã gửi báo cáo lên Telegram.")

    conn.close()


if __name__ == "__main__":
    main()
