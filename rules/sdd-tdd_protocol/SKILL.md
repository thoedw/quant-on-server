---
name: SDD & TDD Development Protocol
description: Hiến pháp và Kỷ luật lập trình dành cho Antigravity Agents. Bao gồm chu trình Spec-Driven Development (SDD), Test-Driven Development (TDD) và đặc quyền thử nghiệm R&D.
---

# BỘ QUY TẮC BẮT BUỘC (ANTIGRAVITY AGENT PROTOCOL)

> **Mục đích:** Gói tài liệu này thiết lập hành vi cốt lõi của Antigravity Agent khi tham gia dự án Quant. Bất kỳ khi nào thực hiện viết lệnh thay đổi mã nguồn, Agent phải tuân thủ nghiêm ngặt các quy định dưới đây.

## 1. Luật Bất Biến: SDD & TDD (Spec-Driven & Test-Driven Development)

### 1.1 Khởi sinh từ Đặc tả (SDD)
- Bạn **KHÔNG ĐƯỢC PHÉP** sử dụng công cụ `replace_file_content` hoặc `multi_replace_file_content` để viết mã nguồn sản xuất (Production Code) nếu chưa hoàn thành các bước kế hoạch.
- Mọi tính năng mới đều phải được lên cấu trúc ý tưởng và lưu vào `spec.md` (Định nghĩa cái Gì và Tại Sao) và `plan.md` (Định nghĩa Làm như thế nào).

### 1.2 TDD: Kiểm thử đi trước Mã nguồn
Không có logic nghiệp vụ nào được phép tồn tại nếu không bị thử thách trước bởi Unit Test.
- **Bước 1 (RED):** Viết file test sử dụng pytest. Dùng Mock API, Dữ liệu giả lập để buộc cho Test phải báo lỗi (`FAIL/ERROR`).
- **Bước 2 (GREEN):** Chuyển sang file cấu trúc, cập nhật lượng Code tối thiểu để Test báo `PASS`.
- **Bước 3 (REFACTOR):** Dọn dẹp, tái cấu trúc mã mà vẫn đảm bảo Test `PASS`.

## 2. Kỷ luật Nâng cấp (Improvement) và Sửa Lỗi (Bug-fixing)

- Bất kỳ khi nào code chạy lỗi (Bug) hoặc người dùng yêu cầu Nâng cấp logic, luật (1) vẫn CỰC KỲ CHI PHỐI.
- **Tiên Quyết:** KHÔNG nhảy vào sửa code ngay lập tức!
- **Hành động:** Bạn phải bắt đầu việc sửa lỗi bằng cách Tái tạo Lỗi (Reproduce Bug) thông qua một kịch bản Test mới. Hãy viết Test mô phỏng lại đúng báo lỗi đó cho đến khi Test `FAILED`, rồi sau đó mới sửa code gốc để chuyển về trạng thái Xanh.
- Khi cập nhật/thêm API mới, cấm việc làm hỏng các test case cũ đã chạy thành công trước đó.

## 3. Mệnh đề Tạm Đình Chỉ (The "Experimentation" Exception)

- Trong các nhiệm vụ R&D (Dịch ngược API, phá cấu trúc mã hóa CafeF/SSC, thử nghiệm LLM framework mới...), hệ tư tưởng SDD & TDD được phép **TẠM THỜI ĐÌNH CHỈ**.
- Khuyến khích tạo các file script (Scratch/Debug) nhỏ gọn kiểu cắm-rút như `debug_api.py`, `test_extract.py` để tìm ra **Giải pháp hoạt động được (A Workable Solution)**.

### Hiệu lệnh Rút quân & Đồng bộ lõi:
Ngay khi chức năng tìm được Workable Solution ở môi trường thử nghiệm, đặc quyền trên chấm dứt! Bạn PHẢI:
1. Dừng ngay việc hoàn thiện file nháp. Xóa code nháp dư thừa.
2. Cập nhật lại kiến trúc tìm được vào `plan.md`.
3. Khởi tạo Test Case TDD mới từ con số Không mô phỏng lại kết quả đạt được.
4. Triển khai Code sạch, chuẩn Production vào module gốc.
