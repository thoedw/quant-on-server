# Plan: EOD Daily Close — Đảm bảo dữ liệu 7 TF chính xác cuối ngày

**Ngày bắt đầu**: 2026-04-15  
**Status**: 🟡 Chờ review & approve để triển khai  
**Branch**: `feature/macro-quant-ekg`

---

## Bối cảnh

`intraday_engine.py` thu tick MQTT từ DNSE và tích lũy nến 7 timeframe (1m→1W) ghi vào SQLite trong ngày. Tuy nhiên, engine có thể bị miss tick (restart, MQTT drop, ATO/ATC...) dẫn đến:
- Volume thiếu so với thực tế
- Một số nến 1m/5m... không tồn tại trong DB

**Giải pháp**: Cuối mỗi ngày giao dịch (15:45 ICT), chạy job lấy dữ liệu OHLCV chính xác từ **DNSE chart API** (authoritative) → ghi đè vào `stock_prices` → đảm bảo data đúng cho VWAP/TWAP và các thuật toán quant.

---

## Priority

| Priority | Mục tiêu | Output |
|---|---|---|
| **P0** | DNSE OHLCV 7 TF → `stock_prices` (chính xác) | Data sạch cho quant |
| **P1** | So sánh engine vs DNSE → log chất lượng | Improve realtime engine |

---

## Quyết định thiết kế đã confirmed

1. **DNSE là nguồn sự thật cuối ngày** — ghi đè OHLC + Volume lên engine data
2. **Giữ `buy_vol`/`sell_vol`/`delta`** từ engine (Order Flow, DNSE không có)
3. **Timestamp = ICT (+7h VN)** — nhất quán với engine convention
4. **Reuse** `AsyncDNSEExtractor` + `SQLiteLoader` — không viết lại
5. **Script mới**: `scripts/eod_daily_close.py` (kh biệt với `batch_prices_async.py` chạy full history)

---

## Luồng xử lý

```
15:30 Thị trường đóng cửa
  │
15:45 Cron trigger eod_daily_close.py
  │
  ├─ PHASE 1 (P0) ── DNSE → stock_prices ────────────────────
  │   1. Snapshot engine data hôm nay (đọc từ stock_prices trước)
  │      → lưu tạm dict {(symbol,tf,trade_time): row}
  │   2. Fetch DNSE chart API async (7 TF × 1544 mã, ~3-5s)
  │   3. Convert timestamps → ICT (Asia/Ho_Chi_Minh)
  │   4. Upsert vào stock_prices:
  │        open   = DNSE  ← authoritative
  │        high   = DNSE  ← authoritative
  │        low    = DNSE  ← authoritative
  │        close  = DNSE  ← authoritative
  │        volume = DNSE  ← authoritative
  │        buy_vol, sell_vol, delta = GIỮ NGUYÊN từ engine
  │
  │   ✅ stock_prices đã đủ và chính xác cho VWAP/TWAP
  │
  └─ PHASE 2 (P1) ── Quality Log ────────────────────────────
      5. Compare eng_snapshot vs DNSE per-candle:
         - vol_capture_pct = eng_vol / eod_vol × 100
         - ohlc_max_diff = max(%Δopen, %Δhigh, %Δlow, %Δclose)
         - gap_reason: ENGINE_DOWN | MQTT_DROP | ATO_ATC | PARSE_ERR | UNKNOWN
         - engine_was_up: check Redis last_ticks timestamp
      6. INSERT INTO price_quality_log
      7. Print summary report
```

---

## Upsert SQL (P0 — core logic)

```sql
-- Ghi DNSE authoritative, KHÔNG ghi đè buy_vol/sell_vol/delta
INSERT INTO stock_prices
    (security_id, interval, trade_time,
     open, high, low, close, volume,
     buy_vol, sell_vol, delta)
VALUES (?, ?, ?,  ?, ?, ?, ?, ?,  0, 0, 0)
ON CONFLICT(security_id, interval, trade_time) DO UPDATE SET
    open   = excluded.open,
    high   = excluded.high,
    low    = excluded.low,
    close  = excluded.close,
    volume = excluded.volume
    -- buy_vol, sell_vol, delta: KHÔNG thay đổi
```

---

## Schema DB mới (P1)

### Bảng `price_quality_log`
```sql
CREATE TABLE IF NOT EXISTS price_quality_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT    NOT NULL,           -- '2026-04-15'
    symbol          TEXT    NOT NULL,
    interval        TEXT    NOT NULL,           -- '1m','5m','1D'...
    trade_time      TEXT    NOT NULL,           -- ICT VN
    -- Engine snapshot (đọc trước khi Phase 1 ghi đè)
    eng_open        REAL    DEFAULT 0,
    eng_high        REAL    DEFAULT 0,
    eng_low         REAL    DEFAULT 0,
    eng_close       REAL    DEFAULT 0,
    eng_vol         INTEGER DEFAULT 0,
    eng_buy_vol     INTEGER DEFAULT 0,
    eng_sell_vol    INTEGER DEFAULT 0,
    eng_delta       INTEGER DEFAULT 0,
    -- DNSE EOD (source of truth)
    eod_open        REAL    DEFAULT 0,
    eod_high        REAL    DEFAULT 0,
    eod_low         REAL    DEFAULT 0,
    eod_close       REAL    DEFAULT 0,
    eod_vol         INTEGER DEFAULT 0,
    -- Chỉ số đánh giá
    vol_capture_pct REAL,                       -- eng_vol / eod_vol × 100
    ohlc_max_diff   REAL,                       -- max %Δ của OHLC
    gap_reason      TEXT,                       -- ENGINE_DOWN|MQTT_DROP|ATO_ATC|PARSE_ERR|OK
    engine_was_up   INTEGER DEFAULT 0,          -- 1 nếu Redis có tick trong window
    status          TEXT    NOT NULL,           -- OK|LOW_VOL|OHLC_DIFF|MISSING|EXTRA
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_date, symbol, interval, trade_time)
);
CREATE INDEX IF NOT EXISTS idx_pql_date_status ON price_quality_log(run_date, status);
CREATE INDEX IF NOT EXISTS idx_pql_gap_reason  ON price_quality_log(gap_reason);
```

### Bảng `engine_session_log`
```sql
CREATE TABLE IF NOT EXISTS engine_session_log (
    session_id      TEXT PRIMARY KEY,           -- UUID, ghi Redis khi engine start
    started_at      DATETIME NOT NULL,
    ended_at        DATETIME,                   -- NULL nếu crash
    symbols_tracked INTEGER DEFAULT 0,
    total_ticks     INTEGER DEFAULT 0,
    end_reason      TEXT                        -- CLEAN|CRASH|SIGNAL
);
```

### View `v_engine_quality`
```sql
CREATE VIEW IF NOT EXISTS v_engine_quality AS
SELECT
    run_date, interval, gap_reason,
    COUNT(*)                                              AS total_candles,
    SUM(CASE WHEN status='OK'      THEN 1 ELSE 0 END)   AS ok_count,
    SUM(CASE WHEN status='MISSING' THEN 1 ELSE 0 END)   AS missing_count,
    SUM(CASE WHEN status='LOW_VOL' THEN 1 ELSE 0 END)   AS low_vol_count,
    ROUND(AVG(CASE WHEN vol_capture_pct IS NOT NULL
                   THEN vol_capture_pct END), 1)         AS avg_capture_pct,
    COUNT(DISTINCT symbol)                               AS affected_symbols
FROM price_quality_log
GROUP BY run_date, interval, gap_reason
ORDER BY run_date DESC, missing_count DESC;
```

---

## Files cần tạo/sửa

| File | Thay đổi |
|---|---|
| `scripts/eod_daily_close.py` | **[NEW]** Script chính |
| `securities_master/database.py` | Thêm `ensure_quality_tables()` |
| `scripts/setup_cron.sh` | Thêm cron 15:45 ICT |

---

## Cron

```bash
# 15:45 ICT T2-T6 (= 08:45 UTC)
45 8 * * 1-5  cd /Users/tuanho/quant && source venv/bin/activate && \
              python3 scripts/eod_daily_close.py >> /tmp/eod_daily_close.log 2>&1
```

---

## Verification

```bash
# Test dry-run với 3 mã
python3 scripts/eod_daily_close.py --symbols VCB,HPG,TCB --dry-run

# Kiểm tra P0 — volume đúng
sqlite3 data/securities_master.db "
  SELECT s.symbol, sp.close, sp.volume, sp.buy_vol, sp.sell_vol
  FROM stock_prices sp JOIN securities s ON sp.security_id=s.security_id
  WHERE sp.interval='1D' AND date(sp.trade_time)=date('now','localtime')
    AND s.symbol IN ('VCB','HPG','TCB');"

# Kiểm tra P1 — engine quality
sqlite3 data/securities_master.db "
  SELECT status, gap_reason, COUNT(*), ROUND(AVG(vol_capture_pct),1)
  FROM price_quality_log
  WHERE run_date=date('now','localtime')
  GROUP BY status, gap_reason ORDER BY 3 DESC;"
```

---

## Open Questions (cần resolve trước khi code)

1. **OHLC tolerance**: 5% hay khác? (đề xuất 5%)
2. **Volume threshold**: 80% capture = OK (đề xuất)  
   → Có muốn threshold khác nhau cho HOSE vs UPCOM không?  
   → UPCOM thinly traded, capture có thể thấp hơn tự nhiên
3. **Scope timeframe**: Đủ 7 TF hay chỉ 1m + 1D + 1W?  
   → Crawl 7 TF = 10,808 requests (~5s); chỉ 1D = 1,544 requests (~1s)
4. **engine_session_log**: Có muốn thêm session tracking vào `intraday_engine.py` ngay không (Redis UUID)?

---

## Liên quan

- `scripts/refresh_daily_volume.py` — đã chạy 15:45, lấy volume + giá vào `ref_prices`
- `scripts/batch_prices_async.py` — backfill lịch sử (không thay thế)
- `realtime/intraday_engine.py` — nguồn buy_vol/sell_vol/delta
- `price_board/server.js` — đọc `stock_prices` để hiển thị bảng giá
