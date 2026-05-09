#!/usr/bin/env python3
"""
scripts/masvn_qc_time.py
═══════════════════════════════════════════════════════════════
QC theo thời gian: tìm thời điểm intraday_engine bắt đầu ghi,
so sánh vol DB vs DNSE trong cửa sổ engine_start → now.

NOTE: trade_time trong DB lưu dưới dạng VN local time ISO string
      "2026-04-28T10:10:00" (không có tz suffix, không phải UTC).
      DNSE API nhận Unix timestamp UTC.
"""
import os, sys, sqlite3, requests, time
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from realtime.watchlist_db import load_watchlist

VN_TZ    = timezone(timedelta(hours=7))
DB_PATH  = os.getenv("SMD_DB_PATH", os.path.join(PROJECT_ROOT, "data", "securities_master.db"))
DNSE_URL = "https://services.entrade.com.vn/chart-api/v2/ohlcs/stock"

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"
C = "\033[96m"; B = "\033[1m";  E = "\033[0m"


def fetch_dnse_1m(symbol: str, t_from: int, t_to: int):
    """Lấy list (unix_ts_utc, volume) 1m từ DNSE public API."""
    try:
        r = requests.get(DNSE_URL, params={
            "from": t_from, "to": t_to,
            "symbol": symbol, "resolution": "1"
        }, timeout=12)
        if r.ok:
            d = r.json()
            ts_list = d.get("t") or []
            v_list  = d.get("v") or []
            return list(zip(ts_list, v_list))
    except Exception:
        pass
    return []


def vn_str_to_ts(vn_str: str) -> int:
    """'2026-04-28T10:10:00' (VN local) → unix timestamp UTC."""
    dt_vn = datetime.strptime(vn_str.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
    return int(dt_vn.replace(tzinfo=VN_TZ).timestamp())


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="vip")
    args = p.parse_args()

    now_vn  = datetime.now(VN_TZ)
    date_vn = now_vn.strftime("%Y-%m-%d")

    # DB query range: VN local ISO strings (engine stores VN time with T)
    db_open_vn = date_vn + "T09:00:00"
    db_now_vn  = now_vn.strftime("%Y-%m-%dT%H:%M:%S")

    # DNSE Unix timestamps (UTC)
    t_dnse_open = int(datetime.strptime(date_vn + " 09:00:00", "%Y-%m-%d %H:%M:%S")
                      .replace(tzinfo=VN_TZ).timestamp())
    t_dnse_now  = int(now_vn.timestamp())

    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    watchlist = load_watchlist(list_name=args.name, db_path=DB_PATH)

    if not watchlist:
        print(f"{R}❌ Watchlist '{args.name}' trống.{E}"); return

    # ── Tìm first tick toàn DB hôm nay (VN time) ──
    first_row = conn.execute("""
        SELECT MIN(trade_time) as ft
        FROM stock_prices
        WHERE interval='1m' AND trade_time >= ? AND trade_time <= ?
    """, (db_open_vn, db_now_vn)).fetchone()

    first_vn_str = first_row["ft"]
    if first_vn_str:
        first_vn_dt  = datetime.strptime(str(first_vn_str).replace("T"," ")[:19], "%Y-%m-%d %H:%M:%S")
        late_min     = int((first_vn_dt - first_vn_dt.replace(hour=9, minute=0, second=0)).total_seconds() / 60)
        t_eng_start  = vn_str_to_ts(str(first_vn_str))
        late_color   = Y if late_min > 5 else G
    else:
        first_vn_dt = None; late_min = 0; t_eng_start = t_dnse_open
        late_color  = Y

    print(f"\n{'='*84}")
    print(f"  {B}{C}⏰ INTRADAY ENGINE TIME-WINDOW QC — {date_vn} {now_vn.strftime('%H:%M:%S')}{E}")
    print(f"{'='*84}")
    print(f"  ├ Market open (VN)  : 09:00")
    if first_vn_dt:
        print(f"  ├ Engine first tick : {late_color}{first_vn_dt.strftime('%H:%M:%S')} VN  (muộn {late_min} phút){E}")
    else:
        print(f"  ├ Engine first tick : {R}N/A — không có dữ liệu hôm nay{E}")
    print(f"  └ Now               : {now_vn.strftime('%H:%M:%S')} VN")

    print(f"\n  Cửa sổ A: DNSE(09:00→now)         vs DB  — thấy toàn bộ gap kể cả late-start")
    print(f"  Cửa sổ B: DNSE(engine_start→now)   vs DB  — fair comparison, loại trừ late-start")
    print(f"\n{'─'*84}")
    print(f"\n  {'SYM':<5}  {'First(VN)':>9}  {'DB_vol':>9}  {'DNSE_A':>9}  {'Cov_A%':>7}  {'DNSE_B':>9}  {'Cov_B%':>7}  {'Late':>5}  Verdict")
    print(f"  {'─'*78}")

    all_results = []
    for sym in watchlist:
        sec = conn.execute("SELECT security_id FROM securities WHERE symbol=?", (sym,)).fetchone()
        if not sec:
            print(f"  {sym:<5}  — not in DB"); continue
        sid = sec[0]

        # Per-symbol first tick (VN time ISO)
        sym_first = conn.execute("""
            SELECT MIN(trade_time) as ft, SUM(COALESCE(volume,0)) as vol
            FROM stock_prices
            WHERE security_id=? AND interval='1m'
              AND trade_time >= ? AND trade_time <= ?
        """, (sid, db_open_vn, db_now_vn)).fetchone()

        sym_first_str = sym_first["ft"]
        vol_db        = int(sym_first["vol"] or 0)

        if sym_first_str:
            sym_first_dt  = datetime.strptime(str(sym_first_str).replace("T"," ")[:19], "%Y-%m-%d %H:%M:%S")
            sym_late_min  = int((sym_first_dt - sym_first_dt.replace(hour=9, minute=0, second=0)).total_seconds() / 60)
            t_sym_start   = vn_str_to_ts(str(sym_first_str))
            first_label   = sym_first_dt.strftime("%H:%M:%S")
        else:
            sym_first_dt  = None; sym_late_min = 0
            t_sym_start   = t_eng_start
            first_label   = "no data"

        # DNSE 1m
        dnse_data    = fetch_dnse_1m(sym, t_dnse_open, t_dnse_now)
        vol_dnse_a   = int(sum(v for _, v in dnse_data if v))
        vol_dnse_b   = int(sum(v for ts, v in dnse_data if v and ts >= t_sym_start))
        time.sleep(0.15)

        cov_a = round(vol_db * 100.0 / max(vol_dnse_a, 1), 1) if vol_dnse_a else 0
        cov_b = round(vol_db * 100.0 / max(vol_dnse_b, 1), 1) if vol_dnse_b else 0

        if cov_b >= 90:   verdict = f"{G}✅ GOOD{E}";    vcol = G
        elif cov_b >= 75: verdict = f"{Y}⚠️ partial{E}"; vcol = Y
        elif cov_b >= 50: verdict = f"{Y}⚠️ LOW{E}";     vcol = Y
        else:             verdict = f"{R}❌ MISSING{E}";  vcol = R

        late_str = f"{Y}{sym_late_min}m{E}" if sym_late_min > 5 else ("—" if sym_late_min <= 2 else f"{sym_late_min}m")

        print(
            f"  {B}{sym:<5}{E}  {first_label:>9}  {vol_db/1e6:>7.2f}M  "
            f"{vol_dnse_a/1e6:>7.2f}M  {vcol}{cov_a:>6.1f}%{E}  "
            f"{vol_dnse_b/1e6:>7.2f}M  {vcol}{cov_b:>6.1f}%{E}  "
            f"{late_str:>5}  {verdict}"
        )
        all_results.append({
            "sym": sym, "cov_a": cov_a, "cov_b": cov_b,
            "vol_db": vol_db, "vol_dnse_a": vol_dnse_a, "vol_dnse_b": vol_dnse_b,
            "late_min": sym_late_min,
        })

    conn.close()

    good    = [r for r in all_results if r["cov_b"] >= 90]
    partial = [r for r in all_results if 50 <= r["cov_b"] < 90]
    missing = [r for r in all_results if r["cov_b"] < 50 and r["vol_dnse_b"] > 0]
    avg_late  = sum(r["late_min"] for r in all_results) / max(len(all_results), 1)
    avg_cov_a = sum(r["cov_a"]   for r in all_results) / max(len(all_results), 1)
    avg_cov_b = sum(r["cov_b"]   for r in all_results) / max(len(all_results), 1)

    print(f"\n{'─'*84}")
    print(f"  📋 SUMMARY:")
    print(f"     Engine late avg   : {Y if avg_late > 5 else G}{avg_late:.0f} phút{E}")
    print(f"     Avg Cov_A (full)  : {avg_cov_a:.1f}%  ← kể cả gap do late-start")
    print(f"     Avg Cov_B (fair)  : {G if avg_cov_b >= 85 else Y}{avg_cov_b:.1f}%{E}  ← chỉ từ lúc engine start")
    print(f"     Verdict           : {G}✅{E}={len(good)}  {Y}⚠️{E}={len(partial)}  {R}❌{E}={len(missing)}")

    diff_cov = avg_cov_b - avg_cov_a
    print(f"\n  📐 Gap phân tích:")
    if avg_late > 5:
        print(f"     Late-start gap    : Cov_B − Cov_A = {diff_cov:+.1f}%  (vol bị miss do engine khởi động muộn {avg_late:.0f}m)")
    if missing:
        print(f"     {R}❌ Ongoing miss    : {[r['sym'] for r in missing]} — miss vol ngay cả sau engine start{E}")
        print(f"        → Kiểm tra intraday_engine logs: boardevent subscription, tick routing")
    if not missing and avg_cov_b >= 85:
        print(f"     {G}✅ Kết luận        : Engine ghi đủ volume trong cửa sổ hoạt động{E}")
        print(f"        Gap {avg_cov_a:.0f}% (full) hoàn toàn do engine start muộn {avg_late:.0f}m")
        print(f"        → Giải pháp: pm2 pre-start script lúc 08:55 VN")

    print(f"\n{'='*84}\n")


if __name__ == "__main__":
    main()
