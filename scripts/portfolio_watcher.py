#!/usr/bin/env python3
"""
scripts/portfolio_watcher.py
==============================================
Daemon theo dõi danh mục (watchlist) — intraday + end-of-day.

Các mode:
  one-shot  : python3 scripts/portfolio_watcher.py
  monitor   : python3 scripts/portfolio_watcher.py --monitor      ← REAL-TIME trong phiên
  daemon    : python3 scripts/portfolio_watcher.py --daemon        ← EOD 14:50 mỗi ngày

--monitor (khuyến nghị dùng trong phiên):
  • Chạy vòng lặp mỗi INTERVAL_SEC (mặc định 3 phút)
  • Impute ngay bất kỳ nến 1m nào bị miss side (commit)
  • In live VWAP dashboard cho toàn watchlist
  • Tự động trigger EOD pipeline (ATC + rebuild) khi 14:50
"""

import os
import sys
import time
import sqlite3
import logging
import argparse
import asyncio
import aiohttp
import numpy as np

from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _load_env():
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_env()

DB_PATH  = Path(os.environ.get("SMD_DB_PATH", PROJECT_ROOT / "data" / "securities_master.db"))
VN_TZ    = timezone(timedelta(hours=7))
DNSE_URL = "https://services.entrade.com.vn/chart-api/v2/ohlcs/stock"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [PortfolioWatcher] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================
# WATCHLIST — Danh mục cổ phiếu đang nắm giữ
# ============================================================
WATCHLIST = [
    "HPG",
    "SHB",
    "MBB",
    "ACB",
    "VND",
    "SSI",
    "POW",
    "VRE",
    "PSI",
    "NKG",
]


# ============================================================
# BƯỚC 1: IMPUTE — Vá side cho nến 1m missing
# ============================================================
def _estimate_side(open_: float, high: float, low: float,
                   close: float, volume: int) -> tuple[int, int]:
    """Volume Position Method (Easley et al. 2012)."""
    rng = high - low
    if rng < 1e-6 or volume <= 0:
        if close >= open_:
            return volume, 0
        else:
            return 0, volume
    buy_frac = max(0.0, min(1.0, (close - low) / rng))
    buy_vol  = round(volume * buy_frac)
    return int(buy_vol), int(volume - buy_vol)


def run_impute_watchlist(conn: sqlite3.Connection, date_str: str) -> dict:
    """
    Impute buy_vol/sell_vol cho 10 mã watchlist, chỉ ngày hôm nay.
    Commit ngay (không cần flag riêng).
    """
    # Lấy security_id của watchlist
    placeholders = ",".join("?" * len(WATCHLIST))
    sec_rows = conn.execute(
        f"SELECT security_id, symbol FROM securities WHERE symbol IN ({placeholders})",
        WATCHLIST
    ).fetchall()
    sid_map = {r[0]: r[1] for r in sec_rows}
    sid_list = list(sid_map.keys())

    if not sid_list:
        logger.warning("Không tìm thấy mã nào trong watchlist!")
        return {}

    sid_ph = ",".join("?" * len(sid_list))

    # Tìm nến cần impute: volume > 0, buy_vol = 0, ngày hôm nay
    rows = conn.execute(f"""
        SELECT rowid, security_id, open, high, low, close, volume
        FROM   stock_prices
        WHERE  security_id IN ({sid_ph})
          AND  interval = '1m'
          AND  date(trade_time) = ?
          AND  volume > 0
          AND  (buy_vol IS NULL OR buy_vol = 0)
          AND  (sell_vol IS NULL OR sell_vol = 0)
        ORDER  BY security_id, trade_time
    """, sid_list + [date_str]).fetchall()

    if not rows:
        logger.info(f"  [{date_str}] Watchlist: không có nến 1m nào cần impute ✅")
        return {sym: 0 for sym in WATCHLIST}

    logger.info(f"  [{date_str}] Tìm thấy {len(rows):,} nến 1m cần impute trong watchlist")

    updates   = []
    per_sym   = {}
    for r in rows:
        rowid, sid, o, h, l, c, v = r
        if None in (o, h, l, c) or v <= 0:
            continue
        bv, sv = _estimate_side(o, h, l, c, v)
        updates.append((bv, sv, bv - sv, rowid))
        sym = sid_map.get(sid, "?")
        per_sym[sym] = per_sym.get(sym, 0) + 1

    # Batch commit
    BATCH = 2000
    for i in range(0, len(updates), BATCH):
        conn.executemany("""
            UPDATE stock_prices
            SET    buy_vol  = ?,
                   sell_vol = ?,
                   delta    = ?
            WHERE  rowid    = ?
        """, updates[i:i + BATCH])
        conn.commit()

    for sym, cnt in sorted(per_sym.items()):
        logger.info(f"    ✅ {sym}: imputed {cnt} nến")

    logger.info(f"  Tổng imputed: {len(updates):,} nến 1m ✅")
    return per_sym


# ============================================================
# BƯỚC 2: ATC — Fetch giá đóng cửa chính thức từ DNSE
# ============================================================
async def _fetch_atc_one(session: aiohttp.ClientSession, symbol: str,
                          date_str: str) -> dict | None:
    """
    Fetch nến 1D từ DNSE cho 1 mã.
    Thử resolution='D' trước; nếu null (chưa settle) → tổng hợp từ 1m cuối ngày.
    """
    d       = datetime.strptime(date_str, "%Y-%m-%d")
    from_ts = int(datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=VN_TZ).timestamp()) - 86400
    to_ts   = int(datetime(d.year, d.month, d.day, 17, 0, 0, tzinfo=VN_TZ).timestamp())

    headers = {"User-Agent": "Mozilla/5.0"}

    # Thử D resolution trước
    try:
        params = {"symbol": symbol, "from": from_ts, "to": to_ts, "resolution": "D"}
        async with session.get(DNSE_URL, params=params, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                data = await r.json()
                times = data.get("t") or []
                for i, t in enumerate(times):
                    dt_vn = datetime.fromtimestamp(t, tz=VN_TZ).strftime("%Y-%m-%d")
                    if dt_vn == date_str:
                        return {
                            "open":   data["o"][i],
                            "high":   data["h"][i],
                            "low":    data["l"][i],
                            "close":  data["c"][i],
                            "volume": int(data["v"][i]),
                        }
    except Exception:
        pass

    # Fallback: tổng hợp từ nến 1m của ngày (dùng khi D chưa available)
    try:
        params = {"symbol": symbol, "from": from_ts, "to": to_ts, "resolution": "1"}
        async with session.get(DNSE_URL, params=params, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return None
            data = await r.json()
            times   = data.get("t") or []
            opens1  = data.get("o") or []
            highs1  = data.get("h") or []
            lows1   = data.get("l") or []
            closes1 = data.get("c") or []
            vols1   = data.get("v") or []

            # Lọc nến đúng ngày
            day_idx = [
                i for i, t in enumerate(times)
                if datetime.fromtimestamp(t, tz=VN_TZ).strftime("%Y-%m-%d") == date_str
            ]
            if not day_idx:
                return None

            o_1d = opens1[day_idx[0]]
            h_1d = max(highs1[i] for i in day_idx)
            l_1d = min(lows1[i]  for i in day_idx)
            c_1d = closes1[day_idx[-1]]
            v_1d = int(sum(vols1[i] for i in day_idx))

            return {"open": o_1d, "high": h_1d, "low": l_1d, "close": c_1d, "volume": v_1d}
    except Exception as e:
        logger.warning(f"  [{symbol}] ATC fetch error: {e}")
    return None


async def fetch_atc_all(symbols: list, date_str: str) -> dict:
    """Fetch ATC song song cho toàn bộ watchlist."""
    async with aiohttp.ClientSession(
        headers={"User-Agent": "Mozilla/5.0"},
        connector=aiohttp.TCPConnector(limit=20)
    ) as session:
        tasks = {sym: _fetch_atc_one(session, sym, date_str) for sym in symbols}
        results = {}
        for sym, coro in tasks.items():
            results[sym] = await coro
        return results


def upsert_atc(conn: sqlite3.Connection, date_str: str,
               atc_data: dict, sid_map: dict) -> int:
    """
    Ghi đè nến 1D từ DNSE vào stock_prices.
    Chỉ cập nhật OHLCV, không đụng buy_vol/sell_vol.
    """
    trade_time = f"{date_str}T00:00:00"  # chuẩn hóa
    updates = 0
    for sym, data in atc_data.items():
        if not data:
            logger.warning(f"  [{sym}] ATC: chưa có data từ DNSE")
            continue
        sid = sid_map.get(sym)
        if not sid:
            continue
        conn.execute("""
            INSERT INTO stock_prices
                (security_id, interval, trade_time, open, high, low, close, volume,
                 buy_vol, sell_vol, delta)
            VALUES (?, '1D', ?, ?, ?, ?, ?, ?, 0, 0, 0)
            ON CONFLICT(security_id, interval, trade_time) DO UPDATE SET
                open   = excluded.open,
                high   = excluded.high,
                low    = excluded.low,
                close  = excluded.close,
                volume = excluded.volume
        """, (sid, trade_time,
              data["open"], data["high"], data["low"], data["close"], data["volume"]))
        logger.info(
            f"  ✅ {sym} ATC: C={data['close']:.2f}  Vol={data['volume']/1e6:.2f}M"
        )
        updates += 1
    conn.commit()
    return updates


# ============================================================
# VWAP SLOPE ENGINE
# Phân tích xu hướng VWAP theo 3 khung: 1M (22d) / 1W (5d) / Intraday
# ============================================================

def _linreg_slope_pct(values: list) -> tuple:
    """
    OLS linear regression → slope chuẩn hóa (% per period) + R².
    Returns (slope_pct, r2).
    """
    n = len(values)
    if n < 3:
        return 0.0, 0.0
    x = np.arange(n, dtype=float)
    y = np.array(values, dtype=float)
    x_m, y_m = x.mean(), y.mean()
    ss_xx = ((x - x_m) ** 2).sum()
    ss_xy = ((x - x_m) * (y - y_m)).sum()
    ss_yy = ((y - y_m) ** 2).sum()
    if ss_xx == 0 or y_m == 0:
        return 0.0, 0.0
    slope_pct = (ss_xy / ss_xx) / y_m * 100
    r2 = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy > 0 else 0.0
    return round(slope_pct, 4), round(r2, 3)


def _cross_signal(hist: list) -> str:
    """
    Phát hiện giá cắt VWAP (như MA cross).
    hist: list of (vwap, session_close) sorted oldest→newest.
    """
    if len(hist) < 2:
        return "—"
    v_prev, c_prev = hist[-2]
    v_curr, c_curr = hist[-1]
    if v_prev == 0 or v_curr == 0:
        return "—"
    above_prev = c_prev >= v_prev
    above_curr = c_curr >= v_curr
    if not above_prev and above_curr:
        return "🟢 GOLD↑"
    if above_prev and not above_curr:
        return "🔴 DEATH↓"
    return "↑ above" if above_curr else "↓ below"


def _intraday_vwap_slope(conn: sqlite3.Connection, sid: int,
                         date_str: str) -> tuple:
    """
    Slope của rolling VWAP intraday (1m candles).
    Returns (slope_pct_per_1m, r2).
    """
    rows = conn.execute("""
        SELECT close, volume
        FROM stock_prices
        WHERE security_id=? AND interval='1m'
          AND date(trade_time)=?
        ORDER BY trade_time
    """, (sid, date_str)).fetchall()

    if not rows:
        return 0.0, 0.0

    cum_pv, cum_v = 0.0, 0.0
    rolling = []
    for c, v in rows:
        if v and v > 0:
            cum_pv += (c or 0) * v
            cum_v  += v
        rolling.append(cum_pv / cum_v if cum_v > 0 else (c or 0))

    return _linreg_slope_pct(rolling)


def vwap_slope_scan(conn: sqlite3.Connection,
                    symbols: list, date_str: str) -> dict:
    """
    Tính VWAP slope (1M/1W/intraday) + cross signal cho danh sách mã.
    Trả về dict keyed by symbol:
      {
        'slp_1m': float, 'r2_1m': float,   # slope % per day, 22d window
        'slp_1w': float, 'r2_1w': float,   # slope % per day, 5d window
        'slp_id': float, 'r2_id': float,   # slope % per 1m candle
        'cross':  str,                      # GOLD↑ / DEATH↓ / above / below
        'vwap_1d': float,                   # current day VWAP
        'close_1d': float,                  # last close
      }
    """
    result = {}

    for sym in symbols:
        row = conn.execute(
            "SELECT security_id FROM securities WHERE symbol=?", (sym,)
        ).fetchone()
        if not row:
            continue
        sid = row[0]

        # Fetch daily VWAP history (25 ngày để đủ 22 phiên)
        daily = conn.execute("""
            SELECT trade_date, vwap, session_close
            FROM daily_vwap_summary
            WHERE security_id=? AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 25
        """, (sid, date_str)).fetchall()
        daily = list(reversed(daily))  # oldest → newest

        if not daily:
            continue

        vwaps_all = [r[1] for r in daily]
        hist_cross = [(r[1], r[2]) for r in daily]

        # 1M slope (22 phiên)
        w_1m = vwaps_all[-22:] if len(vwaps_all) >= 22 else vwaps_all
        slp_1m, r2_1m = _linreg_slope_pct(w_1m)

        # 1W slope (5 phiên)
        w_1w = vwaps_all[-5:] if len(vwaps_all) >= 5 else vwaps_all
        slp_1w, r2_1w = _linreg_slope_pct(w_1w)

        # Intraday slope
        slp_id, r2_id = _intraday_vwap_slope(conn, sid, date_str)

        # Cross
        cross = _cross_signal(hist_cross)

        result[sym] = {
            "slp_1m":   slp_1m,
            "r2_1m":    r2_1m,
            "slp_1w":   slp_1w,
            "r2_1w":    r2_1w,
            "slp_id":   slp_id,
            "r2_id":    r2_id,
            "cross":    cross,
            "vwap_1d":  daily[-1][1],
            "close_1d": daily[-1][2],
        }

    return result


def print_slope_report(slope_data: dict, date_str: str):
    """In bảng VWAP Slope report ra terminal."""

    def arr(s, r2):
        """Slope → directional arrow với R² gating."""
        if r2 < 0.30:
            return "↔"
        if s > 0.15:  return "⬆⬆"
        if s > 0.05:  return "↗ "
        if s > 0:     return "→↗"
        if s > -0.05: return "→↘"
        if s > -0.15: return "↘ "
        return "⬇⬇"

    def composite(d):
        up = (d["slp_1m"] > 0) + (d["slp_1w"] > 0) + (d["slp_id"] > 0)
        pct = (d["close_1d"] - d["vwap_1d"]) / d["vwap_1d"] * 100 if d["vwap_1d"] else 0
        cross = d["cross"]
        if "GOLD" in cross:   return "🟢 BREAKOUT"
        if "DEATH" in cross:  return "🔴 BREAKDOWN"
        if up >= 2 and pct > 0:  return "▲ ALIGNED BULL"
        if up >= 2 and pct < 0:  return "🔶 ACCUM ZONE"
        if up == 0 and pct < 0:  return "▼ DIST/SELL"
        return "➖ MIXED"

    sep = "=" * 104
    print(f"\n{sep}")
    print(f"  📐 VWAP SLOPE SCAN — {date_str}")
    print(
        f"  {'SYM':<5} {'Close':>7} {'vsVWAP':>7} "
        f"{'Slp1M':>7} {'R²':>4} {'1M':>2}  "
        f"{'Slp1W':>7} {'R²':>4} {'1W':>2}  "
        f"{'SlpID':>7} {'R²':>4} {'ID':>2}  "
        f"{'Cross':<16} {'Signal'}"
    )
    print(f"  {'-'*100}")

    for sym, d in slope_data.items():
        vwap   = d["vwap_1d"]
        close  = d["close_1d"]
        pct    = (close - vwap) / vwap * 100 if vwap else 0
        sig    = composite(d)
        cross  = d["cross"]

        print(
            f"  {sym:<5} {close:>7.2f} {pct:>+6.2f}% "
            f"  {d['slp_1m']:>+6.3f}% {d['r2_1m']:>4.2f} {arr(d['slp_1m'], d['r2_1m'])}  "
            f"  {d['slp_1w']:>+6.3f}% {d['r2_1w']:>4.2f} {arr(d['slp_1w'], d['r2_1w'])}  "
            f"  {d['slp_id']:>+6.4f}% {d['r2_id']:>4.2f} {arr(d['slp_id'], d['r2_id'])}  "
            f"  {cross:<16} {sig}"
        )

    print(f"{sep}")
    print("  Slope = % per period | 1M per day (22d OLS) | 1W per day (5d OLS) | ID per 1m candle")
    print("  R² ≥ 0.5 = trend đáng tin | arrows: ⬆⬆↗→↗ ↔ →↘↘⬇⬇")
    print("  Cross: 🟢 GOLD↑ = giá cắt lên VWAP | 🔴 DEATH↓ = giá cắt xuống VWAP")


# ============================================================
# BƯỚC 3: REBUILD daily_vwap_summary cho watchlist
# ============================================================
def rebuild_vwap_watchlist(conn: sqlite3.Connection, date_str: str,
                            sid_list: list) -> int:
    """
    Gọi daily_vwap_builder.compute_for_date() chỉ cho watchlist.
    """
    try:
        from scripts.daily_vwap_builder import compute_for_date
    except ImportError:
        logger.warning("⚠️  Không import được daily_vwap_builder")
        return 0

    # compute_for_date trả về số mã rebuilt cho ngày đó (toàn thị trường).
    # Để chỉ rebuild watchlist, ta truyền security_id filter nếu hàm hỗ trợ.
    # Nếu không, rebuild ngày hôm nay và chấp nhận overcount nhỏ.
    n = compute_for_date(conn, date_str)
    logger.info(f"  Rebuilt daily_vwap_summary: {n} mã-ngày cho {date_str}")
    return n


# ============================================================
# MAIN PIPELINE
# ============================================================
def run_pipeline(date_str: str | None = None):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    if date_str is None:
        date_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info(f"🚀 PortfolioWatcher — {date_str}")
    logger.info(f"   Watchlist: {', '.join(WATCHLIST)}")
    logger.info("=" * 60)

    # Lấy sid_map
    ph = ",".join("?" * len(WATCHLIST))
    rows = conn.execute(
        f"SELECT symbol, security_id FROM securities WHERE symbol IN ({ph})",
        WATCHLIST
    ).fetchall()
    sid_map = {r[0]: r[1] for r in rows}
    missing = [s for s in WATCHLIST if s not in sid_map]
    if missing:
        logger.warning(f"⚠️  Không tìm thấy trong DB: {missing}")

    # ── BƯỚC 1: IMPUTE ──────────────────────────────────────
    logger.info("\n─── BƯỚC 1: IMPUTE (VPM) ───")
    run_impute_watchlist(conn, date_str)

    # ── BƯỚC 2: ATC ─────────────────────────────────────────
    logger.info("\n─── BƯỚC 2: ATC từ DNSE ───")
    vn_now = datetime.now(VN_TZ)
    # Chỉ fetch ATC sau 14:46 VN (sàn đã đóng)
    atc_cutoff = vn_now.replace(hour=14, minute=46, second=0, microsecond=0)
    if vn_now >= atc_cutoff or date_str != vn_now.strftime("%Y-%m-%d"):
        atc_data = asyncio.run(fetch_atc_all(list(sid_map.keys()), date_str))
        upsert_atc(conn, date_str, atc_data, sid_map)
    else:
        remaining = int((atc_cutoff - vn_now).total_seconds())
        logger.info(f"  Chưa đến 14:46 VN (còn {remaining}s). Bỏ qua fetch ATC.")

    # ── BƯỚC 3: REBUILD VWAP ────────────────────────────────
    logger.info("\n─── BƯỚC 3: REBUILD daily_vwap_summary ───")
    rebuild_vwap_watchlist(conn, date_str, list(sid_map.values()))

    conn.close()
    logger.info("\n✅ PortfolioWatcher hoàn tất!")


# ============================================================
# INTRADAY LIVE DASHBOARD
# ============================================================
INTERVAL_SEC = 180   # refresh mỗi 3 phút

MARKET_OPEN  = (9,  0)   # 09:00 VN
MARKET_CLOSE = (14, 46)  # 14:46 VN (sau ATC)
EOD_TRIGGER  = (14, 50)  # trigger EOD pipeline


def _is_market_hour(now: datetime) -> bool:
    h, m = now.hour, now.minute
    return (h, m) >= MARKET_OPEN and (h, m) < MARKET_CLOSE


def _live_snapshot(conn: sqlite3.Connection, sid_map: dict, date_str: str) -> str:
    """
    Query nhanh DB và render bảng dashboard cho terminal.
    Bao gồm: intraday VWAP stats + slope 1M/1W/ID + cross signal.
    """
    lines = []
    now_str = datetime.now(VN_TZ).strftime("%H:%M:%S")

    # Lấy slope data cho cả watchlist
    slope_data = vwap_slope_scan(conn, list(sid_map.keys()), date_str)

    lines.append(f"\n{'='*108}")
    lines.append(f"  📊 WATCHLIST LIVE — {date_str}  [{now_str} VN]")
    lines.append(
        f"  {'SYM':<5} {'Close':>6} {'vsVWAP':>7} {'NetΔ':>10} "
        f"{'BuyR':>5} {'Vol(M)':>6}  "
        f"{'Slp1M':>7} {'Slp1W':>7} {'SlpID':>7}  "
        f"{'Cross':<14} {'Signal'}"
    )
    lines.append(f"  {'-'*104}")

    for sym in sorted(sid_map.keys()):
        sid = sid_map[sym]
        rows = conn.execute("""
            SELECT close, volume,
                   COALESCE(buy_vol,0), COALESCE(sell_vol,0)
            FROM stock_prices
            WHERE security_id=? AND interval='1m'
              AND date(trade_time)=?
            ORDER BY trade_time
        """, (sid, date_str)).fetchall()

        if not rows:
            lines.append(f"  {sym:<5} {'—':>6}")
            continue

        total_vol = sum(r[1] for r in rows) or 1
        total_bv  = sum(r[2] for r in rows)
        total_sv  = sum(r[3] for r in rows)
        net_delta = total_bv - total_sv
        last_c    = rows[-1][0] or 0.0

        cum_pv = sum((r[0] or 0) * (r[1] or 0) for r in rows)
        vwap   = cum_pv / total_vol if total_vol > 0 else 0
        pct_vwap  = (last_c - vwap) / vwap * 100 if vwap > 0 else 0
        buy_ratio = total_bv * 100.0 / max(total_bv + total_sv, 1)

        # Slope data
        sd = slope_data.get(sym, {})
        slp_1m = sd.get("slp_1m", 0.0)
        r2_1m  = sd.get("r2_1m", 0.0)
        slp_1w = sd.get("slp_1w", 0.0)
        r2_1w  = sd.get("r2_1w", 0.0)
        slp_id = sd.get("slp_id", 0.0)
        r2_id  = sd.get("r2_id", 0.0)
        cross  = sd.get("cross", "—")

        # Composite signal
        up = (slp_1m > 0) + (slp_1w > 0) + (slp_id > 0)
        if "GOLD" in cross:
            sig = "🟢 BREAKOUT"
        elif "DEATH" in cross:
            sig = "🔴 BREAKDOWN"
        elif net_delta > 5_000_000 and pct_vwap < 0:
            sig = "🐋 HIDDEN ACCUM"
        elif up >= 2 and pct_vwap > 0:
            sig = "▲ BULL"
        elif up >= 2 and pct_vwap < 0:
            sig = "🔶 ACCUM"
        elif net_delta < 0 and pct_vwap < 0:
            sig = "▼ SELL"
        else:
            sig = "➖ MIXED"

        # Hiển thị slope với dấu + và arrow nhanh
        def fmt_slp(s, r2):
            mark = "*" if r2 >= 0.5 else " "
            return f"{s:>+6.3f}%{mark}"

        lines.append(
            f"  {sym:<5} {last_c:>6.2f} {pct_vwap:>+6.2f}% {net_delta:>+10,} "
            f"{buy_ratio:>4.0f}% {total_vol/1e6:>6.1f}M  "
            f"{fmt_slp(slp_1m, r2_1m)} {fmt_slp(slp_1w, r2_1w)} {fmt_slp(slp_id, r2_id)}  "
            f"{cross:<14} {sig}"
        )

    lines.append(f"{'='*108}")
    lines.append("  * = R²≥0.5 (trend đáng tin) | Slp: % per period (1M=day, 1W=day, ID=1m candle)")
    lines.append("  Cross: 🟢 giá cắt lên VWAP | 🔴 giá cắt xuống VWAP")
    return "\n".join(lines)


def run_monitor(interval_sec: int = INTERVAL_SEC):
    """
    Real-time intraday loop:
      - Impute missing side mỗi interval_sec giây (commit ngay)
      - In live dashboard cho watchlist
      - Trigger EOD pipeline tự động lúc 14:50
    """
    print(f"\n🟢 MONITOR MODE — refresh mỗi {interval_sec}s | Ctrl+C để dừng")
    print(f"   Watchlist: {', '.join(WATCHLIST)}")
    print(f"   Market: {MARKET_OPEN[0]:02d}:{MARKET_OPEN[1]:02d} → "
          f"{MARKET_CLOSE[0]:02d}:{MARKET_CLOSE[1]:02d} VN\n")

    last_eod_date = None

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    # Lấy sid_map một lần
    ph   = ",".join("?" * len(WATCHLIST))
    rows = conn.execute(
        f"SELECT symbol, security_id FROM securities WHERE symbol IN ({ph})",
        WATCHLIST
    ).fetchall()
    sid_map = {r[0]: r[1] for r in rows}

    try:
        while True:
            now      = datetime.now(VN_TZ)
            date_str = now.strftime("%Y-%m-%d")
            is_wd    = now.weekday() < 5

            if is_wd and _is_market_hour(now):
                # ── Impute missing nến ngay lập tức ──────────────
                imputed = run_impute_watchlist(conn, date_str)
                n_imputed = sum(imputed.values()) if isinstance(imputed, dict) else 0

                # ── Live dashboard ────────────────────────────────
                dashboard = _live_snapshot(conn, sid_map, date_str)
                # Clear terminal
                print("\033[2J\033[H", end="")  # clear screen
                print(dashboard)
                if n_imputed:
                    print(f"  ⚡ Vừa impute {n_imputed} nến thiếu side")

            elif is_wd and now.hour == EOD_TRIGGER[0] and now.minute >= EOD_TRIGGER[1]:
                # ── EOD trigger ───────────────────────────────────
                if date_str != last_eod_date:
                    print(f"\n⏰ [{now.strftime('%H:%M')}] Kích hoạt EOD pipeline...")
                    try:
                        run_pipeline(date_str)
                        last_eod_date = date_str
                        print("✅ EOD pipeline hoàn tất!")
                    except Exception as e:
                        print(f"❌ EOD lỗi: {e}")
            else:
                h, m = now.hour, now.minute
                print(f"  [{now.strftime('%H:%M:%S')}] Chờ phiên giao dịch "
                      f"({MARKET_OPEN[0]:02d}:{MARKET_OPEN[1]:02d}–"
                      f"{MARKET_CLOSE[0]:02d}:{MARKET_CLOSE[1]:02d} VN)...",
                      end="\r")

            time.sleep(interval_sec)

    except KeyboardInterrupt:
        print("\n\n👋 Monitor dừng.")
    finally:
        conn.close()


# ============================================================
# DAEMON MODE — chờ 14:50 mỗi ngày làm việc
# ============================================================
def run_daemon():
    logger.info("🔄 Daemon mode: sẽ chạy lúc 14:50 VN mỗi ngày T2-T6")
    last_run_date = None
    while True:
        now    = datetime.now(VN_TZ)
        is_weekday = now.weekday() < 5
        date_str   = now.strftime("%Y-%m-%d")
        run_time   = now.replace(hour=14, minute=50, second=0, microsecond=0)

        if is_weekday and now >= run_time and date_str != last_run_date:
            logger.info(f"⏰ [{now.strftime('%H:%M')}] Kích hoạt pipeline...")
            try:
                run_pipeline(date_str)
                last_run_date = date_str
            except Exception as e:
                logger.error(f"❌ Pipeline lỗi: {e}", exc_info=True)

        time.sleep(30)


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PortfolioWatcher — Impute + ATC + VWAP rebuild cho watchlist"
    )
    parser.add_argument(
        "--monitor", action="store_true",
        help="Real-time intraday: impute + live dashboard + auto EOD trigger"
    )
    parser.add_argument(
        "--interval", type=int, default=INTERVAL_SEC,
        help=f"Giây giữa mỗi refresh (mặc định: {INTERVAL_SEC}s)"
    )
    parser.add_argument(
        "--daemon", action="store_true",
        help="Chỉ chạy EOD pipeline, chờ 14:50 VN mỗi ngày T2-T6"
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="One-shot cho ngày cụ thể (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--scan", action="store_true",
        help="In báo cáo VWAP Slope (1M/1W/Intraday) + cross signal cho watchlist"
    )
    args = parser.parse_args()

    if args.monitor:
        run_monitor(interval_sec=args.interval)
    elif args.daemon:
        run_daemon()
    elif args.scan:
        _date = args.date or datetime.now(VN_TZ).strftime("%Y-%m-%d")
        _conn = sqlite3.connect(str(DB_PATH))
        _conn.execute("PRAGMA journal_mode=WAL")
        _slope = vwap_slope_scan(_conn, WATCHLIST, _date)
        print_slope_report(_slope, _date)
        _conn.close()
    else:
        run_pipeline(date_str=args.date)
