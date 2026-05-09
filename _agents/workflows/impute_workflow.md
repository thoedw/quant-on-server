---
name: impute
description: |
  Kiểm tra và vá dữ liệu buy_vol=0 trong DB bằng BVC Imputer (Bulk Volume Classification)
  và Side Imputer (Volume Position Method) + PT Vol Imputer.
  Chạy trên Oracle Linux 192.168.2.2 (source of truth DB).
  Triệu tập bằng:
    /impute                    ← dry-run hôm nay (preview)
    /impute --commit           ← vá hôm nay, ghi DB
    /impute --date 2026-04-23  ← vá 1 ngày cụ thể
    /impute --since 2026-04-01 ← backfill từ ngày đó đến nay
    /impute --check HPG,SHB    ← kiểm tra nhanh 1-2 mã
author: Antigravity
version: "2.0"
---

# Mục tiêu Kích hoạt

Workflow này kích hoạt khi user yêu cầu:
- `/impute` / `vá buy_vol` / `kiểm tra side imputer`
- `buy_vol hôm nay có bao nhiêu mã bằng 0?`
- `chạy impute cho ngày 2026-04-23`
- `backfill impute từ đầu tháng`

**Trích xuất từ yêu cầu user:**
- `{DATE}` = ngày cụ thể (YYYY-MM-DD) | để trống = hôm nay VN
- `{SINCE}` = ngày bắt đầu backfill (nếu user yêu cầu backfill)
- `{SYMBOLS}` = danh sách mã cụ thể, VD: `HPG,SHB` | để trống = toàn DB
- `{COMMIT}` = `--commit` nếu user muốn ghi DB | để trống = dry-run (preview)

---

## Bước 1: Chẩn đoán — Kiểm tra buy_vol=0 trong DB

// turbo
```bash
# Chạy trên Oracle Linux 192.168.2.2 (source of truth DB)
ssh -t tuanho@192.168.2.2 'bash -s' << 'SSHEOF'
cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 - << 'PYEOF'
import sqlite3
from datetime import datetime, timezone, timedelta

VN_TZ   = timezone(timedelta(hours=7))
DB      = 'data/securities_master.db'
DATE_VN = '{DATE}'  # ← điền ngày hoặc để auto

if not DATE_VN or DATE_VN == '{DATE}':
    DATE_VN = datetime.now(VN_TZ).strftime('%Y-%m-%d')

conn = sqlite3.connect(DB)

print(f'=== SIDE IMPUTER DIAGNOSIS — {DATE_VN} ===')
print()

# 1. Tổng quan buy_vol=0 ngày hôm nay (1D interval)
row = conn.execute("""
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN COALESCE(buy_vol,0)=0 AND volume>0 THEN 1 ELSE 0 END) as missing,
        SUM(CASE WHEN COALESCE(buy_vol,0)>0 THEN 1 ELSE 0 END) as has_side
    FROM stock_prices
    WHERE interval='1D' AND date(trade_time)=?
""", (DATE_VN,)).fetchone()

if row and row[0] > 0:
    total, missing, has_side = row
    pct_ok = has_side * 100.0 / max(total, 1)
    print(f'  1D interval [{DATE_VN}]:')
    print(f'    Tổng mã có giá : {total:>5}')
    print(f'    Có buy_vol     : {has_side:>5}  ({pct_ok:.1f}%)')
    print(f'    buy_vol = 0    : {missing:>5}  ({100-pct_ok:.1f}%) ← cần vá')
else:
    print(f'  ❌ Không có dữ liệu 1D cho ngày {DATE_VN}')

print()

# 2. Breakdown theo sàn
rows = conn.execute("""
    SELECT s.exchange,
        COUNT(*) as total,
        SUM(CASE WHEN COALESCE(sp.buy_vol,0)=0 AND sp.volume>0 THEN 1 ELSE 0 END) as missing
    FROM stock_prices sp
    JOIN securities s ON sp.security_id=s.security_id
    WHERE sp.interval='1D' AND date(sp.trade_time)=?
    GROUP BY s.exchange
    ORDER BY missing DESC
""", (DATE_VN,)).fetchall()

print(f'  Breakdown theo sàn:')
print(f'  {"Sàn":<8} {"Tổng":>6} {"Thiếu":>6} {"Tỷ lệ":>8}')
print(f'  {"-"*32}')
for r in rows:
    pct = r[2] * 100.0 / max(r[1], 1)
    status = '✅' if pct < 5 else '⚠️ ' if pct < 20 else '❌'
    print(f'  {r[0]:<8} {r[1]:>6} {r[2]:>6} {pct:>7.1f}%  {status}')

print()

# 3. 10 mã thiếu buy_vol đáng chú ý (vol cao nhất)
rows = conn.execute("""
    SELECT s.symbol, s.exchange, sp.volume, sp.close
    FROM stock_prices sp
    JOIN securities s ON sp.security_id=s.security_id
    WHERE sp.interval='1D' AND date(sp.trade_time)=?
      AND COALESCE(sp.buy_vol,0)=0 AND sp.volume>0
    ORDER BY sp.volume DESC LIMIT 10
""", (DATE_VN,)).fetchall()

if rows:
    print(f'  Top 10 mã vol cao nhất đang thiếu buy_vol:')
    print(f'  {"Symbol":<8} {"Sàn":<6} {"Volume":>12} {"Close":>8}')
    print(f'  {"-"*38}')
    for r in rows:
        print(f'  {r[0]:<8} {r[1]:<6} {r[2]:>12,} {r[3]:>8.2f}')
else:
    print(f'  ✅ Không có mã nào thiếu buy_vol ngày {DATE_VN}!')

# 4. Kiểm tra 7 ngày gần nhất
print()
print(f'  Tổng quan 7 ngày gần nhất:')
print(f'  {"Ngày":<12} {"Tổng":>6} {"Thiếu":>6} {"OK%":>8}')
print(f'  {"-"*35}')
rows7 = conn.execute("""
    SELECT date(trade_time) as d,
        COUNT(*) as total,
        SUM(CASE WHEN COALESCE(buy_vol,0)=0 AND volume>0 THEN 1 ELSE 0 END) as missing
    FROM stock_prices
    WHERE interval='1D' AND date(trade_time) <= ?
    GROUP BY d ORDER BY d DESC LIMIT 7
""", (DATE_VN,)).fetchall()
for r in rows7:
    pct = (r[1]-r[2])*100.0/max(r[1],1)
    bar = '█' * int(pct/5)
    print(f'  {r[0]:<12} {r[1]:>6} {r[2]:>6} {pct:>7.1f}%  {bar}')

conn.close()
PYEOF
SSHEOF
```

---

## Bước 2: Chạy Imputer

Có 2 imputer, dùng theo ưu tiên:
- **BVC Imputer** (khuyến nghị) — Bulk Volume Classification (Easley et al. 2016), ~87% accuracy, chỉ cần OHLCV 1m
- **Side Imputer** (legacy) — Volume Position Method, ~72% accuracy

### Mode A — Dry-run (preview, không ghi DB)

// turbo
```bash
# Xem trước kết quả BVC — an toàn, không ghi gì
ssh -t tuanho@192.168.2.3 "cd ~/quant && source venv_py11/bin/activate && PYTHONPATH=. python3 scripts/bvc_imputer.py --date {DATE} 2>&1"
```

### Mode B — BVC Commit hôm nay (KHUYẾN NGHỊ)

// turbo
```bash
ssh -t tuanho@192.168.2.3 "cd ~/quant && source venv_py11/bin/activate && PYTHONPATH=. python3 scripts/bvc_imputer.py --date {DATE} --commit --no-vwap-rebuild 2>&1"
```

### Mode C — BVC Backfill từ ngày {SINCE}

// turbo
```bash
ssh -t tuanho@192.168.2.3 "cd ~/quant && source venv_py11/bin/activate && PYTHONPATH=. python3 scripts/bvc_imputer.py --since {SINCE} --commit --no-vwap-rebuild 2>&1"
```

### Mode D — BVC Backfill toàn bộ lịch sử

// turbo
```bash
ssh -t tuanho@192.168.2.3 "cd ~/quant && source venv_py11/bin/activate && PYTHONPATH=. python3 scripts/bvc_imputer.py --all --commit --no-vwap-rebuild 2>&1"
```

### Mode E — Side Imputer legacy (khi cần debug/so sánh)

// turbo
```bash
ssh -t tuanho@192.168.2.3 "cd ~/quant && source venv_py11/bin/activate && PYTHONPATH=. python3 scripts/side_imputer.py --date {DATE} --commit --no-vwap-rebuild 2>&1 | head -50"
```

---

## Bước 3: Chạy PT Vol Imputer (nếu cần)

PT Vol Imputer lấy dữ liệu `pt_vol` (block trade/thỏa thuận) từ Yahoo Finance.
Chỉ chạy khi Side Imputer không đủ hoặc user yêu cầu kiểm tra `pt_vol`.

```bash
# Dry-run
ssh -t tuanho@192.168.2.2 "cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 scripts/ptvol_imputer.py --date {DATE} --dry-run 2>&1 | head -30"

# Commit
ssh -t tuanho@192.168.2.2 "cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 scripts/ptvol_imputer.py --date {DATE} 2>&1"

# Chỉ cho một số mã cụ thể
ssh -t tuanho@192.168.2.2 "cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 scripts/ptvol_imputer.py --date {DATE} --symbols {SYMBOLS} 2>&1"
```

---

## Bước 4: Verify sau khi Impute

// turbo
```bash
# Kiểm tra lại kết quả sau khi commit — so sánh with Bước 1
ssh -t tuanho@192.168.2.2 'bash -s' << 'SSHEOF'
cd ~/quant && source venv/bin/activate && PYTHONPATH=. python3 - << 'PYEOF'
import sqlite3
from datetime import datetime, timezone, timedelta

VN_TZ   = timezone(timedelta(hours=7))
DB      = 'data/securities_master.db'
DATE_VN = '{DATE}'

if not DATE_VN or DATE_VN == '{DATE}':
    DATE_VN = datetime.now(VN_TZ).strftime('%Y-%m-%d')

conn = sqlite3.connect(DB)

print(f'=== POST-IMPUTE VERIFY — {DATE_VN} ===')

row = conn.execute("""
    SELECT
        COUNT(*) as total,
        SUM(CASE WHEN COALESCE(buy_vol,0)=0 AND volume>0 THEN 1 ELSE 0 END) as missing,
        SUM(CASE WHEN COALESCE(buy_vol,0)>0 THEN 1 ELSE 0 END) as has_side,
        SUM(volume) as total_vol,
        SUM(buy_vol) as total_bv,
        SUM(sell_vol) as total_sv
    FROM stock_prices
    WHERE interval='1D' AND date(trade_time)=?
""", (DATE_VN,)).fetchone()

total, missing, has_side, tvol, tbv, tsv = row
pct_ok = has_side * 100.0 / max(total, 1)
side_cov = (tbv+tsv)*100.0/max(tvol,1) if tvol else 0

print(f'  buy_vol coverage : {pct_ok:.1f}%  ({has_side}/{total} mã)')
print(f'  side coverage    : {side_cov:.1f}%  (buy+sell / total vol)')
print(f'  còn thiếu        : {missing} mã')
print()

if missing == 0:
    print('  ✅ HOÀN HẢO — Toàn bộ mã đã có buy_vol!')
elif pct_ok >= 90:
    print(f'  ✅ TỐT — {pct_ok:.1f}% coverage, còn {missing} mã nhỏ chưa có data')
elif pct_ok >= 80:
    print(f'  ⚠️  CHẤP NHẬN — {pct_ok:.1f}% coverage')
else:
    print(f'  ❌ CHƯA ĐỦ — {pct_ok:.1f}% coverage, cần kiểm tra lại')

conn.close()
PYEOF
SSHEOF
```

---

## Bước 5: AI Interpretation

Sau khi chạy các bước trên, Antigravity nhận xét:

1. **Tỷ lệ coverage**: ≥90% là đạt — nếu thấp hơn cần investigate thêm
2. **Pattern thiếu**: Nếu cùng sàn/nhóm mã thiếu nhiều → có thể vấn đề data source
3. **Khả năng BVC**: Nếu MASVN frozen → BVC phủ coverage 1544 mã với ~87% accuracy
4. **Khả năng Side Imputer legacy**: Chỉ dùng khi debug/so sánh

---

## Lệnh nhanh tham khảo

```bash
# BVC Imputer hôm nay (KHUYẾN NGHỊ — ~87% accuracy)
ssh tuanho@192.168.2.3 "cd ~/quant && source venv_py11/bin/activate && PYTHONPATH=. \
  python3 scripts/bvc_imputer.py --commit --date TODAY --no-vwap-rebuild"

# BVC Check coverage
ssh tuanho@192.168.2.3 "cd ~/quant && source venv_py11/bin/activate && PYTHONPATH=. \
  python3 scripts/bvc_imputer.py --check-only"

# BVC Backfill toàn bộ lịch sử (chạy 1 lần)
ssh tuanho@192.168.2.3 "cd ~/quant && source venv_py11/bin/activate && PYTHONPATH=. \
  python3 scripts/bvc_imputer.py --all --commit --no-vwap-rebuild"
```

> **Tự động hóa**: BVC được tích hợp vào 2 điểm:
> 1. **`dnse_refill_worker.py`** — chạy real-time mỗi 5 phút trong phiên, fill ngậy sau DNSE upsert
> 2. **`eod_daily_close.py` Phase 2.65** — chạy EOD lúc 15:45, fill các bars còn thiếu sau khi có đủ DNSE data
> 3. **`bvc_imputer.py`** — chạy thủ công khi cần backfill hoặc kiểm tra
