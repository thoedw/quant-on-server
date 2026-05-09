---
name: vwap
description: Phân tích toàn diện VWAP intraday + interday cho một mã cổ phiếu. Bóc tách hành vi fund manager qua PVWAP anchor, delta dòng tiền, bounce detection và signal scoring v3. Triệu tập bằng `/vwap` + mã cổ phiếu.
author: Antigravity
---

# Mục tiêu Kích hoạt

Khi người dùng gõ lệnh kiểu:
- `/vwap SHB`
- `/vwap HPG`
- `phân tích VWAP dòng tiền SHB`
- `em xem VWAP VCB hôm nay đi`

Antigravity BẮT BUỘC thực hiện **đúng 5 bước** dưới đây theo thứ tự. Trích xuất **mã cổ phiếu** từ yêu cầu của user (VD: "SHB"), và **ngày phân tích** (mặc định = hôm nay theo VN timezone, hoặc theo user chỉ định).

---

## Bước 1: Lấy Dữ liệu Nền (PVWAP + Intraday)

// turbo
```bash
# Chạy trên Oracle Linux 192.168.2.2 (source of truth DB + 32GB RAM)
ssh -t tuanho@192.168.2.2 'bash -s' << 'SSHEOF'
cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 - << 'PYEOF'
import sqlite3, sys, math
sys.path.insert(0,'.')
from realtime.vwap_engine import VWAPEngine, _session_open_utc
from collections import defaultdict
from datetime import datetime, timezone, timedelta

VN_TZ   = timezone(timedelta(hours=7))
DB      = 'data/securities_master.db'
SYMBOL  = '{SYMBOL}'  # ← Thay thế bằng mã user yêu cầu
DATE_VN = '{DATE_VN}' # ← Thay bằng ngày phân tích (YYYY-MM-DD) hoặc hôm nay

conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row

# 0. Security ID
row = conn.execute('SELECT security_id, symbol, exchange FROM securities WHERE symbol=?', (SYMBOL,)).fetchone()
if not row:
    print(f'❌ Không tìm thấy mã {SYMBOL}'); conn.close(); exit()
sid = row['security_id']
print(f'=== {SYMBOL} | security_id={sid} | {row[\"exchange\"]} | {DATE_VN} ===')

# 1. PVWAP — lịch sử 5 ngày từ daily_vwap_summary
pvwap_rows = conn.execute('''
    SELECT trade_date, vwap, vwap_upper1, vwap_lower1, vwap_upper2, vwap_lower2,
           cum_volume, cum_delta, buy_vol, sell_vol, side_cov_pct,
           session_open, session_close
    FROM daily_vwap_summary
    WHERE security_id=? AND trade_date < ?
    ORDER BY trade_date DESC LIMIT 5
''', (sid, DATE_VN)).fetchall()

print()
print('=== PVWAP HISTORY (5 phiên gần nhất) ===')
print(f'  {\"Date\":<12} {\"VWAP\":>6} {\"Open\":>6} {\"Close\":>6} {\"Vol(M)\":>7} {\"Delta\":>12} {\"SideCov\":>8}')
print('  '+'-'*65)
for r in pvwap_rows:
    trend = '▲' if r['session_close'] >= r['session_open'] else '▼'
    net_d = r['cum_delta'] or 0
    print(f'  {r[\"trade_date\"]:<12} {r[\"vwap\"]:>6.2f} {r[\"session_open\"]:>6.2f} {trend}{r[\"session_close\"]:>5.2f} {r[\"cum_volume\"]/1e6:>7.1f}M {net_d:>+12,} {r[\"side_cov_pct\"]:>7.1f}%')

pvwap = dict(pvwap_rows[0]) if pvwap_rows else None
if pvwap:
    pv = pvwap['vwap']
    print(f'''
  PVWAP hôm qua ({pvwap[\"trade_date\"]}): {pv:.2f}
  Bands : [{pvwap[\"vwap_lower2\"]:.2f} | {pvwap[\"vwap_lower1\"]:.2f} | PVWAP {pv:.2f} | {pvwap[\"vwap_upper1\"]:.2f} | {pvwap[\"vwap_upper2\"]:.2f}]
  Delta Hôm qua: {pvwap[\"cum_delta\"]:+,} (buy={pvwap[\"buy_vol\"]:,} sell={pvwap[\"sell_vol\"]:,})''")
else:
    pv = None

# 2. Intraday hôm nay
session_open = _session_open_utc(DATE_VN)
rows = conn.execute('''
    SELECT trade_time, open, high, low, close, volume,
           COALESCE(buy_vol,0) as bv, COALESCE(sell_vol,0) as sv,
           COALESCE(buy_vol,0)-COALESCE(sell_vol,0) as delta
    FROM stock_prices
    WHERE security_id=? AND interval='1m'
      AND trade_time>=? AND date(trade_time,'+7 hours')=?
    ORDER BY trade_time
''', (sid, session_open, DATE_VN)).fetchall()

print(f'''
=== INTRADAY {DATE_VN} ({len(rows)} nến 1m) ===''")

if not rows:
    print('  ❌ Không có dữ liệu 1m hôm nay')
    conn.close(); exit()

first = rows[0]; last = rows[-1]
total_vol = sum(r[5] for r in rows)
total_bv  = sum(r[6] for r in rows)
total_sv  = sum(r[7] for r in rows)
total_d   = sum(r[8] for r in rows)
h_high    = max(r[2] for r in rows)
h_low     = min(r[3] for r in rows)
side_cov  = round((total_bv+total_sv)*100.0/max(total_vol,1),1)

# VWAP hôm nay
cum_pv=cum_v=cum_pv2=0.0
for r in rows:
    p=r[4] or 0.0; v=r[5] or 0
    cum_pv+=p*v; cum_v+=v; cum_pv2+=p*p*v
vwap_today = cum_pv/cum_v if cum_v>0 else 0
std = math.sqrt(max(0,(cum_pv2/cum_v)-vwap_today**2)) if cum_v>0 else 0

pos_vs_vwap = 'TRÊN' if last[4]>vwap_today else 'DƯỚI'
pvwap_cmp   = f'TRÊN PVWAP' if (pv and last[4]>pv) else f'DƯỚI PVWAP' if pv else ''

print(f'''  Open  : {first[1]:.2f}  |  High: {h_high:.2f}  |  Low: {h_low:.2f}  |  Close: {last[4]:.2f}
  Volume: {total_vol/1e6:.2f}M CP  |  Buy: {total_bv/1e6:.2f}M  |  Sell: {total_sv/1e6:.2f}M  |  Delta: {total_d:+,}
  Side Coverage: {side_cov}%
  VWAP hôm nay: {vwap_today:.2f}  ±1σ=[{vwap_today-std:.2f}, {vwap_today+std:.2f}]  ±2σ=[{vwap_today-2*std:.2f}, {vwap_today+2*std:.2f}]
  Close vs VWAP : {pos_vs_vwap} ({abs(last[4]-vwap_today)/vwap_today*100:.2f}%)''")

if pv:
    pct_pvwap = (last[4]-pv)/pv*100
    print(f'  Close vs PVWAP: {pvwap_cmp} ({pct_pvwap:+.2f}%)')

# 3. Timeline delta accumulation theo giờ
print(f'\n  Diễn biến delta theo giờ:')
buckets = defaultdict(lambda: {'bv':0,'sv':0,'v':0,'close':0})
for r in rows:
    t_str = str(r[0]).replace('T',' ')
    # extract local VN hour directly
    try:
        import re
        nums = re.findall(r'\\d+', t_str)
        h_vn = int(nums[3]) if len(nums)>3 else 0
        bucket= f'{h_vn:02d}h'
    except: bucket='??h'
    buckets[bucket]['bv'] += r[6]; buckets[bucket]['sv'] += r[7]
    buckets[bucket]['v']  += r[5]; buckets[bucket]['close'] = r[4]

for bk in sorted(buckets):
    b = buckets[bk]
    d = b['bv']-b['sv']
    bar = '█'*min(int(abs(d)/30000),20)
    sign = '+' if d>=0 else '-'
    print(f'    {bk}: vol={b[\"v\"]/1e6:.2f}M  delta={d:>+10,}  {sign}{bar}')

# 4. 10 nến gần nhất
print(f'\n  10 nến 1m gần nhất:')
print(f'  {\"Time (VN+7)\":<17} {\"O\":>6} {\"H\":>6} {\"L\":>6} {\"C\":>6} {\"Vol\":>9} {\"Δ\":>10} {\"T\"}')
for r in rows[-10:]:
    t_str = str(r[0]).replace('T',' ')
    try:
        import re
        nums = re.findall(r'\\d+', t_str)
        h_vn = int(nums[3]) if len(nums)>3 else 0; m_vn = int(nums[4]) if len(nums)>4 else 0
        t_vn = f'{h_vn:02d}:{m_vn:02d}'
    except: t_vn='?'
    d = r[8]
    trend = '▲' if r[4]>=r[1] else '▼'
    print(f'  {t_vn:<17} {r[1]:>6.2f} {r[2]:>6.2f} {r[3]:>6.2f} {r[4]:>6.2f} {r[5]:>9,} {d:>+10,} {trend}')

# 5. Bounce count
bounces=0; was_below=False
for r in rows[-20:]:
    c=r[4] or 0.0
    if c<=0: continue
    if c<vwap_today*0.998: was_below=True
    elif was_below and c>=vwap_today*0.998: bounces+=1; was_below=False
print(f'\n  VWAP bounces (20 nến cuối): {bounces}')

# 6. EOD 1D check
eod = conn.execute('''
    SELECT trade_time, open, high, low, close, volume, buy_vol, sell_vol
    FROM stock_prices WHERE security_id=? AND interval='1D'
    ORDER BY trade_time DESC LIMIT 3
''', (sid,)).fetchall()
if eod:
    print(f'\n  EOD 1D gần nhất:')
    for r in eod:
        print(f'    {r[0][:10]}: O={r[1]:.2f} H={r[2]:.2f} L={r[3]:.2f} C={r[4]:.2f} Vol={r[5]/1e6:.2f}M buy={r[6]} sell={r[7]}')

conn.close()
PYEOF
SSHEOF
```

**Lưu ý thay thế:** trước khi chạy, hãy điền `{SYMBOL}` = mã user cung cấp (VD: `SHB`) và `{DATE_VN}` = ngày phân tích (VD: `2026-04-22`).

---

## Bước 2: Chạy Whale Hunter Signals (v3)

// turbo
```bash
# Chạy trên Oracle Linux 192.168.2.2 (source of truth DB + 32GB RAM)
ssh -t tuanho@192.168.2.2 'bash -s' << 'SSHEOF'
cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 - << 'PYEOF'
import sqlite3, sys
sys.path.insert(0,'.')
from realtime.vwap_engine import VWAPEngine
from scripts.whale_hunter import (
    score_hidden_accumulation, score_vwap_reclaim,
    score_delta_divergence, score_vwap_rejection,
    score_pvwap_support_test, score_vwap_bounce,
    _compute_vol_surge, _band_zone,
    WhaleHunter, MIN_SCORE, SIDE_QUALITY_GATE
)

DB      = 'data/securities_master.db'
SYMBOL  = '{SYMBOL}'
DATE_VN = '{DATE_VN}'

conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
sid = conn.execute('SELECT security_id FROM securities WHERE symbol=?', (SYMBOL,)).fetchone()[0]

# Tính side quality
row = conn.execute('''
    SELECT SUM(COALESCE(volume,0)), SUM(COALESCE(buy_vol,0)+COALESCE(sell_vol,0))
    FROM stock_prices WHERE interval='1m' AND date(trade_time,'+7 hours')=? AND volume>0
''', (DATE_VN,)).fetchone()
side_cov = round((row[1] or 0)*100.0/max(row[0] or 1,1), 1)
delta_reliable = side_cov >= SIDE_QUALITY_GATE

# Snapshot VWAP
eng   = VWAPEngine(DB)
snaps = eng.compute_all(top_n=800, date_vn=DATE_VN)
snap_obj = next((s for s in snaps if s.security_id==sid), None)

if not snap_obj:
    print(f'❌ Không có VWAP snapshot cho {SYMBOL} ngày {DATE_VN}')
    conn.close(); exit()

snap = {
    'snapshot_time': snap_obj.snapshot_time,
    'vwap': snap_obj.vwap, 'last_close': snap_obj.last_close,
    'vwap_upper1': snap_obj.vwap_upper1, 'vwap_lower1': snap_obj.vwap_lower1,
    'vwap_upper2': snap_obj.vwap_upper2, 'vwap_lower2': snap_obj.vwap_lower2,
    'cum_volume': snap_obj.cum_volume, 'cum_delta': snap_obj.cum_delta,
}

wh = WhaleHunter(DB)
recent = wh._get_recent_candles(conn, sid, n=12)
vs     = _compute_vol_surge(recent)
pvwap  = wh._get_pvwap(conn, sid)
prev   = wh._get_prev_vwap(conn, sid, snap['snapshot_time'])

print(f'=== WHALE HUNTER v3 SIGNALS — {SYMBOL} {DATE_VN} ===')
print(f'Side coverage market: {side_cov}% | delta_reliable={delta_reliable}')
print(f'Vol surge (recent): {vs:.1f}x | Band zone: {_band_zone(snap)}')
if pvwap:
    print(f'PVWAP ({pvwap[\"trade_date\"]}): {pvwap[\"vwap\"]:.2f}')
print()

ICONS = {
    'HIDDEN_ACCUMULATION':'🐋','VWAP_RECLAIM':'🚀','DELTA_DIVERGENCE':'📊',
    'VWAP_REJECTION':'🔴','PVWAP_SUPPORT_TEST':'🎯','VWAP_BOUNCE':'🔁',
}

signals = []
checks = [
    ('HIDDEN_ACCUMULATION', 'BUY',  *score_hidden_accumulation(snap, recent, vs, delta_reliable)),
    ('DELTA_DIVERGENCE',    'BUY',  *score_delta_divergence(snap, recent, vs, delta_reliable)),
    ('PVWAP_SUPPORT_TEST',  'BUY',  *score_pvwap_support_test(snap, pvwap, recent, vs, delta_reliable)),
    ('VWAP_BOUNCE',         'BUY',  *score_vwap_bounce(snap, recent, vs)),
]
s2,d2 = score_vwap_reclaim(snap, prev, vs, delta_reliable)
s4,d4 = score_vwap_rejection(snap, recent, vs, delta_reliable)
if s2>=MIN_SCORE and s4>=MIN_SCORE:
    checks.append(('VWAP_RECLAIM','BUY',s2,d2) if s2>s4 else ('VWAP_REJECTION','SELL',s4,d4))
elif s2>=MIN_SCORE: checks.append(('VWAP_RECLAIM','BUY',s2,d2))
elif s4>=MIN_SCORE: checks.append(('VWAP_REJECTION','SELL',s4,d4))

for sig,dir_,sc,det in checks:
    icon = ICONS.get(sig,'•')
    status = '✅ TRIGGER' if sc>=MIN_SCORE else '○ miss'
    print(f'  {icon} {sig:<22} | {sc:>5.1f} | {dir_:<5} | {status}')
    if sc>=MIN_SCORE:
        for k,v in det.items():
            print(f'       {k}: {v}')
        signals.append((sig,dir_,sc,det))

print()
if signals:
    print(f'🔔 KẾT LUẬN: {len(signals)} signal(s) kích hoạt cho {SYMBOL}:')
    for sig,dir_,sc,det in sorted(signals,key=lambda x:-x[2]):
        print(f'   {ICONS[sig]} {sig} [{dir_}] score={sc:.0f}')
else:
    print(f'⚪ Không có signal nào đạt ngưỡng {MIN_SCORE} cho {SYMBOL} hôm nay')

conn.close()
PYEOF
SSHEOF
```

---

## Bước 3: Phân tích Dòng tiền Tổ chức (AI Interpretation)

Sau khi 2 bước trên trả về số liệu, Antigravity đóng vai **Quản lý Quỹ / Portfolio Manager** để diễn giải kết quả theo mô hình sau:

### 3.1 — Bối cảnh PVWAP (Interday)
- So sánh giá hiện tại với PVWAP: đang trên/dưới?
- Xu hướng delta 5 ngày: net mua hay bán?
- Nếu giá dưới PVWAP nhiều ngày + delta dương → **Silent Accumulation** (gom hàng thầm lặng)
- Nếu giá trên PVWAP nhiều ngày + delta giảm → **Distribution** (xả hàng)

### 3.2 — Hành vi Intraday
- Nhận xét timeline delta từng giờ: giờ nào dòng tiền mạnh nhất?
- Nến bất thường: vol surge > 3× bình thường ở đâu? Giải thích context (absorption hay breakout)
- Bounce count: fund đang bảo vệ VWAP hay để giá tự do?

### 3.3 — Đánh giá Tín hiệu WH v3
- Giải thích từng signal kích hoạt theo ngôn ngữ "hành vi fund"
- Side Coverage %: MASVN feed có đủ tin cậy không?
- Kết luận: BUY / SELL / WAIT + lý do tóm tắt 1-2 câu

### 3.4 — Kịch bản Tiếp theo
- Nếu BUY: chờ gì để vào lệnh? (VD: "Nếu ngày mai SHB mở cửa > PVWAP 15.13 + vol surge → PVWAP_SUPPORT_TEST kích hoạt")
- Nếu SELL: ngưỡng xả nào? (VD: "Rejection tại PVWAP + delta âm → thoát")
- Nếu WAIT: cần thêm dữ liệu gì?

---

## Bước 4: Kiểm tra Dữ liệu Liên quan (Optional — khi user yêu cầu thêm)

Nếu user muốn đào sâu hơn, chạy thêm:

```bash
# Chạy trên Oracle Linux 192.168.2.2
ssh -t tuanho@192.168.2.2 'bash -s' << 'SSHEOF'
cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 - << 'PYEOF'
import sqlite3
conn = sqlite3.connect('data/securities_master.db')
rows = conn.execute('''
    SELECT ws.signal_type, ws.direction, ws.score, ws.signal_time,
           ws.last_close, ws.vwap, ws.details_json
    FROM whale_signals ws
    JOIN securities s ON ws.security_id=s.security_id
    WHERE s.symbol='{SYMBOL}'
    ORDER BY ws.signal_time DESC LIMIT 10
''').fetchall()
if not rows:
    print('Chưa có signal nào lưu trong DB cho {SYMBOL}')
else:
    for r in rows:
        print(f'{r[3]} | {r[0]:<22} | {r[1]} | score={r[2]} | close={r[4]} vwap={r[5]}')
conn.close()
PYEOF
SSHEOF
```

---

## Bước 5: Báo cáo Cuối cùng

Antigravity tổng hợp toàn bộ thành một **báo cáo ngắn gọn** gồm:

```
📌 [SYMBOL] — VWAP Analysis {DATE_VN}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 PVWAP        : {giá trị}  ({ngày hôm qua})
📈 Close hôm nay: {giá trị}  ({trên/dưới} PVWAP {x}%)
💧 VWAP hôm nay : {giá trị}
🔄 Net Delta    : {tổng delta cả ngày}
👁️ Side Coverage: {%}

🔔 Signals Kích hoạt:
   {ICON} {TÊN SIGNAL} [BUY/SELL] score={điểm}
   └─ {1-2 câu giải thích theo hành vi fund}

📋 Kết luận: {BUY/SELL/WAIT — lý do tóm tắt}
📅 Kịch bản Tiếp theo: {điều kiện cần theo dõi}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
