# Daily Decision Log — 2026-04-23

> **Phiên:** Thứ Tư, 23/04/2026 | VN timezone
> **Người:** Tuan Ho | **AI:** Antigravity (Google DeepMind)

---

## 🎯 Mục tiêu Phiên

1. Bổ sung tiêu chí **VWAP Slope** (1M / 1W / Intraday) vào hệ thống phân tích
2. Tạo **workflow `/vwap_slope`** để quét tín hiệu theo mã hoặc toàn thị trường
3. Lưu tài liệu kỹ thuật giải thích phương pháp

---

## ✅ Đã Hoàn Thành

### 1. VWAP Slope Engine (đã có trong `portfolio_watcher.py`)
- Tích hợp OLS linear regression cho 3 khung: 1M (22d), 1W (5d), Intraday (1m)
- Chuẩn hóa slope theo `% per period / mean VWAP` để so sánh cross-symbol
- Confidence gate: R² ≥ 0.5 → trend đáng tin
- Cross signal: GOLD↑ / DEATH↓ (so sánh session_close vs daily VWAP T vs T-1)

### 2. Workflow `/vwap_slope` — `_agents/workflows/vwap_slope_scan.md`
**Mode A — Scan 1 mã:**
- Slope 3 khung + R² + arrow indicator
- PVWAP history 5 phiên (VWAP, Open, Close, NetΔ, BuyR%, SideCov)
- Rolling VWAP trajectory intraday (drift % từ đầu phiên)
- Composite signal + Quality Score /8

**Mode B — Market/Watchlist scan:**
- Scan 729 mã trong ~4 giây
- Filter: `GOLD` | `DEATH` | `BULL` | `ACCUM` | `all`
- Phân tier: Tier-1 (Q≥6) / Tier-2 (Q 4-5) / Tier-3 (Q<4)
- Market breadth summary + tone (BULLISH/NEUTRAL/BEARISH)

### 3. Tài liệu kỹ thuật — `docs/`
- `Giải thích chi tiết — VWAP Slope & Cross Signal.md` — giải thích OLS, R², cross signal, composite
- `So sánh vwap vs vwap_slope.md` — hướng dẫn khi nào dùng workflow nào

---

## 📊 Market Intelligence Hôm Nay (2026-04-23)

### Market Breadth
| Metric | Giá trị | Diễn giải |
|---|---|---|
| Mã GOLD↑ | 192 / 729 (26%) | Nhiều mã vừa breakout VWAP |
| Mã trên VWAP | 422 / 729 (57%) | 🟢 BULLISH tone |
| Mã DEATH↓ | 159 / 729 (22%) | Distribution vẫn đang diễn ra ở một phần thị trường |

### Top Picks từ Market Scan (GOLD Tier-1)
| Mã | Close | Slp1M | R²1M | Ghi chú |
|---|---|---|---|---|
| **TCB** | 33.30 | +0.52% | 0.65 | Vol thực 12.8M, 1W R²=0.94 |
| **HCM** | 26.80 | +1.43% | **0.95** | Slope 1M tin cậy nhất thị trường |
| **KBC** | 34.20 | +0.99% | 0.85 | Pullback trong uptrend, Vol 3.2M |
| **NKG** | 14.50 | +0.51% | 0.78 | Watchlist, GOLD↑, BuyR=72% |

### Watchlist Report (10 mã core)
| Mã | Signal | Q | Đặc điểm |
|---|---|---|---|
| VRE | 🔴 BREAKDOWN | 7/8 | ⚠️ Nghịch lý: 1M+1W slope ⬆⬆ cao nhưng giá DEATH cross |
| NKG | 🟢 BREAKOUT | 6/8 | GOLD↑, BuyR=72%, pullback đã xong |
| SSI | ➖ MIXED | 6/8 | 1M uptrend tốt (R²=0.66), 1W pullback mạnh (R²=0.88) |
| HPG | ➖ MIXED | 5/8 | Vol 44.2M, nhưng tuần này chaos |
| SHB | 🔴 BREAKDOWN | 5/8 | DEATH cross + BuyR 79% hôm nay = absorption? |
| POW | ➖ MIXED | 4/8 | ⚠️ Downtrend R²=0.95 intraday, nhưng WH: VWAP_BOUNCE+HIDDEN_ACCUM |

### POW Deep Dive (Whale Hunter)
- **2 signals kích hoạt:** `VWAP_BOUNCE (100)` + `HIDDEN_ACCUMULATION (90)`
- **Nghịch lý chính:** 5 phiên distribution liên tiếp → hôm nay lần đầu Net Delta dương (+3.3M)
- **ATC pattern:** BuyR=81% trong giờ 14h với vol 4.5M → institutional absorption cuối phiên
- **Kết luận:** WAIT → theo dõi ngày mai; cần mở cửa ≥ 12.80 + delta dương để confirm reversal

---

## 🏗️ Kiến trúc Quyết định

### Quy trình phân tích hàng ngày (đã chuẩn hóa)
```
Sáng sớm:
  /vwap_slope market filter=GOLD   → Tìm breakout candidates
  /vwap_slope watchlist            → Check sức khỏe danh mục

Trong/cuối phiên:
  /vwap_slope [MÃ]                 → Check nhanh slope 3 khung
  /vwap [MÃ]                       → Phân tích sâu khi cần quyết định

Công thức phán quyết:
  GOLD↑ + 1M slope dương R²≥0.5 + NetΔ dương + Vol thực → BUY candidate
  DEATH↓ + 1W slope âm R²≥0.5 + NetΔ âm → SELL/avoid
```

### Design Decision: Tại sao dùng OLS thay vì EMA?
- EMA nhạy cảm với outlier cuối chuỗi; OLS fit toàn bộ chuỗi
- R² cho biết mức độ tin cậy của slope — EMA không có metric này
- OLS normalized by mean VWAP → so sánh được giữa các mã giá khác nhau

---

## 📁 Files Changed

| File | Loại | Mô tả |
|---|---|---|
| `scripts/portfolio_watcher.py` | Modified | VWAP Slope Engine đã tích hợp |
| `_agents/workflows/vwap_slope_scan.md` | New | Workflow /vwap_slope |
| `docs/Giải thích chi tiết — VWAP Slope & Cross Signal.md` | New | Tài liệu kỹ thuật |
| `docs/So sánh vwap vs vwap_slope.md` | New | Hướng dẫn sử dụng |
| `docs/daily_decision_log_2026_04_23.md` | New | File này |

---

## 🔮 Next Steps (Phiên tiếp theo)

1. **Theo dõi POW ngày mai** — confirm reversal hay dead cat bounce
2. **Theo dõi VRE** — nghịch lý BREAKDOWN trong khi slope 2 khung rất mạnh
3. **Cân nhắc:** tích hợp VWAP Slope vào Telegram alert (phase sau)
4. **Cân nhắc:** thêm `--scan watchlist` shortcut vào `portfolio_watcher.py --scan`

---

*Log tạo bởi Antigravity | Kết thúc phiên 23/04/2026 17:32 VN*
