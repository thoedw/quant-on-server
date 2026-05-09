"""
=============================================================
VWAP Engine — Tính VWAP real-time từ nến 1m trong DB
=============================================================
Công thức:
  VWAP = Σ(close * volume) / Σ(volume)  — tính từ 9:15 VN
  Bands = VWAP ± N * σ  (Volume-weighted std dev)
  Cum Delta = Σ(buy_vol - sell_vol)  — tích lũy từ đầu phiên

Cách dùng (standalone — sau khi intraday_engine đã thu thập dữ liệu):
  from realtime.vwap_engine import VWAPEngine
  engine = VWAPEngine(db_path)
  snapshots = engine.compute_all(top_n=200)
"""

import sqlite3
import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Giờ mở cửa thị trường VN: 9:15 (UTC+7) = 2:15 UTC
VN_TZ   = timezone(timedelta(hours=7))
OPEN_H  = 9
OPEN_M  = 15


def _session_open_utc(date_vn: str) -> str:
    """Trả về chuỗi datetime UTC của 9:15 VN cho ngày date_vn (YYYY-MM-DD)."""
    dt_vn = datetime.strptime(date_vn, "%Y-%m-%d").replace(
        hour=OPEN_H, minute=OPEN_M, tzinfo=VN_TZ
    )
    dt_utc = dt_vn.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%d %H:%M:%S")


class VWAPSnapshot:
    """Kết quả tính VWAP cho một mã tại một thời điểm."""
    __slots__ = [
        "security_id", "snapshot_time", "vwap",
        "vwap_upper1", "vwap_lower1", "vwap_upper2", "vwap_lower2",
        "cum_volume", "cum_delta", "last_close",
    ]

    def __init__(self, security_id: int, snapshot_time: str,
                 vwap: float, std_dev: float,
                 cum_volume: int, cum_delta: int, last_close: float):
        self.security_id   = security_id
        self.snapshot_time = snapshot_time
        self.vwap          = round(vwap, 4)
        self.vwap_upper1   = round(vwap + 1 * std_dev, 4)
        self.vwap_lower1   = round(vwap - 1 * std_dev, 4)
        self.vwap_upper2   = round(vwap + 2 * std_dev, 4)
        self.vwap_lower2   = round(vwap - 2 * std_dev, 4)
        self.cum_volume    = cum_volume
        self.cum_delta     = cum_delta
        self.last_close    = last_close

    def as_tuple(self):
        return (
            self.security_id, self.snapshot_time,
            self.vwap, self.vwap_upper1, self.vwap_lower1,
            self.vwap_upper2, self.vwap_lower2,
            self.cum_volume, self.cum_delta, self.last_close,
        )

    def __repr__(self):
        sign = "+" if self.cum_delta >= 0 else ""
        pos  = "ABOVE" if self.last_close >= self.vwap else "BELOW"
        return (
            f"<VWAP sid={self.security_id} "
            f"vwap={self.vwap:.2f} close={self.last_close:.2f} [{pos}] "
            f"Δcum={sign}{self.cum_delta:,} vol={self.cum_volume:,}>"
        )


class VWAPEngine:
    """
    Tính VWAP intraday cho nhiều mã từ bảng stock_prices (interval=1m).

    Quy trình:
    1. Lấy top_n mã có volume cao nhất hôm nay
    2. Với mỗi mã: kéo toàn bộ nến 1m từ 9:15 đến hiện tại
    3. Tính VWAP, Bands, Cum Delta
    4. Upsert kết quả vào bảng vwap_snapshots
    """

    MIN_CANDLES = 5  # Yêu cầu tối thiểu số nến để tính VWAP có ý nghĩa

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, detect_types=0)
        conn.row_factory = sqlite3.Row
        return conn

    def _today_vn(self) -> str:
        return datetime.now(VN_TZ).strftime("%Y-%m-%d")

    def compute_vwap(self, candles: list) -> Optional[VWAPSnapshot]:
        """
        Tính VWAP + Bands từ danh sách nến.
        candles: list of Row với (security_id, trade_time, close, volume, delta)
        """
        candles = [c for c in candles if c["volume"] is not None]
        if len(candles) < self.MIN_CANDLES:
            return None

        security_id = candles[0]["security_id"]
        snapshot_time = candles[-1]["trade_time"]  # Thời điểm nến mới nhất
        last_close = candles[-1]["close"] or 0.0

        cum_pv     = 0.0  # Σ(price * volume)
        cum_vol    = 0    # Σ(volume)
        cum_delta  = 0    # Σ(delta)
        cum_pv2    = 0.0  # Σ(price² * volume) — để tính variance

        for c in candles:
            price  = c["close"] or 0.0
            vol    = c["volume"] or 0
            delta  = c["delta"] or 0

            cum_pv    += price * vol
            cum_vol   += vol
            cum_delta += delta
            cum_pv2   += price * price * vol

        if cum_vol == 0:
            return None

        vwap = cum_pv / cum_vol

        # Volume-weighted variance: E[X²] - E[X]²
        variance = max(0.0, (cum_pv2 / cum_vol) - (vwap ** 2))
        std_dev  = math.sqrt(variance)

        return VWAPSnapshot(
            security_id  = security_id,
            snapshot_time= snapshot_time,
            vwap         = vwap,
            std_dev      = std_dev,
            cum_volume   = cum_vol,
            cum_delta    = cum_delta,
            last_close   = last_close,
        )

    def compute_all(self, top_n: int = 300, date_vn: str = None) -> list[VWAPSnapshot]:
        """
        Tính VWAP cho top_n mã có volume cao nhất hôm nay.
        Trả về danh sách VWAPSnapshot và upsert vào DB.
        """
        if date_vn is None:
            date_vn = self._today_vn()

        session_open = _session_open_utc(date_vn)
        conn = self._get_conn()

        # trade_time lưu VN local → dùng date(trade_time) trực tiếp
        top_sql = """
            SELECT sp.security_id, SUM(sp.volume) as total_vol
            FROM stock_prices sp
            WHERE sp.interval = '1m'
              AND date(sp.trade_time) = ?
            GROUP BY sp.security_id
            HAVING SUM(sp.volume) > 0
            ORDER BY total_vol DESC
            LIMIT ?
        """
        top_rows = conn.execute(top_sql, (date_vn, top_n)).fetchall()
        if not top_rows:
            logger.warning("VWAPEngine: Không có dữ liệu hôm nay.")
            conn.close()
            return []

        top_ids = [r["security_id"] for r in top_rows]
        placeholders = ",".join("?" * len(top_ids))

        # 2. Kéo toàn bộ nến 1m ngày đó cho top_n mã
        candle_sql = f"""
            SELECT security_id, trade_time, close, volume,
                   COALESCE(buy_vol, 0) - COALESCE(sell_vol, 0) as delta
            FROM stock_prices
            WHERE interval = '1m'
              AND date(trade_time) = ?
              AND security_id IN ({placeholders})
            ORDER BY security_id, trade_time ASC
        """
        all_candles = conn.execute(
            candle_sql, [date_vn] + top_ids
        ).fetchall()

        # 3. Nhóm theo security_id
        grouped: dict[int, list] = {}
        for c in all_candles:
            sid = c["security_id"]
            if sid not in grouped:
                grouped[sid] = []
            grouped[sid].append(c)

        # 4. Tính VWAP cho từng mã
        snapshots = []
        for sid, candles in grouped.items():
            snap = self.compute_vwap(candles)
            if snap:
                snapshots.append(snap)

        # 5. Upsert vào DB
        if snapshots:
            upsert_sql = """
                INSERT INTO vwap_snapshots
                    (security_id, snapshot_time, vwap,
                     vwap_upper1, vwap_lower1, vwap_upper2, vwap_lower2,
                     cum_volume, cum_delta, last_close)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(security_id, snapshot_time)
                DO UPDATE SET
                    vwap=excluded.vwap,
                    vwap_upper1=excluded.vwap_upper1,
                    vwap_lower1=excluded.vwap_lower1,
                    vwap_upper2=excluded.vwap_upper2,
                    vwap_lower2=excluded.vwap_lower2,
                    cum_volume=excluded.cum_volume,
                    cum_delta=excluded.cum_delta,
                    last_close=excluded.last_close
            """
            conn.executemany(upsert_sql, [s.as_tuple() for s in snapshots])
            conn.commit()
            logger.info(
                f"VWAPEngine: Đã tính {len(snapshots)} VWAP snapshots "
                f"(top_n={top_n}, date={date_vn})"
            )

        conn.close()
        return snapshots

    def get_latest(self, security_id: int, conn: sqlite3.Connection) -> Optional[dict]:
        """Lấy snapshot VWAP mới nhất của một mã từ DB (dùng trong signal detector)."""
        row = conn.execute(
            """
            SELECT * FROM vwap_snapshots
            WHERE security_id = ?
            ORDER BY snapshot_time DESC LIMIT 1
            """,
            (security_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_latest_many(
        self, security_ids: list[int], conn: sqlite3.Connection
    ) -> dict[int, dict]:
        """Lấy snapshot VWAP mới nhất cho nhiều mã cùng lúc."""
        if not security_ids:
            return {}
        ph = ",".join("?" * len(security_ids))
        rows = conn.execute(
            f"""
            SELECT vs.*
            FROM vwap_snapshots vs
            INNER JOIN (
                SELECT security_id, MAX(snapshot_time) as max_t
                FROM vwap_snapshots
                WHERE security_id IN ({ph})
                GROUP BY security_id
            ) latest ON vs.security_id = latest.security_id
                     AND vs.snapshot_time = latest.max_t
            """,
            security_ids,
        ).fetchall()
        return {r["security_id"]: dict(r) for r in rows}
