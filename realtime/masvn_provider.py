import asyncio
import logging
import msgpack
import websockets
from datetime import datetime
from typing import List

from realtime.feed_provider import FeedProvider

logger = logging.getLogger(__name__)

# ============================================================
# MASVN Field Mapping (xác nhận bằng Recon Script 2026-04-17)
# Cấu trúc gói tin thực tế: {'p': ['market.quote.SYM', { data }]}
# s  = symbol
# c  = giá khớp cuối (đơn vị ĐỒNG, chia 1000 → nghìn đồng)
# mv = match volume (khối lượng lệnh vừa khớp)
# mb = match side ("BUY" / "SELL") — có sẵn, không cần classify lại
# ti = timestamp unix milliseconds
# ============================================================

class MASVNProvider(FeedProvider):
    name = "MASVN"
    priority = 2

    def __init__(self):
        super().__init__()
        self.uri = "wss://mastrade.masvn.com/ws/"
        self.ws = None
        self._listen_task = None
        self._symbols = []

    async def connect(self) -> bool:
        try:
            self.ws = await websockets.connect(
                self.uri,
                ping_interval=None,  # MASVN dùng Application-level Ping (chuỗi rỗng "")
                max_size=None,
                close_timeout=10,
            )

            # SocketCluster Handshake
            handshake = {"e": ["#handshake", {"authToken": None}, 1]}
            await self.ws.send(msgpack.packb(handshake))
            res = await asyncio.wait_for(self.ws.recv(), timeout=5)

            if isinstance(res, bytes):
                res_data = msgpack.unpackb(res, strict_map_key=False)
                if isinstance(res_data, dict) and 'r' in res_data:
                    logger.info("✅ MASVN Handshake thành công!")
                    self.is_connected = True
                    self._listen_task = asyncio.create_task(self._listen_loop())
                    return True

            logger.error(f"❌ MASVN Handshake lỗi: {res}")
            return False
        except Exception as e:
            logger.error(f"❌ MASVN Provider kết nối thất bại: {e}")
            self.is_connected = False
            return False

    async def subscribe(self, symbols: List[str]):
        if not self.is_connected:
            return

        self._symbols = symbols
        chunk_size = 50
        chunks = [symbols[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]

        cid = 2
        for chunk in chunks:
            for sym in chunk:
                msg = {"e": ["#subscribe", {"channel": f"market.quote.{sym}"}, cid]}
                cid += 1
                await self.ws.send(msgpack.packb(msg))
            await asyncio.sleep(0.1)

        logger.info(f"✅ MASVN đã subscribe {len(symbols)} mã CK.")

    async def _listen_loop(self):
        """
        Lắng nghe luồng dữ liệu từ MASVN.

        Giao thức SocketCluster của MASVN (xác nhận qua Recon 2026-04-17):
        - Server Ping: gửi chuỗi "" (rỗng)
        - Client Pong: phải trả về "" (rỗng) ngay lập tức
        - Disconnect code 4001 = Ping Timeout (không pong kịp)
        - Dữ liệu giá: {'p': ['market.quote.SYM', { field_dict }]}
        """
        try:
            async for msg in self.ws:

                # --- Ping/Pong: Server gửi "" → phải Pong "" ngay ---
                if isinstance(msg, str):
                    await self.ws.send("")
                    continue

                if isinstance(msg, bytes):
                    try:
                        data = msgpack.unpackb(msg, strict_map_key=False)
                    except Exception:
                        continue

                    if not isinstance(data, dict):
                        continue

                    # --- ACK từ #subscribe / #handshake (key 'r') → bỏ qua ---
                    if 'r' in data:
                        continue

                    # --- Gói tin giá thực tế (key 'p') ---
                    if 'p' in data and isinstance(data['p'], list) and len(data['p']) >= 2:
                        channel = data['p'][0]  # "market.quote.VNM"
                        payload = data['p'][1]  # { field dict }
                        if isinstance(channel, str) and channel.startswith('market.quote.'):
                            self._process_quote(payload)

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"⚠️ MASVN mất kết nối. Code={e.code} Reason={e.reason}")
            self.is_connected = False
        except Exception as e:
            logger.error(f"MASVN Listen Loop lỗi: {e}")

    def _process_quote(self, data: dict):
        """
        Parse gói tin giá từ MASVN theo field mapping đã được xác nhận:
          s  → symbol
          c  → giá khớp cuối (đơn vị ĐỒNG → chia 1000 = nghìn đồng)
          mv → match volume (số CP vừa khớp trong lệnh này)
          mb → "BUY" / "SELL" (có sẵn, dùng trực tiếp)
          ti → unix timestamp milliseconds
        """
        if not data or not self.on_tick:
            return
        try:
            symbol = data.get('s')
            if not symbol:
                return

            price_raw = data.get('c', 0)
            vol       = data.get('mv', 0)
            side      = data.get('mb', 'NEUTRAL')  # "BUY" / "SELL"

            if not price_raw or not vol:
                return

            # Giá MASVN đơn vị ĐỒNG → quy về nghìn đồng
            price = round(price_raw / 1000.0, 2)

            # Timestamp (unix ms → datetime)
            ti = data.get('ti')
            if ti:
                ts = datetime.fromtimestamp(ti / 1000.0)
            else:
                ts = datetime.now()

            self.on_tick(symbol, price, int(vol), ts, self.name, side)

        except Exception as e:
            logger.debug(f"MASVN _process_quote lỗi: {e} | data={data}")

    async def disconnect(self):
        self.is_connected = False
        if self._listen_task:
            self._listen_task.cancel()
        if self.ws:
            await self.ws.close()
