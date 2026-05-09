---
name: export_quant_state
description: Chuẩn bị đóng gói Não AI vào local chuẩn bị cho FreeFileSync hoạt động.
---

# Lệnh Bài `/export_quant_state`

*(Kịch bản áp dụng kết hợp với phần mềm FreeFileSync)*

Người dùng chuẩn bị rời khỏi máy. Mã nguồn và CSDL SQLite đã tự động nằm sẵn trong `/Users/tuanho/quant`. Bí quyết ở đây là: Chúng ta nén "Não AI" (Từ thư mục hệ thống `~/.gemini/antigravity`) thành 1 File Tar nằm NGAY BÊN TRONG dự án `quant`.
Sau thao tác này, người dùng chỉ cần tự tay bấm nút kích hoạt `FreeFileSync` để phần mềm "Quét" một phát mọi dữ liệu từ Local ném sang USB.

Dùng lệnh `// turbo-all` để chạy tự động.

---

// turbo
### 1. Thu Trữ Não Bộ
Nén toàn bộ Não Bộ AI hiện hành xuống thành 1 khối thả ngay vào thư mục làm việc hiện tại. Khối này sẽ đi nhờ "chuyến tầu FreeFileSync" cùng với anh em Codebase và SQLite.
```bash
# Xóa hộp sọ ảo cũ và đóng gói não mới
rm -f ./antigravity_brain_export.tar.gz
tar -czf ./antigravity_brain_export.tar.gz -C /Users/tuanho .gemini/antigravity

# Ngăn chặn Git bốc file 700MB này đẩy lên Github làm sập kho lưu trữ
if ! grep -q "antigravity_brain_export.tar.gz" .gitignore; then
    echo "antigravity_brain_export.tar.gz" >> .gitignore
fi
```

### 2. Thông báo chờ Đồng Bộ
*Thao tác Rút tủy đã xong! Đồ thị thần kinh hiện đang nằm tại tệp `antigravity_brain_export.tar.gz` trong thư mục dự án.*
*=> VIỆC TIẾP THEO: Đi ra ngoài màn hình và bấm Nút SYNC của phần mềm **FreeFileSync** để tống cả Cụm Thư mục này ra USB nhé! Chúc bạn đi đường bình an!*
