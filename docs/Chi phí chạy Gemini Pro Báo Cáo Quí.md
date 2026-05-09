Chào bạn, để lập dự toán ngân sách một cách bài bản, chúng ta sẽ xây dựng bài toán ước tính chi phí dựa trên các giả định thực tế về khối lượng dữ liệu của Báo cáo Thường niên (Annual Report) trên thị trường chứng khoán Việt Nam.

### 1. Giả định biến số (Assumptions)
- **Số lượng mã cổ phiếu**: 1,700 mã (chạy 1 lần mỗi quý).
- **Đầu vào (Input)**: Báo cáo thường niên / Cáo bạch ở Việt Nam thường trung bình dao động từ 100,000 đến 300,000 tokens (bao gồm cả phân tích văn bản và nhận diện bảng biểu đồ thị). Để an toàn biên độ, giả định trung bình là **250,000 tokens / 1 doanh nghiệp**.
- **Đầu ra (Output)**: Kết quả Graph JSON xuất ra. Mặc định cực kỳ cô đọng, giả định trung bình **1,000 tokens / 1 doanh nghiệp**.
- **Tỷ giá**: 1 USD ≈ 25,500 VND.

### 2. Ước tính Tổng Tokens mỗi Quý
- **Tổng Input Tokens**: 1,700 mã × 250,000 = **425 Triệu Tokens**
- **Tổng Output Tokens**: 1,700 mã × 1,000 = **1.7 Triệu Tokens**

—

### 3. Phương án 1: Dùng mô hình Thông minh Chuyên gia (Gemini 3.1 Pro / 1.5 Pro)
*Mức giá tham khảo API cho Context Window > 128k (Tính giá thương mại trả phí - Pay As You Go)*
- Giá Input: Khoảng ~$2.50 / 1 Triệu tokens
- Giá Output: Khoảng ~$7.50 / 1 Triệu tokens

**Chi phí (USD):**
- Input Cost: 425 * $2.50 = **$1,062.50**
- Output Cost: 1.7 * $7.50 = **$12.75**
- **Tổng USD**: ~$1,075 / Quý

💰 **Quy đổi VND**: ~$1,075 × 25,500 đ ≈ **27,412,500 VNĐ / Quý** 
*(Tương đương khoảng 16 ngàn đồng / 1 mã cổ phiếu - Một mức giá cực kỳ rẻ để có một bản trích xuất chất lượng chuyên gia tài chính cấp cao phân tích)*.

—

### 4. Phương án 2: Dùng mô hình Tốc độ cao (Gemini Flash)
*(Giả sử bạn cải thiện kỹ thuật Prompt để ép Flash thông minh gần bằng Pro)*
*Mức giá tham khảo chung của dòng Flash cho Context Window > 128k:*
- Giá Input: Khoảng ~$0.15 / 1 Triệu tokens (Flash siêu rẻ)
- Giá Output: Khoảng ~$0.60 / 1 Triệu tokens

**Chi phí (USD):**
- Input Cost: 425 * $0.15 = **$63.75**
- Output Cost: 1.7 * $0.60 = **$1.02**
- **Tổng USD**: ~$65 / Quý

💰 **Quy đổi VND**: ~$65 × 25,500 đ ≈ **1,657,500 VNĐ / Quý**
*(Tương đương nhỉnh hơn 1 ngàn đồng / 1 mã cổ phiếu)*.

—

### 💡 Khuyến nghị Kiến trúc Lượng hóa:
Nếu ngân sách cho phép, **Phương án 1 (Pro)** hoàn toàn đáng giá (27 triệu VNĐ/Quý). Việc dùng Pro giúp tránh các lỗi "ảo giác" (hallucination) nghiêm trọng như:
1. Xác định nhầm Công ty Con thành Đối thủ (Vì AI đọc lướt).
2. Trượt mất dữ liệu sở hữu của các thế lực phía sau.

Cách tối ưu nhất (Hybrid Routing): Lập trình **pipeline quét 1,700 mã bằng Flash trước** (mất ~1.6 triệu VNĐ). Sau đó dùng `batch_u2g.py` chạy audit lại 100 mã cổ phiếu trụ cột (VN100) bằng **Gemini Pro** (Chỉ tốn thêm ~1.6 triệu VNĐ nữa). Tổng chi phí chỉ dao động tầm **3 - 4 triệu VNĐ/Quý** mà vẫn đảm bảo độ chính xác tuyệt đối cho rổ VN100.