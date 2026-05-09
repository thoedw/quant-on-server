import struct
import asyncio
import logging
import paho.mqtt.client as mqtt

from typing import List, Callable
from realtime.feed_provider import FeedProvider

logger = logging.getLogger(__name__)

# CONFIG
DNSE_BOARD_URL = "https://banggia.dnse.com.vn"

# ============================================================
# PROTOBUF HELPERS
# ============================================================
def parse_varint(raw: bytes, idx: int):
    val = 0; shift = 0
    while idx < len(raw):
        b = raw[idx]; idx += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return val, idx

def parse_proto_fields(raw: bytes) -> dict:
    idx = 0
    fields = {}
    while idx < len(raw):
        try:
            tag = raw[idx]; idx += 1
            fn  = tag >> 3
            wt  = tag & 0x7

            if wt == 0:   # varint
                v, idx = parse_varint(raw, idx)
                fields[fn] = v
            elif wt == 1:  # 64-bit double
                if idx + 8 <= len(raw):
                    fields[fn] = struct.unpack('<d', raw[idx:idx+8])[0]
                    idx += 8
            elif wt == 2:  # length-delimited
                slen, idx = parse_varint(raw, idx)
                payload = raw[idx:idx+slen]
                idx += slen
                try:
                    fields[fn] = payload.decode('utf-8')
                except Exception:
                    fields[fn] = payload  
            elif wt == 5:  # 32-bit float
                if idx + 4 <= len(raw):
                    fields[fn] = struct.unpack('<f', raw[idx:idx+4])[0]
                    idx += 4
            else:
                break
        except Exception:
            break
    return fields

def extract_stockinfo_tick(data: bytes) -> dict | None:
    fields = parse_proto_fields(data)
    price = fields.get(12, fields.get(13, 0.0))
    # Strict validation: price must be realistic (0.1 to 2000, in nghìn đồng)
    if not isinstance(price, float) or not (0.1 <= price <= 2000.0):
        return None
        
    cum_vol = fields.get(17, 0)
    # Strict validation: volume must not be negative or astronomically large
    if not isinstance(cum_vol, int) or not (0 <= cum_vol <= 2_000_000_000):
        cum_vol = 0
        
    return {
        'price':   round(price, 2),
        'cum_vol': cum_vol,
    }

def extract_boardevent_ticks(data: bytes) -> list[dict]:
    ticks = []
    idx = 0
    while idx < len(data):
        try:
            tag = data[idx]; idx += 1
            fn = tag >> 3; wt = tag & 0x7
            if wt == 0:
                _, idx = parse_varint(data, idx)
            elif wt == 1:
                idx += 8
            elif wt == 2:
                slen, idx = parse_varint(data, idx)
                nested = data[idx:idx+slen]; idx += slen
                nf = parse_proto_fields(nested)
                sym = None
                price = 0.0
                volume = 0
                for k, v in nf.items():
                    if isinstance(v, str) and 2 <= len(v) <= 10 and v.isupper():
                        sym = v
                    elif isinstance(v, float) and 0.1 <= v <= 2000.0:
                        price = round(v, 2)
                    elif isinstance(v, int) and 0 < v < 100_000_000:
                        volume = v
                if sym and price > 0:
                    ticks.append({'symbol': sym, 'price': price, 'volume': volume})
            elif wt == 5:
                idx += 4
            else:
                break
        except Exception:
            break
    return ticks

async def get_dnse_credentials():
    from playwright.async_api import async_playwright
    logger.info("⏳ Đang mượn danh Playwright để lấy Cookies từ DNSE...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.goto(DNSE_BOARD_URL, timeout=30000, wait_until='domcontentloaded')
        
        cookies = await context.cookies()
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        headers = {
            "Host": "datafeed-krx.dnse.com.vn",
            "Origin": "https://banggia.dnse.com.vn",
            "User-Agent": await page.evaluate("navigator.userAgent"),
            "Cookie": cookie_str
        }
        await browser.close()
        return headers

# ============================================================
# DNSE PROVIDER
# ============================================================

class DNSEProvider(FeedProvider):
    name = "DNSE"
    priority = 1
    
    BOARD_MARKETS = [
        ('HSX', 'EQ'),
        ('HNX', 'EQ'),
        ('UPX', 'UPX'),
        ('DVX', 'FIO'),
    ]

    def __init__(self, vol_tracker):
        super().__init__()
        self.vol_tracker = vol_tracker
        self.headers = None
        self.clients = []
        self.loop = None
        
    async def connect(self) -> bool:
        try:
            self.headers = await get_dnse_credentials()
            self.loop = asyncio.get_running_loop()
            self.is_connected = True
            return True
        except Exception as e:
            logger.error(f"❌ DNSE Provider kết nối thất bại: {e}")
            self.is_connected = False
            return False

    def _make_topics_payload(self, symbols: List[str]) -> List[str]:
        topics = []
        for sym in symbols:
            topics.append(f"quotes/krx/mdds/stockinfo/v1/roundlot/symbol/{sym}")
            topics.append(f"quotes/krx/mdds/topprice/v1/roundlot/symbol/{sym}")
        for market, product in self.BOARD_MARKETS:
            topics.append(f"quotes/krx/mdds/boardevent/v1/roundlot/market/{market}/product/{product}")
        topics += [
            "quotes/krx/mdds/index/VN30",
            "quotes/krx/mdds/index/VNINDEX"
        ]
        return topics

    def _launch_worker(self, worker_id: int, symbols: List[str], headers: dict):
        client = mqtt.Client(transport="websockets")
        client.ws_set_options(path="/wss", headers=headers)
        client.tls_set()
        
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.max_inflight_messages_set(10000)
        client.max_queued_messages_set(50000)

        def on_connect(c, userdata, flags, rc):
            if rc == 0:
                logger.info(f"🚀 DNSE W{worker_id} kết nối thành công!")
                topics = self._make_topics_payload(symbols)
                for t in topics:
                    c.subscribe(t)
            else:
                logger.error(f"❌ DNSE W{worker_id} kết nối thất bại. RC = {rc}")

        def on_message(c, userdata, msg):
            if self.on_tick:
                # Tín hiệu được ném vào loop chính
                asyncio.run_coroutine_threadsafe(
                    self._process_msg(msg.topic, msg.payload), self.loop
                )
            
        def on_disconnect(c, userdata, rc):
            logger.warning(f"⚠️ DNSE W{worker_id} bị ngắt kết nối. RC = {rc}. Tự động reconnect...")

        client.on_connect = on_connect
        client.on_message = on_message
        client.on_disconnect = on_disconnect

        logger.info(f"📡 DNSE W{worker_id} Đang quay socket...")
        client.connect("datafeed-krx.dnse.com.vn", 443, 60)
        client.loop_start()
        return client

    async def subscribe(self, symbols: List[str]):
        if not self.is_connected or not self.headers:
            logger.error("Chưa kết nối DNSE!")
            return

        CHUNK_SIZE = 50
        chunks = [symbols[i:i + CHUNK_SIZE] for i in range(0, len(symbols), CHUNK_SIZE)]
        total_workers = len(chunks)
        
        logger.info(f"🚀 Khởi tạo {total_workers} DNSE Workers...")

        for i, chunk in enumerate(chunks):
            worker_id = i + 1
            self.clients.append(self._launch_worker(worker_id, chunk, self.headers))
            await asyncio.sleep(0.5)

    async def _process_msg(self, topic: str, payload: bytes):
        from datetime import datetime
        ts = datetime.now()

        if 'stockinfo' in topic:
            symbol = topic.split('/')[-1]
            tick = extract_stockinfo_tick(payload)
            if tick and tick['price'] > 0:
                actual_vol = self.vol_tracker.delta(symbol, tick['cum_vol'])
                if self.on_tick:
                    self.on_tick(symbol, tick['price'], actual_vol, ts, self.name)
        
        elif 'boardevent' in topic:
            ticks = extract_boardevent_ticks(payload)
            for tick in ticks:
                if self.on_tick:
                    self.on_tick(tick['symbol'], tick['price'], tick['volume'], ts, self.name)

    async def disconnect(self):
        for c in self.clients:
            c.loop_stop()
            c.disconnect()
        self.clients = []
        self.is_connected = False
