# Task Checklist: VSD Dashboard & Toàn bộ Khung thời gian DNSE

- `[x]` 1. **Cập nhật DNSE Pipeline (Giá Nến)**
  - `[x]` Ngắt tiến trình Scraping cũ.
  - `[x]` Đã cập nhật `batch_prices.py` để kéo trọn bộ 7 Timeframes (`1m, 5m, 15m, 30m, 1H, 1D, 1W`).
  - `[x]` Tái khởi động dưới dạng Background Task.

- `[x]` 2. **Khởi tạo Vietnam Securities Dashboard (VSD)**
  - `[x]` Khởi tạo dự án Node.js trong `vsd_dashboard/`.
  - `[x]` Cài đặt thư viện dependencies: `express`, `sqlite3`, `cors`.

- `[x]` 3. **Backend API (Node.js/Express)**
  - `[x]` Thiết lập `server.js` chạy trên Port 3000.
  - `[x]` Viết Endpoint `/api/status` chuyên trách đếm tổng số (Progress Bar):
    - Đếm `COUNT(DISTINCT symbol)` từ `etl_run_log` cho DNSE, so với mốc `1544`.
    - Đếm `COUNT(DISTINCT symbol)` cho VietCap, so với mốc `1544`.
  - `[x]` Trả về Percentage (Số phần trăm) cho Front-end.

- `[x]` 4. **Frontend UI (Glassmorphism)**
  - `[x]` Thiết kế `index.html` chia đôi màn hình giao diện Dark Mode.
  - `[x]` Sử dụng CSS thuần với hiệu ứng kính mờ và Gradient rực rỡ, nhấn mạnh vào Vòng cung (Circular Progress) hoặc Thanh ngang đếm phần trăm.
  - `[x]` Gọi API Fetch 3s/lần cập nhật siêu mượt không cần reload.

- `[x]` 5. **Triển khai & Kiểm thử**
  - `[x]` Mở Server ở Background.
  - `[x]` Đánh giá qua Browser.
