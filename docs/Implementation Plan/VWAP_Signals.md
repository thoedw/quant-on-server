# Kế hoạch: VWAP Whale Hunter — Hệ thống phát hiện tín hiệu cá mập

## Bối cảnh & Mục tiêu

**Mục tiêu:** Xây dựng hệ thống tự động phát hiện các mã cổ phiếu có dấu hiệu tích lũy của tổ chức (cá mập) dựa trên VWAP + Order Flow Delta từ dữ liệu real-time và EOD.

**Dữ liệu hiện có:**
- `stock_prices`: `open, high, low, close, volume, buy_vol, sell_vol, delta` — tất cả timeframes (1m→1W)
- Real-time: 38,028 nến/ngày, 777 mã active
- Net Delta hôm nay: **+56,973,342 CP** (thị trường đang NET BUY)

---

## Lý thuyết: Cá mập hoạt động như thế nào?

```
Tổ chức (Smart Money) KHÔNG muốn đẩy giá khi mua:
→ Họ mua từ từ, thường XPÍ dưới VWAP (giá tốt hơn thị trường)
→ Price action đi sideways hoặc giảm nhẹ nhưng delta tích lũy dương
→ Khi đủ hàng, họ "release" → giá bùng vượt VWAP + volume đột biến

Dấu hiệu nhận dạng:
1. HIDDEN ACCUMULATION: Giá ≤ VWAP nhưng cumulative_delta > 0
2. VWAP RECLAIM: Giá từ dưới VWAP cross lên trên với volume surge
3. DELTA DIVERGENCE: Giá giảm nhưng delta tăng (cá mập đỡ hàng)
4. VALUE AREA BOUNCE: Giá test vào POC (Point of Control) rồi bật
```

---

## Kiến trúc hệ thống (3 tầng)

```
┌───────────────────────────────────────────────────┐
│  TẦNG 1: VWAP ENGINE                              │
│  Tính VWAP + Bands + Cumulative Delta real-time   │
│  Input: stock_prices (1m)                         │
│  Output: vwap_intraday bảng trong SQLite / Redis  │
└───────────────────────────────────────────────────┘
                        ↓
┌───────────────────────────────────────────────────┐
│  TẦNG 2: SIGNAL DETECTOR                          │
│  Quét tín hiệu mỗi 5 phút trong giờ giao dịch    │
│  4 loại tín hiệu (xem bên dưới)                   │
│  Output: alerts table + Redis Pub/Sub              │
└───────────────────────────────────────────────────┘
                        ↓
┌───────────────────────────────────────────────────┐
│  TẦNG 3: ALERT DELIVERY                           │
│  Email báo cáo + Price Board Dashboard highlight   │
└───────────────────────────────────────────────────┘
```

---

## 4 Loại tín hiệu (Signals)

### Signal 1: HIDDEN_ACCUMULATION 🐋
**"Cá mập gom hàng bí mật"**
```
Điều kiện:
  - close < VWAP (giá dưới thị trường)
  - cumulative_delta (từ 9:15) > threshold dương
  - delta của nến hiện tại > 0
  - volume > 0 (có giao dịch thật)
  - Kéo dài ít nhất 3 nến 5m liên tiếp

Ý nghĩa: Tổ chức mua mà không đẩy giá lên
→ BUY SETUP: Entry khi giá bắt đầu reclaim VWAP
```

### Signal 2: VWAP_RECLAIM 🚀
**"Đột phá tái chiếm VWAP"**
```
Điều kiện:
  - Nến trước: close < VWAP
  - Nến hiện tại: close > VWAP
  - volume > 2.0x moving_avg_volume (5 ngày, cùng giờ)
  - delta > 0 (mua chủ động)
  - Xảy ra sau Hidden Accumulation (có confirmation)

Ý nghĩa: Cá mập đã gom đủ, bắt đầu bơm
→ BUY SIGNAL: Entry ngay khi candle đóng
→ Stop Loss: Dưới VWAP
→ Target: VWAP + 1 Standard Deviation
```

### Signal 3: DELTA_DIVERGENCE 📊
**"Phân kỳ dòng tiền"**
```
Điều kiện:
  - Giá giảm liên tiếp 3 nến (lower close)
  - Nhưng cumulative_delta tăng liên tiếp (net buying)
  - Volume tăng trong khi giá giảm

Ý nghĩa: Có lực đỡ vô hình — cá mập đang mua dip
→ BUY SETUP: Chờ nến xanh đầu tiên để confirm
→ Strong signal nếu kết hợp với giá gần POC
```

### Signal 4: VWAP_REJECTION (BÁN) 🔴
**"Kháng cự VWAP"**
```
Điều kiện:
  - close > VWAP (giá trên thị trường)
  - cumulative_delta < 0 (net selling)
  - Nến hiện tại có sell_vol > buy_vol rõ rệt
  - Wick trên dài (thử VWAP từ dưới và bị đẩy xuống)

Ý nghĩa: VWAP đang kháng cự, cá mập bán ra
→ SELL/SHORT SIGNAL hoặc thoát vị thế long
```

---

## Schema DB cần thêm

### Bảng `vwap_snapshots` (lưu VWAP mỗi 5 phút)
```sql
CREATE TABLE vwap_snapshots (
    id            INTEGER PRIMARY KEY,
    security_id   INTEGER NOT NULL,
    snapshot_time DATETIME NOT NULL,
    vwap          REAL NOT NULL,         -- VWAP tính từ đầu phiên
    vwap_upper1   REAL,                  -- VWAP + 1σ
    vwap_lower1   REAL,                  -- VWAP - 1σ
    vwap_upper2   REAL,                  -- VWAP + 2σ
    vwap_lower2   REAL,                  -- VWAP - 2σ
    cum_volume    INTEGER,               -- Tổng khối lượng từ đầu phiên
    cum_delta     INTEGER,               -- Tổng buy_vol - sell_vol từ đầu phiên
    poc_price     REAL,                  -- Point of Control (giá có volume lớn nhất)
    UNIQUE(security_id, snapshot_time)
);
```

### Bảng `whale_signals` (lưu tín hiệu phát hiện)
```sql
CREATE TABLE whale_signals (
    id            INTEGER PRIMARY KEY,
    security_id   INTEGER NOT NULL,
    signal_time   DATETIME NOT NULL,
    signal_type   TEXT NOT NULL,    -- HIDDEN_ACCUMULATION|VWAP_RECLAIM|DELTA_DIVERGENCE|VWAP_REJECTION
    direction     TEXT NOT NULL,    -- BUY | SELL
    strength      REAL,             -- 0-100 (điểm tín hiệu)
    price         REAL,
    vwap          REAL,
    cum_delta     INTEGER,
    volume_ratio  REAL,             -- volume / avg_volume
    details       JSON,
    is_sent       INTEGER DEFAULT 0 -- đã gửi alert chưa
);
```

---

## Công thức tính VWAP

```python
# VWAP = Sum(Close * Volume) / Sum(Volume) — tính từ 9:15 VN time
VWAP_t = Σ(close_i * volume_i, i=0..t) / Σ(volume_i, i=0..t)

# VWAP Bands (dùng độ lệch chuẩn giá)
variance = Σ(volume_i * (close_i - VWAP)², i=0..t) / Σ(volume_i)
std_dev = sqrt(variance)
Upper1 = VWAP + 1 * std_dev
Lower1 = VWAP - 1 * std_dev
Upper2 = VWAP + 2 * std_dev
Lower2 = VWAP - 2 * std_dev

# Cumulative Delta
cum_delta_t = Σ(delta_i, i=0..t)  # delta = buy_vol - sell_vol (đã có trong DB)

# Volume Ratio (để detect surge)
avg_vol_5d = avg(vol nến cùng giờ trong 5 ngày giao dịch gần nhất)
vol_ratio = current_volume / avg_vol_5d
```

---

## Kế hoạch triển khai (Atomic Tasks)

### Phase 1: VWAP Engine (Nền tảng)
- [ ] Task 1.1: Tạo schema `vwap_snapshots` + `whale_signals`
- [ ] Task 1.2: Viết `realtime/vwap_engine.py` — tính VWAP rolling từ 1m candles
- [ ] Task 1.3: Tích hợp vào `IntradayEngine` — ghi snapshot mỗi 5 phút

### Phase 2: Signal Detector
- [ ] Task 2.1: Viết `scripts/whale_hunter.py` — chạy mỗi 5 phút
- [ ] Task 2.2: Implement 4 loại signal với scoring (0-100)
- [ ] Task 2.3: Lọc noise bằng minimum score threshold (>= 60)

### Phase 3: Alert & Dashboard
- [ ] Task 3.1: Gửi Email alert khi phát hiện VWAP_RECLAIM hoặc HIDDEN_ACCUM mạnh
- [ ] Task 3.2: Thêm widget "Whale Watch" vào VSD Dashboard (highlight mã)
- [ ] Task 3.3: Alias `wh` = chạy whale_hunter.py thủ công

### Phase 4: EOD Backtesting (kiểm chứng)
- [ ] Task 4.1: Chạy lại signal detector trên dữ liệu lịch sử 30 ngày
- [ ] Task 4.2: Tính tỷ lệ thắng (win rate) của từng loại signal
- [ ] Task 4.3: Tinh chỉnh ngưỡng (threshold) dựa trên backtest

---

## Ưu tiên triển khai

```
TUẦN 1: Phase 1 + Phase 2 (core logic)
TUẦN 2: Phase 3 (alert + dashboard)
TUẦN 3: Phase 4 (backtest + tinh chỉnh)
```

---

## Rủi ro & Biện pháp

| Rủi ro | Biện pháp |
|--------|-----------|
| Volume DNSE = 0 (nhiều mã) | Dùng volume từ MASVN làm fallback; bỏ qua mã có cum_vol = 0 |
| False signal cao | Yêu cầu ít nhất 2/4 điều kiện đồng thời + minimum volume |
| buy_vol bị thiếu (vol=0) | Chỉ tính signal nếu tổng buy_vol+sell_vol > 0 trong phiên |
| Tính VWAP tốn CPU | Dùng rolling sum trong DB; chỉ tính cho Top 200 mã thanh khoản |
