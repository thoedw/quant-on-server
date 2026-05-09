# Mật lệnh Kiến trúc: Phương án First-Tick-Wins và Vai trò của MASVN (16/04/2026)

## Bối cảnh Quyết định (Decision Context)
Vào phiên làm việc 16/04/2026, chúng ta phải đưa ra quyết định nâng cấp SLA của Intraday Engine từ ~92% lên 99.99%. 
Giải pháp được đưa ra là PA3 (Kiến trúc Đa Nguồn). Câu hỏi thiết kế quan trọng nảy sinh: 
1. Nên dùng SSI, TCBS hay MASVN làm Vendor thứ 2?
2. Có nên dùng MASVN để soi chéo dữ liệu EOD (15:45) với DNSE không?

## Quyết Định (The Decision)

### 1. Intraday: Chọn MASVN (bất ngờ lớn)
- **Lý do Reject SSI**: FastConnect đòi hỏi tài khoản chính chủ, mở Key API, đăng ký với công ty chứng khoán. Quá phức tạp và chậm trễ tiến độ.
- **Lý do Chọn MASVN**: Bảng giá Public của MASVN `banggia.masvn.com` phát lộ toàn bộ luồng WebSocket (Giao thức: SocketCluster) dưới dạng JSON thuần tuý. Nguồn tín hiệu này **không bị WAF chặn**, **không cần Auth/Cookie/CAPTCHA**, hoàn toàn mở. Đây là "mỏ vàng" để lập trình Auto-Bot 24/7.
- **Thuật toán áp dụng**: Engine được Refactor thành Kiến trúc Plugin. `TickRouter` sẽ đón cả DNSE và MASVN, sử dụng siêu thuật toán **First-Tick-Wins** với bộ đệm (TTL 3 giây) để lọc trùng. Thao tác siêu tốc giúp tránh hoàn toàn tình trạng mất dữ liệu do nghẽn mạng 1 bên.

### 2. EOD (End Of Day): Từ chối dùng MASVN làm đối soát
- Ban đầu User kiến nghị dùng MASVN làm nguồn 2 để chạy so sánh (Verification) EOD vào lúc 15:45 nhằm bảo hiểm tuyệt đối cho chất lượng dữ liệu của DNSE.
- **Phát hiện Reverse-Engineering**: Hệ thống Browser Agent và Terminal cURL đã chứng minh các API tải dữ liệu lịch sử (Chart OHLCV) của MASVN bị ép vào các cổng `mastrade.masvn.com/api` và khoá khắt khe bởi Load Balancer **F5 Networks BIG-IP**. Việc giải quyết Cookie/CAPTCHA liên tục bằng Headless Browser cho dữ liệu EOD là quá mạo hiểm và mong manh.
- **Quyết định chốt**: `eod_daily_close.py` GIỮ NGUYÊN cấu trúc duy nhất dựa vào Playwright + DNSE (Đã test sửa 77,000 nến đạt 100% không lỗ). Hủy bỏ kế hoạch nối MASVN EOD. Dồn cấu trúc phần cứng vào PA3 Intraday.

## Trạng thái Kế hoạch (Status)
Đã đóng gói tài liệu vào `Implementation Plan` và lên Task cụ thể. Sẽ thi công Refactor ở phiên làm việc tiếp theo.
