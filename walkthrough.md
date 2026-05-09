# Walkthrough: Thiết lập và Chạy Colab MCP Server

Tính năng kết nối với Google Colab qua MCP Protocol đã hoàn thiện dựa trên phương pháp sử dụng Ngrok Proxy. Phương pháp này đảm bảo tính ổn định cao, không phụ thuộc vào automation browser và tuân thủ chặt chẽ Hiến pháp dự án.

## Kiến trúc 
- Môi trường Local: Một process chạy `mcp.server.fastmcp` nhận lệnh.
- Môi trường Colab: Một Flask/FastAPI proxy chạy nhờ ngrok nhận execute call.

## Hướng dẫn Vận hành End-To-End

### Bước 1: Setup trên Google Colab
1. Mở file `colab_mcp/colab_notebook.ipynb` và copy nội dung đó (hoặc open file đó trực tiếp) trên **Google Colab**.
2. Đăng ký tài khoản Ngrok miễn phí tại `ngrok.com`, lấy **Ngrok Auth Token**.
3. Dán Auth Token vào đoạn `NGROK_AUTH_TOKEN = "..."` trong ô code (cell) của Colab.
4. Chạy ô notebook. Trong output sẽ in ra dòng kiểu:
   `[*] COPPY URL NÀY VÀO FILE .env LOCAL CỦA BẠN: https://xxxx.ngrok-free.app`

### Bước 2: Setup trên Máy Local (Máy cá nhân)
1. Mở file `.env` (mà tôi đã tạo cho dự án).
2. Thêm các dòng cấu hình mcp vừa lấy được vào file `.env`:
   ```env
   COLAB_PROXY_URL=https://xxxx.ngrok-free.app
   # COLAB_AUTH_TOKEN=YOUR_SECRET_TOKEN (nếu bạn có cài đặt security token ở script)
   ```

### Bước 3: Đưa MCP Server vào IDE
1. Bật terminal và nạp (activate) môi trường ảo Python:
   ```bash
   source venv/bin/activate
   ```
2. Khởi chạy server kiểm thử thông qua MCP Inspector (Hoặc cấu hình `colab_mcp/server.py` vào Cline/Gemini Desktop Settings trực tiếp):
   ```bash
   npx @modelcontextprotocol/inspector python -m colab_mcp.server
   ```
   > [!TIP]
   > Bạn có thể trực tiếp thêm cấu hình này vào cài đặt AI MCP của bạn:
   > Command: `/Volumes/Data/Antigravity Projects/quant/venv/bin/python`
   > Args: `-m`, `colab_mcp.server`

### Bước 4: Test Lệnh
Server sẽ hiển thị công cụ (tool) tên `execute_code`.
Bạn có thể ra lệnh cho LLM:
*"Hãy in ra số GPU khả dụng trên Colab thông qua tool execute_code"*
LLM sẽ gọi tool thực thị chuỗi:
```python
import torch
print(torch.cuda.is_available())
```
Kết quả (stdout) sẽ được trả về trực tiếp thông qua MCP.

> [!NOTE]
> Mặc định hệ thống unit test sử dụng URL mô phỏng (`mock`) để bypass lệnh curl thực và tuân thủ mô hình Test-First (TDD phase đã qua là Green).

---

# Walkthrough: Phase 6 - U2G Cào Thầm Lặng & Bóc Tách Đồ Thị Bằng LLM

Tính năng "Unstructured to Graph (U2G)" cho phép cào các tài liệu PDF/Word phức tạp sau đó sử dụng sức mạnh của Gemini 3.1 Pro để tự động phân tích và nhả ra các Node, Edge lưu vào cơ sở dữ liệu đồ thị (EKG).

## Các Thành phần Vừa Xây dựng
1. **`PdfCrawler`**: Trình cào ẩn danh bắt chước user-agent, tự động tải file PDF Báo cáo thường niên/Tài liệu ĐHĐCĐ về lưu băm mã `SHA-256` để kiểm soát trùng lặp.
2. **`GeminiGraphParser`**: Sử dụng thư viện `google.generativeai` kết hợp mô hình `gemini-3.1-pro-preview` chuyên dụng đọc file và nhả mảng `JSON` biểu diễn mạng lưới:
   - Các đỉnh `Source`, `Target`.
   - Các quan hệ `relation_type` như `OWNS_SHARES`, `PRODUCES_PRODUCT`, `CONSUMES_MATERIAL`, `OFFICER_AT`.
3. **`GraphLoader`**: Khối nạp dữ liệu (ETL Loader) Upsert các Đỉnh vào bảng `dim_entities` và các Mối quan hệ vào bảng `fact_relationship_network`, có cơ chế chống trùng Edge qua check `hashlib.md5`.
4. **`batch_u2g.py`**: Trình điều phối (Orchestrator) nối 3 thành phần trên lại với nhau phục vụ chạy cron định kỳ (Batch Job).

## Kết quả Chạy Demo
Đã chạy hệ thống với đoạn text mẫu của Tập đoàn Hòa Phát (HPG) thông qua lệnh:
```bash
venv/bin/python scripts/batch_u2g.py --demo
```

### Log Phản hồi của Gemini và DB Loader:
```
2026-04-14 13:12:46,699 [INFO] Bắt đầu quy trình U2G (Unstructured to Graph) cho mã: HPG
2026-04-14 13:12:46,700 [INFO] 📡 Gửi tài liệu vào Gemini (LLM) để trích xuất Graph JSON...
2026-04-14 13:13:05,395 [INFO] ✨ Gemini trả về 6 cấu trúc cạnh (Edges).
2026-04-14 13:13:05,414 [INFO] ✅ Tải thành công vào SQLite EKG schema: {'entities_inserted': 6, 'relationships_inserted': 6, 'status': 'success'}
```

### Truy vấn Kết quả trong SQLite:
```sql
SELECT s.entity_name as source, t.entity_name as target, r.relation_type
FROM fact_relationship_network r 
JOIN dim_entities s ON r.source_entity_id = s.entity_id 
JOIN dim_entities t ON r.target_entity_id = t.entity_id;
```
Kết quả trích xuất được 6 lưới cạnh bao gồm Chủ tịch HĐQT, Cấu trúc chuỗi cung ứng Vĩ Mô Đầu vào - Đầu ra:
1. `Ông Trần Đình Long` -> `HPG` (`OFFICER_AT`)
2. `HPG` -> `Khu liên hợp sản xuất gang thép Dung Quất` (`OWNS_SHARES`)
3. `HPG` -> `Thép cuộn cán nóng (HRC)` (`PRODUCES_PRODUCT`)
4. `HPG` -> `Thép Xây Dựng` (`PRODUCES_PRODUCT`)
5. `HPG` -> `Quặng Sắt` (`CONSUMES_MATERIAL`)
6. `HPG` -> `Than Cốc` (`CONSUMES_MATERIAL`)

> [!TIP]
> Tất cả Pipeline đã vượt qua TDD và sẵn sàng tích hợp với Crawler Data thực tế kéo từ CafeF/SSC.
