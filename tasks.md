# Danh sách Tác vụ (Tasks) - Securities Master Database

> 🔴 **Lưu ý Cốt lõi**: Tuân thủ chu trình TDD (Red-Green-Refactor). Test phải viết trước mô đun thực tế.

- `[ ]` **Giai đoạn 1: Khởi động & Ràng buộc Yêu cầu**
  - `[ ]` Nhận phản hồi của Người dùng về 3 câu hỏi (Timeframe, Danh sách mã, Data tài chính) để ghim yêu cầu.
  - `[ ]` Khởi tạo cấu trúc file và directory trống.

- `[ ]` **Giai đoạn 2: Lớp Nền tảng (Models & DB)**
  - `[ ]` Viết Unit test cho `models.py` (dataclasses) và `database.py` (tạo SQLite schema chuẩn).
  - `[ ]` Code `models.py` & `database.py` cho `securities`, `daily_prices`, `etl_run_log`. Đảm bảo (Green).
  
- `[ ]` **Giai đoạn 3: Extract & Transform layer**
  - `[ ]` Cài đặt `vnstock` vào `venv` (nếu chưa có). Viết Unit Test mock payload của vnstock.
  - `[ ]` Implement `extractors/vnstock_extractor.py`.
  - `[ ]` Viết unit test & implement `transformers/ohlcv_transformer.py` (clean logic NaN, validate ISO date).

- `[ ]` **Giai đoạn 4: Load & Orchestration**
  - `[ ]` Bổ sung Unit test & implement `loaders/sqlite_loader.py` (xử lý upsert, ON CONFLICT).
  - `[ ]` Gắn kết vào `pipeline.py` (lệnh chạy ETL).
  
- `[ ]` **Giai đoạn 5: Tự động hóa & Kiểm thử thực tế**
  - `[ ]` Viết script `run_etl.sh` và thiết lập demo cron job logic.
  - `[ ]` Chạy giả lập toàn bộ pipeline CLI qua file `python -m securities_master.pipeline` với 1-2 mã ticker.
  - `[ ]` Chụp ảnh màn hình Database / Walkthrough, Review nghiệm thu.

- `[x]` **Giai đoạn 6: Pipeline Khai thác Dữ liệu Sâu (U2G - Knowledge Graph)**
  - `[x]` Implement `PdfCrawler` (Stealth HTTP extract files)
  - `[x]` Implement `GeminiGraphParser` (LLM JSON extraction)
  - `[x]` Implement `GraphLoader` (Upsert Nodes and Edges into Schema EKG)
  - `[x]` Viết script `scripts/batch_u2g.py` (Composer pipeline: download -> parse -> load)
  - `[x]` Chạy thử `batch_u2g.py` với giả lập dummy text và nghiệm thu kết quả vào SQLite.
