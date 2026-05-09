import os
import sys
import time
import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
import feedparser
import requests
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [SHB-SCANNER] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Môi trường
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(root_dir, '.env')
load_dotenv(env_path)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

STATE_FILE = os.path.join(root_dir, 'data', 'shb_news_state.json')

# Đảm bảo thư mục tồn tại
os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

class ScannerException(Exception):
    pass

def send_telegram(text: str):
    """Gửi tin nhắn qua Telegram, sử dụng retry nếu nghẽn mạng."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Không có cấu hình Telegram, bỏ qua send_telegram")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    @retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(3))
    def _send():
        response = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
        response.raise_for_status()
        
    try:
        _send()
    except Exception as e:
        logger.error(f"Lỗi gửi Telegram: {e}")

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except:
            pass
    return set()

def save_state(state_urls):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(state_urls), f, indent=2)

@retry(wait=wait_exponential(multiplier=2, min=10, max=60), 
       stop=stop_after_attempt(5),
       retry=retry_if_exception_type((requests.exceptions.RequestException, ScannerException)))
def fetch_rss_feed():
    query = '"SHB" AND ("Khối ngoại" OR "Bán vốn" OR "Phát hành" OR "Dragon Capital" OR "KIM")'
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=vi&gl=VN&ceid=VN:vi"
    logger.info(f"Cào RSS bằng query: {query}")
    
    # Fake User Agent quan trọng khi truy cập vào ban đêm
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        if hasattr(feed, 'status') and feed.status not in (200, 301, 302):
             raise ScannerException(f"Feed error status {feed.status}")
        return feed.entries
    except Exception as e:
        logger.warning(f"Lỗi bắt RSS, ném ra exception để retry: {e}")
        raise e

def evaluate_news_with_ai(title: str, summary: str):
    """
    Sử dụng Gemini để làm màng lọc tín hiệu.
    Yêu cầu trả về JSON chuẩn xác: {"score": 9, "summary": "..."}
    """
    if not GEMINI_API_KEY:
        return 0, ""
        
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""Đánh giá bản tin sau xem có mang TÍN HIỆU CAO cho cổ phiếu SHB không.
Tiêu đề: {title}
Trích dẫn: {summary}

Tiêu chí:
- Điểm 0-4: Chỉ nhắc tên SHB qua loa, tin rác, thị trường chung.
- Điểm 5-7: Tin tức về SHB kinh doanh, lợi nhuận (Tín hiệu vừa).
- Điểm 8-10: Sắp bán vốn (M&A), Khối ngoại mua thỏa thuận, Phát hành riêng lẻ với quỹ KIM/Dragon Capital... (Tín hiệu Mạnh).

Trả về ĐÚNG 1 ĐOẠN JSON duy nhất (không bọc trong \u0060\u0060\u0060json...):
{{"score": 9, "summary": "Tóm tắt ngắn gọn 2 dòng về tại sao tin này quan trọng"}}"""

    try:
        resp = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0)
        )
        text = resp.text.strip().removeprefix('```json').removesuffix('```').strip()
        data = json.loads(text)
        return data.get("score", 0), data.get("summary", "")
    except Exception as e:
        logger.error(f"Lỗi gọi Gemini filter: {e}")
        return 0, ""

def main():
    now_str = datetime.now().strftime("%H:%M")
    logger.info(f"Khởi động SHB Catalyst Scanner lúc {now_str}")
    send_telegram(f"🟢 `[{now_str}]` Máy quét xúc tác `SHB` khởi động...")
    
    try:
        entries = fetch_rss_feed()
        logger.info(f"Lấy được {len(entries)} bài báo từ RSS.")
        
        state_urls = load_state()
        new_alerts = 0
        
        for entry in entries:
            url = entry.link
            if url in state_urls:
                 continue
                 
            # Xử lý bài mới
            logger.info(f"Found new article: {entry.title}")
            score, ai_summary = evaluate_news_with_ai(entry.title, entry.summary)
            logger.info(f"AI Score: {score}")
            
            if score >= 8:
                msg = (f"🚨 **[SHB CATALYST ALERT] Chú ý M&A / Khối ngoại!**\n\n"
                       f"🔥 **Điểm Tín hiệu:** {score}/10\n"
                       f"📰 **Tiêu đề:** {entry.title}\n"
                       f"🤖 **AI Lọc:** {ai_summary}\n\n"
                       f"🔗 **Link:** [Đọc thêm]({url})")
                send_telegram(msg)
                new_alerts += 1
                
            state_urls.add(url)
            
        save_state(state_urls)
        
        if new_alerts == 0:
            logger.info("Hoàn tất quét. Không có tín hiệu mạnh mới.")
        else:
            logger.info(f"Đã cảnh báo {new_alerts} tin tức khẩn.")
            
        send_telegram(f"✅ `[{datetime.now().strftime('%H:%M')}]` Quét SHB an toàn. {len(entries)} tin nguồn, {new_alerts} tín hiệu.")
        
    except Exception as e:
        error_msg = f"🔴 **[CRASH] SHB Scanner thất bại!**\n```\n{e}\n```"
        logger.error(error_msg)
        send_telegram(error_msg)

if __name__ == "__main__":
    main()
