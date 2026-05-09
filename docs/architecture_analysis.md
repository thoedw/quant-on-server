# Phân tích Quyết định Kiến trúc (Architectural Decision Records)

Tài liệu này phân tích chi tiết các lựa chọn công nghệ cho Phase 2 của dự án Quant, nhằm tối ưu hóa hiệu năng và khả năng bảo trì.

## 1. Có nên dùng Apache Kafka cho Message Queue không?

**Apache Kafka** là một nền tảng event-streaming phân tán, nổi tiếng với khả năng chịu tải hàng triệu tin nhắn mỗi giây.

### Phân tích kỹ thuật:
- **Ưu điểm thiết kế:** Ưu điểm lớn nhất của Kafka với Quant là **Event Replay**. Nó lưu log tin nhắn xuống đĩa. Bạn có thể "tua lại" quá trình thị trường nhả lệnh để backtest mà hệ thống (Trading Engine) lầm tưởng là đang chạy thực ngoài đời (Paper-trading/Simulation quá hoàn hảo). Hơn nữa, Kafka tạo sự tách biệt (Decoupling) 100%: Bộ phận cào Data cứ cào, bộ phận AI trên Colab cứ lấy, bộ phận phân tích tín hiệu xử lý riêng.
- **Nhược điểm (Trade-off):** Cực kỳ cồng kềnh (Over-engineering). Kafka yêu cầu chạy hệ sinh thái Java (JVM/Zookeeper/KRaft cluster). Nó sẽ tiêu tốn từ 1 - 2GB RAM tĩnh trên máy Mac Mini M4 của bạn chỉ để đứng chờ dữ liệu. Việc setup, maintain cấu hình vô cùng phức tạp đối với hệ thống cá nhân.

### Đề xuất (Recommendation)
**KHÔNG NÊN** dùng Kafka ở giai đoạn dự án chạy trên Single-node (một máy chủ Mac Mini). 
**👉 Giải pháp thay thế ưu việt:** Sử dụng **Redis (Pub/Sub hoặc Redis Streams)**. 
- Redis chạy thẳng trên RAM, độ trễ ở mức sub-millisecond (nano-giây), hoàn hảo cho High-Frequency Trading.
- Tiêu tốn cực ít tài nguyên (chỉ ~10MB RAM).
- Nếu luồng thuật toán chạy thẳng trong Python cùng process với WebSocket, dùng native `asyncio.Queue` là tối thượng nhất (0 network overhead). Nhưng nếu tách Node.js UI với lõi Python, thì kiến trúc **Redis** là phù hợp nhất.

---

## 2. Bash Cronjob vs Lập lịch trên VSD Dashboard (`node-cron`)

Để chạy kịch bản cào dữ liệu cuối ngày (Incremental Batch), chúng ta có 2 ngã rẽ về kiểm soát (Control flow).

### Lựa chọn A: OS Cronjob (Ví dụ: `crontab -e` trên Mac)
- **Ưu điểm:** Khởi chạy bằng chính đồng hồ của Nhân hệ điều hành (Kernel). Gần như tuyệt đối đáng tin cậy. Hoàn toàn tách biệt khỏi ứng dụng (App crash thì cronjob vẫn chạy bình thường).
- **Nhược điểm:** Phân mảnh quản trị (Fragmented config). Khi bạn đổi sang máy khác/deploy lên VPS, bạn phải setup lại crontab thủ công. VSD Dashboard không thể giao tiếp trực tiếp hay có nút tắt/mở tiến trình này dễ dàng.

### Lựa chọn B: Application Scheduler (`node-cron` trên VSD Dashboard)
- **Ưu điểm:** Quản trị tập trung (Centralized). Cấu hình lịch trình nằm thẳng trong mã nguồn Github. Bạn có thể dễ dàng thiết kế 1 nút bấm "Bật/Tắt Auto-Scrape" trên giao diện VSD, và UI sẽ đếm lùi thời gian "Bao lâu nữa thì cào tiếp". Code `server.js` sẽ kích hoạt `child_process.spawn()` để gọi Python.
- **Nhược điểm:** Ràng buộc sinh tử (Coupling). Nếu do lỗi nào đó mà tiến trình VSD Dashboard bị chết, tiến trình kéo giá của toàn hệ thống cũng "ngủ quên" luôn.

### Đề xuất (Recommendation)
**NÊN DÙNG Application Scheduler.**
Lý do: Tầm nhìn của giao diện VSD Dashboard là trở thành Trạm Điều khiển Trung tâm. Việc gom cấu hình lịch trình (lúc 16h hằng ngày) vào trong Codebase giúp hệ thống của bạn có tính đóng gói cao (Plug-and-play). Chỉ cần bọc Dashboard trong một Process Manager (như `pm2` hoặc setup `launchd` của Mac) để tự boot lại khi crash là triệt tiêu được nhược điểm.

---

## 3. Kiến trúc Giữ Dữ liệu Real-time (Trade-tick) trong RAM

Đối với thuật toán giao dịch thuật toán (Algo-trading), cứ mỗi lệnh khớp trên thị trường DNSE đẩy về 1 Tick, chúng ta phải phản ứng ngay. Không thể `INSERT` SQL rồi `SELECT` SQL ra tính RSI/MACD được vì quá chậm và hao mòn ổ SSD. Mọi thứ phải tính trên RAM (Memory).

### Chi tiết kỹ thuật thiết kế in-RAM
1. **Cấu trúc lưu trữ (Data Structures):**
   - Sự lưu trữ sẽ diễn ra thông qua cấu trúc **Circular Buffer (Bộ đệm vòng)** ví dụ như `collections.deque(maxlen=10000)` trong Python, hoặc mảng Numpy chiều dài cố định.
   - Tại sao? Bạn không cần toàn bộ dữ liệu từ quá khứ để giao dịch hôm nay. Nếu xài EMA(200), bạn chỉ cần giữ 200 - 300 nến trong RAM. Mỗi lúc có 1 Tick giá mới đẩy vòng đệm tới một bước, giá cũ nhất rớt đài. Giải phóng bộ dọn rác (GC) của ngôn ngữ lập trình.

2. **Cách luân chuyển dữ liệu không độ trễ (Zero-copy / IPC):**
   - Tiến trình WebSocket (Python) đọc dòng lệnh từ Internet. Tiến trình Chiến lược (Strategy Engine) đứng kế bên húp dữ liệu ngay lập tức.
   - Để tối ưu, nếu dùng luồng song song, thay vì giao tiếp qua Socket TCP gây nghẽn rác bộ nhớ, ta đưa thẳng pointer bộ nhớ dùng chung (Shared Memory) vào Numpy, thuật toán sẽ đọc với tốc độ cực quang.

3. **Chiến lược Rũ bỏ và Persist (Rác và Sao lưu):**
   - Vùng RAM sẽ liên tục biến dạng theo mili-giây và bị bay màu khi tắt máy.
   - Điều này không hề hấn gì. Bởi vì sau khi hoàn thành ngày giao dịch, lúc 16:00 chiều Cỗ máy **Batch Incremental** của chúng ta đã nhảy vào hút toàn bộ nến 1D tĩnh chuẩn xác và sạch sẽ vào thẻ nhớ SQLite vĩnh cửu. Sự vô thường của RAM trong ngày là một lợi thế chứ không phải rủi ro.

### Luồng làm việc lý thuyết trên RAM
`DNSE Server ---[WebSocket]---> Streamer Listener (Python) ---> (Memory Deque/Redis) ---[Trigger]---> Algorithm Model (Check signal MUA/BÁN) ---> Execution API (Đặt lệnh)`
