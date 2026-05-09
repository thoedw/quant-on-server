# Đặc tả Yêu cầu (Specification): MCP Server kết nối Google Colab

## 1. Mục tiêu (What & Why)
- **Cái gì (What):** Xây dựng một Model Context Protocol (MCP) Server để kết nối và tương tác với môi trường Google Colab.
- **Tại sao (Why):** Giúp Agent/LLM ở môi trường cục bộ (local) có thể gửi mã nguồn Python đến Google Colab để thực thi (tận dụng GPU/TPU miễn phí hoặc thiết lập sẵn), đồng thời nhận về kết quả để làm đầu vào phân tích, phát triển thuật toán quant giao dịch.

## 2. Phạm vi (Scope)
*Lưu ý: Phần này cần người dùng xác nhận thông qua các câu hỏi làm rõ trong Bản kế hoạch thực thi (Implementation Plan).*
- Dự kiến sử dụng ngôn ngữ **Python** để tạo MCP Server cục bộ.
- Xây dựng 2 module chính:
  1. **Local MCP Server**: Cài đặt trên máy cá nhân, tuân thủ chuẩn `@modelcontextprotocol/sdk` (Python SDK).
  2. **Colab Endpoint/Proxy**: Kịch bản (Notebook) chạy trên Google Colab để expose API (có thể sử dụng ngrok/localtunnel) nhận mã gửi từ Local MCP Server.
- Các công cụ (Tools) mà MCP server sẽ phơi bày (expose):
  - `run_colab_code(code: str)`: Chạy một đoạn mã Python trên Colab và trả về kết quả console/lỗi.
  - V.v. (tùy thuộc vào thiết kế chi tiết).

## 3. Ràng buộc & Tiêu chuẩn
- **Tuân thủ Hiến pháp Dự án:** Viết bài kiểm thử (Test-First) trước khi viết mã sản xuất. Đảm bảo cấu trúc đơn giản, rõ ràng, không sử dụng pattern phức tạp không cần thiết (Anti-Abstraction).
- Mọi logic phải được đặc tả đầy đủ trong `plan.md` và `tasks.md` trước khi code. Ràng buộc cập nhật Spec khi có thay đổi.
