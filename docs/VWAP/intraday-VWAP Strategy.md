# 📖 Giải thích chiến lược VWAP Whale Hunter

---

## VWAP là gì và tại sao nó quan trọng?

**VWAP = Volume Weighted Average Price** — Giá trung bình có trọng số theo khối lượng, tính từ lúc mở cửa (9:15) đến thời điểm hiện tại.

```
VWAP = Σ(Giá × Khối lượng) / Σ(Khối lượng)
```

**Ý nghĩa thực tiễn:**
> VWAP là **đường ranh giới công bằng** của thị trường trong ngày. Mọi giao dịch trên VWAP = mua đắt hơn thị trường. Dưới VWAP = mua rẻ hơn thị trường.

Các **tổ chức lớn (cá mập, quỹ đầu tư)** dùng VWAP làm benchmark — họ cố gắng mua **dưới VWAP** và bán **trên VWAP** để đánh giá hiệu quả thực thi lệnh của mình.

---

## Cum Delta là gì?

```
Cum Delta = Tổng(buy_vol - sell_vol) từ đầu phiên
```

- `buy_vol` = khối lượng do **bên MUA chủ động** (khớp tại giá Ask)
- `sell_vol` = khối lượng do **bên BÁN chủ động** (khớp tại giá Bid)
- `Delta > 0` = **BUY pressure** — bên mua đang tấn công
- `Delta < 0` = **SELL pressure** — bên bán đang tấn công

---

## 4 Tín hiệu — Ý nghĩa & Hành động

---

### 🐋 Signal 1: HIDDEN_ACCUMULATION
**"Cá mập gom hàng bí mật"**

```
Điều kiện:
  ✅ Giá ĐANG ở dưới VWAP (close < VWAP)
  ✅ Cum Delta > 0 (có bên mua âm thầm hút hàng)
  ✅ 3 nến gần nhất liên tiếp có buy_vol > sell_vol
  ✅ Giá đang tiến dần lên VWAP (gap < 0.5%)
```

**Câu chuyện đằng sau:**
> Cá mập đang **lặng lẽ mua vào** trong khi giá vẫn được giữ dưới VWAP. Họ không muốn đẩy giá lên vì sẽ tốn thêm tiền — nên họ gom từng chút một. Khi gom đủ, họ mới "bật công tắc" và giá bùng lên.

**Ví dụ thực tế từ scan:**
```
🐋 HCM | HIDDEN_ACCUM | ΔCum=+3,767,335 (54% vol)
→ Hơn 54% TOÀN BỘ khối lượng ngày của HCM là MUA RÒNG
→ Nhưng giá vẫn... dưới VWAP → Cá mập đang gom!
```

**Hành động:**
| Thời điểm | Hành động |
|---|---|
| Khi phát hiện | ⏳ **THEO DÕI** — chưa vào lệnh |
| Khi giá bắt đầu chạm VWAP | 🎯 **Đặt lệnh chờ** tại VWAP |
| Khi giá đóng cửa **trên VWAP** | ✅ **BUY** — xác nhận reclaim |
| Stop Loss | ❌ Dưới đáy phiên hoặc VWAP - 1% |
| Take Profit | 🎯 VWAP + 1σ (upper band) |

---

### 🚀 Signal 2: VWAP_RECLAIM
**"Đột phá tái chiếm VWAP — Tín hiệu mạnh nhất!"**

```
Điều kiện:
  ✅ Nến TRƯỚC: giá dưới VWAP (prev_close < prev_vwap)
  ✅ Nến HIỆN TẠI: giá vượt lên trên VWAP (close > vwap)
  ✅ Cum Delta > 0 (BẮT BUỘC — không có delta dương = fake!)
  ✅ Giá vượt càng xa VWAP, score càng cao (>0.5% = +30 điểm)
```

**Câu chuyện đằng sau:**
> Đây là thời điểm cá mập **"xả khóa"** — họ đã gom đủ hàng, giờ cho phép giá đi lên. Thường xảy ra sau HIDDEN_ACCUMULATION. Đây là **entry point lý tưởng nhất** theo trend.

**Ví dụ thực tế:**
```
🚀 HCM | VWAP_RECLAIM | Score=100 | ΔCum=+3,691,535 (52.7% vol)
   Cross: 27.40 → 27.70 (VWAP=27.45) | Vượt 0.9% trên VWAP
→ Không chỉ vượt VWAP — còn có hơn 52% volume là MUA RÒNG!
→ Đây là tín hiệu MẠNH NHẤT trong ngày
```

**Hành động:**
| Thời điểm | Hành động |
|---|---|
| Khi nến đóng cửa trên VWAP | ✅ **BUY NGAY** — entry tại close |
| Hoặc chờ retest VWAP | 🎯 Buy khi giá pullback về VWAP rồi bật |
| Stop Loss | ❌ Nếu giá đóng cửa **dưới VWAP** → cắt lỗ |
| Take Profit 1 | 🎯 VWAP + 1σ |
| Take Profit 2 | 🎯 High của ngày / kháng cự gần nhất |

---

### 📊 Signal 3: DELTA_DIVERGENCE
**"Phân kỳ dòng tiền — Cá mập đỡ hàng"**

```
Điều kiện:
  ✅ Giá giảm liên tiếp 3 nến (lower close, lower close, lower close)
  ✅ Nhưng delta trong 3/4 nến gần nhất là DƯƠNG
  ✅ Cum Delta tổng phiên > 0 (xác nhận accumulation dài hạn)
  ✅ Giá đang dưới VWAP (mua ở vùng discount)
```

**Câu chuyện đằng sau:**
> Giá đang rớt, nhưng **có ai đó đang "ăn" toàn bộ lệnh bán**. Bán bao nhiêu, họ hấp thụ bấy nhiêu. Đây là dấu hiệu cá mập đang "chống đỡ" để gom hàng rẻ — giá sắp chạm đáy.

```
Giá:   ↘10.5 → ↘10.3 → ↘10.1   (giảm liên tiếp)
Delta: ↗+500  → ↗+800 → ↗+1200  (tăng liên tiếp!)
                                   ← AI đang mua?!
```

**Hành động:**
| Thời điểm | Hành động |
|---|---|
| Khi phát hiện | ⏳ **THEO DÕI** — chưa vào |
| Khi xuất hiện nến XANH đầu tiên | ✅ **BUY** — đây là confirmation |
| Stop Loss | ❌ Dưới đáy của nến phân kỳ |
| Take Profit | 🎯 Về VWAP |

---

### 🔴 Signal 4: VWAP_REJECTION
**"Kháng cự VWAP — Bán ra hoặc né tránh"**

```
Điều kiện:
  ✅ Giá đang TRÊN VWAP (close > vwap)
  ✅ Cum Delta < 0 (SELL pressure đang thắng)
  ✅ 3 nến gần nhất có sell_vol > buy_vol liên tiếp
  ✅ Giá gần VWAP (gap < 0.5%) → sắp bị kéo xuống
```

**Câu chuyện đằng sau:**
> Giá đang "lơ lửng" trên VWAP nhưng thực chất bên bán đang thống trị. Tổ chức đang **xả hàng** trong khi giá còn trên VWAP. Sắp bị kéo xuống.

**Ví dụ:**
```
🔴 CEO | VWAP_REJECTION | ΔCum=-1,600,200 (99% vol là BÁN RÒNG!)
→ 99% volume CEO hôm nay là bán chủ động
→ Giá vẫn trên VWAP nhưng chỉ là ảo
→ TRÁNH XA hoặc cân nhắc short
```

**Hành động:**
| Nếu đang hold | Hành động |
|---|---|
| Nếu đang có lệnh long | ⚠️ **THOÁT LỆNH** hoặc siết stop loss |
| Nếu chưa vào | ❌ **KHÔNG MUA** dù giá đang xanh |
| Nếu chơi short | 🔴 **SHORT** khi giá xuyên xuống VWAP |

---

## 🎯 Quy trình giao dịch thực tế

```
Sáng 9:15 → Khởi động intra + whl

├── 9:30 - 10:30: THEO DÕI pha tích lũy
│   └── Tìm HIDDEN_ACCUMULATION Score > 70
│       └── Đưa vào watchlist
│
├── 10:30 - 13:00: VÙNG VÀNG vào lệnh
│   └── Tìm VWAP_RECLAIM Score > 80
│       └── BUY ngay khi nến đóng trên VWAP
│
├── 13:00 - 14:00: QUẢN LÝ VỊ THẾ
│   └── Nếu thấy VWAP_REJECTION trên mã đang hold
│       └── Thoát 50% hoặc siết stop
│
└── 14:30 - 15:00: ĐÓNG LỆNH
    └── Đóng toàn bộ intraday trước 14:45
```

---

## ⚠️ Nguyên tắc vàng

> **Signal chỉ có giá trị khi mã có thanh khoản đủ lớn!**
> 
> - `CEO: ΔCum=-1.6M` trên 7M CP giao dịch = **SỐ THẬT, đáng tin**
> - `PMB: ΔCum=-600` trên 14K CP giao dịch = **quá ít, bỏ qua**

**Checklist trước khi vào lệnh:**
- [ ] Score ≥ 80 (không phải 70)
- [ ] Cum volume > 100,000 CP
- [ ] `delta_ratio_pct` > 10% (không phải vài chục CP lẻ)
- [ ] Kiểm tra tin tức mã đó trên tab `nz` (Intraday-News)
- [ ] Xem giá reference (tham chiếu) để biết room tăng còn bao nhiêu