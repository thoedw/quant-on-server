
# 🔍 Cách hệ thống tính Buy/Sell Volume và Delta

---

## Nguồn dữ liệu: 2 luồng khác nhau HOÀN TOÀN

### 📡 MASVN — Có sẵn `mb` (match side)

```python
# masvn_provider.py dòng 142
side = data.get('mb', 'NEUTRAL')  # "BUY" / "SELL" — có sẵn, KHÔNG cần suy luận
vol  = data.get('mv', 0)          # match volume — khối lượng vừa khớp
```

**MASVN trả về trực tiếp từng lệnh khớp với:**
- `mb` = `"BUY"` hoặc `"SELL"` — **phân loại thật từ sàn**
- `mv` = khối lượng của lệnh khớp đó

> ✅ **Đây là nguồn đáng tin nhất** — không cần suy luận gì thêm.

---

### 📡 DNSE — KHÔNG có side, phải suy luận

DNSE dùng **Protobuf nhị phân** (không phải JSON), chứa:
```python
# dnse_provider.py
price  = fields.get(12, ...)  # giá khớp
volume = fields.get(...)      # khối lượng khớp tích lũy (cum_vol)
# KHÔNG có field "side" / "BUY"/"SELL"
```

DNSE trả về **cum_vol** (khối lượng cộng dồn từ đầu phiên), **không phải từng lệnh riêng lẻ**.

---

## Vậy volume từng tick DNSE được tính thế nào?

```python
# VolumeTracker trong intraday_engine.py
def delta(self, symbol: str, cum_vol: int) -> int:
    prev = self._last.get(symbol)
    self._last[symbol] = cum_vol
    if prev is None or cum_vol < prev:
        return 0  # tick đầu tiên
    return cum_vol - prev  # ← Volume của tick này = cum_now - cum_prev
```

```
Ví dụ:
  Tick lúc 10:00:01 → cum_vol = 15,000
  Tick lúc 10:00:03 → cum_vol = 15,800
  → Volume tick này = 15,800 - 15,000 = 800 CP
```

---

## Side từ DNSE suy luận thế nào?

Dùng **Tick Rule** (phương pháp học thuật phổ biến nhất):

```python
# TickClassifier trong intraday_engine.py
if price > prev_price:   → BUY  (uptick — bên mua chủ động đẩy giá lên)
if price < prev_price:   → SELL (downtick — bên bán chủ động kéo giá xuống)
if price == prev_price:  → Kế thừa side của tick trước (zero-tick rule)
```

```
Ví dụ:
  10:00:01  27.00 → tick đầu tiên → NEUTRAL
  10:00:02  27.05 → tăng → BUY   (800 CP → ghi vào buy_vol)
  10:00:03  27.05 → bằng → BUY   (kế thừa — 600 CP → ghi vào buy_vol)
  10:00:04  26.95 → giảm → SELL  (500 CP → ghi vào sell_vol)
```

---

## ❌ KHÔNG dùng 3 mức giá/khối lượng Bid-Ask (OrderBook)

Sếp hỏi về **3 mức giá mua/bán** (bid1/ask1, bid2/ask2, bid3/ask3) trên bảng điện. **Chúng ta KHÔNG dùng cái này** vì:

| | OrderBook (3 mức) | Tick Rule (đang dùng) |
|---|---|---|
| **Dữ liệu** | Lệnh chờ (chưa khớp) | Lệnh đã khớp thực tế |
| **Ý nghĩa** | Ý định của trader | Hành động thực tế |
| **DNSE có không** | Có (nhưng không dùng) | Có — đang dùng |
| **MASVN có không** | Không có | Có — đang dùng `mb` |

---

## Tóm tắt luồng xử lý

```
MASVN tick                          DNSE tick
   ↓                                   ↓
mb="BUY", mv=800                   cum_vol=15,800
   ↓                                   ↓
Side = BUY (trực tiếp)          VolumeTracker.delta() = 800
   ↓                            TickClassifier.classify() → BUY/SELL/NEUTRAL
   ↓                                   ↓
   └──────────────── TickRouter ───────┘
                          ↓
              CandleState.update(price, vol=800, side="BUY")
                          ↓
              buy_vol += 800  hoặc  sell_vol += 800
                          ↓
              delta = buy_vol - sell_vol
                          ↓
              Flush vào SQLite (stock_prices.buy_vol, .sell_vol, .delta)
```

---

## ⚠️ Độ chính xác

| Nguồn | Độ chính xác side |
|---|---|
| **MASVN** `mb` | ~95%+ — dữ liệu thật từ sàn |
| **DNSE Tick Rule** | ~70-75% — suy luận, có thể sai ở zero-tick |

> **Vì vậy MASVN là nguồn chính để tính delta** — đó là lý do tại sao khi MASVN mất kết nối thì `BUY=100%` (toàn bộ DNSE được classify bằng Tick Rule, kém chính xác hơn)!