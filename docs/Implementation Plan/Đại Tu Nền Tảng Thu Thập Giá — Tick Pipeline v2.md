# Đại Tu Nền Tảng Thu Thập Giá — Tick Pipeline v2

## Mục tiêu

Xây dựng lại pipeline thu thập tick để đảm bảo:
1. **Không sót tick hợp lệ** — fingerprint chính xác hơn
2. **Không đếm tick 2 lần** — xử lý cross-second boundary
3. **MASVN side `mb` luôn được ưu tiên** — không bị ghi đè bởi Tick Rule

---

## Chẩn đoán vấn đề hiện tại

### Bug 1: Fingerprint `symbol:giây:volume` — Sót tick hợp lệ

```
Fingerprint = f"{symbol}:{fp_ts}:{volume}"
```

**Trường hợp bị sót:**
```
10:00:01.100 → VNM 800 CP @ 27.05   → Fingerprint: "VNM:1713337201:800" ← ACCEPTED
10:00:01.700 → VNM 800 CP @ 27.10   → Fingerprint: "VNM:1713337201:800" ← BỊ BỎ (false dedup!)
```
→ Lệnh thứ 2 là giao dịch THẬT nhưng bị loại vì trùng fingerprint.
→ Thị trường thanh khoản cao (VNM, HPG, VHM) có thể mất 5-10 tick/giây.

### Bug 2: Cross-second boundary — Tick bị đếm 2 lần
```
DNSE  gửi lúc 10:00:00.950 → fp_ts = 1713337200 → "VNM:1713337200:800" ← ACCEPTED
MASVN gửi lúc 10:00:01.020 → fp_ts = 1713337201 → "VNM:1713337201:800" ← ACCEPTED (dup!)
→ Volume bị cộng 2 lần!
```

### Bug 3: MASVN `mb` không bao giờ được dùng
```python
# masvn_provider.py dòng 157 — side bị BỎ QUA
self.on_tick(symbol, price, int(vol), ts, self.name)  # ← không truyền side!

# tick_router.py dòng 56 — luôn dùng Tick Rule dù là MASVN
side = self.classifier.classify(symbol, price)  # ← sai với MASVN
```
→ MASVN có `mb` chính xác 95%+ nhưng code bỏ qua hoàn toàn.

---

## Giải pháp

### Nguyên tắc thiết kế mới
- **MASVN trumps DNSE về SIDE** — nếu MASVN arrive sau vẫn cập nhật side
- **Fingerprint phải cấp độ giao dịch** — `symbol + giây + volume + price_bin`
- **Window 2 giây** — tránh miss dedup tại biên giây
- **Source-aware routing** — DNSE và MASVN xử lý khác nhau

---

## Các thay đổi chi tiết

---

### Component 1: `realtime/feed_provider.py`

#### [MODIFY] Thêm `side` vào signature `on_tick`

```python
# Trước — signature cũ (không có side)
on_tick: Callable[[str, float, int, datetime, str], None]

# Sau — truyền thêm side (Optional, mặc định None)
on_tick: Callable[[str, float, int, datetime, str, str | None], None]
```

---

### Component 2: `realtime/masvn_provider.py`

#### [MODIFY] Truyền `side=mb` thật vào `on_tick`

```python
# Dòng 157 hiện tại (SAI)
self.on_tick(symbol, price, int(vol), ts, self.name)

# Dòng 157 sau khi fix (ĐÚNG)
self.on_tick(symbol, price, int(vol), ts, self.name, side)
# side = data.get('mb', 'NEUTRAL') — "BUY" / "SELL" thật từ sàn
```

---

### Component 3: `realtime/tick_router.py`

#### [MODIFY] Fingerprint nâng cấp + MASVN priority logic

**3a. Fingerprint mới — thêm price_bin, window 2 giây:**
```python
# Cũ (nhiều lỗ hổng)
fp_ts       = int(ts.timestamp())
fingerprint = f"{symbol}:{fp_ts}:{volume}"

# Mới — window 2 giây + price_bin để phân biệt giao dịch khác giá
fp_ts       = int(ts.timestamp() / 2) * 2      # làm tròn về bội số 2 giây
price_bin   = int(round(price * 10))            # làm tròn 0.1 nghìn đồng
fingerprint = f"{symbol}:{fp_ts}:{volume}:{price_bin}"
```

**3b. MASVN Priority — nếu tick trùng fingerprint nhưng source = MASVN:**
```python
def route_tick(self, symbol, price, volume, ts, source, side=None):
    ...
    # Dedup check
    if fingerprint in self._dedup_cache:
        # Nếu MASVN đến sau DNSE — cập nhật side trong candle đang mở
        if source == 'MASVN' and side in ('BUY', 'SELL'):
            self._stats['masvn_side_override'] += 1
            # Gọi accumulator.update_side() để vá lại side
            asyncio.run_coroutine_threadsafe(
                self.accumulator.update_side(symbol, side, ts), loop
            )
        else:
            self._stats['dedup_hits'] += 1
        return

    # Tick mới → Accept
    self._dedup_cache[fingerprint] = (now, source)
    ...
    # Side logic: ưu tiên side thật, fallback tick rule
    if side and side in ('BUY', 'SELL'):
        final_side = side  # MASVN hoặc nguồn đáng tin
    else:
        final_side = self.classifier.classify(symbol, price)  # DNSE → tick rule
```

---

### Component 4: `realtime/intraday_engine.py`

#### [MODIFY] Thêm `update_side()` vào `CandleState` và `CandleAccumulator`

```python
class CandleState:
    def update_side(self, volume: int, old_side: str, new_side: str):
        """
        Vá lại side khi MASVN confirm sau DNSE.
        Hoàn tác buy_vol/sell_vol theo old_side, ghi lại theo new_side.
        """
        if old_side == 'BUY':
            self.buy_vol  = max(0, self.buy_vol  - volume)
        elif old_side == 'SELL':
            self.sell_vol = max(0, self.sell_vol - volume)

        if new_side == 'BUY':
            self.buy_vol  += volume
        elif new_side == 'SELL':
            self.sell_vol += volume

        self.delta = self.buy_vol - self.sell_vol
```

> [!IMPORTANT]
> `update_side()` chỉ vá được nếu tick chưa bị flush ra DB. Nếu đã flush (chu kỳ 30s) thì override không thể hồi tố — chấp nhận được vì MASVN thường đến trong vòng 50-200ms.

---

### Component 5: `realtime/tick_router.py`

#### [MODIFY] Lưu `side` trong dedup_cache để `update_side()` biết hoàn tác

```python
# Dedup cache mới — lưu (timestamp, source, side) thay vì chỉ timestamp
self._dedup_cache[fingerprint] = {
    'ts'    : now,
    'source': source,
    'side'  : final_side,
    'volume': volume,
    'symbol': symbol,
}
```

---

## Sơ đồ luồng sau khi fix

```
MASVN tick (mb="SELL", mv=800)          DNSE tick (cum_vol, no side)
        ↓                                         ↓
_process_quote():                         _process_msg():
  side = data['mb'] = "SELL"               actual_vol = vol_tracker.delta()
  on_tick(..., side="SELL")                on_tick(..., side=None)
        ↓                                         ↓
        └───────────── TickRouter.route_tick() ───┘
                              ↓
            Fingerprint = "VNM:1713337200:800:271"

  Case A: DNSE đến trước (phổ biến)
    → ACCEPTED: side=None → TickClassifier → "BUY" (tick rule, tạm thời)
    → CandleState.buy_vol += 800

    Sau đó MASVN đến:
    → fingerprint trùng + source=MASVN + mb="SELL"
    → accumulator.update_side("VNM", old="BUY", new="SELL")
    → CandleState.buy_vol -= 800, sell_vol += 800  ← ĐÚNG!

  Case B: MASVN đến trước (hiếm)
    → ACCEPTED: side="SELL" (mb thật) → CandleState.sell_vol += 800

    Sau đó DNSE đến:
    → fingerprint trùng + source=DNSE
    → dedup_hits += 1, bỏ qua hoàn toàn  ← ĐÚNG!
```

---

## Kế hoạch triển khai

### Giai đoạn 1: Fingerprint + Side Propagation (Ít rủi ro nhất)
- [ ] Fix `masvn_provider.py` — truyền `side` vào `on_tick`
- [ ] Fix `tick_router.py` — fingerprint mới + nhận `side` từ provider
- [ ] Fix `tick_router.py` — dùng `side` thật nếu có, fallback tick rule

### Giai đoạn 2: MASVN Override (Phức tạp hơn)
- [ ] Thêm `update_side()` vào `CandleState`
- [ ] Thêm `update_side()` vào `CandleAccumulator`
- [ ] Fix `tick_router.py` — gọi `update_side()` khi MASVN đến sau

### Giai đoạn 3: Monitoring & Validation
- [ ] Thêm metrics: `masvn_side_override`, `fp_collision`, `cross_boundary_dup`
- [ ] Log thống kê cuối ngày: "MASVN overrode X ticks (Y%)"
- [ ] So sánh delta trước và sau fix trên cùng ngày dữ liệu

---

## Verification Plan

### Automated Tests
```bash
# 1. Unit test fingerprint mới không còn collision
python3 -m pytest tests/test_tick_router.py -v

# 2. Chạy intra và kiểm tra metrics sau 15 phút
intra
# Kiểm tra trong log: masvn_side_override > 0 (MASVN đang được dùng)
```

### Manual Verification
- Heartbeat log phải hiện `BUY=40-60%` thay vì `BUY=100%` (tick rule luôn bias)
- Kiểm tra `dedup_hits` giảm, `masvn_side_override` tăng
- Cum delta cuối ngày của VNM, HPG realistic hơn (không phải toàn BUY)

---

## Mức độ rủi ro

| Thay đổi | Rủi ro | Lý do |
|---|---|---|
| Fingerprint mới | 🟡 Thấp | Chỉ thay string format |
| MASVN side propagation | 🟡 Thấp | Thêm param, backward compatible |
| MASVN override (update_side) | 🟠 Trung bình | Cần async lock cẩn thận |
| Window 2 giây | 🟡 Thấp | Giảm cross-boundary miss |

> [!WARNING]
> Giai đoạn 2 (MASVN override) cần test kỹ với async lock — nếu `update_side` chạy đúng lúc `flush_all` thì có thể race condition. Cần review kỹ trước khi deploy.
