"""
=============================================================
Whale Hunter — Phát hiện tín hiệu tích lũy của tổ chức (Cá Mập)
=============================================================
Chạy mỗi 5 phút trong giờ giao dịch để quét 4 loại tín hiệu:

  🐋 HIDDEN_ACCUMULATION  — Giá ≤ VWAP nhưng dòng tiền tích lũy dương
  🚀 VWAP_RECLAIM         — Giá vượt VWAP với volume đột biến
  📊 DELTA_DIVERGENCE     — Giá giảm nhưng cum_delta tăng (cá mập đỡ hàng)
  🔴 VWAP_REJECTION       — Giá bị đẩy xuống từ VWAP (áp lực bán)

Cách chạy:
  cd /Users/tuanho/quant && source venv/bin/activate
  python3 scripts/whale_hunter.py              # Chạy một lần
  python3 scripts/whale_hunter.py --loop       # Chạy tự động mỗi 5 phút
  python3 scripts/whale_hunter.py --backtest   # Chạy trên dữ liệu hôm nay
"""

import os
import sys
import json
import sqlite3
import logging
import argparse
import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from realtime.vwap_engine import VWAPEngine, VWAPSnapshot

# Load .env
def _load_env():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip().strip('"'))
_load_env()

# ── Cấu hình ───────────────────────────────────────────────
DB_PATH        = PROJECT_ROOT / "data" / "securities_master.db"
VN_TZ          = timezone(timedelta(hours=7))
SCAN_INTERVAL  = 5 * 60           # 5 phút
MARKET_OPEN    = (9, 15)          # 9:15 VN
MARKET_CLOSE   = (15, 0)          # 15:00 VN
TOP_N          = 300              # Chỉ quét top 300 mã thanh khoản
MIN_SCORE      = 70               # Ngưỡng tối thiểu
MIN_CUM_VOL    = 10_000           # Tối thiểu 10,000 CP đã giao dịch
MIN_CANDLES_HA = 3                # HIDDEN_ACCUMULATION: số nến liên tiếp tối thiểu
EMAIL_SCORE_THRESHOLD = 80        # Chỉ gửi email khi signal Score >= 80

# ── Vol Surge & Side Quality Gate ──────────────────────────
SIDE_QUALITY_GATE = 50.0   # % side_coverage — dưới ngưỡng này delta KHÔNG đáng tin
VOL_SURGE_MIN     = 1.5    # Nến hiện tại phải ≥ 1.5× avg 5 nến trước
VOL_SURGE_STRONG  = 2.5    # ≥ 2.5× avg = surge mạnh → bonus điểm cao

# ── SMTP Config (đọc từ .env) ──────────────────────────────
SMTP_HOST  = "smtp.gmail.com"
SMTP_PORT  = 587
SMTP_EMAIL  = os.environ.get("SMTP_EMAIL", "")
SMTP_PASS   = os.environ.get("SMTP_PASSWORD", "")
SMTP_TARGET = os.environ.get("SMTP_TARGET", "")

# ── Telegram Config (đọc từ .env) ──────────────────────────
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [WhaleHunter] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# EMAIL ALERTER
# ============================================================

class EmailAlerter:
    """
    Gửi email HTML alert khi phát hiện tín hiệu whale score >= EMAIL_SCORE_THRESHOLD.
    Dùng Gmail SMTP với App Password (không cần OAuth).
    """
    ICONS = {
        "HIDDEN_ACCUMULATION": "🐋",
        "VWAP_RECLAIM"       : "🚀",
        "DELTA_DIVERGENCE"   : "📊",
        "VWAP_REJECTION"     : "🔴",
        "PVWAP_SUPPORT_TEST" : "🎯",
        "VWAP_BOUNCE"        : "🔁",
        "PT_ACCUMULATION"    : "🏦",
        "PT_DUMPING"         : "🏚️",
    }
    COLORS = {
        "BUY" : "#00c853",  # xanh lá
        "SELL": "#d50000",  # đỏ
    }

    def __init__(self):
        self.enabled = bool(SMTP_EMAIL and SMTP_PASS and SMTP_TARGET)
        if not self.enabled:
            logger.warning("EmailAlerter: Thiếu SMTP config trong .env — tắt email alert.")

    def _build_html(self, signals: list[dict], sym_map: dict) -> str:
        now_vn = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M:%S")
        rows = ""
        for sig in sorted(signals, key=lambda x: -x["score"]):
            sym    = sym_map.get(sig["security_id"], f"ID={sig['security_id']}")
            icon   = self.ICONS.get(sig["signal_type"], "⚡")
            snap   = sig["snap"]
            color  = self.COLORS.get(sig["direction"], "#888")
            arrow  = "⬆️ MUA" if sig["direction"] == "BUY" else "⬇️ BÁN"
            delta_str = f"+{snap['cum_delta']:,}" if snap['cum_delta'] >= 0 else f"{snap['cum_delta']:,}"
            rows += f"""
            <tr>
              <td style="padding:8px;font-size:20px;text-align:center">{icon}</td>
              <td style="padding:8px;font-weight:bold;font-size:16px">{sym}</td>
              <td style="padding:8px;color:#555">{sig['signal_type']}</td>
              <td style="padding:8px;color:{color};font-weight:bold">{arrow}</td>
              <td style="padding:8px;text-align:center">
                <span style="background:{color};color:white;padding:2px 8px;
                             border-radius:12px;font-weight:bold">
                  {sig['score']:.0f}
                </span>
              </td>
              <td style="padding:8px">{snap['last_close']:.2f}k</td>
              <td style="padding:8px;color:#555">{snap['vwap']:.2f}k</td>
              <td style="padding:8px;color:{color}">{delta_str}</td>
            </tr>"""

        return f"""
        <html><body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:20px">
        <div style="max-width:800px;margin:0 auto;background:white;
                    border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.1)">

          <!-- Header -->
          <div style="background:linear-gradient(135deg,#1a237e,#283593);
                      color:white;padding:24px;">
            <h2 style="margin:0">🐋 VWAP Whale Hunter Alert</h2>
            <p style="margin:6px 0 0;opacity:.8">{now_vn} &nbsp;|&nbsp; {len(signals)} tín hiệu Score ≥ {EMAIL_SCORE_THRESHOLD}</p>
          </div>

          <!-- Table -->
          <div style="padding:20px">
            <table width="100%" style="border-collapse:collapse;">
              <thead>
                <tr style="background:#e8eaf6;">
                  <th style="padding:8px"></th>
                  <th style="padding:8px;text-align:left">Mã CK</th>
                  <th style="padding:8px;text-align:left">Signal</th>
                  <th style="padding:8px;text-align:left">Hướng</th>
                  <th style="padding:8px">Score</th>
                  <th style="padding:8px;text-align:left">Giá</th>
                  <th style="padding:8px;text-align:left">VWAP</th>
                  <th style="padding:8px;text-align:left">Cum Delta</th>
                </tr>
              </thead>
              <tbody>{rows}</tbody>
            </table>
          </div>

          <!-- Footer -->
          <div style="background:#f5f5f5;padding:12px 20px;color:#888;font-size:12px">
            ⚠️ Thông tin chỉ mang tính tham khảo. Tự chịu trách nhiệm khi giao dịch.<br>
            🤖 Quant AI — VWAP Engine v1.0 | hotrantuan@gmail.com
          </div>
        </div>
        </body></html>"""

    def send(self, signals: list[dict], sym_map: dict) -> bool:
        """Gửi email. Trả về True nếu thành công."""
        if not self.enabled or not signals:
            return False
        try:
            msg = MIMEMultipart("alternative")
            now_vn = datetime.now(VN_TZ).strftime("%H:%M")
            # Subject tóm tắt top signal
            top = sorted(signals, key=lambda x: -x["score"])[0]
            top_sym = sym_map.get(top["security_id"], "???")
            top_icon = self.ICONS.get(top["signal_type"], "⚡")
            msg["Subject"] = (
                f"{top_icon} [{now_vn}] {top_sym} {top['signal_type']} "
                f"Score={top['score']:.0f} (+{len(signals)-1} signals)"
            )
            msg["From"]    = SMTP_EMAIL
            msg["To"]      = SMTP_TARGET

            html = self._build_html(signals, sym_map)
            msg.attach(MIMEText(html, "html", "utf-8"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_EMAIL, SMTP_PASS)
                server.send_message(msg)

            logger.info(f"📧 Email alert đã gửi → {SMTP_TARGET} ({len(signals)} signals)")
            return True
        except Exception as e:
            logger.error(f"❌ Email alert thất bại: {e}")
            return False


_email_alerter = EmailAlerter()


# ============================================================
# TELEGRAM ALERTER
# ============================================================

class TelegramAlerter:
    """
    Gửi tin nhắn Telegram instant khi phát hiện tín hiệu whale.
    Dùng Bot API — không cần thư viện đặc biệt, chỉ dùng httpx (có sẵn).
    """
    API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    ICONS = {
        "HIDDEN_ACCUMULATION": "🐋",
        "VWAP_RECLAIM"       : "🚀",
        "DELTA_DIVERGENCE"   : "📊",
        "VWAP_REJECTION"     : "🔴",
        "PVWAP_SUPPORT_TEST" : "🎯",
        "VWAP_BOUNCE"        : "🔁",
        "PT_ACCUMULATION"    : "🏦",
        "PT_DUMPING"         : "🏚️",
    }

    def __init__(self):
        self.enabled = bool(TG_TOKEN and TG_CHAT_ID)
        if not self.enabled:
            logger.warning("TelegramAlerter: Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID — tắt Telegram alert.")
        else:
            logger.info(f"📱 TelegramAlerter: Sẵn sàng gửi → chat_id={TG_CHAT_ID}")

    @staticmethod
    def _esc(text: str) -> str:
        """Escape ký tự đặc biệt cho MarkdownV2."""
        special = r"\_*[]()~`>#+-=|{}.!"
        return "".join(f"\\{c}" if c in special else c for c in str(text))

    def _build_message(self, signals: list[dict], sym_map: dict) -> str:
        now_vn = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M")
        e = self._esc
        lines = [
            f"🐋 *VWAP WHALE HUNTER* — {e(now_vn)}",
            f"_{e(len(signals))} tín hiệu mạnh \\| Score ≥ {e(EMAIL_SCORE_THRESHOLD)}_",
            "─" * 28,
        ]
        for sig in sorted(signals, key=lambda x: -x["score"]):
            sym      = sym_map.get(sig["security_id"], "???")
            icon     = self.ICONS.get(sig["signal_type"], "⚡")
            sig_name = sig["signal_type"].replace("_", "\\_")
            snap     = sig["snap"]
            arrow    = "⬆️ MUA" if sig["direction"] == "BUY" else "⬇️ BÁN"
            delta    = snap["cum_delta"]
            delta_str = e(f"+{delta:,}" if delta >= 0 else f"{delta:,}")
            pct_vwap  = (snap["last_close"] - snap["vwap"]) / snap["vwap"] * 100
            price_s  = e(f"{snap['last_close']:.2f}")
            vwap_s   = e(f"{snap['vwap']:.2f}")
            pct_s    = e(f"{pct_vwap:+.2f}")
            lines += [
                f"{icon} *{e(sym)}* — {sig_name}",
                f"  {arrow} \\| Score: `{e(int(sig['score']))}/100`",
                f"  Giá: `{price_s}` \\| VWAP: `{vwap_s}` \\({pct_s}%\\)",
                f"  ΔCum: `{delta_str}` CP",
                "",
            ]
        lines.append("⚠️ Tôi tự chịu trách nhiệm khi giao dịch\\.")
        return "\n".join(lines)

    def send(self, signals: list[dict], sym_map: dict) -> bool:
        """Gửi tin nhắn Telegram. Trả về True nếu thành công."""
        if not self.enabled or not signals:
            return False
        try:
            import httpx
            text = self._build_message(signals, sym_map)
            url  = self.API_URL.format(token=TG_TOKEN)
            resp = httpx.post(url, json={
                "chat_id"    : TG_CHAT_ID,
                "text"       : text,
                "parse_mode" : "MarkdownV2",
            }, timeout=10)
            if resp.status_code == 200:
                logger.info(f"📱 Telegram alert đã gửi → {TG_CHAT_ID} ({len(signals)} signals)")
                return True
            else:
                logger.error(f"❌ Telegram lỗi {resp.status_code}: {resp.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"❌ Telegram alert thất bại: {e}")
            return False


_tg_alerter = TelegramAlerter()



# ============================================================
# HELPER: Vol Surge & Band Zone
# ============================================================

def _compute_vol_surge(recent_candles: list) -> float:
    """
    Tỷ lệ volume nến CUỐI vs trung bình 5 nến TRƯỚC đó.
    recent_candles đã được sắp xếp tăng dần theo thời gian.
    vol_surge = 1.0  → không có surge
    vol_surge = 2.5  → vol gấp 2.5 lần bình thường = tín hiệu mạnh
    """
    if len(recent_candles) < 2:
        return 1.0
    last_vol = recent_candles[-1]["volume"] or 0
    prev_5   = recent_candles[-6:-1]  # tối đa 5 nến trước nến cuối
    if not prev_5:
        return 1.0
    avg_prev = sum(c["volume"] or 0 for c in prev_5) / len(prev_5)
    return round(last_vol / max(avg_prev, 1), 2)


def _band_zone(snap: dict) -> str:
    """
    Phân vùng giá theo VWAP Bands.
    Dùng để bonus/penalty điểm theo vị trí giá trong dải band.
    """
    p = snap["last_close"]
    u2 = snap.get("vwap_upper2", snap["vwap"] * 1.02)
    u1 = snap.get("vwap_upper1", snap["vwap"] * 1.01)
    l1 = snap.get("vwap_lower1", snap["vwap"] * 0.99)
    l2 = snap.get("vwap_lower2", snap["vwap"] * 0.98)
    if   p >= u2:           return "ABOVE_2SD"
    elif p >= u1:           return "ABOVE_1SD"
    elif p >= snap["vwap"]: return "ABOVE_VWAP"
    elif p >= l1:           return "BELOW_VWAP"
    elif p >= l2:           return "BELOW_1SD"
    else:                   return "BELOW_2SD"


# ============================================================
# SIGNAL SCORERS  (v2: Vol Surge + Band Zone + Side Quality)
# ============================================================

def score_hidden_accumulation(
    snap: dict, recent_candles: list,
    vol_surge: float = 1.0, delta_reliable: bool = True
) -> tuple[float, dict]:
    """
    🐋 HIDDEN_ACCUMULATION: Giá ≤ VWAP nhưng cum_delta > 0 liên tục.
    Phụ thuộc hoàn toàn vào delta → tắt nếu MASVN down.

    Scoring v2:
      Base 40   : close < VWAP và cum_delta > 0
      Vol Surge : -15 / +10 / +20 theo 3 mức
      Band Zone : +15 nếu BELOW_2SD, +10 nếu BELOW_1SD
      Delta Seq : +15 nếu 3 nến liên tục buy pressure
      Delta Ratio: +15 nếu cum_delta > 1% vol
      Gap VWAP  : +15 nếu giá áp sát VWAP (< 0.5%)
    """
    if not delta_reliable:
        return 0.0, {}  # MASVN down → delta vô nghĩa

    details = {}
    score   = 0.0

    vwap       = snap["vwap"]
    last_close = snap["last_close"]
    cum_delta  = snap["cum_delta"]
    cum_vol    = snap["cum_volume"]

    if cum_vol < MIN_CUM_VOL:
        return 0.0, {}
    if last_close >= vwap or cum_delta <= 0:
        return 0.0, {}

    score += 40
    details["price_vs_vwap"] = f"{last_close:.2f} < VWAP {vwap:.2f}"

    # Vol Surge
    details["vol_surge"] = f"{vol_surge:.1f}×"
    if   vol_surge >= VOL_SURGE_STRONG: score += 20
    elif vol_surge >= VOL_SURGE_MIN:    score += 10
    else:                               score -= 15  # Conviction yếu

    # Band Zone — mua ở vùng oversold = tốt hơn
    zone = _band_zone(snap)
    details["band_zone"] = zone
    if   zone == "BELOW_2SD": score += 15
    elif zone == "BELOW_1SD": score += 10

    # Delta Sequence
    if recent_candles:
        pos = sum(1 for c in recent_candles[-MIN_CANDLES_HA:]
                  if (c["buy_vol"] or 0) > (c["sell_vol"] or 0))
        if pos >= MIN_CANDLES_HA:
            score += 15
            details["delta_seq"] = f"{pos}/{MIN_CANDLES_HA} nến buy"

    # Delta Ratio
    dr = abs(cum_delta) / max(cum_vol, 1)
    details["delta_ratio_pct"] = round(dr * 100, 2)
    if dr > 0.01:
        score += 15

    # Gap to VWAP
    gap = (vwap - last_close) / vwap
    details["gap_to_vwap_pct"] = round(gap * 100, 2)
    if gap < 0.005:
        score += 15

    return min(score, 100.0), details


def score_vwap_reclaim(
    snap: dict, prev_snap: dict,
    vol_surge: float = 1.0, delta_reliable: bool = True
) -> tuple[float, dict]:
    """
    🚀 VWAP_RECLAIM: Giá cross VWAP từ dưới lên với volume surge.
    Tín hiệu BUY mạnh nhất — BẮT BUỘC phải có vol surge.

    Scoring v2:
      Base 50   : price cross VWAP
      Vol Surge : BẮT BUỘC ≥ 1.5× — không có vol = fake breakout (-30)
      Band Zone : +15 nếu giá vọt vào ABOVE_1SD hoặc ABOVE_2SD
      Cum Delta : +20/+10 nếu delta_reliable và dương
      Overshoot : +15 nếu vượt > 0.5%, +8 nếu > 0.2%
    """
    details = {}
    score   = 0.0

    if not prev_snap:
        return 0.0, {}

    cur_close  = snap["last_close"]
    cur_vwap   = snap["vwap"]
    prev_close = prev_snap["last_close"]
    prev_vwap  = prev_snap["vwap"]
    cum_delta  = snap["cum_delta"]
    cum_vol    = snap["cum_volume"]

    if cum_vol < MIN_CUM_VOL:
        return 0.0, {}

    if not (prev_close < prev_vwap and cur_close > cur_vwap):
        return 0.0, {}

    # Delta guard (chỉ áp dụng khi delta đáng tin)
    if delta_reliable and cum_delta < 0:
        return 0.0, {}  # Fake breakout: giá vượt nhưng bị bán mạnh

    score += 50
    details["vwap_cross"] = f"{prev_close:.2f} → {cur_close:.2f} (VWAP={cur_vwap:.2f})"

    # Vol Surge — BẮT BUỘC cho RECLAIM
    details["vol_surge"] = f"{vol_surge:.1f}×"
    if   vol_surge >= VOL_SURGE_STRONG: score += 25
    elif vol_surge >= VOL_SURGE_MIN:    score += 15
    else:                               score -= 30  # Breakout không vol = rất nguy hiểm

    # Band Zone — breakout vào vùng premium càng tốt
    zone = _band_zone(snap)
    details["band_zone"] = zone
    if zone in ("ABOVE_1SD", "ABOVE_2SD"):
        score += 15

    # Cum Delta (chỉ dùng nếu delta đáng tin)
    if delta_reliable and cum_delta > 0:
        dr = cum_delta / max(cum_vol, 1)
        if dr > 0.01:
            score += 20
            details["cum_delta"] = f"+{cum_delta:,} ({dr*100:.1f}% vol)"
        else:
            score += 10
            details["cum_delta"] = f"+{cum_delta:,}"

    # Overshoot
    overshoot = (cur_close - cur_vwap) / cur_vwap
    details["overshoot_pct"] = round(overshoot * 100, 2)
    if   overshoot > 0.005: score += 15
    elif overshoot > 0.002: score += 8

    return min(score, 100.0), details


def score_delta_divergence(
    snap: dict, recent_candles: list,
    vol_surge: float = 1.0, delta_reliable: bool = True
) -> tuple[float, dict]:
    """
    📊 DELTA_DIVERGENCE: Giá ↘ nhưng delta ↗ — cá mập đỡ hàng ngầm.
    Phụ thuộc hoàn toàn vào buy/sell data → tắt nếu MASVN down.

    Scoring v2:
      Base 50   : price_falling AND delta_rising
      Vol Surge : +10 nếu surge >= 1.5×
      Band Zone : +10 nếu BELOW_1SD / BELOW_2SD (diverge tại oversold)
      Cum Delta : +20 nếu overall dương
      Below VWAP: +20 nếu giá dưới VWAP
    """
    if not delta_reliable:
        return 0.0, {}  # Signal này chỉ có nghĩa khi delta chính xác

    details = {}
    score   = 0.0

    if len(recent_candles) < 4:
        return 0.0, {}

    cum_vol = snap["cum_volume"]
    if cum_vol < MIN_CUM_VOL:
        return 0.0, {}

    last_4  = recent_candles[-4:]
    closes  = [c["close"] for c in last_4 if c["close"]]
    if len(closes) < 3:
        return 0.0, {}

    price_falling = all(closes[i] < closes[i-1] for i in range(1, min(3, len(closes))))
    deltas        = [(c["buy_vol"] or 0) - (c["sell_vol"] or 0) for c in last_4]
    delta_rising  = sum(1 for d in deltas if d > 0) >= 3

    if not (price_falling and delta_rising):
        return 0.0, {}

    score += 50
    details["price_trend"]   = f"↘ {closes[0]:.2f} → {closes[-1]:.2f}"
    details["delta_signals"] = f"{sum(1 for d in deltas if d > 0)}/4 nến buy pressure"

    # Vol Surge
    details["vol_surge"] = f"{vol_surge:.1f}×"
    if vol_surge >= VOL_SURGE_MIN:
        score += 10

    # Band Zone — diverge tại vùng oversold = tín hiệu đáng tin hơn
    zone = _band_zone(snap)
    details["band_zone"] = zone
    if zone in ("BELOW_1SD", "BELOW_2SD"):
        score += 10

    if snap["cum_delta"] > 0:
        score += 20
        details["cum_delta"] = f"+{snap['cum_delta']:,}"

    if snap["last_close"] < snap["vwap"]:
        score += 20
        details["below_vwap"] = True

    return min(score, 100.0), details


def score_vwap_rejection(
    snap: dict, recent_candles: list,
    vol_surge: float = 1.0, delta_reliable: bool = True
) -> tuple[float, dict]:
    """
    🔴 VWAP_REJECTION: Giá ở trên VWAP nhưng bị đẩy xuống — áp lực bán.
    Khi delta không tin cậy: chỉ fire nếu vol_surge >= VOL_SURGE_STRONG.

    Scoring v2:
      Base 40   : price > VWAP và (cum_delta < 0 hoặc vol surge xác nhận)
      Vol Surge : +20 nếu >= STRONG, +10 nếu >= MIN
      Band Zone : +15 nếu reject từ ABOVE_1SD / ABOVE_2SD
      Sell Seq  : +20 nếu 3 nến liên tục có sell pressure
      Delta Ratio: +20 nếu delta_reliable và |ratio| > 1%
      Near VWAP : +15 nếu giá áp sát VWAP từ trên (< 0.5%)
    """
    details = {}
    score   = 0.0

    vwap       = snap["vwap"]
    last_close = snap["last_close"]
    cum_delta  = snap["cum_delta"]
    cum_vol    = snap["cum_volume"]

    if cum_vol < MIN_CUM_VOL:
        return 0.0, {}
    if last_close <= vwap:
        return 0.0, {}

    if delta_reliable:
        if cum_delta >= 0:
            return 0.0, {}  # Giá trên VWAP + delta dương → không phải rejection
        # Guard bổ sung: rejection phải có ít nhất vol bình thường
        # vol_surge = 0 = nến trắng / không có giao dịch → bỏ qua
        if vol_surge < 0.3:
            return 0.0, {}
        details["cum_delta"] = f"{cum_delta:,} (NET SELL)"
    else:
        # Không có delta → bù bằng vol surge mạnh
        if vol_surge < VOL_SURGE_STRONG:
            return 0.0, {}
        details["note"] = "Delta unreliable — vol surge confirmation"

    score += 40
    details["price_vs_vwap"] = f"{last_close:.2f} > VWAP {vwap:.2f}"

    # Vol Surge
    details["vol_surge"] = f"{vol_surge:.1f}×"
    if   vol_surge >= VOL_SURGE_STRONG: score += 20
    elif vol_surge >= VOL_SURGE_MIN:    score += 10

    # Band Zone — bị reject từ vùng premium càng nguy hiểm
    zone = _band_zone(snap)
    details["band_zone"] = zone
    if zone in ("ABOVE_1SD", "ABOVE_2SD"):
        score += 15

    # Sell Sequence
    if recent_candles:
        sell_seq = sum(1 for c in recent_candles[-MIN_CANDLES_HA:]
                       if (c["sell_vol"] or 0) > (c["buy_vol"] or 0))
        if sell_seq >= MIN_CANDLES_HA:
            score += 20
            details["sell_seq"] = f"{sell_seq}/{MIN_CANDLES_HA} nến sell"

    # Delta Ratio (chỉ khi tin cậy)
    if delta_reliable:
        dr = abs(cum_delta) / max(cum_vol, 1)
        if dr > 0.01:
            score += 20
            details["delta_ratio_pct"] = round(dr * 100, 2)

    # Near VWAP
    gap = (last_close - vwap) / vwap
    if gap < 0.005:
        score += 15
        details["near_vwap_rejection"] = True

    return min(score, 100.0), details


def score_pvwap_support_test(
    snap: dict, pvwap_data: dict,
    recent_candles: list,
    vol_surge: float = 1.0, delta_reliable: bool = True
) -> tuple[float, dict]:
    """
    🎯 PVWAP_SUPPORT_TEST: Giá test VWAP phên hôm qua rồi bật lên.

    Lý thuyết quản lý quỹ:
      PVWAP = benchmark của fund manager ngày hôm qua.
      Nếu giá hôm nay test PVWAP từ trên xuống và bật lên:
      → Fund manager đố giá tại PVWAP (bảo vệ vị thế tích lũy hôm qua)
      → Tín hiệu mua mạnh nhất cho swing trading

    Scoring:
      Base 40   : close hiện tại > PVWAP và recent candle đã test PVWAP
      Vol Surge : BUY kèm vol surge tại điểm test = xác nhận hấp thụ
      Delta     : +20 nếu cum_delta dương (fund đang mua thất)
      Overshoot : +15 nếu đã vào vật trên PVWAP rõ rệt
      Band Zone : +10 nếu PVWAP nằm trong dải ±1σ ngày hôm nay
    """
    if not pvwap_data or len(recent_candles) < 3:
        return 0.0, {}

    pvwap      = pvwap_data["vwap"]
    last_close = snap["last_close"]
    cum_vol    = snap["cum_volume"]

    if cum_vol < MIN_CUM_VOL:
        return 0.0, {}

    # Điều kiện cốt lõi: close hiện tại TRÊN PVWAP
    if last_close <= pvwap:
        return 0.0, {}

    # Kiểm tra recent candles có test PVWAP không
    closes = [c["close"] or 0.0 for c in recent_candles]
    pvwap_tested = any(
        (pvwap * 0.995) <= c <= (pvwap * 1.005)
        for c in closes if c > 0
    )
    was_below_pvwap = any(c < pvwap for c in closes if c > 0)

    if not (pvwap_tested or was_below_pvwap):
        return 0.0, {}

    score = 40
    details = {
        "pvwap"       : f"{pvwap:.2f}",
        "current"     : f"{last_close:.2f}",
        "pvwap_date"  : pvwap_data.get("trade_date", "-"),
    }
    if pvwap_tested:
        details["pattern"] = "BOUNCE (chạm PVWAP rồi bật)"
    else:
        details["pattern"] = "RECOVERY (từ dưới PVWAP lên)"

    # Vol Surge — cần có vol để xác nhận absorption
    details["vol_surge"] = f"{vol_surge:.1f}×"
    if   vol_surge >= VOL_SURGE_STRONG: score += 25
    elif vol_surge >= VOL_SURGE_MIN:    score += 15
    else:                               score -= 10

    # Cum Delta
    if delta_reliable and snap["cum_delta"] > 0:
        score += 20
        details["cum_delta"] = f"+{snap['cum_delta']:,}"

    # Overshoot: giá đã vượt PVWAP rõ (không phải micro-cross)
    overshoot = (last_close - pvwap) / pvwap
    details["overshoot_pct"] = round(overshoot * 100, 2)
    if   overshoot > 0.008: score += 15
    elif overshoot > 0.003: score += 8

    # Band Zone kiểm tra PVWAP nằm trong dải hiện tại
    zone = _band_zone(snap)
    details["band_zone"] = zone
    if zone in ("ABOVE_VWAP", "ABOVE_1SD"):
        score += 10  # PVWAP xấp xỉ VWAP ngày = vùng cân bằng mạnh

    return min(score, 100.0), details


def score_vwap_bounce(
    snap: dict, recent_candles: list,
    vol_surge: float = 1.0
) -> tuple[float, dict]:
    """
    🔁 VWAP_BOUNCE: Giá test VWAP intraday nhiều lần từ dưới lên.
    Mỗi lần giá bật khỏi VWAP = fund manager đang bảo vệ giá.
    Không phụ thuộc delta → luôn chạy (kể cả khi MASVN down).

    Scoring:
      Base 30   : 1 lần bounce
      +15/bounce : mỗi lần bật thêm (tối đa +30)
      Vol Surge : +10 nếu surge >= 1.5×
      Cum Delta : +20 nếu dương (test và bật lên thật)
      Near VWAP : +10 nếu giá hiện tại ở gần VWAP (setup cho breakout)
    """
    if len(recent_candles) < 4:
        return 0.0, {}

    vwap       = snap["vwap"]
    last_close = snap["last_close"]
    cum_vol    = snap["cum_volume"]

    if cum_vol < MIN_CUM_VOL:
        return 0.0, {}

    # Giá hiện tại phải ở gần hoặc trên VWAP (không quá xa)
    if last_close > vwap * 1.015:  # Đã bứt khỏi xa, không phải bounce context
        return 0.0, {}
    if last_close < vwap * 0.98:   # Quá thấp, chưa recover
        return 0.0, {}

    # Đếm số lần bật từ dưới VWAP lên
    bounces    = 0
    was_below  = False
    closes     = [c["close"] or 0.0 for c in recent_candles]

    for c in closes:
        if c <= 0:
            continue
        if c < vwap * 0.998:
            was_below = True
        elif was_below and c >= vwap * 0.998:
            bounces  += 1
            was_below = False

    if bounces < 1:
        return 0.0, {}

    score = 30 + min(bounces * 15, 30)
    details = {
        "vwap"    : f"{vwap:.2f}",
        "bounces" : bounces,
        "vol_surge": f"{vol_surge:.1f}×",
    }

    if vol_surge >= VOL_SURGE_MIN:
        score += 10

    if snap["cum_delta"] > 0:
        score += 20
        details["cum_delta"] = f"+{snap['cum_delta']:,}"

    gap = abs(last_close - vwap) / vwap
    if gap < 0.003:
        score += 10
        details["near_vwap"] = True

    return min(score, 100.0), details


PT_RATIO_MIN     = 0.10   # 10% — pt_vol phải chiếm ≥ 10% tổng vol mới có ý nghĩa
PT_RATIO_STRONG  = 0.25   # 25% — tín hiệu block trade rất mạnh


def score_pt_accumulation(
    snap: dict, delta_reliable: bool = True
) -> tuple[float, dict]:
    """
    🏦 PT_ACCUMULATION: Tổ chức mua thỏa thuận với giá cao hơn VWAP.
    avg_pt_price > vwap → premium bỏ ra = sẵn sàng trả giá cao để tích lũy.

    Scoring:
      Base 50   : avg_pt_price > vwap AND pt_ratio >= PT_RATIO_MIN
      Premium   : +20 nếu premium > 1%, +10 nếu > 0.3%
      PT Ratio  : +15 nếu pt_ratio >= PT_RATIO_STRONG (≥ 25%)
      Cum Delta : +15 nếu delta_reliable và dương (lệnh khớp cũng mua)
    """
    pt_vol      = snap.get("pt_vol", 0)
    avg_pt      = snap.get("avg_pt_price", 0.0)
    pt_ratio    = snap.get("pt_ratio", 0.0)
    vwap        = snap["vwap"]
    cum_vol     = snap["cum_volume"]

    if pt_vol == 0 or avg_pt <= 0 or pt_ratio < PT_RATIO_MIN:
        return 0.0, {}
    if avg_pt <= vwap:
        return 0.0, {}  # Không phải premium
    if cum_vol < MIN_CUM_VOL:
        return 0.0, {}

    premium = (avg_pt - vwap) / vwap
    details = {
        "avg_pt_price": f"{avg_pt:.3f}",
        "vwap"        : f"{vwap:.3f}",
        "premium_pct" : round(premium * 100, 2),
        "pt_vol"      : f"{pt_vol:,}",
        "pt_ratio_pct": round(pt_ratio * 100, 1),
    }

    score = 50.0
    if   premium > 0.01: score += 20
    elif premium > 0.003: score += 10

    if pt_ratio >= PT_RATIO_STRONG:
        score += 15
        details["strong_block"] = True

    if delta_reliable and snap["cum_delta"] > 0:
        score += 15
        details["cum_delta"] = f"+{snap['cum_delta']:,}"

    return min(score, 100.0), details


def score_pt_dumping(snap: dict) -> tuple[float, dict]:
    """
    🏚️ PT_DUMPING: Tổ chức xả thỏa thuận với giá thấp hơn VWAP.
    avg_pt_price < vwap → chấp nhận discount để thoát hàng nhanh.

    Scoring:
      Base 50   : avg_pt_price < vwap AND pt_ratio >= PT_RATIO_MIN
      Discount  : +20 nếu discount > 1%, +10 nếu > 0.3%
      PT Ratio  : +15 nếu pt_ratio >= PT_RATIO_STRONG
      Above VWAP: +15 nếu giá khớp lệnh vẫn trên VWAP (xả nhưng che giấu)
    """
    pt_vol   = snap.get("pt_vol", 0)
    avg_pt   = snap.get("avg_pt_price", 0.0)
    pt_ratio = snap.get("pt_ratio", 0.0)
    vwap     = snap["vwap"]
    cum_vol  = snap["cum_volume"]

    if pt_vol == 0 or avg_pt <= 0 or pt_ratio < PT_RATIO_MIN:
        return 0.0, {}
    if avg_pt >= vwap:
        return 0.0, {}  # Không phải discount
    if cum_vol < MIN_CUM_VOL:
        return 0.0, {}

    discount = (vwap - avg_pt) / vwap
    details  = {
        "avg_pt_price": f"{avg_pt:.3f}",
        "vwap"        : f"{vwap:.3f}",
        "discount_pct": round(discount * 100, 2),
        "pt_vol"      : f"{pt_vol:,}",
        "pt_ratio_pct": round(pt_ratio * 100, 1),
    }

    score = 50.0
    if   discount > 0.01:  score += 20
    elif discount > 0.003: score += 10

    if pt_ratio >= PT_RATIO_STRONG:
        score += 15
        details["strong_block"] = True

    # Giá khớp lệnh còn trên VWAP nhưng thỏa thuận dưới → cá mập che giấu bán
    if snap["last_close"] > vwap:
        score += 15
        details["hidden_dump"] = "Giá lệnh trên VWAP nhưng PT dưới — dump ẩn"

    return min(score, 100.0), details


# ============================================================
# MAIN SCANNER
# ============================================================

class WhaleHunter:
    def __init__(self, db_path: str):
        self.db_path    = str(db_path)
        self.vwap_eng   = VWAPEngine(self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, detect_types=0)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_recent_candles(
        self, conn: sqlite3.Connection, security_id: int, n: int = 10
    ) -> list:
        """Lấy n nến 1m gần nhất của một mã trong phiên hôm nay."""
        today_vn = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        rows = conn.execute(
            """
            SELECT trade_time, close, volume, buy_vol, sell_vol,
                   COALESCE(buy_vol,0) - COALESCE(sell_vol,0) as delta
            FROM stock_prices
            WHERE security_id = ? AND interval = '1m'
              AND date(trade_time) = ?
            ORDER BY trade_time DESC LIMIT ?
            """,
            (security_id, today_vn, n),
        ).fetchall()
        return list(reversed(rows))  # Trả về theo thứ tự thời gian tăng dần

    def _get_prev_vwap(
        self, conn: sqlite3.Connection, security_id: int, current_snap_time: str
    ) -> dict | None:
        """Lấy VWAP snapshot ngay trước snapshot hiện tại."""
        row = conn.execute(
            """
            SELECT * FROM vwap_snapshots
            WHERE security_id = ? AND snapshot_time < ?
            ORDER BY snapshot_time DESC LIMIT 1
            """,
            (security_id, current_snap_time),
        ).fetchone()
        return dict(row) if row else None

    def _get_pvwap(
        self, conn: sqlite3.Connection, security_id: int
    ) -> dict | None:
        """
        Lấy VWAP phên HÔM QUA từ daily_vwap_summary.
        Đây là PVWAP anchor — benchmark của fund manager.
        """
        today = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        row = conn.execute("""
            SELECT vwap, vwap_upper1, vwap_lower1, vwap_upper2, vwap_lower2,
                   cum_delta, buy_vol, sell_vol, side_cov_pct, trade_date
            FROM   daily_vwap_summary
            WHERE  security_id = ? AND trade_date < ?
            ORDER  BY trade_date DESC LIMIT 1
        """, (security_id, today)).fetchone()
        return dict(row) if row else None

    def _live_side_quality(self, conn: sqlite3.Connection) -> float | None:
        """
        Tính live side_coverage_pct từ nến 1m hôm nay.
        Dùng để quyết định delta có đáng tin không.
        Returns: float % hoặc None nếu chưa có data.
        """
        today_vn = datetime.now(VN_TZ).strftime("%Y-%m-%d")
        row = conn.execute("""
            SELECT
                SUM(COALESCE(volume, 0))                           AS total_vol,
                SUM(COALESCE(buy_vol,0) + COALESCE(sell_vol,0))   AS side_vol
            FROM stock_prices
            WHERE interval = '1m'
              AND date(trade_time) = ?
              AND volume > 0
        """, (today_vn,)).fetchone()
        if row and row[0] and row[0] > 0:
            return round(row[1] * 100.0 / row[0], 1)
        return None

    def _save_signal(
        self, conn: sqlite3.Connection,
        security_id: int, signal_type: str, direction: str,
        score: float, snap: dict, details: dict
    ):
        """Lưu signal vào bảng whale_signals (bỏ qua duplicate trong 15 phút)."""
        # Chống trùng lặp: nếu đã có signal cùng loại trong 15 phút → bỏ qua
        existing = conn.execute(
            """
            SELECT id FROM whale_signals
            WHERE security_id = ? AND signal_type = ?
              AND signal_time >= datetime(?, '-15 minutes')
            LIMIT 1
            """,
            (security_id, signal_type, snap["snapshot_time"]),
        ).fetchone()
        if existing:
            return False

        conn.execute(
            """
            INSERT INTO whale_signals
                (security_id, signal_time, signal_type, direction, score,
                 price, vwap, cum_delta, vol_ratio, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                security_id, snap["snapshot_time"], signal_type, direction,
                round(score, 1),
                snap["last_close"], snap["vwap"], snap["cum_delta"],
                None,  # vol_ratio — Phase 2 mở rộng
                json.dumps(details, ensure_ascii=False),
            ),
        )
        return True

    def run_once(self, top_n: int = TOP_N) -> list[dict]:
        """
        Chạy một vòng quét:
        1. Tính/cập nhật VWAP cho top_n mã
        2. Quét 4 loại signal
        3. Lưu vào DB
        4. Trả về danh sách signal tìm được
        """
        logger.info(f"🔍 Bắt đầu quét whale signals (top_n={top_n})...")

        # BƯỚC 1: Cập nhật VWAP snapshots
        snapshots = self.vwap_eng.compute_all(top_n=top_n)
        if not snapshots:
            logger.warning("Không có VWAP snapshot — bỏ qua lần quét này.")
            return []

        logger.info(f"   VWAP đã tính: {len(snapshots)} mã")

        conn    = self._get_conn()
        signals = []

        # ── Side Quality Gate ────────────────────────────────────
        side_cov       = self._live_side_quality(conn)
        delta_reliable = (side_cov is None) or (side_cov >= SIDE_QUALITY_GATE)

        if not delta_reliable:
            logger.warning(
                f"⚠️  SIDE QUALITY GATE: {side_cov:.1f}% < {SIDE_QUALITY_GATE}% "
                f"— MASVN có thể đang down! "
                f"Tín hiệu delta bị vô hiệu. Chỉ chạy price-based signals."
            )
        elif side_cov:
            logger.info(f"✅ Side Quality: {side_cov:.1f}% — delta đáng tin")

        # Map security_id → snapshot để tra nhanh
        snap_map = {s.security_id: {
            "snapshot_time": s.snapshot_time,
            "vwap"         : s.vwap,
            "vwap_upper1"  : s.vwap_upper1,
            "vwap_lower1"  : s.vwap_lower1,
            "vwap_upper2"  : s.vwap_upper2,
            "vwap_lower2"  : s.vwap_lower2,
            "cum_volume"   : s.cum_volume,
            "cum_delta"    : s.cum_delta,
            "last_close"   : s.last_close,
            # Put-through fields
            "pt_vol"       : s.pt_vol,
            "avg_pt_price" : s.avg_pt_price,
            "pt_ratio"     : s.pt_ratio,
        } for s in snapshots}

        # BƯỚC 2: Quét signal cho từng mã
        for snap_obj in snapshots:
            sid    = snap_obj.security_id
            snap   = snap_map[sid]
            recent = self._get_recent_candles(conn, sid, n=10)

            # Tính Vol Surge từ recent candles
            vol_surge = _compute_vol_surge(recent)

            found_signals = []

            # Signal 1: HIDDEN_ACCUMULATION
            s1, d1 = score_hidden_accumulation(snap, recent, vol_surge, delta_reliable)
            if s1 >= MIN_SCORE:
                found_signals.append(("HIDDEN_ACCUMULATION", "BUY", s1, d1))

            # Signal 2: VWAP_RECLAIM
            prev_snap = self._get_prev_vwap(conn, sid, snap["snapshot_time"])
            s2, d2    = score_vwap_reclaim(snap, prev_snap, vol_surge, delta_reliable)
            has_reclaim = s2 >= MIN_SCORE

            # Signal 3: DELTA_DIVERGENCE
            s3, d3 = score_delta_divergence(snap, recent, vol_surge, delta_reliable)
            if s3 >= MIN_SCORE:
                found_signals.append(("DELTA_DIVERGENCE", "BUY", s3, d3))

            # Signal 4: VWAP_REJECTION
            s4, d4      = score_vwap_rejection(snap, recent, vol_surge, delta_reliable)
            has_rejection = s4 >= MIN_SCORE

            # ⛔ MUTUAL EXCLUSION: cùng lúc có RECLAIM + REJECTION = vùng tranh chấp
            # Chỉ giữ tín hiệu mạnh hơn
            if has_reclaim and has_rejection:
                if s2 > s4:
                    found_signals.append(("VWAP_RECLAIM", "BUY", s2, {**d2, "note": "Winner vs REJECTION"}))
                else:
                    found_signals.append(("VWAP_REJECTION", "SELL", s4, {**d4, "note": "Winner vs RECLAIM"}))
            elif has_reclaim:
                found_signals.append(("VWAP_RECLAIM", "BUY", s2, d2))
            elif has_rejection:
                found_signals.append(("VWAP_REJECTION", "SELL", s4, d4))

            # Signal 5: PVWAP_SUPPORT_TEST (đa phên — dùng daily_vwap_summary)
            pvwap_data = self._get_pvwap(conn, sid)
            s5, d5 = score_pvwap_support_test(snap, pvwap_data, recent, vol_surge, delta_reliable)
            if s5 >= MIN_SCORE:
                found_signals.append(("PVWAP_SUPPORT_TEST", "BUY", s5, d5))

            # Signal 6: VWAP_BOUNCE (intraday — không phụ thuộc delta)
            s6, d6 = score_vwap_bounce(snap, recent, vol_surge)
            if s6 >= MIN_SCORE:
                found_signals.append(("VWAP_BOUNCE", "BUY", s6, d6))

            # Signal 7: PT_ACCUMULATION (thỏa thuận premium > VWAP)
            s7, d7 = score_pt_accumulation(snap, delta_reliable)
            if s7 >= MIN_SCORE:
                found_signals.append(("PT_ACCUMULATION", "BUY", s7, d7))

            # Signal 8: PT_DUMPING (thỏa thuận discount < VWAP)
            s8, d8 = score_pt_dumping(snap)
            if s8 >= MIN_SCORE:
                found_signals.append(("PT_DUMPING", "SELL", s8, d8))

            # Lưu và thu thập
            for sig_type, direction, score, details in found_signals:
                saved = self._save_signal(
                    conn, sid, sig_type, direction, score, snap, details
                )
                if saved:
                    signals.append({
                        "security_id": sid,
                        "signal_type": sig_type,
                        "direction"  : direction,
                        "score"      : score,
                        "snap"       : snap,
                        "details"    : details,
                    })

        conn.commit()
        conn.close()

        # BƯỚC 3: In báo cáo
        self._print_report(signals)
        return signals

    def _print_report(self, signals: list[dict]):
        """In báo cáo signal ra console."""
        if not signals:
            logger.info("✅ Không tìm thấy signal đáng chú ý lần quét này.")
            return

        now_vn = datetime.now(VN_TZ).strftime("%H:%M:%S")
        logger.info(f"\n{'='*60}")
        logger.info(f"🐋 WHALE SIGNAL REPORT — {now_vn}")
        logger.info(f"   Tổng: {len(signals)} tín hiệu | Threshold: {MIN_SCORE}/100")
        logger.info(f"{'='*60}")

        # Lấy tên symbol cho các security_id
        conn = self._get_conn()
        ids  = list({s["security_id"] for s in signals})
        ph   = ",".join("?" * len(ids))
        sym_map = {
            r["security_id"]: r["symbol"]
            for r in conn.execute(
                f"SELECT security_id, symbol FROM securities WHERE security_id IN ({ph})",
                ids,
            ).fetchall()
        }
        conn.close()

        ICONS = {
            "HIDDEN_ACCUMULATION": "🐋",
            "VWAP_RECLAIM"       : "🚀",
            "DELTA_DIVERGENCE"   : "📊",
            "VWAP_REJECTION"     : "🔴",
            "PVWAP_SUPPORT_TEST" : "🎯",
            "VWAP_BOUNCE"        : "🔁",
            "PT_ACCUMULATION"    : "🏦",
            "PT_DUMPING"         : "🏚️",
        }

        for sig in sorted(signals, key=lambda x: -x["score"]):
            sym    = sym_map.get(sig["security_id"], f"ID={sig['security_id']}")
            icon   = ICONS.get(sig["signal_type"], "⚡")
            snap   = sig["snap"]
            d      = sig["details"]
            arrow  = "⬆️ BUY" if sig["direction"] == "BUY" else "⬇️ SELL"

            logger.info(
                f"{icon} {sym:6s} | {sig['signal_type']:<22s} | "
                f"Score={sig['score']:.0f} | {arrow} | "
                f"Price={snap['last_close']:.2f} VWAP={snap['vwap']:.2f} | "
                f"ΔCum={snap['cum_delta']:+,}"
            )
            if d:
                logger.info(f"         Details: {d}")

        logger.info(f"{'='*60}\n")

        # ── Telegram: gửi TẤT CẢ signal (score >= MIN_SCORE=70) ──
        if signals:
            _tg_alerter.send(signals, sym_map)

        # ── Email: chỉ gửi signal mạnh (score >= 80) để tránh spam ──
        high_score = [s for s in signals if s["score"] >= EMAIL_SCORE_THRESHOLD]
        if high_score:
            _email_alerter.send(high_score, sym_map)


# ============================================================
# ENTRY POINT
# ============================================================

def is_market_hours() -> bool:
    """Kiểm tra có đang trong giờ giao dịch VN không."""
    now_vn = datetime.now(VN_TZ)
    t = (now_vn.hour, now_vn.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE


async def run_loop():
    """Chạy WhaleHunter tự động mỗi SCAN_INTERVAL giây trong giờ giao dịch."""
    hunter = WhaleHunter(DB_PATH)
    logger.info(f"🔄 Whale Hunter LOOP mode — scan mỗi {SCAN_INTERVAL//60} phút")
    while True:
        if is_market_hours():
            hunter.run_once()
        else:
            logger.info("⏸  Ngoài giờ giao dịch — đang chờ...")
        await asyncio.sleep(SCAN_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="VWAP Whale Hunter")
    parser.add_argument("--loop",     action="store_true", help="Chạy loop tự động")
    parser.add_argument("--top",      type=int, default=TOP_N, help="Số mã quét")
    parser.add_argument("--min-score",type=float, default=MIN_SCORE)
    args = parser.parse_args()

    min_score = args.min_score

    if args.loop:
        asyncio.run(run_loop())
    else:
        hunter = WhaleHunter(DB_PATH)
        hunter.min_score = min_score
        hunter.run_once(top_n=args.top)


if __name__ == "__main__":
    main()
