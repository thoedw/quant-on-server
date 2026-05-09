import time
import asyncio
import logging
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)


class TickRouter:
    """
    Bộ định tuyến tick v2 — Source-Aware, MASVN Priority.

    Nguyên tắc hoạt động:
    1. Fingerprint = symbol + window_2s + volume + price_bin
       → Tránh false dedup (2 lệnh cùng volume khác giá trong 1s)
       → Tránh miss dedup tại biên giây (DNSE@0.95s vs MASVN@1.02s)

    2. MASVN trumps DNSE về SIDE:
       - DNSE đến trước  → Accept tick, side = Tick Rule (tạm thời)
       - MASVN đến sau   → Không bỏ qua, gọi update_side() để vá lại delta
       - MASVN đến trước → Accept tick, side = mb thật (~95% chính xác)
       - DNSE đến sau    → Dedup bỏ qua hoàn toàn

    3. Metrics rõ ràng để giám sát chất lượng delta mỗi phiên.
    """

    def __init__(self, accumulator, classifier, redis_tracker):
        self.accumulator   = accumulator
        self.classifier    = classifier
        self.redis_tracker = redis_tracker

        # Dedup cache: fingerprint → {'ts', 'source', 'side', 'volume'}
        self._dedup_cache: dict[str, dict] = {}
        self._dedup_ttl = 4.0   # 4s — đủ rộng cho cross-second boundary

        self._stats = defaultdict(int)
        self.tick_count = 0
        self.buy_count  = 0
        self.sell_count = 0

    @staticmethod
    def _is_masvn(source: str) -> bool:
        """True cho cả 'MASVN' lẫn 'MASVN-W1', 'MASVN-W2', ..."""
        return source == 'MASVN' or source.startswith('MASVN-W')

    # ──────────────────────────────────────────────────────────────
    # FINGERPRINT v2
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _make_fingerprint(symbol: str, price: float, volume: int, ts: datetime) -> str:
        """
        Fingerprint v2: symbol + window_2s + volume + price_bin

        - window_2s   : làm tròn về bội số 2 giây → bắc cầu DNSE/MASVN chênh ≤ 2s
        - price_bin   : int(price * 10) → 0.1k precision để phân biệt lệnh khác giá
        - volume      : phân biệt các lệnh khác khối lượng trong cùng window

        Ví dụ: "VNM:1713337200:800:271"
          → VNM, window bắt đầu lúc 10:00:00, vol=800, price≈27.1k
        """
        window   = int(ts.timestamp() / 2) * 2    # bội số 2 giây
        price_bin = int(round(price * 10))          # làm tròn 0.1 nghìn đồng
        return f"{symbol}:{window}:{volume}:{price_bin}"

    # ──────────────────────────────────────────────────────────────
    # MAIN ROUTE CALLBACK
    # ──────────────────────────────────────────────────────────────

    def route_tick(self, symbol: str, price: float, volume: int,
                   ts: datetime, source: str, side: str = None):
        """
        Callback unified cho tất cả providers.

        Parameters
        ----------
        side : str | None
            'BUY' | 'SELL' | 'NEUTRAL' | None
            - MASVN truyền side thật (mb field, ~95% chính xác)
            - DNSE truyền None → sẽ dùng Tick Rule
        """
        if price <= 0 or volume <= 0:
            return

        fingerprint = self._make_fingerprint(symbol, price, volume, ts)
        now = time.time()

        # ── CASE 1: Fingerprint đã tồn tại ───────────────────────
        if fingerprint in self._dedup_cache:
            cached = self._dedup_cache[fingerprint]

            # MASVN (bất kỳ worker nào) đến sau DNSE → vá lại side
            if self._is_masvn(source) and side in ('BUY', 'SELL'):
                old_side = cached.get('side', 'NEUTRAL')
                # Nếu cached cũng từ MASVN → tick trùng từ worker song song, bỏ qua
                if self._is_masvn(cached.get('source', '')):
                    self._stats['masvn_worker_dedup'] += 1
                elif old_side != side:
                    self._stats['masvn_side_override'] += 1
                    try:
                        loop = asyncio.get_running_loop()
                        asyncio.run_coroutine_threadsafe(
                            self.accumulator.update_side(
                                symbol, ts, volume, old_side, side
                            ),
                            loop
                        )
                    except RuntimeError:
                        pass
                else:
                    self._stats['masvn_side_confirm'] += 1
            else:
                self._stats['dedup_hits'] += 1
            return

        # ── CASE 2: Tick mới — chấp nhận ─────────────────────
        # Xác định side cuối cùng
        native = side if side in ('BUY', 'SELL') else None
        final_side, confidence = self.classifier.classify(symbol, price, native_side=native)

        # Track stats theo confidence
        if confidence == 'NATIVE':
            self._stats['side_native'] += 1
        elif confidence == 'LEE_READY':
            self._stats['side_lee_ready'] += 1
        else:
            self._stats['side_neutral'] += 1
        self._stats[f'{source}_accepted'] += 1
        self.tick_count += 1

        # Ghi vào dedup cache (kèm side để MASVN có thể override sau nếu cần)
        self._dedup_cache[fingerprint] = {
            'ts'        : now,
            'source'    : source,
            'side'      : final_side,
            'confidence': confidence,
            'volume'    : volume,
        }

        if final_side == 'BUY':
            self.buy_count  += 1
        elif final_side == 'SELL':
            self.sell_count += 1

        # Đẩy vào Accumulator
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(
                self.accumulator.ingest(symbol, price, volume, final_side, ts),
                loop
            )
        except RuntimeError:
            pass

        # Track Redis
        self.redis_tracker.track(symbol)

        # Log mẫu
        if self.tick_count <= 10 or self.tick_count % 500 == 0:
            icon = '⬆️' if final_side == 'BUY' else ('⬇️' if final_side == 'SELL' else '➡️')
            src_tag = '★' if self._is_masvn(source) and side in ('BUY', 'SELL') else ' '
            logger.info(
                f"[{source}{src_tag}] 📈 TICK #{self.tick_count} | "
                f"{symbol} | {price:.2f}k | vol={volume} | {icon}{final_side}"
            )

    # ──────────────────────────────────────────────────────────────
    # HOUSEKEEPING
    # ──────────────────────────────────────────────────────────────

    async def dedup_cleanup_loop(self):
        """Xóa cache hết hạn mỗi 30s để tiết kiệm RAM."""
        while True:
            await asyncio.sleep(30)
            now = time.time()
            to_delete = [
                k for k, v in self._dedup_cache.items()
                if now - v['ts'] > self._dedup_ttl
            ]
            for k in to_delete:
                del self._dedup_cache[k]

    def get_stats(self) -> dict:
        total = self.buy_count + self.sell_count
        buy_rate = (self.buy_count / total * 100) if total > 0 else 0

        # Classifier stats
        clf_stats = self.classifier.get_stats()

        return {
            'buy_rate'            : round(buy_rate, 1),
            'total_ticks'         : self.tick_count,
            'dnse_accepted'       : self._stats.get('DNSE_accepted', 0),
            'masvn_accepted'      : sum(v for k, v in self._stats.items() if k.endswith('_accepted') and 'MASVN' in k),
            # Side classification breakdown
            'side_native'         : self._stats.get('side_native', 0),
            'side_lee_ready'      : self._stats.get('side_lee_ready', 0),
            'side_neutral'        : self._stats.get('side_neutral', 0),
            'side_quality_pct'    : clf_stats.get('side_pct', 0.0),
            # Legacy (compat)
            'masvn_side_native'   : self._stats.get('side_native', 0),
            'masvn_side_override' : self._stats.get('masvn_side_override', 0),
            'masvn_side_confirm'  : self._stats.get('masvn_side_confirm', 0),
            'masvn_worker_dedup'  : self._stats.get('masvn_worker_dedup', 0),
            'dedup_hits'          : self._stats.get('dedup_hits', 0),
            'routing'             : dict(self._stats),
        }
