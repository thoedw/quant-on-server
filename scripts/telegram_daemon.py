import os
import sys
import time
import httpx
import logging
import subprocess
from dotenv import load_dotenv

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [TG-DAEMON] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Load Environment
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(root_dir, '.env')
load_dotenv(env_path)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TOKEN or not ALLOWED_CHAT_ID:
    logger.error("❌ Thiếu cấu hình TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID trong file .env!")
    sys.exit(1)

def send_message(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        resp = httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"❌ Không thể gửi tin nhắn phản hồi: {e}")

def process_command(chat_id: str, text: str):
    # Chuẩn hóa văn bản
    text = text.strip()
    
    # Chỉ bắt các lệnh yêu cầu phân tích
    if text.startswith(('/soi', '/news', '/s ')):
        # Tách mảng các từ đằng sau lệnh
        parts = text.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            send_message(chat_id, "⚠️ Sếp chưa nhập mã cổ phiếu. Ký pháp trúng đích: /soi VND HPG")
            return
            
        raw_symbols = parts[1].strip()
        # Chuẩn hóa symbols (vứt dấu phẩy, chuyển chữ hoa)
        clean_symbols = [s.strip().upper() for s in raw_symbols.replace(',', ' ').split() if s.strip()]
        
        if not clean_symbols:
            return
            
        sym_str = ", ".join(clean_symbols)
        logger.info(f"🚀 Nhận lệnh phân tích On-demand cho các mã: {sym_str}")
        
        # Bắn log xác nhận ngay lập tức
        ack_msg = f"🚀 Đã tiếp nhận lệnh phân tích On-demand!\n\n🔍 Đang triển khai đội Crawler đi múc Vĩ mô và gom tin tức cho: {sym_str}.\n🧠 Trí tuệ AI Gemini sẽ tổng hợp và ném lại kết quả cho sếp sau ~30 đến 60 giây nữa..."
        send_message(chat_id, ack_msg)
        
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        python_bin = os.path.join(root_dir, "venv", "bin", "python3")
        if not os.path.exists(python_bin):
            python_bin = sys.executable  # fallback về Python hiện tại

        engine_script = os.path.join(root_dir, "scripts", "nightly_news_engine.py")
        sym_arg = ",".join(clean_symbols)

        # Chạy 1 shell command: engine cào tin → xong → morning_ai_news phân tích
        # Dùng bash -c để chain 2 lệnh tuần tự trong 1 Popen (non-blocking)
        chain_cmd = (
            f"cd {root_dir} && "
            f"PYTHONPATH={root_dir} {python_bin} scripts/nightly_news_engine.py "
            f"{' '.join(clean_symbols)} --force-run --skip-macro && "
            f"PYTHONPATH={root_dir} {python_bin} scripts/morning_ai_news.py "
            f"--symbols {sym_arg}"
        )

        try:
            subprocess.Popen(
                ["bash", "-c", chain_cmd],
                stdout=open(f"/tmp/soi_{sym_arg}.log", "w"),
                stderr=subprocess.STDOUT,
            )
            logger.info(f"✅ Đã kích hoạt chuỗi Crawler → AI cho: {sym_str}")
        except Exception as e:
            logger.error(f"Lỗi khi boot Engine: {e}")
            send_message(chat_id, "❌ Lỗi khởi động cỗ máy hốt tin. Sếp kiểm tra lại Server nhé!")
            
    elif text == "/ping":
        send_message(chat_id, "🟢 Quant Whale Hunter: Radar vẫn đang xoay! Sếp muốn soi gì cứ chat /soi <mã CK> nhé!")

def main():
    logger.info("=" * 60)
    logger.info("🤖 TELEGRAM BOT WATCHDOG KHỞI ĐỘNG CÙNG SERVER")
    logger.info("=" * 60)
    logger.info(f"Đang canh gác trên Chat ID: {ALLOWED_CHAT_ID}")
    
    offset = 0
    base_url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    
    while True:
        try:
            # Long-polling vắt lên 30 giây để không tốn CPU/Băng thông, cực mượt
            resp = httpx.get(
                base_url, 
                params={"offset": offset, "timeout": 30}, 
                timeout=35
            )
            
            if resp.status_code == 200:
                data = resp.json()
                if "result" in data:
                    for update in data["result"]:
                        update_id = update.get("update_id", 0)
                        offset = update_id + 1 # Nhấc offset để lần tới không đọc lại tin cũ
                        
                        message = update.get("message")
                        if not message:
                            continue
                            
                        chat_id = str(message.get("chat", {}).get("id", ""))
                        text = message.get("text", "")
                        
                        # Cửa khóa bảo mật: Chỉ trò chuyện với TuanHo
                        if chat_id != ALLOWED_CHAT_ID:
                            logger.warning(f"CẢNH BÁO BẢO MẬT: Phát hiện Chat ID lạ gõ cửa ({chat_id}). Đã block!!")
                            continue
                            
                        if text:
                            logger.info(f"Nhận tin nhắn từ sếp: {text}")
                            process_command(chat_id, text)
                            
        except httpx.ReadTimeout:
            # Chuyện bình thường của Long-polling, quay vòng tiếp
            pass
        except httpx.RequestError as e:
            logger.error(f"Lỗi Mạng cúp/hắt hơi: {e}")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Lỗi Daemon Chết Hụt: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
