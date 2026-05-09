"""
Red-Lightning Daemon - DNSE WebSocket Realtime Feed
Architecture: Playwright Browser Bridge (Chromium) + Redis Buffer

Lý do dùng Playwright thay raw websocket:
- DNSE datafeed-krx.dnse.com.vn yêu cầu browser fingerprint chính xác
- Raw python websockets bị từ chối HTTP 400 do thiếu Chromium-specific headers
- Playwright mimics browser hoàn hảo, nhận đủ 100% data stream

Data flow: DNSE WS -> Playwright -> Redis List -> Aggregator -> SQLite DB
"""
import os
import sys
import json
import logging
import asyncio
import struct
import redis.asyncio as redis
from dotenv import load_dotenv

# Thêm root dự án vào path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [Red-Lightning] %(message)s',
    handlers=[
        logging.FileHandler("red_lightning.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# ============================================================
# CONFIG
# ============================================================
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DNSE_BOARD_URL = "https://banggia.dnse.com.vn"

# Topics giá realtime cần thiết
REALTIME_TOPICS_KEYWORDS = [
    'quotes/krx/mdds/boardevent',   # Board events (khớp lệnh)
    'quotes/krx/mdds/stockinfo',    # Stock info (giá, vol, OI)
    'stats/index',                   # Index stats (VN30, VNINDEX)
]

redis_client = None


# ============================================================
# MQTT Binary Parser - parse protobuf-style DNSE frames
# ============================================================

def extract_topic_from_frame(payload: bytes) -> tuple[str, bytes]:
    """Trích xuất topic từ MQTT PUBLISH frame nhị phân của DNSE"""
    try:
        if len(payload) < 4:
            return "", payload

        # Byte 0: fixed header (0x30 = PUBLISH QoS 0)
        ptype = (payload[0] & 0xF0) >> 4
        if ptype != 3:  # Không phải PUBLISH
            return "", payload

        # Remaining length (variable byte encoding)
        idx = 1
        remaining_len = 0
        mult = 1
        while idx < len(payload):
            byte = payload[idx]
            idx += 1
            remaining_len += (byte & 0x7F) * mult
            mult *= 128
            if not (byte & 0x80):
                break

        # Topic length (2 bytes big-endian)
        if idx + 2 > len(payload):
            return "", payload

        topic_len = struct.unpack('>H', payload[idx:idx+2])[0]
        idx += 2

        if idx + topic_len > len(payload):
            return "", payload

        topic = payload[idx:idx+topic_len].decode('utf-8', errors='replace')
        idx += topic_len

        # Skip packet identifier nếu QoS > 0
        qos = (payload[0] & 0x06) >> 1
        if qos > 0:
            idx += 2

        # MQTT v5: skip properties
        if idx < len(payload):
            props_len = payload[idx]
            idx += 1 + int(props_len)

        data = payload[idx:] if idx < len(payload) else b''
        return topic, data

    except Exception:
        return "", payload


def extract_symbol_from_topic(topic: str) -> str:
    """Lấy mã CK từ topic path"""
    parts = topic.split('/')
    if parts:
        return parts[-1]
    return "UNKNOWN"


# ============================================================
# MAIN LOOP - Playwright Browser Bridge
# ============================================================

async def main():
    global redis_client

    # Kết nối Redis (optional - daemon chạy được dù không có Redis)
    try:
        redis_client = await redis.from_url(REDIS_URL, decode_responses=False)
        await redis_client.ping()
        logger.info("🟢 Kết nối REDIS thành công.")
    except Exception as e:
        logger.warning(f"Redis không khả dụng ({e}). Chạy ở chế độ LOG-ONLY.")
        redis_client = None

    retry_count = 0

    while True:
        try:
            from playwright.async_api import async_playwright

            tick_count = 0
            logger.info(f"🚀 Khởi động Playwright Browser (lần {retry_count + 1})...")

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage']
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
                page = await context.new_page()

                ws_connected = asyncio.Event()

                def on_ws(ws):
                    logger.info(f"⚡ WebSocket kết nối tới: {ws.url}")
                    ws_connected.set()

                    async def on_recv_async(payload: bytes):
                        nonlocal tick_count

                        if not isinstance(payload, bytes) or len(payload) < 5:
                            return

                        topic, data = extract_topic_from_frame(payload)

                        if not topic:
                            return

                        tick_count += 1

                        # Lọc chỉ topics quan trọng
                        is_relevant = any(kw in topic for kw in REALTIME_TOPICS_KEYWORDS)

                        if not is_relevant:
                            return

                        symbol = extract_symbol_from_topic(topic)

                        # Log mỗi 50 tick để không spam
                        if tick_count <= 10 or tick_count % 50 == 0:
                            logger.info(f"📈 TICK #{tick_count} | {topic[-60:]} | {len(data)}B data")

                        # Đẩy vào Redis
                        if redis_client:
                            try:
                                tick_record = {
                                    "topic": topic,
                                    "symbol": symbol,
                                    "data_hex": data.hex()[:200],
                                    "tick_n": tick_count,
                                }
                                await redis_client.rpush(
                                    f"tick_buffer:{symbol}",
                                    json.dumps(tick_record)
                                )
                            except Exception as e:
                                logger.debug(f"Redis push lỗi: {e}")

                    def on_recv(payload):
                        # Playwright gọi handler sync nhưng ta cần async
                        asyncio.ensure_future(on_recv_async(payload))

                    ws.on('framereceived', on_recv)

                page.on('websocket', on_ws)

                # Load trang board giá DNSE
                logger.info(f"📡 Đang tải {DNSE_BOARD_URL}...")
                try:
                    await page.goto(
                        DNSE_BOARD_URL,
                        timeout=30000,
                        wait_until='domcontentloaded'
                    )
                except Exception as e:
                    logger.warning(f"Page load warning (thường gặp): {e}")

                # Đợi WebSocket mở
                try:
                    await asyncio.wait_for(ws_connected.wait(), timeout=15)
                    logger.info("✅ WebSocket feed đang hoạt động! Đang hứng TICK...")
                except asyncio.TimeoutError:
                    logger.error("❌ Không tìm thấy WebSocket sau 15 giây. Restart...")
                    await browser.close()
                    retry_count += 1
                    await asyncio.sleep(10)
                    continue

                # Vòng lặp giữ kết nối sống - cứ 60 giây report
                while True:
                    await asyncio.sleep(60)
                    logger.info(f"💓 Heartbeat: {tick_count} ticks nhận tổng cộng. Page alive: {not page.is_closed()}")

                    if page.is_closed():
                        logger.warning("Page bị đóng. Restart...")
                        break

        except ImportError:
            logger.error("playwright chưa được cài đặt! Chạy: pip install playwright && playwright install chromium")
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            logger.info("Tiến trình bị hủy bởi người dùng.")
            break
        except Exception as e:
            logger.error(f"Lỗi nghiêm trọng: {type(e).__name__}: {e}")

        retry_count += 1
        wait = min(60, 10 * retry_count)
        logger.info(f"⏳ Khởi động lại sau {wait} giây...")
        await asyncio.sleep(wait)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Ngắt bởi người dùng (Ctrl+C).")
