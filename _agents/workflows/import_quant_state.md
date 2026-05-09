---
name: import_quant_state
description: Cấy ghép Não bộ AI cục bộ từ bản Backup Sync sau khi chạy FreeFileSync.
---

# Lệnh Bài `/import_quant_state`

*(Kịch bản áp dụng kết hợp với phần mềm FreeFileSync)*

Khi bạn đến đích (Công ty / Ở Nhà), việc đầu tiên bạn làm là chạy FreeFileSync để đắp toàn bộ thư mục `/Volumes/Courses/quant` đè vào ổ Local `/Users/tuanho/quant`. Lúc này, cả Mã Nguồn, Cơ sở Dữ liệu (SQLite) và Khối File Nhớ AI (Brain) đều đã được nạp đầy vào thư mục Local.
Nhiệm vụ của lệnh bài này là Rã nén bộ nhớ AI đó và đút vào đúng hệ thần kinh lõi của thiết bị.

Dùng lệnh `// turbo-all` để chạy tự động.

---

> [!WARNING]
> CẢNH BÁO TỬ HUYỆT: **TUYỆT ĐỐI KHÔNG DÙNG AI (GEMINI) ĐỂ CHẠY LỆNH NÀY TRỰC TIẾP!**
> Việc AI tự chạy lệnh `rm -rf` vào chính não bộ của nó (.gemini/antigravity) sẽ gây ra chấn thương sụp đổ hệ thống ngay lập tức (Xóa mất phiên đang chạy).

Bạn **PHẢI** mở ứng dụng Terminal Terminal mặc định của MacOS (iTerm / Terminal), đảm bảo VSCode đã được đóng, và copy dán cụm lệnh sau bằng tay:

```bash
# Đóng toàn bộ IDE trước khi chạy
rm -rf /Users/tuanho/.gemini/antigravity
tar -xzf /Users/tuanho/quant/antigravity_brain_export.tar.gz -C /Users/tuanho
```

### 2. Định tuyến hoàn tất
*Não bộ đã khôi phục. AI đã biết chính xác DB nằm ở đâu và ta đang code dở dòng nào. Hãy Refresh/Reload Window IDE để bắt đầu làm việc!*
