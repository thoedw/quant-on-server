# Kế hoạch Triển khai (Implementation Plan) - Securities Master Database

## 1. Nguồn Dữ liệu & Kiến trúc
- Sử dụng mô hình **ETL**: Extract (kết nối thư viện lấy dữ liệu) -> Transform (validate, clean rác) -> Load (Upsert vào SQLite).
- Dùng thư viện Python **`vnstock`** làm nguồn gốc dữ liệu miễn phí trong Giai đoạn 1. 

## 2. Thiết kế Database Schema
Sử dụng **SQLite** (`securities_master.db`) với 3 bảng:
- `securities`: Lưu danh sách các symbol (VNM, HPG...), sàn giao dịch, phân loại (EQUITY/ETF).
- `daily_prices`: Chứa timeseries OHLCV (trade_date, open, high, low, close, volume). Có unique constraint `(security_id, trade_date)` để upsert an toàn.
- `etl_run_log`: Bảng audit trail để theo dõi các tác vụ cron chạy thành công hay thất bại.

## 3. Cấu trúc Module (`securities_master/`)
Áp dụng tính đơn giản hóa (Constitution: Simplicity), tách bạch trách nhiệm nhưng không dùng quá nhiều interface.
- `models.py`: Các dataclass biểu diễn dữ liệu (`Security`, `OHLCVRecord`).
- `database.py`: Quản lý SQLite connection, Schema creation, CRUD utilities.
- `extractors/vnstock_extractor.py`: Chịu trách nhiệm wrapper hàm lấy data.
- `transformers/ohlcv_transformer.py`: Validate cột, clean `close=NaN`, format ISO timestamp.
- `loaders/sqlite_loader.py`: Chunk & Bulk insert.
- `pipeline.py`: Composer kết nối 3 bước E-T-L. Bổ sung hỗ trợ param `incremental=True` để tự động query DB lấy thời gian nến cuối cùng.

## 4. Cơ chế Kéo Giá Incremental Thông minh
- **Database Query**: Tạo hàm `get_latest_price_date(security_id, interval)` trong `database.py` quét bảng `stock_prices`.
- **Logic Pipeline**: Khi tham số `incremental=True` (mặc định cho các cron job hàng ngày) được truyền vào, `pipeline.py` sẽ bỏ qua tham số `start_date` cố định/cứng, và dùng `MAX(trade_time)` lấy được từ DB (trừ lùi 1 giờ/1 ngày tùy interval để tránh sót) làm mốc bắt đầu.
- **Tiến trình Lịch sử:** Script cào gốc (như `batch_prices.py`) có thể chạy độc lập không ảnh hưởng, nhưng script cron chạy hàng ngày (`batch_daily.py`) sẽ được sửa lại để vận hành 100% bằng chế độ incremental.

## 4. Constitution Check (Kiểm tra Hiến pháp)
- **Test-First**: Bắt buộc viết các file `tests/test_securities_master/` (mock response, test error) trước khi code logic E/T/L.
- **Simplicity**: Thay vì dùng SQLAlchemy phức tạp (ORM), dùng chuỗi lệnh SQL thô (raw SQL) kết hợp `sqlite3` driver để tối ưu hóa performance khi bulk insert hàng triệu row.

## 5. Cấu trúc Enterprise Knowledge Graph (Tri thức Đồ thị Doanh nghiệp)
Để giải quyết bài toán UBO (Sở hữu) và Macro-Quant (Chuỗi cung ứng vĩ mô), database được nâng cấp theo chuẩn Knowledge Graph thu nhỏ:
- **`dim_entities`**: Bảng Master Data chứa toàn bộ đỉnh (Nodes). Bao gồm: Cá nhân, Doanh nghiệp niêm yết/tư nhân, và cả các Danh từ Vĩ mô (Sản phẩm Hóa chất, Cao su, Thép xây dựng, Ngành IT...). Tránh trùng lặp 1 khái niệm thành 2 đỉnh.
- **`fact_relationship_network`**: Bảng Đồ thị lưu đa dạng các cạnh (Edges) kết nối. 
  - **Sở hữu**: `Source_ID (Tổ chức) -> OWNS_SHARES -> Target_ID (Công ty)`.
  - **Chuỗi cung ứng**: `Source_ID (Công ty) -> PRODUCES -> Target_ID (Sản phẩm)`.
  - **Chuỗi cung ứng**: `Source_ID (Công ty) -> CONSUMES -> Target_ID (Nguyên liệu)`.
- **Cơ chế SCD Type 2**: Dữ liệu sở hữu và cấu trúc thay đổi chậm (kể cả việc DN đổi ngành nghề kinh doanh) sẽ không bị ghi đè, mà đóng `valid_to` để cho phép hệ thống quant quay lại kiểm thử lịch sử theo Point-In-Time (Tránh Look-ahead bias).

## 6. Pipeline Khai thác Dữ liệu Sâu (Unstructured to Graph - U2G)
- **Extration (Khai thác tĩnh)**: Truy vấn các mảng `shareholders`, `officers`, `subsidiaries` từ đối trọng API `VCI` và `KBS`.
- **Stealth PDF Crawling**: Cào thầm lặng tệp Báo cáo Thường niên, Cáo bạch (Nơi ghi chi tiết Chuỗi cung ứng, Đầu vào/Ra) từ SSC/CafeF bằng HTTP Request. Các file tải về chạy qua hàm Băm (SHA-256) ghim vào `document_registry`.
- **LLM Mining (Song kiếm Đồ thị)**: Quét file thông qua Context Window của Gemini 3.1 Pro theo form Prompt cố định để LLM nhả về JSON Đỉnh - Cạnh. LLM sẽ làm 2 nhiệm vụ: (1) Bóc tách Quyền lực/Sân sau, (2) Bóc tách Sản phẩm trọng yếu Đầu vào/Đầu ra. Đổ file JSON này ngược về bảng Mạng lưới `fact_relationship_network` của SQLite.

## 7. Câu hỏi chưa chốt (Open Questions)
1. Chỉ lấy `daily` OHLCV hay cần thêm timeframe khác (weekly, 1h)?
2. Init pipeline cho toàn bộ sàn (~1700 mã) hay chỉ một nhóm cụ thể (VN30...)?
3. Ngân sách Tokens cho Gemini API khi quét và dịch hàng ngàn bản báo cáo PDF lớn (tránh tràn bill GCP)?


## Giai đoạn 4: Động cơ Phân tích Tin tức AI (Morning AI News Workflow)
**Mục tiêu**: Đóng gói cụm tin tức thu thập được từ Nightly News Engine, tận dụng cấu trúc thông tin của Database để xuất ra Báo cáo Nhận định thị trường hàng ngày.

- **Bước 1 (Lọc dữ liệu)**: Thiết kế truy vấn SQL lấy tất cả bản ghi `news_sentiment` được thêm mới trong vòng 24 giờ qua (hoặc từ mốc thời gian chạy trước đó). Giới hạn hoặc nén nội dung (Summarize) nếu vượt quá Context Window của Gemini 1.5 Pro.
- **Bước 2 (Chế tạo Prompt)**: Đóng gói System Prompt với vai trò "Trưởng phòng Tự doanh / Quản lý quỹ". Xác định Rõ rào cản phân tích: (1) Đánh giá Vĩ mô, (2) Đánh giá Ngành, (3) Nhận định Công ty cụ thể kèm khuyến nghị.
- **Bước 3 (Triển khai LLM)**: Tích hợp thư viện `google-generativeai` sử dụng `gemini-1.5-pro-latest` (hoặc model mạnh nhất có sẵn), load credentials từ `.env`.
- **Bước 4 (Đồng bộ Vault)**: Format kết quả dưới định dạng Markdown thuần, đổ trực tiếp vào đường dẫn Obsidian (`/Users/tuanho/Library/CloudStorage/GoogleDrive-tramminhho@gmail.com/My Drive/myVault/TuanHo/0. Daily Trading News/Bản tin chứng khoán ngày yyyy-mm-dd.md`).
- **Bước 5 (Hook Orchestration)**: Bổ sung thẳng lệnh chạy tiến trình AI này vào hàm `main()` của `scripts/nightly_news_engine.py` sau hành động tải tin.

**Complexity Tracking**: Giữ Script ngắn gọn dưới dạng 1 file Python độc lập `scripts/morning_ai_news.py`, tránh tạo ra thêm class hay abstraction dư thừa trừ phi phải kết nối DB phức tạp.
