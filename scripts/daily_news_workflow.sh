#!/bin/bash
# ==========================================
# VSD Daily News & AI Workflow
# Execution Time: 05:00 AM (Mon - Fri)
# ==========================================

# 1. Khởi tạo môi trường
echo "=========================================="
echo "[$(date '+%Y-%m-%d %H:%M:%S')] BẮT ĐẦU CRONJOB BUỔI SÁNG"
echo "=========================================="

export SMD_DB_PATH="/Users/tuanho/quant/data/securities_master.db"
cd /Users/tuanho/quant

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Lỗi: Không tìm thấy thư mục venv!"
    exit 1
fi

# Load variables from .env to ensure cron environment matches terminal
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

# 1.5. Chạy SHB Catalyst Scanner (Rình mồi Khối Ngoại/M&A)
echo "------------------------------------------"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] PHA 0: SHB CATALYST SCANNER (RELIABILITY FRAMEWORK)"
echo "------------------------------------------"
python scripts/shb_catalyst_scanner.py


# 2. Chạy Cỗ Máy Tin Tức VietCap (Lấy 10-20 tin mới nhất của 1544 mã)
echo "------------------------------------------"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] PHA 1: KIẾM TÌM TIN MỚI (METADATA)"
echo "------------------------------------------"
python scripts/batch_news.py --delay 1
# Lưu ý: Script này có cơ chế Smart Resume, chạy rất êm.

# 3. Chạy Cỗ Máy Đắp Thịt (Full-text)
echo "------------------------------------------"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] PHA 2: BƠM THỊT VĂN BẢN (FULL-TEXT)"
echo "------------------------------------------"
# Đắp thịt liên tục cho đến khi không còn record rỗng nào, giả định 1 lần quét 500 bài là dư xài cho 1 ngày
python scripts/batch_fulltext.py --limit 1000

echo "=========================================="
echo "[$(date '+%Y-%m-%d %H:%M:%S')] KẾT THÚC CÀO DỮ LIỆU. CHUẨN BỊ GỌI AI!"
echo "=========================================="

# 4. Triệu hồi Trợ lý AI (Google Gemini) để phân tích báo cáo sáng
python scripts/morning_ai_summary.py

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ALL WORKFLOWS COMPLETED SUCCESSFULLY."
