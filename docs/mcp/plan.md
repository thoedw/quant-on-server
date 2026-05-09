# Kế hoạch Triển khai Kỹ thuật (Implementation Plan) - MCP Server Google Colab

## 1. Description & Goal (Mô tả và Mục tiêu)
Thực thi thiết kế từ `spec.md` để tạo ra một máy chủ cung cấp công cụ (MCP Server) giúp giao tiếp với Google Colab.
Vì đây là dự án Quant (nặng về Python), chúng ta sẽ thiết kế một server dựa trên Python. Kế hoạch này đi vào các chi tiết cấu trúc (How) để hiện thực hóa `spec.md`.

## 2. Constitution Check (Kiểm tra Hiến pháp)
- **SDD Compliance**: Đặc tả đã được tạo (`spec.md`). Bản kế hoạch này theo sát `spec.md`.
- **Test-First**: Đã quy hoạch bước tạo unit test kiểm tra giao thức MCP.
- **Simplicity**: Mô hình client-server đơn giản bằng FastAPI hoặc các thư viện chuẩn cho MCP Python SDK. Không dùng Class hay Interface thừa thãi khi chưa cần thiết.
- **Language**: Tiếng Việt được sử dụng tại đây theo quy định.

## 3. User Review Required (Câu hỏi mở lấy ý kiến người dùng)
> [!WARNING]
> Cần sự xác nhận của người dùng về phương thức kết nối cụ thể:
> 1. Bạn muốn chạy một Notebook trên Colab với **Ngrok/Localtunnel** để mở một HTTP endpoint, sau đó MCP Local gọi vào Endpoint đó? (Giải pháp phổ biến, dễ tùy chỉnh)
> 2. Hay bạn muốn tích hợp thư viện **google-colab** MCP Server đã có sẵn hỗ trợ nội bộ qua browser automation?
> 3. Bạn có muốn code của server nằm gọn trong một thư mục như `colab_mcp/` chứa toàn bộ test, router và script không?

## 4. Proposed Changes (Đề xuất thay đổi)

### Cấu trúc thư mục (Dự kiến)
#### [NEW] `colab_mcp/__init__.py`
#### [NEW] `colab_mcp/server.py` (Chứa logic khởi tạo MCP server và expose tool)
#### [NEW] `colab_mcp/colab_notebook.ipynb` (File mẫu để user copy lên Colab chạy HTTP proxy nhận lệnh)
#### [NEW] `tests/test_server.py` (Bài tập Unit test được định nghĩa và phải pass theo chuẩn Red-Green-Refactor)

## 5. Verification Plan (Xác minh)
- **TDD Verification**: Excute lệnh pytest để chứng minh mock command được thực thi qua hàm của MCP thành công.
- **End-to-End**: Bật Colab + chạy `python -m colab_mcp.server`, kết nối bằng MCP Inspector hoặc yêu cầu local LLM thực thi một hàm `print("hello from colab")`.
