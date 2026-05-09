# Spec: Unified Intraday Engine

## Vấn đề (What)
Hiện tại 2 tiến trình tách rời (Red-Lightning + Aggregator) với Redis làm trung gian. 
Vấn đề:
- Redis là single point of failure không cần thiết
- Volume bằng 0 vì stock-info topic không có dữ liệu volume
- Chỉ build nến 1m, không build 5m/15m/30m/1H/1D/1W

## Mục tiêu (Why)
Một tiến trình duy nhất làm toàn bộ pipeline:
- Thu TICK thực (price + volume) từ DNSE WebSocket
- Build OHLCV nến cho 7 timeframe: 1m, 5m, 15m, 30m, 1H, 1D, 1W
- Ghi thẳng vào SQLite
- EOD batch có thể đối chiếu và fill gap

## Giải pháp Kỹ thuật

### 1. WebSocket Topics cần subscribe
| Topic | Mục đích |
|---|---|
| `quotes/krx/mdds/stockinfo/v1/roundlot/symbol/{SYMBOL}` | Price snapshot (ref, ceil, floor, current) |
| `quotes/krx/mdds/boardevent/v1/roundlot/market/HSX/product/EQ` | **Tick khớp lệnh có volume** - HSX |
| `quotes/krx/mdds/boardevent/v1/roundlot/market/HNX/product/EQ` | Tick khớp lệnh - HNX |
| `quotes/krx/mdds/boardevent/v1/roundlot/market/UPX/product/UPX` | Tick khớp lệnh - UPCOM |

### 2. CandleAccumulator (In-Memory)
```python
TIMEFRAMES = {
    '1m':  timedelta(minutes=1),
    '5m':  timedelta(minutes=5),
    '15m': timedelta(minutes=15),
    '30m': timedelta(minutes=30),
    '1H':  timedelta(hours=1),
    '1D':  timedelta(hours=8.5),  # 09:00 - 15:30
    '1W':  timedelta(weeks=1),
}

# State per symbol per timeframe
state[symbol][tf] = {
    'open': float, 'high': float, 'low': float, 
    'close': float, 'volume': int, 'period_start': datetime
}
```

### 3. Flush Schedule
- **1m**: mỗi phút tại giây :00
- **5m, 15m, 30m**: khi period_start thay đổi (tick đầu tiên sau mốc thời gian)
- **1H**: khi sang giờ mới
- **1D**: lúc 15:31 (sau đóng cửa)
- **1W**: thứ Sáu 15:31

### 4. Ghi SQLite
- **Upsert** (INSERT OR REPLACE) để EOD batch có thể overwrite với data chính xác hơn
- Batch writer: ghi mỗi 500 records hoặc 10 giây, whichever comes first

### 5. Redis (optional)
- Không bắt buộc nữa
- Chỉ giữ nếu muốn expose tick cho các consumer khác (streaming analytics)
