"""
realtime/masvn_worker.py
────────────────────────
MASVNWorker  — 1 WebSocket kết nối SocketCluster, tự hồi sinh khi đứt kết nối.
MASVNManager — Điều phối 2 workers Active-Active cho Tier 1 (thanh khoản cao).

Thiết kế:
  • 2 workers cùng subscribe TOÀN BỘ Tier-1 symbols → dự phòng 100%
  • Auto-reconnect với exponential backoff (3 → 6 → 12 → ... → 60s)
  • TickRouter.dedup (fingerprint window_2s) loại trùng hoàn toàn tự động
  • Gọi on_tick giống hệt MASVNProvider cũ → KHÔNG đổi downstream code
"""

import asyncio
import logging
import msgpack
import websockets
from datetime import datetime
from typing import List, Callable, Optional

logger = logging.getLogger(__name__)

MASVN_URI     = "wss://mastrade.masvn.com/ws/"
HANDSHAKE_MSG = {"e": ["#handshake", {"authToken": None}, 1]}
RECONNECT_BASE_SEC  = 3
RECONNECT_CAP_SEC   = 60
HANDSHAKE_TIMEOUT   = 8    # giây chờ handshake response

# Frozen detection: nếu không nhận tick trong X giây → force reconnect
FROZEN_TIMEOUT_SEC  = 120  # 2 phút không tick = frozen
MARKET_OPEN_HOUR    = 9    # 09:00 VN
MARKET_CLOSE_HOUR   = 15   # 15:00 VN (15:30 thực tế nhưng buffer 30p)


# ═══════════════════════════════════════════════════════════════
# MASVN WORKER — 1 WebSocket / 1 tập symbols
# ═══════════════════════════════════════════════════════════════

class MASVNWorker:
    """
    Một kết nối WebSocket SocketCluster tự hồi sinh.

    Vòng đời:
        start() → kết nối + subscribe → listen loop
                → nếu dropped → sleep(backoff) → kết nối lại → subscribe lại
                → lặp lại cho đến khi stop() được gọi.
    """

    def __init__(
        self,
        worker_id: str,
        symbols: List[str],
        on_tick: Callable,
    ):
        self.worker_id  = worker_id
        self.symbols    = symbols
        self.on_tick    = on_tick

        self._running   = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None

        # Thống kê nội bộ
        self.ticks_sent      = 0
        self.reconnect_count = 0
        self.is_connected    = False

        # Frozen detection
        self._last_tick_time: float = 0.0   # unix timestamp của tick cuối
        self._frozen_reconnects: int = 0

    # ── Public API ──────────────────────────────────────────────

    async def start(self):
        """Khởi động vòng lặp kết nối trong background task."""
        self._running = True
        self._last_tick_time = 0.0
        self._task = asyncio.create_task(
            self._reconnect_loop(),
            name=f"masvn-worker-{self.worker_id}"
        )
        self._watchdog_task = asyncio.create_task(
            self._frozen_watchdog(),
            name=f"masvn-watchdog-{self.worker_id}"
        )
        logger.info(f"🟡 MASVNWorker [{self.worker_id}] khởi động ({len(self.symbols)} mã).")

    async def stop(self):
        """Dừng worker sạch sẽ."""
        self._running = False
        for task in (self._task, self._watchdog_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await self._close_ws()
        logger.info(f"🔴 MASVNWorker [{self.worker_id}] đã dừng.")

    # ── Reconnect Loop ──────────────────────────────────────────

    async def _frozen_watchdog(self):
        """
        Watchdog: phát hiện MASVN frozen (connected nhưng không có tick).

        Khi MASVN WebSocket vẫn alive nhưng server ngừng push data
        (hiện tượng xảy ra sau DNSE mass-disconnect làm MASVN stale):
          - is_connected = True (không có exception)
          - ticks_sent không tăng
          - _last_tick_time không cập nhật

        → Force close WebSocket để trigger reconnect loop.
        """
        import time
        check_interval = 30  # check mỗi 30s
        while self._running:
            await asyncio.sleep(check_interval)
            now = time.time()
            now_dt = datetime.now()
            # Chỉ check trong giờ giao dịch
            if not (MARKET_OPEN_HOUR <= now_dt.hour < MARKET_CLOSE_HOUR):
                self._last_tick_time = 0.0  # reset ngoài giờ
                continue
            if not self.is_connected:
                continue  # đang reconnect rồi, không cần làm gì
            if self._last_tick_time == 0.0:
                # Chưa có tick nào → khởi tạo baseline
                self._last_tick_time = now
                continue
            elapsed = now - self._last_tick_time
            if elapsed >= FROZEN_TIMEOUT_SEC:
                self._frozen_reconnects += 1
                logger.warning(
                    f"🧊 Worker [{self.worker_id}] FROZEN: "
                    f"{elapsed:.0f}s không có tick! "
                    f"Force-close WS để trigger reconnect "
                    f"(lần #{self._frozen_reconnects})"
                )
                await self._close_ws()   # _reconnect_loop sẽ tự reconnect
                self._last_tick_time = 0.0

    async def _reconnect_loop(self):
        backoff = RECONNECT_BASE_SEC
        while self._running:
            try:
                await self._connect_and_listen()
                # Kết nối đóng graceful (server đóng) → reset backoff
                backoff = RECONNECT_BASE_SEC
                logger.warning(f"⚠️  Worker [{self.worker_id}] bị ngắt. Reconnect sau {backoff}s...")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"⚠️  Worker [{self.worker_id}] lỗi: {type(e).__name__}: {e}. Reconnect sau {backoff}s...")

            if not self._running:
                break

            self.is_connected = False
            self.reconnect_count += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_CAP_SEC)

    async def _connect_and_listen(self):
        """Kết nối, handshake, subscribe và lắng nghe đến khi mất kết nối."""
        self.is_connected = False
        backoff_reset = False

        async with websockets.connect(
            MASVN_URI,
            ping_interval=None,   # MASVN dùng application-level ping (chuỗi rỗng "")
            max_size=None,
            close_timeout=10,
            open_timeout=15,
        ) as ws:
            self._ws = ws

            # ── Handshake SocketCluster ──────────────────────
            await ws.send(msgpack.packb(HANDSHAKE_MSG))
            raw = await asyncio.wait_for(ws.recv(), timeout=HANDSHAKE_TIMEOUT)

            if isinstance(raw, bytes):
                res = msgpack.unpackb(raw, strict_map_key=False)
                if not (isinstance(res, dict) and 'r' in res):
                    raise ConnectionError(f"Handshake thất bại: {res}")
            else:
                raise ConnectionError("Handshake: nhận được string thay vì bytes")

            logger.info(f"✅ Worker [{self.worker_id}] kết nối MASVN thành công!")

            # ── Subscribe ───────────────────────────────────
            await self._subscribe(ws)
            self.is_connected = True

            # ── Listen Loop ─────────────────────────────────
            async for msg in ws:
                if not self._running:
                    break

                if isinstance(msg, str):
                    # Application-level Ping từ server → Pong ngay
                    await ws.send("")
                    continue

                if isinstance(msg, bytes):
                    try:
                        data = msgpack.unpackb(msg, strict_map_key=False)
                    except Exception:
                        continue

                    if not isinstance(data, dict):
                        continue
                    if 'r' in data:
                        continue   # ACK từ subscribe/handshake

                    # Gói tin giá thực: {'p': ['market.quote.SYM', {...}]}
                    if 'p' in data and isinstance(data['p'], list) and len(data['p']) >= 2:
                        channel = data['p'][0]
                        payload = data['p'][1]
                        if isinstance(channel, str) and channel.startswith('market.quote.'):
                            self._process_quote(payload)

        self._ws = None
        self.is_connected = False

    async def _subscribe(self, ws):
        """Subscribe từng mã với delay nhỏ theo chunk để tránh flood."""
        cid = 2
        chunk_size = 50
        for i in range(0, len(self.symbols), chunk_size):
            chunk = self.symbols[i:i + chunk_size]
            for sym in chunk:
                msg = {"e": ["#subscribe", {"channel": f"market.quote.{sym}"}, cid]}
                cid += 1
                await ws.send(msgpack.packb(msg))
            await asyncio.sleep(0.05)   # 50ms giữa các chunk, tránh rate-limit

        logger.info(f"📡 Worker [{self.worker_id}] đã subscribe {len(self.symbols)} mã.")

    async def _close_ws(self):
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ── Parse Quote ─────────────────────────────────────────────

    def _process_quote(self, data: dict):
        """
        Parse gói giá MASVN:
          s  → symbol
          c  → giá (đơn vị ĐỒNG → chia 1000 = nghìn đồng)
          mv → match volume (CP vừa khớp)
          mb → 'BUY' | 'SELL' (side thật, ~95%)
          ti → unix timestamp ms
        """
        if not data or not self.on_tick:
            return
        try:
            symbol    = data.get('s')
            price_raw = data.get('c', 0)
            vol       = data.get('mv', 0)
            side      = data.get('mb', 'NEUTRAL')

            if not symbol or not price_raw or not vol:
                return

            price = round(price_raw / 1000.0, 2)

            ti = data.get('ti')
            ts = datetime.fromtimestamp(ti / 1000.0) if ti else datetime.now()

            self.on_tick(symbol, price, int(vol), ts, f"MASVN-W{self.worker_id}", side)
            self.ticks_sent += 1
            # Cập nhật timestamp tick cuối để watchdog detect frozen
            import time
            self._last_tick_time = time.time()

        except Exception as e:
            logger.debug(f"Worker [{self.worker_id}] parse lỗi: {e} | data={data}")

    def get_stats(self) -> dict:
        import time
        now = time.time()
        elapsed = int(now - self._last_tick_time) if self._last_tick_time > 0 else -1
        return {
            'worker_id'        : self.worker_id,
            'is_connected'     : self.is_connected,
            'ticks_sent'       : self.ticks_sent,
            'reconnect_count'  : self.reconnect_count,
            'frozen_reconnects': self._frozen_reconnects,
            'secs_since_tick'  : elapsed,
            'symbols'          : len(self.symbols),
        }


# ═══════════════════════════════════════════════════════════════
# MASVN MANAGER — Điều phối 2 workers Active-Active cho Tier 1
# ═══════════════════════════════════════════════════════════════

class MASVNManager:
    """
    Quản lý 2 MASVNWorker chạy song song (Active-Active).

    Cả 2 worker cùng subscribe TOÀN BỘ tier1_symbols.
    Khi 1 worker rớt, worker còn lại đảm bảo KHÔNG mất side data.
    TickRouter.dedup (fingerprint window_2s) xử lý trùng lặp hoàn toàn tự động.

    Cách dùng:
        mgr = MASVNManager(tier1_symbols, on_tick=router.route_tick)
        await mgr.start()
        # ... phiên giao dịch ...
        await mgr.stop()
    """

    NUM_WORKERS = 2

    def __init__(self, tier1_symbols: List[str], on_tick: Callable):
        self.tier1_symbols = tier1_symbols
        self.on_tick       = on_tick
        self.workers: List[MASVNWorker] = []

        # Tạo 2 workers, cùng subscribe toàn bộ tier1_symbols
        for i in range(self.NUM_WORKERS):
            self.workers.append(
                MASVNWorker(
                    worker_id=f"T1-{i+1}",
                    symbols=tier1_symbols,
                    on_tick=on_tick,
                )
            )

    async def start(self):
        logger.info(
            f"🚀 MASVNManager: khởi động {self.NUM_WORKERS} workers Active-Active "
            f"cho {len(self.tier1_symbols)} Tier-1 symbols."
        )
        await asyncio.gather(*[w.start() for w in self.workers])

    async def stop(self):
        logger.info("🛑 MASVNManager: đang dừng tất cả workers...")
        await asyncio.gather(*[w.stop() for w in self.workers])

    def get_all_stats(self) -> List[dict]:
        return [w.get_stats() for w in self.workers]

    @property
    def any_connected(self) -> bool:
        """True nếu ít nhất 1 worker đang kết nối."""
        return any(w.is_connected for w in self.workers)

    @property
    def all_connected(self) -> bool:
        """True nếu toàn bộ 2 workers đang kết nối (trạng thái khoẻ mạnh)."""
        return all(w.is_connected for w in self.workers)
