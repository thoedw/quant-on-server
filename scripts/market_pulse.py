#!/usr/bin/env python3
"""
scripts/market_pulse.py
==============================================
Market Pulse — Bản tin AI giữa phiên & cuối phiên

Chạy 2 lần/ngày (cron — Thứ 2–6):
  11:30 ICT → --session morning  → Tổng kết phiên sáng + dự báo chiều
  16:00 ICT → --session eod      → Đánh giá thị trường + Radar ngày mai

Kiến trúc:
  1. Lấy tin vĩ mô + tin mã từ DB (news_sentiment) trong ngày hôm nay
  2. Lấy VWAP snapshot intraday (top 30 mã thanh khoản cao + portfolio)
  3. Build prompt context theo session type
  4. Gọi Gemini AI phân tích
  5. Gửi kết quả qua Telegram (chunk nếu > 4096 ký tự)

Chạy tay: python3 scripts/market_pulse.py --session eod
          python3 scripts/market_pulse.py --session morning --dry-run
"""

import os
import sys
import sqlite3
import logging
import argparse
import httpx
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Load .env ──────────────────────────────────────────────────────────────────
_env = PROJECT_ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _, _v = _line.partition('=')
            os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

from google import genai

# ── Config ─────────────────────────────────────────────────────────────────────
TG_TOKEN   = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
GEMINI_KEY = os.environ.get('GEMINI_TIER1_KEY') or os.environ.get('GEMINI_API_KEY', '')
DB_PATH    = os.environ.get('SMD_DB_PATH', str(PROJECT_ROOT / 'data' / 'securities_master.db'))
VN_TZ      = timezone(timedelta(hours=7))

PORTFOLIO  = ["HPG", "SHB", "MBB", "ACB", "VND", "SSI", "POW", "VRE", "PSI", "NKG"]
TOP_N_SCAN = 30   # Top N mã bổ sung ngoài portfolio cho VWAP scan

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [MarketPulse] %(message)s',
    handlers=[
        logging.FileHandler('/tmp/market_pulse.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

def get_today_news(conn: sqlite3.Connection, date_vn: str) -> dict:
    """
    Lấy tin vĩ mô và tin mã hôm nay từ news_sentiment.
    Trả về {'macro': [...], 'stocks': {symbol: [articles]}}
    """
    conn.row_factory = sqlite3.Row

    # Tin vĩ mô (security_id của _MACRO hoặc symbol=NULL)
    macro_rows = conn.execute("""
        SELECT n.title, n.summary, n.published_at, n.ai_sentiment, n.ai_rating
        FROM news_sentiment n
        JOIN securities s ON n.security_id = s.security_id
        WHERE s.symbol = '_MACRO'
          AND date(n.published_at, '+7 hours') = ?
        ORDER BY n.published_at DESC
        LIMIT 30
    """, (date_vn,)).fetchall()

    # Tin mã cổ phiếu hôm nay
    stock_rows = conn.execute("""
        SELECT s.symbol, n.title, n.summary, n.published_at,
               n.ai_sentiment, n.ai_rating
        FROM news_sentiment n
        JOIN securities s ON n.security_id = s.security_id
        WHERE s.asset_type = 'EQUITY'
          AND date(n.published_at, '+7 hours') = ?
        ORDER BY n.ai_rating DESC, n.published_at DESC
        LIMIT 150
    """, (date_vn,)).fetchall()

    stocks_dict: dict[str, list] = {}
    for r in stock_rows:
        sym = r['symbol']
        if sym not in stocks_dict:
            stocks_dict[sym] = []
        stocks_dict[sym].append({
            'title'     : r['title'],
            'summary'   : r['summary'] or '',
            'sentiment' : r['ai_sentiment'],
            'score'     : r['ai_rating'],
        })

    logger.info(f"  📰 Tin vĩ mô hôm nay: {len(macro_rows)} bài")
    logger.info(f"  📈 Tin mã hôm nay: {len(stock_rows)} bài ({len(stocks_dict)} mã)")
    return {'macro': [dict(r) for r in macro_rows], 'stocks': stocks_dict}


def get_vwap_snapshot(conn: sqlite3.Connection, date_vn: str, symbols: list[str]) -> list[dict]:
    """
    Lấy VWAP intraday snapshot cho danh sách mã.
    """
    conn.row_factory = sqlite3.Row
    from realtime.vwap_engine import VWAPEngine, _session_open_utc

    try:
        eng   = VWAPEngine(DB_PATH)
        snaps = eng.compute_all(top_n=800, date_vn=date_vn)
        sym2snap = {}
        # Map security_id → symbol
        ph = ','.join(['?']*len(symbols))
        secs = conn.execute(
            f'SELECT symbol, security_id FROM securities WHERE symbol IN ({ph})', symbols
        ).fetchall()
        sid2sym = {r['security_id']: r['symbol'] for r in secs}
        for s in snaps:
            if s.security_id in sid2sym:
                sym2snap[sid2sym[s.security_id]] = s

        result = []
        for sym in symbols:
            if sym not in sym2snap:
                continue
            s = sym2snap[sym]
            # PVWAP hôm qua
            sid = next((r['security_id'] for r in secs if r['symbol']==sym), None)
            pvwap_row = None
            if sid:
                pvwap_row = conn.execute("""
                    SELECT vwap, cum_delta, side_cov_pct FROM daily_vwap_summary
                    WHERE security_id=? AND trade_date < ?
                    ORDER BY trade_date DESC LIMIT 1
                """, (sid, date_vn)).fetchone()

            pv = pvwap_row['vwap'] if pvwap_row else None
            pct_pvwap = (s.last_close - pv) / pv * 100 if pv else None
            result.append({
                'symbol'    : sym,
                'close'     : s.last_close,
                'vwap'      : s.vwap,
                'pvwap'     : pv,
                'pct_pvwap' : pct_pvwap,
                'cum_delta' : s.cum_delta,
                'cum_volume': s.cum_volume,
                'band_zone' : _get_band_zone(s),
            })
        logger.info(f"  💧 VWAP snapshot: {len(result)} mã")
        return result
    except Exception as e:
        logger.warning(f"⚠️ Không lấy được VWAP: {e}")
        return []


def _get_band_zone(snap) -> str:
    vwap = snap.vwap or 0
    c    = snap.last_close or 0
    u1   = snap.vwap_upper1 or vwap
    u2   = snap.vwap_upper2 or vwap
    l1   = snap.vwap_lower1 or vwap
    l2   = snap.vwap_lower2 or vwap
    if c >= u2:   return 'ABOVE_2SD'
    elif c >= u1: return 'ABOVE_1SD'
    elif c >= vwap: return 'ABOVE_VWAP'
    elif c >= l1: return 'BELOW_VWAP'
    elif c >= l2: return 'BELOW_1SD'
    else:         return 'BELOW_2SD'


def get_top_symbols_by_volume(conn: sqlite3.Connection, date_vn: str, top_n: int) -> list[str]:
    """Lấy top N mã theo volume phiên hôm nay."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT s.symbol, SUM(p.volume) as vol
        FROM stock_prices p
        JOIN securities s ON p.security_id = s.security_id
        WHERE p.interval = '1m'
          AND date(p.trade_time) = ?
          AND s.asset_type = 'EQUITY'
        GROUP BY s.symbol
        ORDER BY vol DESC
        LIMIT ?
    """, (date_vn, top_n)).fetchall()
    return [r['symbol'] for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# PROMPT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_MORNING = """Bạn là Trưởng Phòng Tự Doanh tại một định chế tài chính lớn tại Việt Nam.
Nhiệm vụ: Nhận dữ liệu thị trường giữa phiên (11:30) → viết **Bản Tin Giữa Phiên** + Dự báo chiều.

Văn phong: Lạnh lùng, sắc bén, bám dữ kiện thực tế, không suy diễn vô căn cứ.
Định dạng: Markdown, tiếng Việt. KHÔNG dùng Bảng (Table) — dùng Danh Sách (List) để tương thích Telegram.
"""

SYSTEM_EOD = """Bạn là Trưởng Phòng Tự Doanh tại một định chế tài chính lớn tại Việt Nam.
Nhiệm vụ: Nhận dữ liệu sau đóng cửa (16:00) → viết **Bản Tin Cuối Phiên** + Radar ngày mai.

Văn phong: Lạnh lùng, sắc bén, bám dữ kiện thực tế, không suy diễn vô căn cứ.
Định dạng: Markdown, tiếng Việt. KHÔNG dùng Bảng (Table) — dùng Danh Sách (List) để tương thích Telegram.
"""

TEMPLATE_MORNING = """# 📊 BẢN TIN GIỮA PHIÊN SÁNG — {date} (11:30 ICT)

## 1. Vĩ Mô & Bối Cảnh
- Tóm tắt 3-5 điểm tin vĩ mô quan trọng nhất ảnh hưởng phiên hôm nay.
- Nhận định dòng tiền sáng: Mua gom / Phân phối / Cân bằng?

## 2. VWAP Radar — Dòng Tiền Tổ Chức (Phiên Sáng)
Phân tích nhanh các mã nổi bật dựa trên dữ liệu VWAP:
- Mã nào đang TRÊN PVWAP + delta dương → Fund đang BẢO VỆ / GOM
- Mã nào DƯỚI PVWAP + delta âm → Áp lực BÁN
- Highlight top 3 mã đáng chú ý nhất (kèm lý do)

## 3. Portfolio Check (11:30)
Đánh giá nhanh từng mã danh mục theo dữ liệu VWAP + tin tức:
- 🔹 **[MÃ]** (Ngành): [🟢/🔴/🟡] vs PVWAP: X% | Delta: ±Y | Tin: ...

## 4. 🔥 Kế Hoạch Chiều
- Top 3 mã ưu tiên theo dõi phiên chiều (lý do + ngưỡng kích hoạt)
- Rủi ro cần chú ý: ...
"""

TEMPLATE_EOD = """# 🏁 BẢN TIN CUỐI PHIÊN — {date} (16:00 ICT)

## 1. Tổng Kết Phiên Hôm Nay
- Nhận định chung: Thị trường tăng/giảm/sideway? Breadth (số mã tăng/giảm)?
- Dòng tiền: Nhóm ngành nào dẫn dắt? Nhóm nào bị xả?
- Điểm đặc biệt: Sự kiện/tin bất ngờ trong phiên (nếu có)

## 2. VWAP Post-Market Analysis
Đánh giá 5-7 mã nổi bật nhất qua lăng kính VWAP interday:
- Mã vượt PVWAP + vol surge + delta dương → Tín hiệu tích luỹ
- Mã thất bại tại PVWAP + delta âm → Tín hiệu phân phối
- Kết luận ngắn gọn cho từng mã

## 3. Portfolio End-of-Day
Đánh giá toàn bộ danh mục sau đóng cửa:
- 🔹 **[MÃ]** (Ngành): Close=X | vs PVWAP=±Y% | Delta=±Z | [🟢 GIỮ / 🔴 CẮT LỖ / 🟡 THEO DÕI]
- Tổng hợp: Danh mục hôm nay +/- ? So với hôm qua?

## 4. Tin Vĩ Mô & Catalyst Đáng Chú Ý
- Tổng hợp tin nổi bật trong ngày ảnh hưởng đến phiên mai
- Lịch sự kiện quan trọng ngày mai (nếu biết)

## 5. 🎯 RADAR NGÀY MAI — Lệnh Quan Tâm
Top 5-7 mã cần theo dõi phiên mai (tiêu chí: catalyst rõ + VWAP signal):
1. 🎯 **[MÃ]** (Ngành) — [MUA/BÁN/WATCH] 🔥🔥🔥
   - Lý do: ...
   - Ngưỡng vào: ... | Ngưỡng thoát: ...
2. ...

Chú thích: 🔥🔥🔥 = Conviction cao | 🔥🔥 = Theo dõi chặt | 🔥 = Radar xa
"""


def build_prompt(session: str, date_vn: str, news: dict, vwap_data: list[dict]) -> str:
    """Build full context prompt cho Gemini."""
    now_vn = datetime.now(VN_TZ).strftime('%d/%m/%Y %H:%M')

    # Chọn template
    if session == 'morning':
        system   = SYSTEM_MORNING
        template = TEMPLATE_MORNING.format(date=date_vn)
    else:
        system   = SYSTEM_EOD
        template = TEMPLATE_EOD.format(date=date_vn)

    # Build tin vĩ mô
    macro_text = "<TIN_VI_MO_HOC_NAY>\n"
    if news['macro']:
        for i, a in enumerate(news['macro'][:20], 1):
            macro_text += f"{i}. [{a.get('ai_sentiment','?')}] {a['title']}\n"
            if a.get('summary'):
                macro_text += f"   → {a['summary'][:200]}\n"
    else:
        macro_text += "(Chưa có tin vĩ mô hôm nay — dựa vào dữ liệu VWAP)\n"
    macro_text += "</TIN_VI_MO_HOC_NAY>\n"

    # Build tin mã
    stock_text = "<TIN_TUC_MA_CO_PHIEU>\n"
    # Portfolio trước
    for sym in PORTFOLIO:
        articles = news['stocks'].get(sym, [])
        if articles:
            stock_text += f"\n[{sym}]:\n"
            for a in articles[:3]:
                stock_text += f"  - [{a['sentiment']}] {a['title']}\n"
                if a['summary']:
                    stock_text += f"    {a['summary'][:150]}\n"
    # Mã khác có tin nổi bật
    others = [(sym, arts) for sym, arts in news['stocks'].items()
              if sym not in PORTFOLIO and arts]
    others.sort(key=lambda x: max(a['score'] or 0 for a in x[1]), reverse=True)
    for sym, articles in others[:20]:
        stock_text += f"\n[{sym}]:\n"
        for a in articles[:2]:
            stock_text += f"  - [{a['sentiment']}] {a['title']}\n"
    stock_text += "</TIN_TUC_MA_CO_PHIEU>\n"

    # Build VWAP data
    vwap_text = "<VWAP_INTRADAY_DATA>\n"
    vwap_text += f"Thời điểm snapshot: {now_vn} ICT\n"
    vwap_text += f"{'Mã':<7} {'Close':>7} {'VWAP':>7} {'PVWAP':>7} {'vs PVWAP':>9} {'Delta':>12} {'Vol(M)':>7} Zone\n"
    vwap_text += "-"*70 + "\n"
    for v in vwap_data:
        pct_str = f"{v['pct_pvwap']:+.2f}%" if v['pct_pvwap'] is not None else "N/A"
        delta_str = f"{(v['cum_delta'] or 0):+,}"
        vol_str = f"{(v['cum_volume'] or 0)/1e6:.1f}M"
        vwap_text += (f"{v['symbol']:<7} {v['close']:>7.2f} {v['vwap']:>7.2f} "
                      f"{(v['pvwap'] or 0):>7.2f} {pct_str:>9} {delta_str:>12} "
                      f"{vol_str:>7} {v['band_zone']}\n")
    vwap_text += "\nGhi chú: PVWAP = VWAP phiên hôm qua (benchmark fund manager)\n"
    vwap_text += "Delta = Buy_vol - Sell_vol (dương = mua ròng, âm = bán ròng)\n"
    vwap_text += "</VWAP_INTRADAY_DATA>\n"

    full_prompt = f"""{system}

{macro_text}

{stock_text}

{vwap_text}

---
Dựa trên toàn bộ dữ liệu trên, hãy viết báo cáo theo cấu trúc sau:

{template}
"""
    return full_prompt


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM SENDER
# ══════════════════════════════════════════════════════════════════════════════

def send_telegram_chunked(token: str, chat_id: str, text: str, parse_mode: str = "Markdown"):
    """
    Gửi message qua Telegram, tự động chunk nếu > 4096 ký tự.
    """
    if not token or not chat_id:
        logger.warning("⚠️ Thiếu TG_TOKEN hoặc TG_CHAT_ID — bỏ qua gửi Telegram")
        return False

    url    = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = _chunk_text(text, max_len=4000)
    logger.info(f"📱 Telegram: gửi {len(chunks)} chunk(s)")

    for i, chunk in enumerate(chunks, 1):
        try:
            resp = httpx.post(url, json={
                "chat_id"    : chat_id,
                "text"       : chunk,
                "parse_mode" : parse_mode,
            }, timeout=15)
            if resp.status_code == 200:
                logger.info(f"  ✅ Chunk {i}/{len(chunks)} OK")
            else:
                # Thử lại không có parse_mode nếu bị lỗi Markdown
                resp2 = httpx.post(url, json={
                    "chat_id": chat_id,
                    "text"   : chunk,
                }, timeout=15)
                logger.warning(f"  ⚠️ Chunk {i} retry plain: {resp2.status_code}")
        except Exception as e:
            logger.error(f"  ❌ Chunk {i} lỗi: {e}")

    return True


def _chunk_text(text: str, max_len: int = 4000) -> list[str]:
    """Chia text thành chunks ≤ max_len, ưu tiên cắt tại dòng trống."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text[:max_len].rfind('\n\n')
        if cut < max_len // 2:
            cut = text[:max_len].rfind('\n')
        if cut <= 0:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip('\n')
    return chunks


# ══════════════════════════════════════════════════════════════════════════════
# AI ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def call_gemini(prompt: str, api_key: str, model: str = "models/gemini-2.5-pro") -> str:
    """Gọi Gemini AI và trả về text analysis."""
    client = genai.Client(api_key=api_key)
    logger.info(f"🤖 Gọi Gemini ({model}) — {len(prompt):,} ký tự (~{len(prompt)//4:,} tokens)")
    try:
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        result = resp.text or ""
        logger.info(f"✅ Gemini trả về {len(result):,} ký tự")
        return result
    except Exception as e:
        logger.error(f"❌ Gemini lỗi: {e}")
        return f"❌ Lỗi AI: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Market Pulse — Bản tin AI giữa phiên & cuối phiên")
    parser.add_argument('--session', choices=['morning', 'eod'], required=True,
                        help='morning = 11:30 (giữa phiên), eod = 16:00 (cuối phiên)')
    parser.add_argument('--date',    type=str, default=None,
                        help='Ngày phân tích YYYY-MM-DD (mặc định: hôm nay)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Build prompt nhưng không gọi AI, không gửi Telegram')
    parser.add_argument('--no-tg',   action='store_true',
                        help='Không gửi Telegram (chỉ print ra stdout)')
    args = parser.parse_args()

    date_vn = args.date or datetime.now(VN_TZ).strftime('%Y-%m-%d')
    session_label = "GIỮA PHIÊN SÁNG (11:30)" if args.session == 'morning' else "CUỐI PHIÊN (16:00)"

    logger.info("=" * 65)
    logger.info(f"🚀 MARKET PULSE [{session_label}] — {date_vn}")
    logger.info(f"   DryRun={args.dry_run} | NoTG={args.no_tg}")
    logger.info("=" * 65)

    conn = sqlite3.connect(DB_PATH)

    # 1. News từ DB
    logger.info("📰 [1/4] Lấy tin tức từ DB...")
    news = get_today_news(conn, date_vn)

    # 2. Top mã theo volume + portfolio
    logger.info("📊 [2/4] Lấy VWAP snapshot...")
    top_syms    = get_top_symbols_by_volume(conn, date_vn, TOP_N_SCAN)
    all_symbols = list(dict.fromkeys(PORTFOLIO + top_syms))  # portfolio ưu tiên, dedup
    vwap_data   = get_vwap_snapshot(conn, date_vn, all_symbols)

    # 3. Build prompt
    logger.info("📝 [3/4] Build prompt...")
    prompt = build_prompt(args.session, date_vn, news, vwap_data)
    logger.info(f"   Prompt size: {len(prompt):,} ký tự (~{len(prompt)//4:,} tokens)")

    if args.dry_run:
        logger.info("⚠️  DRY-RUN — Không gọi AI. Preview prompt 500 ký tự đầu:")
        print(prompt[:500])
        conn.close()
        return

    # 4. Gọi Gemini
    logger.info("🤖 [4/4] Gọi Gemini AI...")
    if not GEMINI_KEY:
        logger.error("❌ Thiếu GEMINI_TIER1_KEY hoặc GEMINI_API_KEY trong .env")
        conn.close()
        return

    analysis = call_gemini(prompt, GEMINI_KEY)

    # 5. Gửi Telegram
    header = (
        f"{'☀️' if args.session=='morning' else '🏁'} "
        f"*MARKET PULSE {'GIỮA PHIÊN' if args.session=='morning' else 'CUỐI PHIÊN'}* "
        f"— {date_vn}\n"
        f"{'─'*30}\n"
    )
    full_msg = header + analysis

    if not args.no_tg:
        send_telegram_chunked(TG_TOKEN, TG_CHAT_ID, full_msg)
    else:
        print("\n" + "="*60)
        print(full_msg)

    conn.close()
    logger.info("✅ Market Pulse hoàn tất.")


if __name__ == '__main__':
    main()
