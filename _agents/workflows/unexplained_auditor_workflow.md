---
name: Bóc mẻ Kế toán Kẻ hở (AI CPA Auditor)
description: Workflow tự động kích hoạt vai trò CPA để mổ xẻ và giải thích các mã cổ phiếu đang bị gán cờ `UNEXPLAINED` trong bảng financial_audits.
author: Antigravity
---

# Mục tiêu Kích hoạt
Khi người dùng gõ câu lệnh kiểu như: 
- *"Antigravity, bóc mẻ cho tôi những ca UNEXPLAINED tháng này"*
- *"Có mã nào Audit fail không do API"*
- Hoặc gọi lệnh trực tiếp `/unexplained_auditor_workflow`

Thì Antigravity BẮT BUỘC phải thực hiện chính xác các bước sau theo thứ tự:

### Bước 1: Quét Database lấy Danh sách Tội phạm (Suspects)
Antigravity tự động chạy lệnh SQL trên Database để lấy những bản ghi đang bị treo báo động.
```python
# turbo
python3 -c "
import os
import sqlite3
db_path = os.getenv('SMD_DB_PATH', './data/securities_master.db')
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute('''
    SELECT a.audit_id, s.symbol, a.year, a.ratio_name, a.api_value, a.calc_value, a.diff_pct 
    FROM financial_audits a
    JOIN securities s ON a.security_id = s.security_id
    WHERE a.status = 'UNEXPLAINED'
''')
rows = cur.fetchall()
if not rows:
    print('✅ Chúc mừng! Mọi báo cáo tài chính đều Khớp với chuẩn CFA, không có ca nào tẩu tán số liệu.')
else:
    for r in rows:
        print(f'🚨 [AUDIT_ID: {r[0]}] {r[1]} - Năm {r[2]} | Lệch Chỉ số {r[3]} | Vietcap: {r[4]} | Tự tính: {r[5]} | Độ chệch: {r[6]*100:.2f}%')
"
```

### Bước 2: Deep Dive (Gọi bằng chứng Pháp y JSON)
Nếu Bước 1 tìm thấy các mã Cổ phiếu bị lỗi (Lệch cực nặng, ví dụ `calc_value` ra 15% mà `api_value` ra 25%), Antigravity phải chọc tiếp vào Bảng `financial_reports` để rút cạn `data` JSON của năm đó ra để đối chứng.

*Mục đích:* Dùng não bộ LLM của Đặc vụ CPA xem xét các key kế toán gốc (Lợi ích cổ đông thiểu số, Cổ phiếu quỹ, Tái đánh giá tài sản...) để truy lý do Ratios lại bị vẹo.

### Bước 3: Forensic CPA Report (Báo cáo Pháp Y)
Antigravity viết một thông báo trả lời cho User (Hoặc xuất 1 Artifact Markdown) dưới giọng văn của một **Giám đốc Kiểm Toán (Chief Auditor)**:
- Nêu rõ Nguyên nhân Toán học cốt lõi (Gốc rễ sự sai số).
- Tiết lộ mánh khóe "tỉa tốt" BCTC của công ty hoặc Công thức nội bộ mà Sàn Giao Dịch đã âm thầm giấu diếm. Điểm mặt chính xác *Dòng tài khoản (Line Item)* nào là nguồn cơn.
- Nhấn mạnh nguy cơ AI Model vĩ mô có thể bị "Nhiễu" nếu học nhầm con số sai.

### Bước 4: Chốt Hồ Sơ (Closure)
Sau khi giải thích xong và người dùng gật đầu thông suốt, Antigravity phải đề xuất:
👉 *"Sếp có muốn tôi niêm phong lời giải thích này vào Database và đổi cờ đỏ của nó sang `EXPLAINED_BY_AI` không?"*
Nếu User đồng ý, Antigravity sẽ dùng Tùy chọn Python UPDATE SQL để ghi đè `explanation` vào `financial_audits` và chốt hạ case study!
