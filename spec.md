# Đặc tả Yêu cầu: Nền tảng Dữ liệu Chứng khoán Việt Nam (Securities Master Database)

## 1. Vấn đề (What)
Dự án Quant cần một hệ thống cơ sở dữ liệu gốc (Securities Master Database) lưu trữ tập trung dữ liệu toàn bộ cổ phiếu trên thị trường chứng khoán Việt Nam (HOSE, HNX, UPCOM) và một pipeline ETL (Extract, Transform, Load) tự động cập nhật dữ liệu. 

Yêu cầu cốt lõi:
- Một Database chuẩn hóa (SQLite) lưu trữ danh sách mã chứng khoán (Ticker/Symbol) và biểu đồ giá lịch sử OHLCV (Open, High, Low, Close, Volume).
- Một hệ thống chạy ngầm hoặc theo lịch (Cron/Scheduler) để tự động tải dữ liệu giá hằng ngày vào cuối ngày.
- Pipeline phải có khả năng tự phục hồi, audit log rõ ràng và không bị lặp dữ liệu (duplicate).

## 2. Mục đích (Why)
- Cơ chế Update Incremental thông minh: Tự động tra cứu thời gian nến giao dịch cuối cùng của từng mã chứng khoán/khung thời gian trong DB. Chỉ thực hiện fetch (kéo) những dữ liệu giá mới phát sinh, tuyệt đối không kéo lại toàn bộ dữ liệu lịch sử lặp thừa.
- Tính chính xác: Dữ liệu chuẩn, đã được clean (loại bỏ null, zero volume) là nền tảng sống còn cho mọi chiến lược giao dịch tự động.
- Khả năng mở rộng: Dù ban đầu dùng nguồn dữ liệu miễn phí, cấu trúc phải đủ tính module để sau này dễ dàng đổi sang phân hệ khác.
  
## 3. Bản thiết kế Kiến trúc Hệ thống (Quyết định Thiết kế - Cập nhật)

### 3.1. Động cơ Dữ liệu Giá (DNSE Pricing Engine)
- Thay thế hoàn toàn mã nguồn dựa trên phân tích HTML/API cũ bằng việc sử dụng trực tiếp **LightSpeed OpenAPI của DNSE**.
- Hỗ trợ đa khung thời gian (7 Timeframes: `1m, 5m, 15m, 30m, 1H, 1D, 1W`), tối ưu hóa khả năng Backtesting cho cả Day Trading và Swing Trading.
- Băng thông siêu rộng, không bị Rate Limit.

### 3.2. Động cơ Dữ liệu Tài chính (VietCap Financial Engine)
- Phát triển kỹ thuật Đảo ngược (Reverse Engineering) giả mạo trình duyệt để chọc trực tiếp vào GraphQL Endpoint của VietCap, phá bỏ hoàn toàn giới hạn 4 kỳ miễn phí.
- Khả năng truy xuất liên tục 15 năm lịch sử của hơn 140 chỉ tiêu tài chính kế toán quan trọng.
- Thiết kế cơ chế trễ (Delay 5 giây) giữa các truy vấn nhằm đảm bảo an toàn tuyệt đối và ẩn danh trước tường lửa (WAF).

### 3.3. Mô hình Bộ nhớ (JSON Database Schema)
- Bảng `financial_reports` chuyển sang cấu trúc vô hướng (Schema-less), sử dụng kiểu dữ liệu `JSON` trong SQLite để dung nạp bất kỳ số lượng trường chỉ tiêu nào một cách linh hoạt mà không cần lập cấu trúc cột phức tạp (ALTER TABLE). Đem lại sự mềm dẻo tuyệt đối (Flexibility) cho dự án.

### 3.4. Trạm Giám sát Thời gian thực (Vietnam Securities Dashboard - VSD)
- Server-side: `Node.js + Express.js` với `sqlite3` trực tiếp đọc Log DB.
- Front-end: `Vanilla CSS (Glassmorphism & Dark Mode)`. Cập nhật Fetch trực tiếp không Reload trang, cung cấp một lăng kính toàn cảnh % tiến độ Background Job của 2 Động cơ Thu thập Cốt lõi.

### 3.5. Đồ thị Tri thức Doanh nghiệp & Vĩ mô (Enterprise Knowledge Graph - EKG)
- **Vấn đề**: Để tiến tới Quant Vĩ mô (Macro-Quant), mô hình dữ liệu không thể dừng lại ở việc tìm ra "Ai sở hữu ai" (Cross-Ownership), mà phải giải quyết được bài toán Chuỗi giá trị: "Nguyên liệu đầu vào là gì?", "Sản phẩm đầu ra là gì?", và "Thuộc chu kỳ vĩ mô nào?".
- **Giải pháp Cơ sở dữ liệu**: Nâng cấp Đồ thị Sở hữu thành **Knowledge Graph (Tri thức Đồ thị)**. Bảng Mạng lưới (`fact_relationship_network`) không chỉ lưu quan hệ sở hữu (`OWNS_SHARES`), mà còn lưu các cạnh Chuỗi giá trị (`CONSUMES_MATERIAL`, `PRODUCES_PRODUCT`, `BELONGS_TO_INDUSTRY`). Khi giá Nguyên liệu A thế giới tăng, thuật toán tự chọc theo cạnh `CONSUMES_MATERIAL` để tìm ra ngay mã cổ phiếu B sẽ bị suy giảm biên lợi nhuận.
- **Giải pháp Data Mining**: Tiếp tục sử dụng Pipeline bóc tách PDF bằng Gemini 3.1 Pro. Tuy nhiên, Prompt nạp cho LLM sẽ được mở rộng lệnh: *"Ngoài Sở hữu chéo, hãy đọc Báo cáo Thường niên / Cáo bạch để tìm ra Top 3 Nguyên vật liệu đầu vào và Top 3 Sản phẩm/Dịch vụ đầu ra cốt lõi của doanh nghiệp này"* -> Đổ trực tiếp vảo Mạng lưới Đồ thị.

### 3.6. Động cơ Phân tích Tin tức AI (Morning AI News Workflow)
- Tự động hóa khâu tổng hợp và nhận định thị trường dựa trên tin tức đầu ngày.
- Quét toàn bộ khối lượng bài báo đã cào tĩnh lặng từ đêm (thông qua Nightly News Engine) vào sáng sớm và chuyển vào Mô hình Ngôn ngữ Lớn (Gemini 1.5 Pro).
- Đóng vai trò như một Trưởng phòng Tự doanh / Quản lý quỹ: Phân tích cấu trúc từ Vĩ mô (Macro) -> Ngành (Industry) -> Công ty (Company) kèm khuyến nghị (Buy/Sell/Neutral).
- Tổng hợp thành file Markdown lưu trữ trực tiếp vào kho dữ liệu cá nhân (Obsidian Vault trên Google Drive) dưới định dạng "Bản tin chứng khoán ngày yyyy-mm-dd.md".
