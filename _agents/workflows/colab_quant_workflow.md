---
description: Quy trình sử dụng phối hợp giữa thiết bị nội bộ (Antigravity) và Google Colab thông qua MCP Server
---
# 🚀 Workflow Phối hợp Antigravity & Colab MCP

Quy trình này hướng dẫn cách tổ chức luồng công việc để phát huy tối đa sức mạnh phân tích định lượng (Quant) bằng cách dùng thư mục chứa code nội bộ ở macOS và chạy code nặng (ML/Data) trên GPU của Google Colab.

## 1. Nguyên tắc cốt lõi (Core Principles)
- **Code nằm ở máy tính (Local First)**: Mọi source code (thuật toán, model logic, rules) đều được lưu ở máy nội bộ của bạn tại thư mục `/quant`. AI (tôi) sẽ đọc và sửa code tại máy tính của bạn.
- **Thực thi nằm ở Colab (Remote Execution)**: Bất cứ lúc nào cần chạy thử, train model hay tải Data lớn, chúng ta sẽ không chạy ở máy nội bộ mà gửi lệnh execution qua Colab MCP Server.
- **Dữ liệu vô danh (Data Sanitization)**: Đừng gửi các thông tin quá nhạy cảm. Mã code sẽ truyền qua proxy ngrok trước khi vào Colab.

## 2. Quy trình làm việc hàng ngày (Daily Routine)

### Bước 1: Kích hoạt Colab Proxy
1. Mở notebook `colab_mcp/colab_notebook.ipynb` trên Google Colab.
2. Cắm **Ngrok Auth Token** vào và khởi chạy (Bấm Play). 
3. Xem output để copy URL (vd: `https://abcd.ngrok-free.app`).
4. Dán URL vào file `.env` trên máy cục bộ `COLAB_PROXY_URL=...` (Thường thì nếu URL không đổi theo session trả phí, bạn có thể bỏ qua bước này).

### Bước 2: Gọi Antigravity (AI Agent)
- Bật terminal tại `/quant`, mở môi trường ảo (nếu cần).
- Yêu cầu AI xử lý thuật toán mới. Ví dụ: *"Hãy viết thuật toán XGBoost dự đoán giá AAPL"*

### Bước 3: Vòng lặp Code - Test (Do AI thực hiện)
Khi tôi nhận được yêu cầu của bạn, tôi sẽ tự động thực hiện:
- **Tạo Code Local**: Code thuật toán sẽ được tôi viết vào file `.py` tại `/quant`.
- **Run Remote**: Thay vì chạy ở macOS, tôi tự động parse file `.py` đó và vứt vào hàm `execute_code(code)` của Colab.
- **Debug**: Kết quả hay lỗi hiển thị ở Colab sẽ dội ngược lại về máy nội bộ nhờ MCP, tôi xem log và tự sửa code local của bạn tiếp tục cho đến khi pass (Test-First).

### Bước 4: Lưu trữ Data & Model kết quả
- Hãy chỉ thị tôi tải (download) file weight `.pt` hay file `.csv` từ Colab về lại máy tính cá nhân của bạn sau khi quá trình train dài hơi xong.
- Lệnh download qua request HTTP là cách an toàn nhất thay vì bắt IDE kéo nguyên cục folder đồng bộ.

## 3. Câu lệnh mẫu (Prompt Examples)

Hãy ra lệnh theo dạng sau để tôi hiểu ý đồ chuyển hướng tài nguyên:
- *"Chạy file `strategies/macd.py` trên Colab để xem tốc độ xử lý DataFrame là bao nhiêu."*
- *"Tôi mới tải tệp data 5GB từ Binance. Hãy viết script đẩy file đó lên Colab, chạy mô hình, rồi lấy plot đồ thị `.png` kéo về lại máy lưu vào thư mục `plots/`."*
- *"Kiểm tra xem trên Colab pip đã cài thư viện TA-Lib chưa thông qua mcp server."*

// turbo-all
