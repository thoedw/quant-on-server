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

def _varint(data: bytes, idx: int) -> tuple[int, int]:
    """Đọc varint protobuf nhiều byte — dùng cho trường có field number > 15."""
    result = shift = 0
    while idx < len(data):
        b = data[idx]; idx += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, idx


def _parse_putthrough_snapshot(data: bytes) -> dict | None:
    """
    Parse payload từ topic stockinfo/v1/roundlotputthrough/symbol/{SYM}.

    Outer message = field 41 (tag 0xCA 0x02, length-delimited).
    Inner fields (KRX market data format):
      f2  (varint):  pt_count       — số lệnh thỏa thuận hôm nay
      f9  (double):  latest_pt_price — giá lệnh thỏa thuận mới nhất (nghìn VND)
      f16 (double):  avg_pt_price   — giá bình quân thỏa thuận lũy kế hôm nay (nghìn VND)
                                      (KRX cumulative average put-through price, không phải VWAP nm)
      f25 (double):  pt_val_tỷ      — tổng giá trị thỏa thuận hôm nay (tỷ VND)

    Công thức đã xác minh (0% error):
      pt_vol (CP) = int(pt_val_tỷ × 1_000_000 / avg_pt_price)

    Returns None nếu message không có đủ dữ liệu (symbol chưa có thỏa thuận hôm nay).
    """
    # Bước 1: tìm field 41 trong outer message
    inner = None
    idx = 0
    while idx < len(data):
        try:
            tag_v, idx = _varint(data, idx)
        except Exception:
            break
        fn = tag_v >> 3
        wt = tag_v & 7
        if wt == 0:
            _, idx = _varint(data, idx)
        elif wt == 1:
            idx += 8
        elif wt == 2:
            slen, idx = _varint(data, idx)
            if fn == 41:
                inner = data[idx:idx + slen]
            idx += slen
        elif wt == 5:
            idx += 4
        else:
            break

    if not inner:
        return None

    # Bước 2: đọc các field cần thiết từ inner message
    idx = 0
    pt_count = 0
    latest_pt_price = 0.0
    avg_pt_price = 0.0
    pt_val_tỷ = 0.0

    while idx < len(inner):
        try:
            tag_v, idx = _varint(inner, idx)
        except Exception:
            break
        fn = tag_v >> 3
        wt = tag_v & 7
        if wt == 0:
            val, idx = _varint(inner, idx)
            if fn == 2:
                pt_count = val
        elif wt == 1:
            if idx + 8 > len(inner):
                break
            val = struct.unpack_from('<d', inner, idx)[0]
            idx += 8
            if fn == 9:
                latest_pt_price = val
            elif fn == 16:
                avg_pt_price = val
            elif fn == 25:
                pt_val_tỷ = val
        elif wt == 2:
            slen, idx = _varint(inner, idx)
            idx += slen
        elif wt == 5:
            idx += 4
        else:
            break

    if avg_pt_price <= 0 or pt_val_tỷ <= 0:
        return None

    return {
        'pt_vol':           int(pt_val_tỷ * 1_000_000 / avg_pt_price),
        'avg_pt_price':     avg_pt_price,
        'pt_val_tỷ':        pt_val_tỷ,
        'pt_count':         pt_count,
        'latest_pt_price':  latest_pt_price,
    }


def _parse_foreign_tick(data: bytes) -> dict | None:
    """
    Parse payload từ topic trading_result_of_foreign_investor/market/{MKT}/board/{BOARD}/symbol/{ISIN}.

    Outer message: field 27 (tag 2-byte varint 0xDA 0x01, length-delimited) chứa inner.
    Inner fields (tích lũy từ đầu phiên):
      f7  (varint): foreign_buy_vol   — KL mua nước ngoài (CP)
      f8  (double): foreign_buy_val   — GT mua nước ngoài (VND)
      f9  (varint): foreign_sell_vol  — KL bán nước ngoài (CP)
      f10 (double): foreign_sell_val  — GT bán nước ngoài (VND)

    Returns None nếu cả buy_vol lẫn sell_vol đều bằng 0.
    """
    inner = None
    idx = 0
    while idx < len(data):
        try:
            tag_v, idx = _varint(data, idx)
        except Exception:
            break
        fn = tag_v >> 3
        wt = tag_v & 7
        if wt == 0:
            _, idx = _varint(data, idx)
        elif wt == 1:
            idx += 8
        elif wt == 2:
            slen, idx = _varint(data, idx)
            if fn == 27:
                inner = data[idx:idx + slen]
            idx += slen
        elif wt == 5:
            idx += 4
        else:
            break

    if not inner:
        return None

    idx = 0
    buy_vol = 0
    buy_val = 0.0
    sell_vol = 0
    sell_val = 0.0

    while idx < len(inner):
        try:
            tag_v, idx = _varint(inner, idx)
        except Exception:
            break
        fn = tag_v >> 3
        wt = tag_v & 7
        if wt == 0:
            val, idx = _varint(inner, idx)
            if fn == 7:
                buy_vol = val
            elif fn == 9:
                sell_vol = val
        elif wt == 1:
            if idx + 8 > len(inner):
                break
            val = struct.unpack_from('<d', inner, idx)[0]
            idx += 8
            if fn == 8:
                buy_val = val
            elif fn == 10:
                sell_val = val
        elif wt == 2:
            slen, idx = _varint(inner, idx)
            idx += slen
        elif wt == 5:
            idx += 4
        else:
            break

    if buy_vol == 0 and sell_vol == 0:
        return None

    return {
        'buy_vol':  buy_vol,
        'buy_val':  buy_val,
        'sell_vol': sell_vol,
        'sell_val': sell_val,
        'net_vol':  buy_vol - sell_vol,
    }


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

    # KRX market codes cho topic giao dịch nước ngoài
    FOREIGN_MARKETS = ['STO', 'STX']   # HOSE, HNX
    FOREIGN_BOARDS  = ['G1', 'G4']     # Khớp lệnh liên tục, thỏa thuận

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
            topics.append(f"quotes/krx/mdds/stockinfo/v1/roundlotputthrough/symbol/{sym}")
        for market, product in self.BOARD_MARKETS:
            topics.append(f"quotes/krx/mdds/boardevent/v1/roundlot/market/{market}/product/{product}")
        topics += [
            "quotes/krx/mdds/index/VN30",
            "quotes/krx/mdds/index/VNINDEX"
        ]
        # Giao dịch nước ngoài — wildcard per market/board (không cần enumerate ISIN)
        for mkt in self.FOREIGN_MARKETS:
            for board in self.FOREIGN_BOARDS:
                topics.append(
                    f"quotes/krx/mdds/trading_result_of_foreign_investor"
                    f"/market/{mkt}/board/{board}/#"
                )
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

        # roundlotputthrough phải kiểm tra trước vì topic cũng chứa 'stockinfo'
        if 'roundlotputthrough' in topic:
            if self.on_putthrough:
                symbol = topic.split('/')[-1]
                data = _parse_putthrough_snapshot(payload)
                if data:
                    self.on_putthrough(symbol, data)

        elif 'trading_result_of_foreign_investor' in topic:
            if self.on_foreign_tick:
                parts = topic.split('/')
                # topic: .../market/{MKT}/board/{BOARD}/symbol/{ISIN}
                try:
                    board = parts[parts.index('board') + 1]
                    isin  = parts[-1]
                    # VN equity ISIN: VN000000{SYMBOL}{CHECK} — 12 chars
                    symbol = isin[8:-1] if (len(isin) == 12 and isin.startswith('VN000000')) else None
                except (ValueError, IndexError):
                    symbol = None
                if symbol:
                    data = _parse_foreign_tick(payload)
                    if data:
                        self.on_foreign_tick(symbol, board, data)

        elif 'stockinfo' in topic:
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
