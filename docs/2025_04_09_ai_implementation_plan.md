# Kế hoạch Hoàn thiện Lịch Trình (Cronjob) & Tạo Trợ Lý AI Buổi Sáng

Mục tiêu giai đoạn cuối: Tự động hóa hoàn toàn luồng thu thập tin tức và tích hợp trí tuệ nhân tạo (Gemini) để phục vụ cho các quyết định điểm rơi giao dịch đầu phiên sáng.

## User Review Required

> [!IMPORTANT]
> Đây là một bước đột phá đưa Data Pipeline của bạn thành một Hệ thống Đầu tư thực thụ. Bạn xem qua các thông số thiết kế, đặc biệt là phần Prompt của AI rồi duyệt để tôi tiến hành Code nhé.

## Kế hoạch Triển khai

### 1. Tự Động Hóa Cronjob (Quy trình 5:00 AM)

#### [NEW] `scripts/daily_news_workflow.sh`
* Tạo Bash Script tổng hợp đóng vai trò **Bộ chỉ huy Lịch trình**:
  * Tự động cd vào thư mục làm việc và nạp `venv`.
  * Khai hỏa lệnh `python scripts/batch_news.py` (Cào Metadata tin mới nhất của 1544 mã).
  * Tiếp tục khai hỏa lệnh `python scripts/batch_fulltext.py` (Nối giáo đắp thịt nội dung toàn bộ các bài vừa cào).
* Lên lịch (`Crontab`) cài đặt sẵn vào thiết bị Mac của bạn chạy vào **5 giờ 00 phút sáng (Từ Thứ Hai đến Thứ Sáu)**.

### 2. Trợ Lý AI Buổi Sáng (Morning Insights Workflow)

Vòng lặp sẽ truy vấn CSDL để lấy tất cả bài **CÓ CONTENT** (Full-text) được xuất bản trong khoảng 24 tiếng gần nhất và chuyển giao khối văn bản đó cho Google Gemini (đã có API sẵn trong máy của bạn) để tư duy.

#### [NEW] `scripts/morning_ai_summary.py`
Tạo một Workflow riêng biệt hoạt động độc lập:
1. **Lọc Dữ Liệu**: Kéo các bản tin `published_at` lớn hơn 15:00 PM của ngày hôm trước cho tới thời điểm hiện tại.
2. **Batch Processing**: Nén toàn bộ nội dung Full-text của các bài báo vào khối văn bản dài.
3. **AI Prompt Engineering**: Gửi Context lên `google-generativeai` với yêu cầu nghiêm ngặt:
   * Phân tích các mã chứng khoán được nhắc đến trong các tin.
   * Chắt lọc thành 2 nhóm: Nhóm Tích Cực (Positive) & Nhóm Tiêu Cực (Negative).
   * Lập luận tóm tắt 2-3 câu bằng tiếng Việt sắc sảo về mức độ tác động để bạn đọc lướt trước phiên ATO.
4. **Output**: Trả về màn hình Terminal (dạng bảng Text) hoặc xuất ra file Markdown tại ổ cứng để đọc thư giãn bên tách Cà phê.

---

## Open Questions

1. Thống kê số lượng bài báo trong 1 đêm cũng có thể lên tới 50 - 100 tin, độ dài văn bản có thể tốn 50.000 Token. Bạn muốn gửi trọn vẹn Nội dung bài (Full-text) hay chỉ cần Tiêu đề & Tóm tắt (Summary) để AI xử lý cho nhẹ? 
*(Khuyến nghị: Dùng Full-text siêu sát thương vì Gemini của Google hỗ trợ context cửa sổ lên đến 1,000,000 Tokens rất mạnh).*

## Verification Plan

* Kiểm tra sự sống của `daily_news_workflow.sh` trên Terminal.
* Chạy trực tiếp Lệnh Trợ Lý AI. Do database hiện đang có 206 bản tin mồi vừa được trích xuất thành công, Trợ lý AI sẽ có bài thực hành phân tích trực tiếp màn hình cho bạn xem ngay!
