# Task: Triển khai PA3 (Multi-Feed Engine)

- [ ] **Bước 1: Refactor Kiến trúc Engine (Tách DNSE)**
    - [ ] Tạo `realtime/feed_provider.py` (Abstract Interface).
    - [ ] Tạo `realtime/dnse_provider.py` bốc toàn bộ logic MQTT, Playwright, Protobuf từ Engine cũ sang.
    - [ ] Thiết lập `on_tick` callback trả về chung format `(symbol, price, vol, ts, source)`.

- [ ] **Bước 2: Xây dựng TickRouter (Trung tâm điều phối)**
    - [ ] Tạo `realtime/tick_router.py`.
    - [ ] Cài đặt thuật toán First-Tick-Wins với `fingerprint = f"{symbol}:{int(timestamp)}"`.
    - [ ] Map cache với `TTL = 3.0s` bảo vệ `CandleAccumulator`.

- [ ] **Bước 3: Tích hợp MASVN Provider**
    - [ ] Phân tích và cài đặt `SocketCluster` client cho `masvn_provider.py`.
    - [ ] Mở Websocket tới `wss://mastrade.masvn.com/socketcluster/` bằng JSON.
    - [ ] Parse sự kiện `#publish` từ MASVN và gọi hàm `on_tick`.

- [ ] **Bước 4: Nối các module vào Engine**
    - [ ] Sửa đổi `IntradayEngine` trong `intraday_engine.py`.
    - [ ] Khởi chạy song song cả `DNSEProvider` và `MASVNProvider` qua `asyncio.gather`.
    - [ ] Sửa log Heartbeat hiển thị trạng thái của cả 2 Providers (Tick accepted, Dedup hits).

- [ ] **Bước 5: Thử nghiệm và Nghiệm thu**
    - [ ] Chạy thử Engine.
    - [ ] Kiểm tra tính chính xác qua Crontab `/eod vs engine` vào lúc 15:45.
    - [ ] Viết `Walkthrough` hoàn tất.
