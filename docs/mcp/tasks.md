# Danh sách Tác vụ (Tasks) - MCP Server Google Colab

> 🔴 **Lưu ý Cốt lõi**: Mọi sub-task lập trình đều phải được viết Unit Test trước (Test-First) theo đúng Hiến pháp dự án. Code không qua bài test (hoặc không có bài test) sẽ bị từ chối.

- `[x]` **Giai đoạn 1: Xác nhận Yêu cầu cốt lõi (Constitution Check)**
  - `[x]` Đợi Người dùng trả lời các Câu hỏi mở trong `plan.md` để quyết định phương pháp kết nối.
  - `[x]` Duyệt qua cấu trúc thư mục đề xuất.

- `[x]` **Giai đoạn 2: Thiết lập Môi trường & Test-First Foundation**
  - `[x]` (Sau khi chốt phương án) Viết bài kiểm thử đầu tiên `tests/test_server.py` kiểm tra giả lập kết nối tới MCP Server qua stdio. Bài test này PHẢI thất bại (RED).
  - `[x]` Tạo bộ điều khiển pytest cho dự án, thêm `.gitignore` tương ứng.

- `[x]` **Giai đoạn 3: Phát triển Local MCP Server (Green & Refactor)**
  - `[x]` Phát triển `colab_mcp/server.py` chứa mô hình FastMCP/SDK để đăng ký list tools như `.execute_python_code()`.
  - `[x]` Setup middleware/Client (ví dụ HTTP call thông qua ngrok url của Colab) để server có thể pass dữ liệu sang Colab.
  - `[x]` Chạy Test và đảm bảo bài test pass (GREEN).
  - `[x]` Refactor làm sạch code.

- `[x]` **Giai đoạn 4: Thiết kế Colab Proxy (Notebook)**
  - `[x]` Cụ thể hóa `colab_mcp/colab_notebook.ipynb` chứa sẵn các blocks cài đặt ngrok, expose API trên google colab qua thư viện Flask/FastAPI.
  - `[x]` Code logic nhận `code`, execute qua `exec()` (kèm các bước sanitize nếu cần) và return `stdout`.

- `[x]` **Giai đoạn 5: Build & End-To-End Walkthrough**
  - `[x]` Thử nghiệm kết nối End-to-End giữa Local MCP ở máy cá nhân với 1 tab Colab.
  - `[x]` Kiểm thử độc lập: Dùng tool Agent Simulator để gọi lệnh qua MCP protocol.
  - `[x]` Viết báo cáo `walkthrough.md` đính kèm ảnh (nếu áp dụng).
