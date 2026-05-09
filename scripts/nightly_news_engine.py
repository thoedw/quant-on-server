#!/usr/bin/env python3
"""
scripts/nightly_news_engine.py
VietCap Nightly News Engine — Chạy mỗi đêm lúc 04:00 ICT

Kiến trúc VietCap-Only:
  1. Tin Chủ Đề Vĩ Mô : VietCapMacroExtractor (Playwright + Proxy)
     → Lưu vào security _MACRO
  2. Cổ Phiếu Lẻ      : VietCapNewsExtractor (GraphQL + Proxy)
     → Lưu vào từng security_id theo mã

Proxy: MuaProxy (Rotating Residential, 1GB/gói)
Token: Tự động login bằng VIETCAP_USERNAME/PASSWORD
"""

import os
import sys
import logging
import argparse
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

# ─── Load .env ─────────────────────────────────────────────────────────────────
_env = Path(__file__).parent.parent / '.env'
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _, _v = _line.partition('=')
            os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

from securities_master.news_pipeline import NewsPipeline

# ─── Telegram ──────────────────────────────────────────────────────────────────
TG_TOKEN   = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# ─── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE = Path('/tmp/nightly_news_engine.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


def load_symbols(db_path: str) -> list[str]:
    """Lấy toàn bộ EQUITY symbols từ DB, xếp theo thanh khoản."""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        rows = conn.execute("""
            SELECT s.symbol, COALESCE(AVG(p.volume), 0) as avg_vol
            FROM securities s
            LEFT JOIN stock_prices p ON s.security_id = p.security_id
                AND p.interval = '1D'
                AND p.trade_time >= date('now', '-7 days')
            WHERE s.asset_type = 'EQUITY'
            GROUP BY s.symbol
            ORDER BY avg_vol DESC, s.symbol ASC
        """).fetchall()
        conn.close()
        symbols = [r[0] for r in rows]
        logger.info(f"✅ Tải {len(symbols)} mã EQUITY từ DB (xếp theo thanh khoản).")
        return symbols
    except Exception as e:
        logger.warning(f"⚠️ Không lấy được symbols từ DB: {e}")

    # Fallback vnstock
    try:
        from vnstock import Vnstock
        stock = Vnstock().stock(symbol='ACB', source='VCI')
        symbols = stock.listing.all_symbols()['symbol'].tolist()
        logger.info(f"✅ Fallback: {len(symbols)} mã từ vnstock.")
        return symbols
    except Exception as e:
        logger.error(f"❌ Không thể lấy symbols: {e}")
        return []


def run(
    symbols: list[str],
    delay: float,
    db_path: str,
    dry_run: bool,
    force_run: bool = False,
    skip_macro: bool = False,
):
    """Orchestrate toàn bộ pipeline VietCap."""
    pipeline = NewsPipeline(db_path=db_path, delay_seconds=delay)
    start = datetime.now()

    logger.info("=" * 65)
    logger.info(f"🌙 VIETCAP NIGHTLY NEWS ENGINE — {start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"   Mã: {len(symbols)} | Delay: {delay}s | DryRun: {dry_run}")
    logger.info(f"   DB: {db_path}")
    logger.info(f"   Proxy: MuaProxy ON | Nguồn: VietCap ONLY")
    logger.info("=" * 65)

    if dry_run:
        logger.info("[DRY-RUN] Danh sách 10 mã đầu:")
        for i, s in enumerate(symbols[:10], 1):
            logger.info(f"  {i:4d}. {s}")
        logger.info(f"  ... tổng {len(symbols)} mã")
        return

    # 1️⃣ Tin Chủ Đề Vĩ Mô (1 lần/ngày, dùng Playwright qua Proxy)
    if not skip_macro:
        logger.info(">>> 📡 Luồng 1: Tin Chủ Đề Vĩ Mô (VietCapMacroExtractor)...")
        pipeline.run_macro(incremental=not force_run)
    else:
        logger.info(">>> ⏩ Bỏ qua Luồng Tin Chủ Đề (--skip-macro)")

    # 2️⃣ Cổ Phiếu Lẻ (GraphQL qua Proxy)
    logger.info(f">>> 📈 Luồng 2: Cổ Phiếu Lẻ ({len(symbols)} mã, VietCapNewsExtractor)...")
    pipeline.run(
        symbols=symbols,
        resume_today=not force_run,
        incremental=True,
    )

    elapsed = (datetime.now() - start).total_seconds()
    logger.info("=" * 65)
    logger.info(f"✅ HOÀN TẤT — Thời gian: {elapsed/60:.1f} phút")
    logger.info("=" * 65)
    return elapsed


def print_stats(db_path: str) -> dict:
    import sqlite3
    conn = sqlite3.connect(db_path)
    today = datetime.now().strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT status, COUNT(*) as cnt, SUM(rows_inserted) as total_rows
        FROM etl_run_log
        WHERE source = 'vietcap_daily_news'
          AND date(datetime(started_at, '+7 hours')) = ?
        GROUP BY status
    """, (today,)).fetchall()
    stats = {'rows': rows, 'total_new': 0}
    if rows:
        logger.info("📊 THỐNG KÊ HÔM NAY (Cổ Phiếu Lẻ):")
        for r in rows:
            logger.info(f"   {r[0]:10s}: {r[1]:4d} mã | {r[2] or 0:6d} bài mới")
            if r[0] == 'SUCCESS':
                stats['total_new'] = r[2] or 0
    no_news = conn.execute("""
        SELECT COUNT(*) FROM securities s
        WHERE s.asset_type = 'EQUITY'
          AND s.security_id NOT IN (SELECT DISTINCT security_id FROM news_sentiment)
    """).fetchone()[0]
    logger.info(f"   Mã chưa có tin trong DB: {no_news}")
    conn.close()
    stats['no_news'] = no_news
    return stats


def send_telegram(stats: dict, elapsed_min: float):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    import httpx
    now_str = datetime.now().strftime('%d/%m/%Y %H:%M')
    lines = [
        f"🌙 *VIETCAP NEWS ENGINE* — Hoàn tất",
        f"`{now_str}` — `{elapsed_min:.0f} phút`",
        "─" * 28,
    ]
    for r in stats.get('rows', []):
        icon = "✅" if r[0] == 'SUCCESS' else "⚠️"
        lines.append(f"  {icon} {r[0]}: `{r[1]}` mã | `{r[2] or 0}` bài mới")
    lines.append(f"  🚫 Chưa có tin: `{stats.get('no_news', 0)}` mã")
    lines.append("")
    lines.append("🤖 Morning AI News sắp khởi động...")
    try:
        httpx.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": "\n".join(lines), "parse_mode": "Markdown"},
            timeout=10
        )
        logger.info("📱 Telegram đã gửi!")
    except Exception as e:
        logger.warning(f"⚠️ Telegram lỗi: {e}")


def main():
    parser = argparse.ArgumentParser(description="VietCap Nightly News Engine")
    parser.add_argument('symbols', nargs='*', help='Mã cổ phiếu (VD: VND SHB HPG)')
    parser.add_argument('--top',       type=int,   default=None, help='Top N mã thanh khoản')
    parser.add_argument('--delay',     type=float, default=1.5,  help='Delay giữa requests (s)')
    parser.add_argument('--dry-run',   action='store_true', help='Không fetch, không ghi DB')
    parser.add_argument('--force-run', action='store_true', help='Bỏ SmartResume + re-fetch')
    parser.add_argument('--skip-macro',action='store_true', help='Bỏ qua luồng Tin Chủ Đề')
    parser.add_argument('--stats',     action='store_true', help='Chỉ in thống kê DB')
    args = parser.parse_args()

    db_path = os.getenv('SMD_DB_PATH', './data/securities_master.db')

    if args.stats:
        print_stats(db_path)
        return

    start = datetime.now()

    # Xác định symbols
    if args.symbols:
        symbols_to_run = []
        for raw in args.symbols:
            symbols_to_run.extend(
                s.strip().upper() for s in raw.replace(';', ',').split(',') if s.strip()
            )
        is_on_demand = True
    else:
        symbols_to_run = load_symbols(db_path)
        if args.top:
            symbols_to_run = symbols_to_run[:args.top]
        is_on_demand = False

    elapsed_sec = run(
        symbols         = symbols_to_run,
        delay           = args.delay,
        db_path         = db_path,
        dry_run         = args.dry_run,
        force_run       = args.force_run or is_on_demand,
        skip_macro      = args.skip_macro,
    ) or 0

    stats = print_stats(db_path)
    elapsed_min = elapsed_sec / 60

    if not args.dry_run and not is_on_demand:
        send_telegram(stats, elapsed_min)

        # Chỉ tự gọi Morning AI News trong chế độ Nightly (không phải On-demand)
        # On-demand: telegram_daemon.py tự chain morning_ai_news riêng
        python_bin = os.path.join(Path(__file__).parent.parent, "venv", "bin", "python3")
        if not os.path.exists(python_bin):
            python_bin = sys.executable
        import subprocess
        logger.info("🌤️ Kích hoạt Morning AI News...")
        ai_script = Path(__file__).parent / 'morning_ai_news.py'
        try:
            subprocess.run(
                [python_bin, str(ai_script)],
                check=True,
                env={**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent)}
            )
        except Exception as e:
            logger.error(f"❌ Morning AI News lỗi: {e}")


if __name__ == '__main__':
    main()
