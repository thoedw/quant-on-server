# Đánh giá các Nhà Cung cấp Dữ liệu Chứng khoán (API) cho Cá nhân tại Việt Nam

Để xây dựng hệ thống thuật toán và CSDL nội bộ, tôi đã tổng hợp phân tích các nhà cung cấp DataFeed/API tiềm năng nhất hiện nay trên thị trường.

## 1. Hệ sinh thái Mã nguồn mở / API Các Công ty Chứng khoán

### 🟢 DNSE LightSpeed API (Entrade X)
Đây là "ngôi sao đang lên" trong cộng đồng Quant Việt Nam và được thiết kế đặc biệt cho dân công nghệ.
- **Chi phí**: **Miễn phí** (Chỉ cần mở tài khoản chứng khoán Entrade).
- **Thế mạnh**: 
  - Tài liệu (Docs) rất rõ ràng, chuẩn RESTful và WebSocket.
  - Không chỉ lấy dữ liệu (Market Data cực nhanh), mà còn cho phép **đặt lệnh giao dịch tự động (Trading API)**.
- **Điểm yếu**: Tập trung mạnh vào luồng lệnh (Order flow) và giá thời gian thực. Dữ liệu tài chính (Financials) lịch sử có thể không dày dặn và đầy đủ 10-15 năm như các bên chuyên bán Data.

### 🟢 SSI FastConnect API
SSI cũng mở API cho nhà đầu tư cá nhân gắn với tài khoản giao dịch.
- **Chi phí**: Thường yêu cầu có tài sản trị giá tối thiểu (NAV ~50-100 triệu tuỳ thời điểm) để cấp quyền.
- **Thế mạnh**: Tốc độ và độ ổn định của hệ thống SSI thuộc hàng top thị trường. Trực tiếp kéo dữ liệu Datafeed.
- **Điểm yếu**: Thủ tục đăng ký đôi khi rườm rà. Docs hơi hàn lâm.


## 2. Các Đơn vị Bán Data Chuyên nghiệp

Những bên này sống bằng nghề thu thập, dọn dẹp và làm sạch dữ liệu, nên "Dữ liệu Tài chính (Financials)" của họ vô cùng uy tín và sâu.

### 🟡 WiChart / FireAnt
- **Chi phí cá nhân**: Rơi vào khoảng **3tr - 6tr/năm** (Gói Premium/Pro).
- **Thế mạnh**: Hệ thống chỉ số vĩ mô, chuỗi thời gian, tài chính doanh nghiệp đã được "làm sạch" cực đỉnh. Web trực quan. FireAnt hỗ trợ xuất data sang AmiBroker thông qua MetaKit.
- **Điểm yếu**: Họ chủ yếu cung cấp nền tảng web hoặc plugin Excel/AmiBroker. Việc bạn muốn có **1 key API REST** để viết thẳng bằng Python nạp vào Database cá nhân thường **không có sẵn trong báo giá** (phải liên hệ mua gói Doanh nghiệp rất đắt).

### 🔴 FiinGroup (FiinPro/FiinTrade) & Vietstock
- **Chi phí**: Hàng chục triệu đồng/năm.
- **Thế mạnh**: Tổ chức tài chính, quỹ đầu tư lớn đều dùng data của họ. Chuẩn mực thị trường.
- **Điểm yếu**: Quá đắt và thừa thãi so với một cá nhân tự phát triển hệ thống giao dịch thuật toán.


## 3. Lựa chọn lai (Hybrid Approach)

### 🔵 Thư viện Vnstock (Gói Golden Sponsor)
Vnstock bản chất là một *wrapper* bọc lại các API nội bộ bị lộ (hoặc semi-public) của TCBS, SSI, VNDirect. Gói Golden Sponsor (2.4tr/năm) thực chất không phải là tiền mua "Dữ liệu" (vì dữ liệu đó vốn nằm ở TCBS), mà là tiền mua **"Công cụ Bypass Rate Limit & Proxy để cào Data tốc độ cao"**.
- **Điểm mạnh vô đối**: Viết bằng Python, phù hợp đúng ngôn ngữ bạn đang dùng. Support rất tận tình. Code bọc sẵn cực kỳ nhàn.

## 4. Các Nền tảng Toàn cầu (TradingView & Investing.com)

Rất nhiều nhà phân tích muốn lấy dữ liệu trực tiếp từ **TradingView (Gói Premium)** hoặc **Investing.com (Gói Pro/VIP)** vì chart của họ quá đẹp và chỉ số đầy đủ. Tuy nhiên, nếu xét dưới góc độ Data Pipeline thì đây là một **LỰA CHỌN TỒI**.

### ❌ TradingView 
- **Chi phí**: 15$ - 60$/tháng (Khoảng 4 - 15tr/năm).
- **Thực trạng API**: TradingView **KHÔNG BAO GIỜ** cung cấp API (RESTful) tải dữ liệu thô (OHLCV/Financials) cho người dùng cá nhân (dù bạn mua gói đắt nhất). API của họ chỉ dành riêng cho các Sàn giao dịch hoặc Broker đối tác.
- **Cách lách luật (Scraping)**: Có các thư viện Python lậu như `tvdatafeed` để cào trộm data từ TradingView, nhưng nó thỉnh thoảng bị lỗi đăng nhập và có nguy cơ bị khóa tài khoản (Ban IP/Account).
- **Phù hợp với**: Thuật toán viết bằng **Pine Script** và bắn tín hiệu (Webhook) gián tiếp. KHÔNG NHỮNG KHÔNG tải được về CSDL SQLite của chúng ta một cách ổn định, mà việc viết Script Cron cào hàng ngày là vi phạm chính sách của họ.

### ❌ Investing.com 
- **Chi phí**: Khoảng 10$ - 15$/tháng.
- **Thực trạng API**: Giống TradingView, họ **KHÔNG MỞ API** chính thức để lấy dữ liệu. Dữ liệu chứng khoán Việt Nam của họ cũng lấy lại từ bên thứ 3.
- **Cách lách luật (Scraping)**: Dùng thư viện `investpy`. Tuy nhiên hiện tại Investing.com đã ốp hệ thống bảo mật CloudFlare cực mạnh, chặn gắt gao bot Python (thường xuyên trả về lỗi `HTTP 403 Forbidden`). Quá trình Automated ETL của chúng ta chắc chắn sẽ gãy liên tục.

---

## 🏆 KẾT LUẬN & ĐỀ XUẤT CHO BẠN

Dựa vào việc bạn đang thiết lập một cấu trúc **Database bằng file SQLite Local** và code thuật toán bằng Python:

1. **Option Tiết kiệm nhất (0đ nhưng thủ công)**: Bạn giữ nguyên hệ thống hiện tại, chịu khó để máy chạy cào data chậm chậm qua đêm bằng thư viện miễn phí `vnstock` v3. Tuy nhiên, đành chấp nhận hy sinh dữ liệu tài chính sâu hơn 4 năm.
2. **Option Khuyên dùng nhất cho Data (Tối ưu P/P)**: Nâng cấp **vnstock Golden Sponsor (2.4tr/năm)**. Vì code gốc của chúng ta đang dùng vnstock. Bạn chỉ cần nâng cấp là luồng code chạy như vũ bão, kéo được báo cáo tài chính chục năm mà không phải sửa logic phức tạp. 
3. **Option Tương lai cho Trading (Bot Đặt Lệnh)**: Đăng ký **DNSE LightSpeed API** (hoàn toàn Free). Sang Phase Phân tích Giao dịch, chúng ta sẽ viết code luân chuyển tín hiệu (Signal) vào DNSE API để bot tự động mua bán mà không cần bấm chuột.
