import os
import sqlite3
import pandas as pd
from pathlib import Path
from google import genai
from google.genai import types

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("gemini_analyst")

DB_PATH = 'data/securities_master.db'
PDF_DIR = Path('data/bctc_q1_2026')

SYSTEM_PROMPT = """Bạn là một Chuyên viên Phân tích Báo cáo Tài chính mảng Ngân hàng (Banking Equity Analyst) sắc bén và dày dạn kinh nghiệm tại một Ngân hàng Đầu tư (Investment Bank) hàng đầu.
Nhiệm vụ của bạn là đọc Báo cáo tài chính quý 1/2026 của ngân hàng, kết hợp với bộ dữ liệu lịch sử các quý trước và đưa ra một bản BÁO CÁO PHÂN TÍCH CHUYÊN SÂU.

Yêu cầu định dạng báo cáo (dùng Markdown):
# 📈 BÁO CÁO PHÂN TÍCH TÀI CHÍNH: QUÝ 1/2026 - MÃ {SYMBOL}

## 1. Điểm nhấn Tài chính (Key Highlights)
> (Nêu bật 3-4 điểm đáng chú ý nhất trong kỳ: Lợi nhuận bứt phá/suy giảm, chất lượng tài sản thay đổi ra sao...)

## 2. Bóc tách Kết quả Kinh doanh
- **Tăng trưởng Tín dụng & Huy động:** So với cuối kỳ trước (Q4/2025) và cùng kỳ (Q1/2025).
- **Thu nhập Lãi thuần (NII) & Thu nhập ngoài Lãi (NFI):** Đánh giá động lực chính.
- **Biên lãi ròng (NIM) ước tính:** Có xu hướng mở rộng hay thu hẹp?

## 3. Quản trị Rủi ro & Chất lượng Tài sản
- **Tỷ lệ Nợ xấu (NPL):** Xu hướng Nợ nhóm 3, 4, 5. Có dấu hiệu suy giảm chất lượng tài sản không?
- **Tỷ lệ Bao phủ Nợ xấu (LLR):** Bộ đệm dự phòng có đủ dày? (Trích lập dự phòng trong kỳ thay đổi thế nào).

## 4. Hiệu quả Hoạt động (CIR & Lợi nhuận)
- **Kiểm soát chi phí (CIR):**
- **Lợi nhuận Trước Thuế & Mức độ hoàn thành kế hoạch:** 
- **ROE và ROA ước tính:**

## 5. Kết luận & Khuyến nghị (Góc nhìn Chuyên gia IB)
- Đánh giá sức khỏe tài chính và rủi ro tiềm ẩn.
- Nhận định sơ bộ cho các quý tiếp theo trong môi trường vĩ mô Q1/2026.
"""

def get_historical_data(symbol: str) -> str:
    """Lấy số liệu tài chính lịch sử từ DB để làm ngự cảnh."""
    try:
        conn = sqlite3.connect(DB_PATH)
        query = f"""
            SELECT fr.year, fr.quarter, json_extract(fr.data, '$.revenue') as revenue, json_extract(fr.data, '$.netProfit') as netProfit, json_extract(fr.data, '$.roe') as roe
            FROM financial_reports fr
            JOIN securities s ON fr.security_id = s.security_id
            WHERE s.symbol = '{symbol}' AND fr.period = 'quarterly'
            ORDER BY fr.year DESC, fr.quarter DESC
            LIMIT 5
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        if not df.empty:
            return df.to_csv(index=False)
    except Exception as e:
        logger.error(f"Lỗi lấy lịch sử {symbol}: {e}")
    return "Không có dữ liệu lịch sử."

def find_pdf_for_symbol(symbol: str) -> Path:
    # Match theo symbol_HopNhat hoặc _Rieng, hoặc search alias trong tên file
    files = list(PDF_DIR.glob(f"*{symbol}*Q1_2026*.pdf"))
    if not files:
        if symbol == "SHB":
            files = list(PDF_DIR.glob(f"*Sài*Gòn*Hà_Nộ*HopNhat*.pdf"))
        elif symbol == "MSB":
            files = list(PDF_DIR.glob(f"*Hàng_Hải*.pdf"))
    return files[0] if files else None

def analyze_bank(symbol: str):
    logger.info(f"=== Bắt đầu phân tích {symbol} ===")
    
    # 1. Tìm File
    pdf_path = find_pdf_for_symbol(symbol)
    if not pdf_path:
        logger.error(f"❌ Không tìm thấy file PDF Q1/2026 cho {symbol}.")
        return

    # 2. Lấy DB History
    history_md = get_historical_data(symbol)
    
    logger.info(f"Uploading {pdf_path.name} to Gemini...")
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    
    import shutil
    import tempfile
    
    # Upload file - google.genai has issues with unicode filenames in headers, so we copy it to a temp ASCII file
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
        
    shutil.copy2(pdf_path, tmp_path)
    try:
        uploaded_file = client.files.upload(file=tmp_path)
        logger.info(f"Uploaded as: {uploaded_file.name}")
    finally:
        os.remove(tmp_path)

    
    # 3. Prompting
    prompt = f"""Dưới đây là tài liệu BCTC Q1/2026 của {symbol} (trong file đính kèm) và số liệu lịch sử 5 quý gần nhất từ Database:

[LỊCH SỬ {symbol}]
Year,Quarter,Revenue,NetProfit,ROE
{history_md}

Hãy thực hiện phân tích dựa trên yêu cầu định dạng đã cho."""

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt, uploaded_file],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT.replace('{SYMBOL}', symbol),
                temperature=0.2,
            )
        )
        
        # Save output
        out_path = Path(f"./data/analysis/Q1_2026_{symbol}_Analysis.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(response.text)
        
        logger.info(f"✅ Báo cáo hoàn tất. Đã lưu tại: {out_path}")
        print(f"\n{response.text[:1000]}\n...\n")
        
    except Exception as e:
        logger.error(f"❌ Lỗi khi gửi Gemini: {e}")
    finally:
        try:
            client.files.delete(name=uploaded_file.name)
            logger.info("Đã xóa file trên Gemini server.")
        except Exception:
            pass

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    # Do MBB chưa có trên UBCK, phân tích SHB và thử một bank có sẵn là MSB
    symbols_to_analyze = ["SHB", "MSB"]
    
    for s in symbols_to_analyze:
        analyze_bank(s)
