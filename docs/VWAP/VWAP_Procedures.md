Edited intraday-VWAP%20Strategy.md

Sếp đã lưu tài liệu chiến lược rồi — giờ em giải thích quy trình vận hành thực tế nhé.

---

## 🖥️ Toàn bộ hệ thống có 3 tiến trình độc lập

```
┌─────────────────────────────────────────────────────────┐
│  Terminal 1: intra  →  Thu thập dữ liệu DNSE + MASVN    │
│  Terminal 2: nz     →  Quét tin tức intraday             │
│  Terminal 3: whl    →  Phân tích VWAP + phát tín hiệu   │
└─────────────────────────────────────────────────────────┘
```

---

## 📋 Thứ tự khởi động BẮT BUỘC

> ⚠️ **`intra` phải chạy trước `whl` ít nhất 15 phút** — vì `whl` cần dữ liệu nến từ `intra` để tính VWAP. Nếu `intra` chưa có data thì `whl` sẽ báo "Không có VWAP snapshot".

```
Bước 1 → gõ: intra    (bắt đầu thu thập giá tick)
Bước 2 → gõ: nz       (bắt đầu quét tin tức)
Bước 3 → gõ: whl      (bắt đầu phân tích VWAP, SAU 15 phút)
```

---

## 🔄 Luồng dữ liệu từng bước

```
DNSE (1544 mã)  ──┐
                   ├──→ IntradayEngine ──→ SQLite DB (stock_prices)
MASVN (1544 mã) ──┘         │                      │
                        TickRouter            Flush mỗi 30s
                      (dedup ticks)           (nến 1m, 5m...)
                                                    │
                                                    ▼
                                           VWAPEngine (mỗi 5 phút)
                                           Tính VWAP từ nến 1m
                                           Lưu vào vwap_snapshots
                                                    │
                                                    ▼
                                           WhaleHunter (quét ngay sau)
                                           So sánh giá vs VWAP
                                           → Phát hiện 4 loại signal
                                           → In ra terminal
                                           → Lưu whale_signals
```

---

## ⏱️ Timeline trong một ngày giao dịch

```
08:45  →  Mở iTerm2, khởi động intra + nz
           intra bắt đầu kết nối DNSE (mất ~90 giây cho 31 Workers)
           
09:00  →  Khởi động whl
           Lần quét đầu sẽ có ít data → bình thường
           
09:15  →  Thị trường mở cửa
           intra  : bắt đầu nhận tick, flush nến mỗi 30 giây
           nz     : quét tin tức mới mỗi 5 phút
           whl    : quét VWAP mỗi 5 phút, log ra signal
           
09:30+ →  Bắt đầu có data đủ để VWAP có ý nghĩa
           Tìm HIDDEN_ACCUMULATION trên watchlist cá nhân
           
10:30+ →  VÙNG VÀNG — tìm VWAP_RECLAIM để vào lệnh
           
14:45  →  Đóng toàn bộ lệnh intraday
           
15:00  →  Thị trường đóng cửa
           whl tự ngủ (is_market_hours() = False)
           intra vẫn chạy để sync nến cuối phiên
           
15:15  →  Chạy eod để sync dữ liệu chốt phiên từ DNSE
```

---

## 🎮 Tóm tắt 3 lệnh cần nhớ

| Lệnh | Tiến trình | Làm gì |
|---|---|---|
| `intra` | **Intra-Engine** | Thu thập tất cả tick giá, tạo nến 1m/5m/15m... vào DB |
| `nz` | **Intraday-News** | Quét và tóm tắt tin tức theo mã cổ phiếu |
| `wh` | **Whale-Hunter** | Chạy **1 lần** quét tín hiệu ngay lập tức |
| `whl` | **Whale-Hunter-Loop** | Chạy **tự động mỗi 5 phút** suốt giờ giao dịch |
| `eod` | **EOD-Sync** | Chạy sau 15:00 để sync giá đóng cửa từ DNSE |

---

## 🗂️ Dữ liệu lưu ở đâu?

```
data/securities_master.db
├── stock_prices      ← Nến (1m, 5m, 15m, 30m, 1H, 1D, 1W)  ← intra ghi
├── vwap_snapshots    ← VWAP mỗi 5 phút của top 300 mã       ← whl ghi
├── whale_signals     ← Lịch sử tín hiệu cá mập              ← whl ghi
└── news_sentiment    ← Tin tức đã phân tích                  ← nz ghi

/tmp/intraday_engine.log      ← Log đầy đủ của intra
/tmp/intraday_news_engine.log ← Log đầy đủ của nz
```

---

## ✅ Checklist mỗi sáng

```
□ 1. Mở iTerm2
□ 2. Ô 1: gõ intra  → đợi thấy "DNSE W31 kết nối thành công"
□ 3. Ô 2: gõ nz
□ 4. Ô 3: gõ whl    → đợi 15 phút mới có signal chất lượng
□ 5. 9:15 thị trường mở → bắt đầu theo dõi log whl
□ 6. Khi thấy Score ≥ 80 → kiểm tra tin tức mã đó trên ô nz
□ 7. Vào lệnh theo chiến lược
□ 8. 15:00+ → gõ eod để đồng bộ dữ liệu cuối ngày
```