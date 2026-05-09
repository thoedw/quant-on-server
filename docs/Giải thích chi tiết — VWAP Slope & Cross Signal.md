## 📐 Giải thích chi tiết — VWAP Slope & Cross Signal

---

### 1. Linear Regression là gì? (nền tảng chung)

Tưởng tượng anh có một chuỗi giá trị VWAP theo thời gian. Thay vì nhìn từng điểm riêng lẻ, linear regression **vẽ một đường thẳng xuyên qua toàn bộ chuỗi**, tìm đường "fit tốt nhất" (best fit line) theo phương pháp bình phương nhỏ nhất (OLS):

```
VWAP
  │     •  •
  │   •      •  •
  │ •           ←── đường regression (slope > 0, đi lên)
  │
  └────────────────→ thời gian
```

**Slope** (hệ số góc) của đường đó cho biết: mỗi một đơn vị thời gian trôi qua, VWAP thay đổi bao nhiêu? Em chuẩn hóa thêm thành **% relative to mean** để so sánh được giữa các mã giá khác nhau:

```python
slope_pct = (ss_xy / ss_xx) / y_mean * 100
```

Ý nghĩa: `+0.5% per day` = mỗi ngày VWAP trending lên 0.5% so với mức hiện tại.

**R² (coefficient of determination)** đo mức độ "thẳng" của trend:
- R² = 1.0 → giá đi thành đường thẳng tuyệt đối
- R² = 0.8 → trend rõ ràng, đáng tin
- R² = 0.2 → nhiễu loạn, slope không có nghĩa

---

### 2. Monthly Slope (22 phiên)

```
Dữ liệu: daily_vwap_summary.vwap của 22 phiên gần nhất (~1 tháng giao dịch)

  t=0    t=1    t=2    ...  t=21
VWAP₀  VWAP₁  VWAP₂        VWAP₂₁
 14.9   15.0   15.05        15.31   ← slope > 0 = uptrend tháng
```

**Đơn vị**: `% per trading day`

**Ý nghĩa thực tế:**
- `+0.445% per day` (HPG) → trong 1 tháng, nếu trend tiếp tục: `0.445% × 22 = +9.8%` — tháng rất tốt
- `+0.068% per day` (POW, R²=0.06 ↔)  → slope nhỏ + R² thấp = flat, không trend

**Dùng để đọc cái gì:**
> Đây là **"MA dài hạn"** của VWAP. Nếu slope 1M dương + R²≥0.5 → institutions đang tích lũy đều đặn trong 1 tháng qua.

---

### 3. Weekly Slope (5 phiên)

```
Dữ liệu: chỉ 5 phiên gần nhất (1 tuần giao dịch)

  T-4    T-3    T-2    T-1    T(hôm nay)
VWAP₄  VWAP₃  VWAP₂  VWAP₁  VWAP₀
15.39  15.21  15.21  15.13  15.06     ← slope âm = tuần downtrend (SHB)
```

**Đơn vị**: `% per trading day` (cùng đơn vị với 1M, **để so sánh trực tiếp**)

**Tại sao cần cả 2?** — Đây là kỹ thuật giống **MACD / dual MA**:

| Tình huống | 1M Slope | 1W Slope | Đọc hiệu |
|---|---|---|---|
| **Momentum tăng** | ⬆⬆ + | ⬆⬆ + | Aligned bullish — mua mạnh |
| **Pullback trong trend tăng** | ⬆⬆ + | ⬇⬇ − | Cơ hội buy-the-dip |
| **Momentum đảo chiều** | ↔ flat | ⬇⬇ − | Cảnh báo sớm |
| **Downtrend rõ** | ⬇⬇ − | ⬇⬇ − | Tránh xa |

**SSI hôm nay là ví dụ hoàn hảo:**
```
Slp1M = +0.437% R²=0.66 ⬆⬆   (tháng trend tăng tốt)
Slp1W = -0.832% R²=0.88 ⬇⬇   (tuần đang pullback mạnh, R² rất cao)
→ Đọc: đang hồi trong uptrend — nếu volume buy support thì là cơ hội
```

---

### 4. Intraday Slope

Đây là phần khác biệt nhất — không dùng giá trực tiếp mà dùng **rolling VWAP** (VWAP tích lũy theo từng phút):

```
Rolling VWAP được tính lũy tiến, mỗi nến 1m:

  9:15   9:16   9:17   ...  14:44   14:45
  RVWAP₁ RVWAP₂ RVWAP₃     RVWAP_n
  15.15  15.12  15.10       15.06   ← slope âm cả ngày (giá áp lực xuống)
```

**Tại sao rolling VWAP chứ không phải đơn giản là giá close từng phút?**

VWAP rolling tự nhiên trơn (smooth) hơn vì nó là **trung bình có trọng số theo khối lượng** tích lũy — nhiễu ngẫu nhiên bị triệt tiêu. Slope của nó phản ánh **hướng dòng tiền thực** trong ngày, không bị méo bởi spike giá 1-2 phút.

**Đơn vị**: `% per 1m candle` (nên rất nhỏ, e.g. `0.005%/candle`)

**Đọc hiệu:**
- Slope ID dương + R²≥0.5 → VWAP đang dốc lên trong phiên → dòng tiền bullish
- Slope ID âm + R²≥0.5 → áp lực bán trong phiên đang mạnh dần

**POW hôm nay — khắc nghiệt nhất:**
```
Slp1W = -0.980% R²=0.88 ⬇⬇  (R² rất cao)
SlpID = -0.011% R²=0.95 →↘  (R² = 0.95 — nearly perfect downtrend intraday!)
→ Cả tuần lẫn trong phiên hôm nay đều sell-off đều đặn, không hề dao động
```

---

### 5. Cross Signal — Giống MA Cross

Đây là cách detect **thời điểm giá vượt qua VWAP**, giống y hệt Golden Cross / Death Cross của MA 50/200:

```python
# Ngày hôm qua:
above_prev = session_close_yesterday >= vwap_yesterday   # True/False

# Hôm nay:
above_curr = session_close_today >= vwap_today           # True/False
```

| `above_prev` | `above_curr` | Signal | Ý nghĩa |
|---|---|---|---|
| `False` | `True` | 🟢 GOLD↑ | Giá vừa **cắt LÊN** trên VWAP — bullish breakout |
| `True` | `False` | 🔴 DEATH↓ | Giá vừa **cắt XUỐNG** dưới VWAP — bearish breakdown |
| `True` | `True` | ↑ above | Giá giữ trên VWAP — tiếp tục bullish |
| `False` | `False` | ↓ below | Giá giữ dưới VWAP — tiếp tục bearish |

**Tại sao dùng `session_close` so với `vwap` thay vì intraday?**

VWAP của ngày là **giá trị settle cuối ngày** (toàn bộ volume weighted). Nếu `session_close >= vwap`, có nghĩa là **người mua thắng cuộc tích lũy** của ngày hôm đó — đây là tín hiệu có trọng lượng hơn so với so sánh trong phiên.

---

### 6. Composite Signal — Kết hợp cả 4 tiêu chí

```
         1M Slope    1W Slope    ID Slope    Cross
HPG:       ⬆⬆ +       ↔ flat     →↘ −       ↓ below
           ↑              ↑           ↑           ↑
        trend tháng   mixed    áp lực bán   giá dưới VWAP
→ Signal: MIXED — không đủ alignment để ra quyết định rõ
```

```
NKG:       ⬆⬆ +       ⬇⬇ −       →↘ −      🟢 GOLD↑
                                              ↑
                                      giá vừa cắt lên VWAP khi close
→ Signal: BREAKOUT — dù slope tuần âm, cross signal override
```

**Rule ưu tiên:**
1. **Cross signal mạnh nhất** (GOLD/DEATH override tất cả)
2. Nếu không có cross: `up = (1M>0) + (1W>0) + (ID>0)` — đếm số chiều đang dương
3. Kết hợp với `vs VWAP` (giá đang trên hay dưới) → ra composite signal

---

### Tổng kết trực quan

```
VWAP như 3 lớp MA:

   Daily VWAP ─────────── lớp 1 (so sánh price vs VWAP ngày)
       │
   Slope 1M (22d OLS) ─── lớp 2 (macro trend 1 tháng)
   Slope 1W (5d OLS)  ─── lớp 3 (micro trend 1 tuần, faster)
   Slope ID (1m OLS)  ─── lớp 4 (sentiment trong phiên)
       │
   Cross detection ─────── event trigger (như MA cross)
```

Bộ 4 tiêu chí này giống như dùng **EMA(5) + EMA(22) + MACD histogram + cross event** nhưng thay vì dùng giá thô → dùng VWAP (giá weighted bởi dòng tiền thực) → **chất lượng tín hiệu cao hơn** vì lọc được nhiễu retail.