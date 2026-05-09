---
name: vwap_slope
description: |
  Quét tín hiệu VWAP Slope + Cross Signal cho một mã cụ thể hoặc toàn bộ thị trường.
  Phân tích xu hướng VWAP theo 3 khung thời gian (1M/1W/Intraday) + phát hiện Golden/Death Cross.
  Triệu tập bằng:
    /vwap_slope SHB            ← phân tích 1 mã
    /vwap_slope HOSE           ← quét toàn sàn HOSE
    /vwap_slope market         ← quét toàn thị trường, lọc top signals
    /vwap_slope watchlist      ← chỉ quét watchlist danh mục
author: Antigravity
version: "1.0"
---

# Mục tiêu Kích hoạt

Workflow này kích hoạt khi user yêu cầu:
- `/vwap_slope SHB` / `slope VWAP của HPG`
- `/vwap_slope market` / `quét slope toàn thị trường`
- `/vwap_slope watchlist` / `slope danh mục của anh`
- `mã nào đang có slope VWAP tốt nhất`
- `GOLD cross hôm nay có những mã nào`

**Trích xuất từ yêu cầu user:**
- `{TARGET}` = mã cụ thể (VD: `SHB`) | `market` | `watchlist` | tên sàn (`HOSE`, `HNX`)
- `{DATE_VN}` = ngày phân tích (mặc định = hôm nay VN timezone)
- `{TOP_N}` = số mã top hiển thị khi scan market (mặc định: 20)
- `{FILTER}` = optional filter signal type: `GOLD`, `DEATH`, `BULL`, `ACCUM`, `all`

---

## Bước 1 — Xác định Mode và Chạy Scanner

Dựa vào `{TARGET}`, chọn **một trong hai** lệnh dưới đây:

### Mode A: Scan 1 mã cụ thể

// turbo
```bash
# Chạy trên Oracle Linux 192.168.2.2 (source of truth DB + 32GB RAM)
ssh -t tuanho@192.168.2.2 'bash -s' << 'SSHEOF'
cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 - << 'PYEOF'
import sqlite3, numpy as np
from datetime import datetime, timezone, timedelta

DB      = "data/securities_master.db"
VN_TZ   = timezone(timedelta(hours=7))
SYMBOL  = "{SYMBOL}"   # ← điền mã user yêu cầu
DATE_VN = "{DATE_VN}"  # ← điền ngày (YYYY-MM-DD) hoặc để tự động

if not DATE_VN or DATE_VN == "{DATE_VN}":
    DATE_VN = datetime.now(VN_TZ).strftime("%Y-%m-%d")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# ── Helper functions ──────────────────────────────────────────
def linreg(values):
    n = len(values)
    if n < 3: return 0.0, 0.0
    x = np.arange(n, dtype=float)
    y = np.array(values, dtype=float)
    xm, ym = x.mean(), y.mean()
    ss_xx = ((x-xm)**2).sum(); ss_xy = ((x-xm)*(y-ym)).sum()
    ss_yy = ((y-ym)**2).sum()
    if ss_xx == 0 or ym == 0: return 0.0, 0.0
    slp = (ss_xy/ss_xx)/ym*100
    r2  = (ss_xy**2)/(ss_xx*ss_yy) if ss_yy > 0 else 0.0
    return round(slp,4), round(r2,3)

def arr(s, r2):
    if r2 < 0.30: return "↔  "
    if s > 0.15:  return "⬆⬆ "
    if s > 0.05:  return "↗  "
    if s > 0:     return "→↗ "
    if s > -0.05: return "→↘ "
    if s > -0.15: return "↘  "
    return "⬇⬇ "

def cross_sig(hist):
    if len(hist) < 2: return "—"
    vp,cp = hist[-2]; vc,cc = hist[-1]
    if vp==0 or vc==0: return "—"
    if cp < vp and cc >= vc: return "🟢 GOLD↑"
    if cp >= vp and cc < vc: return "🔴 DEATH↓"
    return "↑ above" if cc >= vc else "↓ below"

def intraday_slope(conn, sid, date_str):
    rows = conn.execute("""
        SELECT close, volume FROM stock_prices
        WHERE security_id=? AND interval='1m' AND date(trade_time)=?
        ORDER BY trade_time
    """, (sid, date_str)).fetchall()
    if not rows: return 0.0, 0.0
    cpv=cv=0.0; rvwap=[]
    for c,v in rows:
        if v and v>0: cpv+=(c or 0)*v; cv+=v
        rvwap.append(cpv/cv if cv>0 else (c or 0))
    return linreg(rvwap)

# ── Fetch data ────────────────────────────────────────────────
row = conn.execute("SELECT security_id FROM securities WHERE symbol=?", (SYMBOL,)).fetchone()
if not row:
    print(f"❌ Không tìm thấy mã {SYMBOL}")
    conn.close(); exit()
sid = row[0]

# Daily VWAP history (30 ngày để có đủ 22 phiên)
daily = conn.execute("""
    SELECT trade_date, vwap, session_open, session_close,
           cum_volume, cum_delta, buy_vol, sell_vol, side_cov_pct
    FROM daily_vwap_summary
    WHERE security_id=? AND trade_date <= ?
    ORDER BY trade_date DESC LIMIT 30
""", (sid, DATE_VN)).fetchall()
daily = list(reversed(daily))

if not daily:
    print(f"❌ Không có daily_vwap_summary cho {SYMBOL}")
    conn.close(); exit()

vwaps  = [r[1] for r in daily]
hist_x = [(r[1], r[3]) for r in daily]  # (vwap, session_close)

# 1M slope (22 phiên)
w22 = vwaps[-22:] if len(vwaps)>=22 else vwaps
slp_1m, r2_1m = linreg(w22)

# 1W slope (5 phiên)
w5 = vwaps[-5:] if len(vwaps)>=5 else vwaps
slp_1w, r2_1w = linreg(w5)

# Intraday slope
slp_id, r2_id = intraday_slope(conn, sid, DATE_VN)

# Cross
cross = cross_sig(hist_x)

cur = daily[-1]
vwap_cur  = cur[1]
close_cur = cur[3]
pct_vwap  = (close_cur - vwap_cur)/vwap_cur*100 if vwap_cur else 0

# ── Print ─────────────────────────────────────────────────────
print(f"\n{'='*64}")
print(f"  📐 VWAP SLOPE — {SYMBOL} | {DATE_VN}")
print(f"{'='*64}")
print(f"  Close    : {close_cur:.2f}")
print(f"  VWAP 1D  : {vwap_cur:.2f}  ({pct_vwap:+.2f}%)")
print(f"  Cross    : {cross}")
print()
print(f"  Slope 1M (22d OLS)  : {slp_1m:>+7.4f}%/day  R²={r2_1m:.3f}  {arr(slp_1m,r2_1m)}")
print(f"  Slope 1W  (5d OLS)  : {slp_1w:>+7.4f}%/day  R²={r2_1w:.3f}  {arr(slp_1w,r2_1w)}")
print(f"  Slope ID  (1m OLS)  : {slp_id:>+7.4f}%/1m   R²={r2_id:.3f}  {arr(slp_id,r2_id)}")
print()

# Composite signal
up = (slp_1m>0) + (slp_1w>0) + (slp_id>0)
if "GOLD" in cross:   sig = "🟢 BREAKOUT — giá vừa cắt LÊN trên VWAP"
elif "DEATH" in cross: sig = "🔴 BREAKDOWN — giá vừa cắt XUỐNG dưới VWAP"
elif up>=2 and pct_vwap>0: sig = "▲ ALIGNED BULL — đa khung đều đang tăng"
elif up>=2 and pct_vwap<0: sig = "🔶 ACCUM ZONE — tích lũy dưới VWAP, slope vẫn dương"
elif up==0 and pct_vwap<0: sig = "▼ DIST/SELL — tất cả khung đều đang giảm"
else: sig = "➖ MIXED — xu hướng chưa rõ ràng"

print(f"  Signal   : {sig}")
print()

# PVWAP history 5 ngày gần nhất
print(f"  Lịch sử VWAP 5 phiên gần nhất:")
print(f"  {'Ngày':<12} {'VWAP':>6} {'Close':>6} {'Delta':>12} {'SideCov':>8} {'C vs V'}")
print(f"  {'-'*58}")
for r in daily[-5:]:
    cvv = "↑" if r[3] >= r[1] else "↓"
    nd  = r[5] or 0
    print(f"  {r[0]:<12} {r[1]:>6.2f} {r[3]:>6.2f} {nd:>+12,} {r[8]:>7.1f}%  {cvv}")

# Intraday detail
intra = conn.execute("""
    SELECT trade_time, close, volume,
           COALESCE(buy_vol,0), COALESCE(sell_vol,0)
    FROM stock_prices
    WHERE security_id=? AND interval='1m' AND date(trade_time)=?
    ORDER BY trade_time
""", (sid, DATE_VN)).fetchall()

if intra:
    print(f"\n  Intraday {DATE_VN}: {len(intra)} nến 1m")
    total_v  = sum(r[2] for r in intra) or 1
    total_bv = sum(r[3] for r in intra)
    total_sv = sum(r[4] for r in intra)
    nd_id    = total_bv - total_sv
    print(f"  Vol={total_v/1e6:.2f}M | NetΔ={nd_id:+,} | BuyR={total_bv*100.0/max(total_bv+total_sv,1):.1f}%")

    # Rolling VWAP visualization (first/mid/last)
    cum_pv=cum_v=0.0; rvwap_pts=[]
    for r in intra:
        if r[2]>0: cum_pv+=(r[1] or 0)*r[2]; cum_v+=r[2]
        rvwap_pts.append(cum_pv/cum_v if cum_v>0 else (r[1] or 0))
    n = len(rvwap_pts)
    if n >= 3:
        q1=rvwap_pts[n//4]; q2=rvwap_pts[n//2]; q3=rvwap_pts[3*n//4]; qf=rvwap_pts[-1]
        t1=str(intra[n//4][0])[11:16]; t2=str(intra[n//2][0])[11:16]
        t3=str(intra[3*n//4][0])[11:16]; tf=str(intra[-1][0])[11:16]
        print(f"\n  Rolling VWAP intraday:")
        print(f"    {t1}: {q1:.2f}  →  {t2}: {q2:.2f}  →  {t3}: {q3:.2f}  →  {tf}: {qf:.2f}")
        trend = "↑" if qf > q1 else "↓"
        print(f"    Direction: {trend} ({(qf-q1)/q1*100:+.3f}% từ đầu phiên)")

print(f"\n{'='*64}")
conn.close()
PYEOF
SSHEOF
```

### Mode B: Scan toàn thị trường / sàn / watchlist

// turbo
```bash
# Chạy trên Oracle Linux 192.168.2.2 (source of truth DB + 32GB RAM)
ssh -t tuanho@192.168.2.2 'bash -s' << 'SSHEOF'
cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 - << 'PYEOF'
import duckdb, numpy as np
from datetime import datetime, timezone, timedelta
from collections import defaultdict

DB      = "data/securities_master.db"
VN_TZ   = timezone(timedelta(hours=7))
DATE_VN = "{DATE_VN}"   # ← điền ngày hoặc để tự động
TARGET  = "{TARGET}"    # ← "market" | "watchlist" | "HOSE" | "HNX"
TOP_N   = {TOP_N}       # ← số mã top hiển thị (mặc định 20)
FILTER  = "{FILTER}"    # ← "GOLD" | "DEATH" | "BULL" | "ACCUM" | "all"

# Xử lý defaults
if not DATE_VN or DATE_VN == "{DATE_VN}":
    DATE_VN = datetime.now(VN_TZ).strftime("%Y-%m-%d")
if not TOP_N or str(TOP_N) == "{TOP_N}":
    TOP_N = 20
if not FILTER or FILTER == "{FILTER}":
    FILTER = "all"

WATCHLIST = ["HPG","SHB","MBB","ACB","VND","SSI","POW","VRE","PSI","NKG"]

# Sử dụng DuckDB kết nối SQLite để đọc batch siêu tốc độ
conn = duckdb.connect()
conn.execute("INSTALL sqlite; LOAD sqlite;")
conn.execute(f"ATTACH '{DB}' AS smd (TYPE sqlite);")

# ── Helper functions ──────────────────────────────────────────
def linreg(values):
    n = len(values)
    if n < 3: return 0.0, 0.0
    x = np.arange(n, dtype=float); y = np.array(values, dtype=float)
    xm,ym = x.mean(),y.mean()
    ss_xx=((x-xm)**2).sum(); ss_xy=((x-xm)*(y-ym)).sum()
    ss_yy=((y-ym)**2).sum()
    if ss_xx==0 or ym==0: return 0.0,0.0
    return round((ss_xy/ss_xx)/ym*100,4), round((ss_xy**2)/(ss_xx*ss_yy) if ss_yy>0 else 0.0, 3)

def cross_sig(hist):
    if len(hist)<2: return "—"
    vp,cp=hist[-2]; vc,cc=hist[-1]
    if vp==0 or vc==0: return "—"
    if cp<vp and cc>=vc: return "GOLD↑"
    if cp>=vp and cc<vc: return "DEATH↓"
    return "above" if cc>=vc else "below"

def composite(slp_1m, slp_1w, slp_id, pct_v, cross):
    up = (slp_1m>0)+(slp_1w>0)+(slp_id>0)
    if "GOLD"  in cross: return "BREAKOUT",  4
    if "DEATH" in cross: return "BREAKDOWN",  0
    if up>=2 and pct_v>0: return "BULL",      3
    if up>=2 and pct_v<0: return "ACCUM",     3
    if up==0 and pct_v<0: return "DIST",      1
    return "MIXED", 2

# ── Lấy danh sách mã cần scan và Batch Queries ────────────────
print(f"\n  [DuckDB] Fast extracting market data cho ngày {DATE_VN}...")

if TARGET.upper() == "WATCHLIST":
    where_sym = "AND s.symbol IN (" + ",".join([f"'{s}'" for s in WATCHLIST]) + ")"
else:
    where_sym = ""

# 1. Fetch Daily VWAP History (25 phiên) cho tất cả mã hợp lệ
daily_query = f"""
    WITH target_sids AS (
        SELECT DISTINCT ds.security_id, s.symbol
        FROM smd.daily_vwap_summary ds
        JOIN smd.securities s ON ds.security_id = s.security_id
        WHERE ds.trade_date = '{DATE_VN}' {where_sym}
    )
    SELECT ds.security_id, t.symbol, ds.trade_date, ds.vwap, ds.session_close
    FROM smd.daily_vwap_summary ds
    JOIN target_sids t ON ds.security_id = t.security_id
    WHERE ds.trade_date <= '{DATE_VN}'
      AND ds.trade_date >= CAST(CAST('{DATE_VN}' AS DATE) - INTERVAL 40 DAYS AS VARCHAR)
    ORDER BY ds.security_id, ds.trade_date ASC
"""
daily_rows = conn.execute(daily_query).fetchall()

daily_dict = defaultdict(list)
for r in daily_rows:
    daily_dict[(r[0], r[1])].append((r[2], r[3], r[4]))  # (date, vwap, close)

# 2. Fetch Intraday 1m candles (siêu tốc qua DuckDB)
intra_query = f"""
    WITH target_sids AS (
        SELECT DISTINCT security_id 
        FROM smd.daily_vwap_summary 
        WHERE trade_date = '{DATE_VN}'
    )
    SELECT security_id, close, volume
    FROM smd.stock_prices
    WHERE interval = '1m' AND CAST(trade_time AS VARCHAR) LIKE '{DATE_VN}%'
      AND volume > 0 AND close IS NOT NULL
      AND security_id IN (SELECT security_id FROM target_sids)
    ORDER BY security_id, trade_time ASC
"""
intra_rows = conn.execute(intra_query).fetchall()

intra_dict = defaultdict(list)
for r in intra_rows:
    intra_dict[r[0]].append((r[1], r[2]))  # (close, volume)

conn.close()

results = []
print(f"  Filter: {FILTER.upper()} | Top: {TOP_N}\n")

# Process in memory
for (sid, symbol), d_hist in daily_dict.items():
    if len(d_hist) < 2: continue
    
    # d_hist đã sort ASC (từ cũ đến mới)
    vwaps  = [x[1] for x in d_hist[-25:]]
    hist_x = [(x[1], x[2]) for x in d_hist[-25:]]

    w22 = vwaps[-22:] if len(vwaps)>=22 else vwaps
    w5  = vwaps[-5:]  if len(vwaps)>=5  else vwaps

    slp_1m,r2_1m = linreg(w22)
    slp_1w,r2_1w = linreg(w5)

    # Intraday slope
    i_hist = intra_dict.get(sid, [])
    slp_id = r2_id = 0.0
    if len(i_hist) >= 3:
        cpv=cv=0.0; rv=[]
        for c,v in i_hist:
            cpv+=c*v; cv+=v
            rv.append(cpv/cv if cv>0 else c)
        slp_id,r2_id = linreg(rv)

    cross   = cross_sig(hist_x)
    vwap_c  = d_hist[-1][1]
    close_c = d_hist[-1][2]
    pct_v   = (close_c-vwap_c)/vwap_c*100 if vwap_c else 0
    sig, priority = composite(slp_1m, slp_1w, slp_id, pct_v, cross)

    results.append({
        "sym": symbol, "close": close_c, "vwap": vwap_c,
        "pct_v": pct_v,
        "slp_1m": slp_1m, "r2_1m": r2_1m,
        "slp_1w": slp_1w, "r2_1w": r2_1w,
        "slp_id": slp_id, "r2_id": r2_id,
        "cross": cross, "sig": sig, "priority": priority,
    })

# ── Lọc và sắp xếp ───────────────────────────────────────────
FILTER_UP = FILTER.upper()
if FILTER_UP not in ("ALL", ""):
    results = [r for r in results if FILTER_UP in r["sig"].upper() or FILTER_UP in r["cross"].upper()]

# Sort: priority desc, sau đó |slp_1m| desc khi cùng priority
results.sort(key=lambda r: (-r["priority"], -abs(r["slp_1m"])))

# ── Print results ─────────────────────────────────────────────
def arr(s,r2):
    if r2<0.30: return "↔"
    if s>0.15: return "⬆⬆"
    if s>0.05: return "↗ "
    if s>0:    return "→↗"
    if s>-0.05:return "→↘"
    if s>-0.15:return "↘ "
    return "⬇⬇"

def icon(sig):
    return {"BREAKOUT":"🟢","BREAKDOWN":"🔴","BULL":"▲ ","ACCUM":"🔶","DIST":"▼ ","MIXED":"➖"}.get(sig,"  ")

sep = "="*108
print(sep)
print(f"  📐 VWAP SLOPE MARKET SCAN — {DATE_VN}  (filter={FILTER_UP}, top={TOP_N})")
print(f"  {'SYM':<7} {'Close':>7} {'vsVWAP':>7}  {'Slp1M':>8} R²  {'1M':>2}  {'Slp1W':>8} R²  {'1W':>2}  {'SlpID':>8} R²  {'ID':>2}  {'Cross':<10} {'Signal'}")
print(f"  {'-'*104}")

shown = 0
for d in results:
    if shown >= TOP_N: break
    cross_disp = d["cross"] if "GOLD" in d["cross"] or "DEATH" in d["cross"] else d["cross"][:10]
    print(
        f"  {d['sym']:<7} {d['close']:>7.2f} {d['pct_v']:>+6.2f}%"
        f"  {d['slp_1m']:>+7.3f}% {d['r2_1m']:.2f} {arr(d['slp_1m'],d['r2_1m'])}"
        f"  {d['slp_1w']:>+7.3f}% {d['r2_1w']:.2f} {arr(d['slp_1w'],d['r2_1w'])}"
        f"  {d['slp_id']:>+7.4f}% {d['r2_id']:.2f} {arr(d['slp_id'],d['r2_id'])}"
        f"  {cross_disp:<10} {icon(d['sig'])} {d['sig']}"
    )
    shown += 1

print(sep)
print(f"  Tổng: {len(results)} mã khớp filter | Hiển thị top {shown}")
print(f"  R² ≥ 0.5 = trend đáng tin | GOLD↑/DEATH↓ = vừa cắt VWAP")

# ── Thống kê tổng hợp ────────────────────────────────────────
from collections import Counter
sc = Counter(r["sig"] for r in results)
cr = Counter(
    "GOLD↑"  if "GOLD"  in r["cross"] else
    "DEATH↓" if "DEATH" in r["cross"] else
    "above"  if "above" in r["cross"] else "below"
    for r in results
)
print(f"\n  === MARKET SUMMARY ===")
print(f"  Signals  : " + " | ".join(f"{k}={v}" for k,v in sc.most_common()))
print(f"  Cross    : " + " | ".join(f"{k}={v}" for k,v in cr.most_common()))
print(f"  Breadth  : {cr.get('above',0)+cr.get('GOLD↑',0)} mã trên VWAP / {len(results)} tổng")
bull_pct = (cr.get('above',0)+cr.get('GOLD↑',0))*100//max(len(results),1)
print(f"  Market tone: {'🟢 BULLISH' if bull_pct>55 else '🔴 BEARISH' if bull_pct<45 else '🟡 NEUTRAL'} ({bull_pct}% trên VWAP)")

conn.close()
PYEOF
SSHEOF
```

---

## Bước 2 — AI Interpretation (Antigravity đọc kết quả)

Sau khi Bước 1 trả về dữ liệu, Antigravity phân tích theo khung sau:

### 2.1 — Đọc tín hiệu theo mức độ ưu tiên

| Priority | Signal | Đọc hiệu |
|---|---|---|
| **1 — Cao nhất** | 🟢 GOLD↑ / 🔴 DEATH↓ | Cross vừa xảy ra → **event-driven** → hành động ngay |
| **2 — Cao** | ▲ BULL / 🔶 ACCUM | Alignment đa khung → trend đang hình thành |
| **3 — Trung bình** | ➖ MIXED | Tín hiệu không nhất quán → theo dõi thêm |
| **4 — Yếu** | ▼ DIST | Bearish alignment → tránh hoặc cắt lỗ |

### 2.2 — Đọc Slope theo combo 1M + 1W

| 1M Slope | 1W Slope | Diễn giải |
|---|---|---|
| ⬆⬆ dương, R²≥0.5 | ⬆⬆ dương, R²≥0.5 | **Momentum mạnh nhất** — tất cả aligned upward |
| ⬆⬆ dương, R²≥0.5 | ⬇⬇ âm, R²≥0.5 | **Pullback trong uptrend** — cơ hội buy-the-dip |
| ↔ flat, R²<0.3 | ⬇⬇ âm, R²≥0.5 | **Cảnh báo sớm** — momentum đang chuyển |
| ⬇⬇ âm | ⬇⬇ âm | **Downtrend rõ ràng** — tránh xa |

### 2.3 — Đọc Intraday Slope

- `R²≥0.5` + slope dương → dòng tiền tích lũy đều trong phiên (professional buying)
- `R²≥0.8` + slope âm → sell-off có tổ chức (không phải panic, có chủ đích)
- `R²<0.3` → phiên hỗn loạn, không có trend rõ ràng

### 2.4 — Đọc Market Breadth (chỉ khi scan market)

- **Breadth > 55%** = thị trường bullish tone → ưu tiên long
- **Breadth < 45%** = thị trường bearish tone → thận trọng
- **Nhiều GOLD↑ cùng lúc** = potential market breakout day
- **Nhiều DEATH↓ cùng lúc** = distribution / rotation đang xảy ra

---

## Bước 3 — Kết luận và Khuyến nghị

Antigravity tổng hợp kết quả theo template:

```
📐 VWAP SLOPE REPORT — {DATE_VN}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[Nếu scan 1 mã:]
📌 {SYMBOL}  Close={close}  vs VWAP {pct:+.2f}%
📉 1M Slope : {slp_1m:+.4f}%/day  R²={r2_1m}  {arrow}
📉 1W Slope : {slp_1w:+.4f}%/day  R²={r2_1w}  {arrow}
📉 ID Slope : {slp_id:+.5f}%/1m   R²={r2_id}  {arrow}
🔀 Cross    : {cross}
🔔 Signal   : {signal}

Diễn giải: {1-2 câu giải thích bằng ngôn ngữ "hành vi fund"}
Kịch bản: {điều kiện cần theo dõi để hành động}

[Nếu scan market:]
🌏 Market Breadth: {%} mã trên VWAP → {tone}
🔝 Top GOLD↑ (breakout candidates): {danh sách 3-5 mã}
⚠️  Top DEATH↓ (breakdown warning): {danh sách 3-5 mã}
💎 Pullback trong uptrend (buy-the-dip): {danh sách}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Chú thích Kỹ thuật

```
Công thức Slope:
  slope_pct = (Σ(xi-x̄)(yi-ȳ) / Σ(xi-x̄)²) / ȳ × 100
  → % per period, normalized by mean VWAP

Công thức R²:
  R² = [Σ(xi-x̄)(yi-ȳ)]² / [Σ(xi-x̄)² × Σ(yi-ȳ)²]
  → 0..1, càng gần 1 trend càng "thẳng" và đáng tin

Cross Signal:
  GOLD↑  : session_close(T-1) < vwap(T-1)  AND  session_close(T) >= vwap(T)
  DEATH↓ : session_close(T-1) >= vwap(T-1) AND  session_close(T) < vwap(T)

Priority Sort:
  BREAKOUT=4 > BULL=ACCUM=3 > MIXED=2 > DIST=1 > BREAKDOWN=0
  Tie-break: |slp_1m| descending
```

---

## Lệnh nhanh tham khảo

```bash
# Tất cả lệnh đều chạy trên Oracle Linux 192.168.2.2
# DB luôn mới nhất | 32GB RAM cache

# Scan 1 mã
ssh tuanho@192.168.2.2 "cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 scripts/portfolio_watcher.py --scan --date 2026-04-24"

# Watchlist
ssh tuanho@192.168.2.2 "cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 scripts/portfolio_watcher.py --scan"

# Hoặc dùng alias đã có trong .zshrc:
qscan
```
