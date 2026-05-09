import os
import sys
import json
import struct
import logging
import asyncio
import redis.asyncio as redis
from datetime import datetime, timezone
import pandas as pd

# Thêm root dự án vào path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from securities_master.database import DatabaseManager
from securities_master.models import PriceRecord
from securities_master.loaders.sqlite_loader import SQLiteLoader

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [Aggregator] %(message)s',
    handlers=[
        logging.FileHandler("aggregator.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


# ============================================================
# PROTOBUF DECODER - Schema DNSE stockinfo frame
# ============================================================
# Schema đã reverse-engineer từ Playwright sniff:
#   f9_double  = Ref price (giá tham chiếu / TC)
#   f10_double = Ceiling (trần)
#   f11_double = Floor (sàn)
#   f12_double = Giá hiện tại / giá khớp lần cuối
#   f13_double = Giá trước khớp
#   f14_double = Volume lô (đơn vị lot = 100 CP điển hình)
#   f15_double = Tổng KL khớp lũy kế phiên
# ============================================================

def decode_tick_protobuf(data_hex: str) -> dict:
    """Parse protobuf binary payload từ DNSE stockinfo frame."""
    def parse_varint(raw, idx):
        val = 0; shift = 0
        while idx < len(raw):
            b = raw[idx]; idx += 1
            val |= (b & 0x7F) << shift
            if not (b & 0x80): break
            shift += 7
        return val, idx

    try:
        raw = bytes.fromhex(data_hex)
        idx = 0
        fields = {}
        while idx < len(raw):
            if idx >= len(raw): break
            tag_byte = raw[idx]; idx += 1
            field_num = tag_byte >> 3
            wire_type = tag_byte & 0x7

            if wire_type == 0:
                val, idx = parse_varint(raw, idx)
                fields[field_num] = val
            elif wire_type == 1:  # 64-bit double
                if idx + 8 <= len(raw):
                    fields[field_num] = struct.unpack('<d', raw[idx:idx+8])[0]
                    idx += 8
            elif wire_type == 2:  # length-delimited
                slen, idx = parse_varint(raw, idx)
                nested = raw[idx:idx+slen]
                idx += slen
                # Parse nested message để lấy cumulative volume (f2 của nested = cum_vol)
                try:
                    nidx = 0
                    nested_fields = {}
                    while nidx < len(nested):
                        ntag = nested[nidx]; nidx += 1
                        nfn = ntag >> 3; nwt = ntag & 0x7
                        if nwt == 0:
                            v, nidx = parse_varint(nested, nidx)
                            nested_fields[nfn] = v
                        elif nwt == 1:
                            if nidx + 8 <= len(nested):
                                nested_fields[nfn] = struct.unpack('<d', nested[nidx:nidx+8])[0]
                                nidx += 8
                        elif nwt == 2:
                            sl, nidx = parse_varint(nested, nidx)
                            nidx += sl
                        else:
                            break
                    if nested_fields:
                        fields[f'n{field_num}'] = nested_fields
                except Exception:
                    pass
                try: fields[f's{field_num}'] = nested.decode('utf-8')
                except: pass
            elif wire_type == 5:  # 32-bit float
                if idx + 4 <= len(raw):
                    fields[field_num] = struct.unpack('<f', raw[idx:idx+4])[0]
                    idx += 4
            else:
                break  # unknown wire type - stop gracefully

        # Volume từ nested f5, field 2 (số chia=100 để thành đơn vị lot)
        cum_vol = 0
        n5 = fields.get('n5', {})
        if n5 and 2 in n5:
            cum_vol = int(n5[2])

        return {
            'price':    fields.get(12, fields.get(13, 0.0)),
            'ref':      fields.get(9,  0.0),
            'ceil':     fields.get(10, 0.0),
            'floor':    fields.get(11, 0.0),
            'volume_cum': cum_vol,
        }
    except Exception:
        return {}



# ============================================================
# CANDLE AGGREGATION
# ============================================================

async def aggregate_ticks_into_candle(symbol_key: str, raw_ticks: list, sec_id: int, current_minute_dt: datetime):
    """Parse ticks từ Redis, decode protobuf, nở thành 1 Cây Nến 1 phút."""
    if not raw_ticks:
        return None

    symbol = symbol_key.replace("tick_buffer:", "")
    decoded = []

    for r in raw_ticks:
        try:
            tick = json.loads(r)
            if 'data_hex' in tick:
                parsed = decode_tick_protobuf(tick['data_hex'])
                if parsed.get('price', 0) > 0:
                    decoded.append(parsed)
            elif 'price' in tick:  # fallback format cũ
                decoded.append({
                    'price': float(tick['price']),
                    'volume_lot': int(tick.get('volume', 0)),
                    'volume_cum': 0
                })
        except Exception:
            continue

    if not decoded:
        logger.debug(f"[{symbol}] Không decode được tick nào có price > 0")
        return None

    prices = [d['price'] for d in decoded if d.get('price', 0) > 0]
    if not prices:
        return None

    # NOTE: Volume thực nằm ở topic boardevent (chưa subscribe).
    # stockinfo frame chỉ có price snapshot, không có volume per-trade.
    # volume_cum từ nested f5 là timestamp/counter, không phải share volume.
    # TODO: Subscribe quotes/krx/mdds/boardevent/... để lấy volume khớp thật.
    volume = 0

    record = PriceRecord(
        security_id=sec_id,
        interval='1m',
        trade_time=current_minute_dt,
        open=prices[0],
        high=max(prices),
        low=min(prices),
        close=prices[-1],
        volume=volume
    )
    return record


# ============================================================
# MAIN WORKER
# ============================================================

async def aggregator_worker():
    redis_client = await redis.from_url(REDIS_URL, decode_responses=True)

    db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
    db = DatabaseManager(db_path)
    loader = SQLiteLoader(db)

    # Nạp danh sách SecurityMapping
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT symbol, security_id FROM securities WHERE asset_type='EQUITY'")
    sec_map = {row['symbol']: row['security_id'] for row in cur.fetchall()}

    logger.info(f"⚙️ Aggregator khởi động. Đã map {len(sec_map)} mã. Quét Redis mỗi 60 giây.")

    # Test decode ngay khi khởi động
    sample_keys = await redis_client.keys("tick_buffer:*")
    if sample_keys:
        sample_key = sample_keys[0]
        sample_symbol = sample_key.replace("tick_buffer:", "")
        sample = await redis_client.lindex(sample_key, -1)
        if sample:
            tick = json.loads(sample)
            if 'data_hex' in tick:
                parsed = decode_tick_protobuf(tick['data_hex'])
                logger.info(f"✅ Decode test [{sample_symbol}]: price={parsed.get('price')}, ref={parsed.get('ref')}, vol_lot={parsed.get('volume_lot')}")

    try:
        while True:
            # Ngủ tới giây số 0 của phút tiếp theo
            now = datetime.now()
            sleep_time = 60 - now.second - (now.microsecond / 1_000_000.0)
            logger.info(f"⏰ Đợi {sleep_time:.1f}s đến hết phút...")
            await asyncio.sleep(sleep_time)

            minute_marker = datetime.now().replace(second=0, microsecond=0)

            # Càn quét toàn bộ buffer
            keys = await redis_client.keys("tick_buffer:*")
            if not keys:
                logger.info("Redis trống - không có tick nào.")
                continue

            batch_records = []

            for key in keys:
                symbol = key.replace("tick_buffer:", "")
                sec_id = sec_map.get(symbol)

                if not sec_id:
                    continue

                ticks_len = await redis_client.llen(key)
                if ticks_len == 0:
                    continue

                # Atomic Pop toàn bộ
                raw_ticks = await redis_client.lpop(key, ticks_len)

                record = await aggregate_ticks_into_candle(key, raw_ticks, sec_id, minute_marker)
                if record:
                    batch_records.append(record)

            if batch_records:
                await asyncio.to_thread(loader.load_prices, batch_records)
                logger.info(f"💾 Đã lưu {len(batch_records)} nến 1M cho phiên {minute_marker.strftime('%H:%M')}.")
            else:
                logger.info(f"⚠️ Không có nến nào được tạo - kiểm tra lại protobuf decoder.")

    except asyncio.CancelledError:
        logger.info("Dừng Aggregator Worker an toàn.")
    finally:
        await redis_client.aclose()


if __name__ == '__main__':
    try:
        asyncio.run(aggregator_worker())
    except KeyboardInterrupt:
        pass
