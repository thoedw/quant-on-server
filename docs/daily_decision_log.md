## 2026-04-15: Paho-MQTT Intraday Engine — Đại Tu Kiến Trúc Realtime & Volume Fix

### Quyết định chiến lược
1. **Pivot sang Paho-MQTT Native Client**: Loại bỏ hoàn toàn Playwright WebSocket Injection làm backbone Realtime. Playwright chỉ còn dùng duy nhất 1 lần khi khởi động để lấy Cookie/User-Agent (< 2 giây). Sau đó Paho-MQTT (thư viện C/Python native) kết nối thẳng TCP/WSS tới `datafeed-krx.dnse.com.vn`. **Kết quả**: Scale từ 100 mã → 1544 mã toàn thị trường với 31 MQTT Workers song song, RAM < 150MB.

2. **Volume Fix — Decode Protobuf Cumulative Volume**: Debug raw Protobuf payload từ topic `stockinfo` và xác định `field[17]` là **cumulative volume** (lũy kế từ đầu ngày). Implement class `VolumeTracker` tính delta: `delta_vol = cum_vol_now - cum_vol_prev` để có volume thực per-tick. `field[6/7]` trong `topprice` là Order Book bid/ask (không dùng cho candle volume).

3. **Scale-up Architecture — Dynamic Worker Spawning**: Thay vì hardcode 2 Workers (chunk1=50, chunk2=50), đổi sang query `securities` table lấy toàn bộ EQUITY symbols và tự động tính `N = ceil(total / 50)` workers. Thêm `asyncio.sleep(0.5)` giữa các spawn để tránh TCP handshake storm.

4. **VSD Dashboard V3 — Intraday Live Panel**: Deprecated card Red-Lightning và Candle Aggregator legacy. Thay bằng 1 card Intraday Engine (Paho-MQTT) tích hợp: stats panel 4 ô (Symbols, Nến 1M, Nến 5M, Trạng thái), Live Log Terminal, bảng 20 nến gần nhất với Delta indicator màu sắc. Server thêm endpoint `/api/intraday-stats` query trực tiếp `stock_prices` hôm nay.

5. **Order Flow Metadata — Tick Rule Classifier**: Mọi tick được phân loại BUY/SELL/NEUTRAL theo Tick Rule chuẩn (UPTICK=BUY, DOWNTICK=SELL, ZERO-TICK=kế thừa). Delta = buy_vol - sell_vol được ghi trực tiếp vào DB theo từng candle.

### Kết quả kiểm chứng (Production)
- 31 Workers kết nối thành công trong < 17 giây
- 1544 mã được subscribe, 800+ mã nhận được tick trong phiên sáng
- 271 nến 1M được flush vào SQLite trong phút đầu tiên
- Heartbeat xác nhận: `ticks=1132 | symbols=801 | candles=271`

### Còn lại (TODO hôm sau)
- [ ] Volume vẫn = 0 ngoài giờ giao dịch (expected, cần verify vào 9:00-11:30 và 13:00-15:00 hôm sau)
- [ ] Test VolumeTracker với delta trong phiên live
- [ ] Xem xét restart auto-recovery khi 1 Worker bị disconnect

---

## 2026-04-14: Dashboard V2 Re-architecture & Nightly Observer
1. **SOP 3 Luồng Tác Vụ**: Mã hóa thẳng vào Constitution nội quy dự án để kiểm soát triệt để các luồng (Intraday, EOD, Quarterly). Nhóm UI lại thành 3 phân vùng độc lập giúp quản lý luồng Crawler bằng Live Socket.
2. **Loại bỏ Chronos Engine Cứng nhắc**: Không giới hạn kéo BCTC bằng luật cứng ngày nộp (20 hàng tháng) để tránh mất dấu các doanh nghiệp nộp sớm.
3. **Bug Fix Core (Smart Resume)**: Vá lỗ hổng mất Caching khi kéo BCTC rỗng (Mã không có gì mới), triệt tiêu hoàn toàn độ trễ 2 giờ không đáng có nếu người dùng F5 nhiều lần.
4. **Nightly Observer (Cron)**: Chủyển `batch_financials.py` thành tiến trình ngủ đông thức dậy lúc 23:00 hằng ngày thông qua OS CronJob. Vượt chướng ngại vật CloudFlare IP Ban bằng Delay tự nhiên.
5. **Dashboard Layout Restructure**: Tái cấu trúc UX/UI theo chiều dọc (Left=Control Zones, Right=Sticky Live Terminal) để xoá bỏ khoảng trắng thừa thãi và theo dõi log gọn gàng.
6. **API Trích giác**: Sinh Endpoint Node.js phục vụ Nút bấm truy xuất thẳng SQLite báo cáo doanh nghiệp tung Báo Cáo Tài Chính trong 24h qua.

## 2026-04-09: Financial News ETL Pipeline & Morning AI Decision Log
1. **Full-Text Filler (Cỗ Máy Đắp Thịt)**: Chuyển đổi dữ liệu thô (metadata) sang bài báo đầy đủ bằng cách quét cơ sở dữ liệu và cào trực tiếp thẻ `div.content` từ CafeF bằng BeautifulSoup. Giảm sự phụ thuộc vào các nguồn API đóng, tăng cường sự chính xác cho Token.
2. **Dashboard UI**: Triển khai Real-time Socket.io cho thẻ "Full-Text Filler" hiển thị luồng phần trăm các tiến trình và số dòng dữ liệu.
3. **Morning AI Summary (Gemini 1.5 Pro)**: Hệ thống định lượng đọc 24h bài viết của mảng Doanh nghiệp, nén vào ngữ cảnh AI để tự động sinh báo cáo rủi ro/cơ hội trước phiên ATO.
4. **Cronjob Integration**: Pipeline được móc thẳng vào crontab macOS đảm bảo duy trì độ ẩm dữ liệu hàng ngày tự động vào lúc 5:00 sáng. Báo cáo Sync qua Google Drive MyVault.
