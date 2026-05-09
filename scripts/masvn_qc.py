#!/usr/bin/env python3
"""
scripts/masvn_qc.py
══════════════════════════════════════════════════════════
QC Worker MASVN: So sánh Vol DB (intraday_engine + MASVN worker)
với Vol thực từ DNSE public API.

Metrics:
  - vol_db      : tổng volume 1m candles trong DB hôm nay
  - vol_dnse    : tổng volume lấy từ DNSE chart API (public, no auth)
  - coverage%   : vol_db / vol_dnse → worker có đang bắt đủ tick không
  - side_cov%   : (buy_vol + sell_vol) / vol_db → MASVN có đang truyền side không
  - buy_pct%    : buy_vol / vol_db
  - delta       : buy_vol - sell_vol

Cách dùng:
  PYTHONPATH=. python3 scripts/masvn_qc.py
  PYTHONPATH=. python3 scripts/masvn_qc.py --name vip
"""

import os, sys, requests, time, sqlite3
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from realtime.watchlist_db import load_watchlist
from realtime.vwap_engine   import _session_open_utc

VN_TZ    = timezone(timedelta(hours=7))
DB_PATH  = os.getenv("SMD_DB_PATH", os.path.join(PROJECT_ROOT, "data", "securities_master.db"))
DNSE_URL = "https://services.entrade.com.vn/chart-api/v2/ohlcs/stock"

# ANSI
G  = "\033[92m"; R  = "\033[91m"; Y  = "\033[93m"
C  = "\033[96m"; B  = "\033[1m";  E  = "\033[0m"


def fetch_dnse_vol(symbol: str, date_vn: str) -> int:
    """Lấy total volume từ DNSE daily candle (public API, no auth)."""
    dt = datetime.strptime(date_vn, "%Y-%m-%d").replace(tzinfo=VN_TZ)
    t_from = int(dt.replace(hour=9,  minute=0, second=0).timestamp())
    t_to   = int(dt.replace(hour=15, minute=30, second=0).timestamp())

    # Thử daily resolution trước (nhanh nhất)
    try:
        r = requests.get(
            DNSE_URL,
            params={"from": t_from - 86400, "to": t_to, "symbol": symbol, "resolution": "D"},
            timeout=8,
        )
        if r.ok:
            d = r.json()
            ts_list = d.get("t") or []
            v_list  = d.get("v") or []
            if ts_list and v_list:
                # Tìm nến đúng ngày hôm nay
                for ts, v in zip(ts_list, v_list):
                    dt_candle = datetime.fromtimestamp(ts, tz=VN_TZ).strftime("%Y-%m-%d")
                    if dt_candle == date_vn and v:
                        return int(v)
    except Exception:
        pass

    # Fallback: 1m candles, cộng tổng
    try:
        r = requests.get(
            DNSE_URL,
            params={"from": t_from, "to": t_to, "symbol": symbol, "resolution": "1"},
            timeout=10,
        )
        if r.ok:
            d = r.json()
            v_list = d.get("v") or []
            if v_list:
                return int(sum(v for v in v_list if v))
    except Exception:
        pass

    return 0


def get_db_stats(conn, sid: int, date_vn: str, session_open: str) -> dict:
    """Lấy vol + side data từ DB."""
    row = conn.execute("""
        SELECT
            COUNT(*)                                        as n_candles,
            SUM(COALESCE(volume,0))                        as vol_total,
            SUM(COALESCE(buy_vol,0))                       as buy_vol,
            SUM(COALESCE(sell_vol,0))                      as sell_vol,
            SUM(COALESCE(buy_vol,0)-COALESCE(sell_vol,0))  as delta,
            MIN(close)                                     as low,
            MAX(close)                                     as high,
            (SELECT close FROM stock_prices
             WHERE security_id=? AND interval='1m' AND date(trade_time)=?
             ORDER BY trade_time DESC LIMIT 1)             as last_close
        FROM stock_prices
        WHERE security_id=? AND interval='1m'
          AND trade_time>=? AND date(trade_time)=?
    """, (sid, date_vn, sid, session_open, date_vn)).fetchone()
    return dict(row) if row else {}


def run_qc(list_name: str = "vip"):
    date_vn      = datetime.now(VN_TZ).strftime("%Y-%m-%d")
    session_open = _session_open_utc(date_vn)
    watchlist    = load_watchlist(list_name=list_name, db_path=DB_PATH)

    if not watchlist:
        print(f"{R}❌ Watchlist '{list_name}' trống.{E}")
        return

    now_str = datetime.now(VN_TZ).strftime("%H:%M:%S")
    print(f"\n{'='*80}")
    print(f"  {B}{C}🔬 MASVN WORKER QC — {date_vn} {now_str}  (list='{list_name}', {len(watchlist)} mã){E}")
    print(f"  So sánh Vol DB (intraday_engine + MASVN worker) vs Vol DNSE (ground truth)")
    print(f"{'='*80}\n")

    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row

    # Market side coverage
    mkt = conn.execute(
        "SELECT SUM(COALESCE(volume,0)), SUM(COALESCE(buy_vol,0)+COALESCE(sell_vol,0)) "
        "FROM stock_prices WHERE interval='1m' AND date(trade_time)=? AND volume>0",
        (date_vn,)
    ).fetchone()
    mkt_vol  = mkt[0] or 0
    mkt_side = mkt[1] or 0
    mkt_cov  = round(mkt_side * 100.0 / max(mkt_vol, 1), 1)
    print(f"  📊 Market-wide side coverage (toàn DB): {mkt_cov}%\n")

    # Header
    print(f"  {'SYM':<5} {'Close':>6}  {'DB Vol':>10}  {'DNSE Vol':>10}  {'VolCov%':>8}  {'SideCov%':>9}  {'Buy%':>6}  {'Delta':>12}  {'Candles':>7}  Status")
    print(f"  {'─'*96}")

    results = []
    for sym in watchlist:
        sec = conn.execute("SELECT security_id FROM securities WHERE symbol=?", (sym,)).fetchone()
        if not sec:
            print(f"  {R}{sym:<5}{E}  — not in DB"); continue

        sid      = sec[0]
        db_stats = get_db_stats(conn, sid, date_vn, session_open)

        vol_db   = int(db_stats.get("vol_total") or 0)
        buy_vol  = int(db_stats.get("buy_vol")   or 0)
        sell_vol = int(db_stats.get("sell_vol")  or 0)
        delta    = int(db_stats.get("delta")     or 0)
        n_candle = int(db_stats.get("n_candles") or 0)
        close_c  = float(db_stats.get("last_close") or 0)

        # DNSE vol (public API)
        vol_dnse = fetch_dnse_vol(sym, date_vn)
        time.sleep(0.15)  # rate-limit nhẹ

        # Metrics
        vol_cov  = round(vol_db  * 100.0 / max(vol_dnse, 1), 1) if vol_dnse else 0
        side_cov = round((buy_vol + sell_vol) * 100.0 / max(vol_db, 1), 1) if vol_db else 0
        buy_pct  = round(buy_vol * 100.0 / max(vol_db, 1), 1) if vol_db else 0

        # Status
        if vol_dnse == 0:
            status   = f"{Y}⚠️ DNSE no data{E}"
            vol_col  = Y
        elif vol_cov >= 90:
            status   = f"{G}✅ GOOD{E}"
            vol_col  = G
        elif vol_cov >= 70:
            status   = f"{Y}⚠️ partial{E}"
            vol_col  = Y
        else:
            status   = f"{R}❌ LOW{E}"
            vol_col  = R

        side_col = G if side_cov >= 80 else (Y if side_cov >= 50 else R)
        delta_col = G if delta >= 0 else R

        print(
            f"  {B}{sym:<5}{E} {close_c:>6.2f}  "
            f"{vol_col}{vol_db/1e6:>8.2f}M{E}  "
            f"{vol_dnse/1e6:>8.2f}M  "
            f"{vol_col}{vol_cov:>7.1f}%{E}  "
            f"{side_col}{side_cov:>8.1f}%{E}  "
            f"{buy_pct:>5.1f}%  "
            f"{delta_col}{delta:>+12,}{E}  "
            f"{n_candle:>7}  "
            f"{status}"
        )

        results.append({
            "sym": sym, "vol_db": vol_db, "vol_dnse": vol_dnse,
            "vol_cov": vol_cov, "side_cov": side_cov,
            "buy_pct": buy_pct, "delta": delta, "n_candle": n_candle,
        })

    conn.close()

    # Summary
    print(f"\n{'─'*80}")
    good    = [r for r in results if r["vol_dnse"] > 0 and r["vol_cov"] >= 90]
    partial = [r for r in results if r["vol_dnse"] > 0 and 70 <= r["vol_cov"] < 90]
    low     = [r for r in results if r["vol_dnse"] > 0 and r["vol_cov"] < 70]
    no_data = [r for r in results if r["vol_dnse"] == 0]
    avg_side_cov = sum(r["side_cov"] for r in results) / max(len(results), 1)

    print(f"  📋 SUMMARY:")
    print(f"     Vol Coverage  : {G}✅ GOOD{E}={len(good)}  {Y}⚠️ partial{E}={len(partial)}  {R}❌ LOW{E}={len(low)}  ⏳ no DNSE={len(no_data)}")
    print(f"     Avg Side Cov  : {avg_side_cov:.1f}%  {'✅' if avg_side_cov >= 80 else '⚠️'}")

    if low:
        print(f"\n  {R}❌ Cần kiểm tra worker cho: {[r['sym'] for r in low]}{E}")
    if avg_side_cov < 80:
        print(f"  {Y}⚠️ Side coverage thấp — kiểm tra pm2 logs watchlist_worker{E}")
    else:
        print(f"  {G}✅ MASVN Worker đang hoạt động tốt!{E}")

    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="vip", help="Tên watchlist (mặc định: vip)")
    args = p.parse_args()
    run_qc(list_name=args.name)
