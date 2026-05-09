import os
import sys
import sqlite3
import logging
import smtplib
import argparse
import httpx
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from google import genai
from google.genai import types
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Thư mục chứa Bản tin chứng khoán (Obsidian Vault trên Google Drive)
TARGET_DIR = "/Users/tuanho/Library/CloudStorage/GoogleDrive-tramminhho@gmail.com/My Drive/myVault/TuanHo/0. Daily Trading News"

# Danh sách mã đang nắm giữ (Portfolio)
PORTFOLIO = ["HPG", "SHB", "MBB", "ACB", "VND", "SSI", "POW", "VRE", "PSI", "NKG"]

SYSTEM_PROMPT = """Bạn là Giám đốc / Trưởng Phòng Tự Doanh tại một định chế tài chính lớn tại Việt Nam.
Nhiệm vụ: Đọc toàn bộ tin tức đêm qua → viết Bản Tin Buổi Sáng + Ra lệnh Quan tâm Giao Dịch cho đội ngũ.

PHƯƠNG PHÁP LUẬN (Cross-Impact Analysis):
1. Vẽ bức tranh vĩ mô từ <TIN_VI_MO_THI_TRUONG_CHUNG>: lãi suất, tỷ giá, hàng hóa, đòng tiền.
2. Chiếu vĩ mô xuống <TIN_TUC_MA_CO_PHIEU_CU_THE>: xác định mã nào có catalyst (xung lực) trong ngày.
3. Tổng hợp để ra nhận định danh mục đang giữ VÀ danh sách mã cần săn hôm nay.

Yêu cầu CẤU TRÚC xuất bằng Markdown, tiếng Việt:
# BẢN TIN TỰ DOANH — {date}

## 1. Bức Tranh Vĩ Mô & Thị Trường
- Xâu chuỗi các sự kiện vĩ mô quan trọng nhất đêm qua.
- Nhận định xu hướng VN-Index phiên hôm nay: Tăng / Giảm / Trung tnh?
- Nhóm ngành nào hưởng lợi, nhóm nào bất lợi?

## 2. Riủi Ro Cần Lưu Ý Hôm Nay
- Tối đa 3 yếu tố rủi ro cụ thể có thể ảnh hưởng phiên.

## 3. 🎯 Portfolio Scan — Danh Mục Đang Giữ
Phân tích từng mã, kể cả khi không có tin riêng — suy luận từ vĩ mô (trình bày theo Dạng Danh Sách, tuyệt đối KHÔNG dùng định dạng Bảng Table để tương thích hiển thị tin nhắn Telegram):
- 🔹 **[HPG]** (Thép): [🟢/🔴/🟡 Nhận định] Tác động: ... | C: ... | R: ...
- 🔹 **[SHB]** (Ngân hàng): [🟢/🔴/🟡 Nhận định] Tác động: ... | C: ... | R: ...
- 🔹 **[MBB]** (Ngân hàng): [🟢/🔴/🟡 Nhận định] Tác động: ... | C: ... | R: ...
- 🔹 **[ACB]** (Ngân hàng): [🟢/🔴/🟡 Nhận định] Tác động: ... | C: ... | R: ...
- 🔹 **[VND]** (Chứng khoán): [🟢/🔴/🟡 Nhận định] Tác động: ... | C: ... | R: ...
- 🔹 **[SSI]** (Chứng khoán): [🟢/🔴/🟡 Nhận định] Tác động: ... | C: ... | R: ...
- 🔹 **[POW]** (Điện): [🟢/🔴/🟡 Nhận định] Tác động: ... | C: ... | R: ...
- 🔹 **[VRE]** (BĐS bán lẻ): [🟢/🔴/🟡 Nhận định] Tác động: ... | C: ... | R: ...
- 🔹 **[PSI]** (Chứng khoán): [🟢/🔴/🟡 Nhận định] Tác động: ... | C: ... | R: ...
- 🔹 **[NKG]** (Thép/Tôn mạ): [🟢/🔴/🟡 Nhận định] Tác động: ... | C: ... | R: ...

## 4. Tin Nổi Bật Cổ Phiếu Đáng Chú Ý
- Tổng hợp các mã (ngoài danh mục) có tin tức catalyst đảo chiều / đột biến trong đêm.
- Gán nhãn tác động ngắn hạn: [🟢 Tăng] / [🔴 Giảm] / [🟡 Chờ]

## 5. 🔥 RADAR THỊ TRƯỜNG — Lệnh Quan Tâm Giao Dịch Hôm Nay
Với tư cách Trưởng Phòng Tự Doanh, liệt kê tối đa 10 mã cần săn trong phiên hôm nay:
Tiêu chí chọn: Có catalyst rõ (tin tất, KQKD, chính sách, dòng vốn) + hỗ trợ bởi vĩ mô. Trình bày Dạng Danh Sách (KHÔNG dùng Bảng Table):

1. 🎯 **[MÃ 1]** (Ngành) - [MUA/BÁN/WATCH] (Độ nóng: 🔥🔥🔥)
   - Catalyst: ...
2. 🎯 **[MÃ 2]** (Ngành) - [MUA/BÁN/WATCH] (Độ nóng: 🔥🔥)
   - Catalyst: ...
...

Ghi chú: 🔥🔥🔥 = Catalyst mạnh, có thể vào GAP / đầu phiên. 🔥 = Theo dõi, chờ tín hiệu.

Văn phong: Lạnh lùng, sắc bén, bám dữ kiện, không suy diễn vô căn cứ.
"""

def get_news_from_db(db_path: str, symbols: list = None):
    try:
        conn = sqlite3.connect(db_path, detect_types=0)
        cur = conn.cursor()

        if symbols:
            # On-demand /soi: _MACRO + mã chỉ định + portfolio
            all_syms = list(dict.fromkeys(symbols + PORTFOLIO))
            ph = ','.join(['?'] * len(all_syms))
            cur.execute(f"""
                SELECT s.symbol, n.title, n.content, n.published_at
                FROM news_sentiment n
                JOIN securities s ON n.security_id = s.security_id
                WHERE n.published_at >= datetime('now', '-24 hours')
                  AND (s.symbol = '_MACRO' OR s.symbol IN ({ph}))
                ORDER BY n.published_at ASC
            """, all_syms)
        else:
            # Bản tin sáng: _MACRO ưu tiên, rồi toàn bộ cổ phiếu (kể cả 1544 mã)
            # Xếp: _MACRO trước → portfolio → các mã khác
            portfolio_ph = ','.join(['?'] * len(PORTFOLIO))
            cur.execute(f"""
                SELECT s.symbol, n.title, n.content, n.published_at
                FROM news_sentiment n
                JOIN securities s ON n.security_id = s.security_id
                WHERE n.published_at >= datetime('now', '-24 hours')
                ORDER BY
                  CASE WHEN s.symbol = '_MACRO' THEN 0
                       WHEN s.symbol IN ({portfolio_ph}) THEN 1
                       ELSE 2 END ASC,
                  n.published_at ASC
            """, PORTFOLIO)

        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"Lỗi truy vấn DB: {e}")
        return []

def send_email(sender_email, sender_password, receiver_email, content, date_str):
    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = f"Báo Cáo Tự Doanh AI - Nhận định Chứng Khoán Ngày {date_str}"
        
        # Gửi Plain-text cho an toàn để nó hiển thị mượt Markdown
        msg.attach(MIMEText(content, 'plain', 'utf-8'))
        
        logger.info("Đang khởi tạo kết nối SMTP sang máy chủ Gmail...")
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        text = msg.as_string()
        server.sendmail(sender_email, receiver_email, text)
        server.quit()
        logger.info(f"✅ ĐÃ PHÁT SÓNG EMAIL THÀNH CÔNG ĐẾN {receiver_email}")
    except Exception as e:
        logger.error(f"❌ LUỒNG EMAIL THẤT BẠI: {e}")

def convert_md_to_tg_html(text: str) -> str:
    # Bảo vệ các ký tự HTML cơ bản để Telegram không hiểu lầm
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    
    # Chuyển đổi Markdown Bold (**chữ**) thành thẻ <b>chữ</b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    
    # Chuyển đổi Headers (# Header) thành <b>Header</b>
    text = re.sub(r'^#+\s+(.*)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    
    # Chuyển đổi các link dạng [Text](URL)
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', text)
    
    return text

def send_telegram_alert(token: str, chat_id: str, content: str):
    try:
        tg_url = f"https://api.telegram.org/bot{token}/sendMessage"
        html_content = convert_md_to_tg_html(content)
        
        parts = [html_content[i:i+4090] for i in range(0, len(html_content), 4090)]
        for chunk in parts:
            resp = httpx.post(tg_url, json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
            }, timeout=15)
            if resp.status_code == 200:
                logger.info("✅ Đã bắn báo cáo On-Demand lên Telegram!")
            else:
                logger.warning(f"Lỗi Telegram: {resp.text}")
    except Exception as e:
        logger.error(f"❌ LUỒNG TELEGRAM THẤT BẠI: {e}")

def main():
    parser = argparse.ArgumentParser(description="Morning AI News / On-demand Analysis")
    parser.add_argument('--symbols', type=str, default=None, help="Danh sách mã cách nhau bằng dấu phẩy")
    args = parser.parse_args()

    symbols_list = [s.strip().upper() for s in args.symbols.split(',')] if args.symbols else None

    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root_dir, '.env')
    load_dotenv(env_path)
    
    # Sử dụng key được user chỉ định trực tiếp cho luồng này
    api_key = os.getenv("GEMINI_TIER1_KEY")
    if not api_key:
        logger.error("Không tìm thấy GEMINI_TIER1_KEY trong .env")
        sys.exit(1)
        
    db_path = os.getenv("SMD_DB_PATH", os.path.join(root_dir, "data/securities_master.db"))
    
    logger.info("Đang đọc luồng tin tức 24h qua từ DB...")
    news_rows = get_news_from_db(db_path, symbols_list)
    if not news_rows:
        logger.info("Không có tin tức nào mới trong 24h qua. Dừng luồng AI Morning.")
        sys.exit(0)
        
    logger.info(f"Phát hiện tổng cộng {len(news_rows)} bài tin tức mới.")
    
    # Đóng gói dữ liệu tách biệt Macro và Normal
    macro_text = "<TIN_VI_MO_THI_TRUONG_CHUNG>\n"
    normal_text = "<TIN_TUC_MA_CO_PHIEU_CU_THE>\n"
    
    total_length = 0
    macro_count = 0
    normal_count = 0
    
    for symbol, title, content, published_at in news_rows:
        clean_content = str(content).strip() if content else str(title).strip()
        
        if symbol == '_MACRO':
            macro_count += 1
            article_text = f"--- TIN VĨ MÔ {macro_count} ---\nTHỜI GIAN: {published_at}\nTIÊU ĐỀ: {title}\nNỘI DUNG: {clean_content}\n"
            macro_text += article_text + "\n"
        else:
            normal_count += 1
            article_text = f"--- BÀI {normal_count} ---\nMÃ CK: {symbol}\nTHỜI GIAN: {published_at}\nTIÊU ĐỀ: {title}\nNỘI DUNG: {clean_content}\n"
            normal_text += article_text + "\n"
            
        total_length += len(article_text)
        
    macro_text += "</TIN_VI_MO_THI_TRUONG_CHUNG>\n"
    normal_text += "</TIN_TUC_MA_CO_PHIEU_CU_THE>\n"
    
    data_text = f"{macro_text}\n{normal_text}"
    
    logger.info(f"Đã nạp {macro_count} tin Vĩ Mô và {normal_count} tin Cổ phiếu.")
    logger.info(f"Tổng khối lượng Text chuẩn bị đẩy lên Gemini: {total_length:,} ký tự (khoảng ~{total_length//4} Tokens).")
    
    # Gọi AI bằng SDK mới google.genai
    client = genai.Client(api_key=api_key)
    
    logger.info("Đang giao tiếp với Gemini 2.5 Pro (Thao tác này có thể tốn vài chục giây đến 2 phút để xử lý Context khổng lồ)...")

    try:
        # Nếu là On-demand thì cấu hình prompt và ghi đè filename
        if symbols_list:
            sys_prompt = SYSTEM_PROMPT.replace(
                "Đọc toàn bộ lượng tin tức từ đêm qua đến sáng nay và viết một 'Bản Tin Chứng Khoán Đầu Ngày'", 
                f"Làm báo cáo PHÂN TÍCH NÓNG ON-DEMAND tập trung vào các mã: {', '.join(symbols_list)}"
            )
            response = client.models.generate_content(
                model='gemini-2.5-pro',
                contents=data_text,
                config=types.GenerateContentConfig(
                    system_instruction=sys_prompt.replace("{date}", datetime.now().strftime("%Y-%m-%d %H:%M")),
                )
            )
        else:
            response = client.models.generate_content(
                model='gemini-2.5-pro',
                contents=data_text,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT.replace("{date}", datetime.now().strftime("%Y-%m-%d")),
                )
            )
        report_content = response.text
    except Exception as e:
        logger.error(f"Khúc gọi API AI bị lỗi: {e}")
        sys.exit(1)
        
    logger.info("AI Đã trả về báo cáo thành công! Đang lưu xuống ổ cứng...")

    # Lưu file Obsidian Vault
    os.makedirs(TARGET_DIR, exist_ok=True)
    current_date = datetime.now().strftime("%Y-%m-%d")

    if symbols_list:
        filename = f"Phân tích On-demand {','.join(symbols_list)} - {datetime.now().strftime('%Y%m%d_%H%M')}.md"
    else:
        filename = f"Bản tin chứng khoán ngày {current_date}.md"

    file_path = os.path.join(TARGET_DIR, filename)

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(report_content)
        logger.info(f"✅ ĐÃ SINH BÁO CÁO TẠI: '{file_path}'")
    except Exception as e:
        logger.error(f"Lỗi khi lưu file {file_path}: {e}")

    # Gửi Telegram — cho cả 2 chế độ: On-demand VÀ Bản tin sáng
    tg_token   = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat_id:
        if not symbols_list:
            # Bản tin sáng: thêm header ngày vào đầu message
            header = f"🌅 <b>BẢN TIN CHỨNG KHOÁN SÁNG {current_date}</b>\n\n"
            send_telegram_alert(tg_token, tg_chat_id, header + report_content)
        else:
            send_telegram_alert(tg_token, tg_chat_id, report_content)
    else:
        logger.warning("⚠️ Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID — bỏ qua gửi Telegram.")

if __name__ == "__main__":
    main()
