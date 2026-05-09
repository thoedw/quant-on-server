# Kế hoạch Triển khai: Xây dựng Dashboard Theo dõi Dữ liệu (Node.js)

## Goal Description
Trong khi 2 cỗ máy (DNSE và VietCap) đang chạy ngầm trong background một khối lượng công việc khổng lồ, chúng ta cần một Trạm Kiểm soát Trực quan (Dashboard). 
Mục tiêu: Xây dựng một Ứng dụng Web bằng **Node.js (Express.js)** để trực tiếp đọc dữ liệu từ `securities_master.db` theo thời gian thực (Real-time). Bảng điều khiển này sẽ có giao diện tuyệt đẹp (Glassmorphism / Dark Mode) giúp bạn theo dõi Tình trạng cào dữ liệu, Tỷ lệ phần trăm hoàn thành, và Báo cáo lỗi (nếu có) của cả 2 luồng Price (7 Timeframes) và Financial.

## User Review Required
> [!IMPORTANT]
> 1. Giao diện sẽ sử dụng công nghệ **Vanilla CSS + HTML/JS** để đảm bảo cực kỳ nhẹ và nhanh (Không dùng TailwindCSS hay React cồng kềnh, theo đúng Hiến pháp thiết kế).
> 2. Node.js app sẽ được khởi tạo trong thư mục `vsd_dashboard/` (Viết tắt của Vietnam Securities Dashboard) nằm ngay trong thư mục gốc. Bạn có đồng ý với tên thư mục này không?

## Proposed Changes

### Tham chiếu Backend (Node.js + Express)
#### [NEW] [vsd_dashboard/package.json](file:///Volumes/Data/Antigravity Projects/quant/vsd_dashboard/package.json)
*   Khởi tạo dự án Node.js.
*   Framework: `express` (chạy web server).
*   Database connector: `sqlite3` (đọc file `.db`).
*   Khởi động qua lệnh `npm start`.

#### [NEW] [vsd_dashboard/server.js](file:///Volumes/Data/Antigravity Projects/quant/vsd_dashboard/server.js)
*   Tạo API `/api/status`: Truy vấn trực tiếp vào bảng `etl_run_log`.
*   Truy vấn 1: Thống kê số lượng mã chứng khoán đã cào thành công `nhóm DNSE` (1m, 5m, 1H, 1D, 1W) / Tổng số 1544 mã.
*   Truy vấn 2: Thống kê số lượng mã lấy Báo cáo tài chính `vietcap_financial` / 1544 mã.
*   Truy vấn 3: Lấy 10 log mới nhất để hiển thị Console trực tiếp trên giao diện.

### Giao diện Bảng điều khiển (Frontend)
#### [NEW] [vsd_dashboard/public/index.html](file:///Volumes/Data/Antigravity Projects/quant/vsd_dashboard/public/index.html)
*   HTML Semantic phân chia làm 2 cột: DNSE Progress và Vietcap Progress.
*   Sử dụng Fetch API để tự động làm mới (Auto-refresh) mỗi 3 giây.
*   Hiển thị thanh Tiến độ (Progress Bar) sinh động.
#### [NEW] [vsd_dashboard/public/style.css](file:///Volumes/Data/Antigravity Projects/quant/vsd_dashboard/public/style.css)
*   Hệ thống biến CSS (CSS Variables) chuẩn mực cho Dark Mode.
*   Thiết kế Glassmorphism (Hiệu ứng kính mờ trong suốt), đổ bóng neon hiện đại.
*   Micro-animations (Hiệu ứng Hover, Progress Bar trôi mượt mà).

## Open Questions
> [!WARNING]
> Do ứng dụng bằng Node.js cần chạy ở một Port (Cổng mạng) cụ thể ở Localhost. Bạn có muốn sử dụng cổng `3000` (http://localhost:3000) mặc định không? Hay một cổng đặc thù khác để tránh xung đột với các app khác trên Mac Mini của bạn?

## Verification Plan
### Automated Tests
- Kiểm tra tính kết nối của cổng 3000 và truy vấn SQLite không bị Lock (Khóa).
### Manual Verification
- Cung cấp link `http://localhost:3000` để bạn tự mình truy cập và cảm nhận vẻ đẹp của Giao diện, cũng như ngắm nhìn Database đang liên tục được lấp đầy.
