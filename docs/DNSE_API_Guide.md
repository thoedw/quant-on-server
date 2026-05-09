# Tài liệu: DNSE LightSpeed API — Session & Deal Fetcher

> Cập nhật: 2026-04-25 | Tác giả: Antigravity AI

---

## Tổng quan

DNSE cung cấp **LightSpeed API** cho phép truy cập dữ liệu tài khoản giao dịch theo chương trình. Hệ thống dùng bảo mật 2 lớp:

```
Lớp 1: Username + Password  →  JWT Token     (đọc dữ liệu tài khoản)
Lớp 2: JWT Token + OTP      →  Trading Token (giao dịch + lịch sử lệnh)
```

---

## Thông tin tài khoản

| Field | Giá trị |
|-------|---------|
| Số tài khoản | `064C220772` |
| Investor ID  | `0001982402` |
| Loại tài khoản | **SpaceX** — Giao dịch theo DEAL |
| dealAccount | ✅ true |
| marginAccount | ✅ true |
| derivativeAccount | ✅ true |

> [!NOTE]
> Credentials thực sự lưu trong file `.env` (không commit). Session cache lưu trong `.dnse_session.json` (chmod 600, gitignored).

---

## Files liên quan

| File | Mô tả |
|------|-------|
| `scripts/dnse_session.py` | Session manager — login, OTP, cache token |
| `scripts/dnse_deal_fetcher.py` | Fetch deal/put-through order history |
| `.env` | Chứa DNSE_USERNAME, DNSE_PASSWORD, API keys |
| `.dnse_session.json` | Cache JWT + trading-token (tự sinh, gitignored) |

---

## Hướng dẫn sử dụng

### Bước 1 — Khởi tạo session buổi sáng

Chạy **1 lần mỗi ngày** trước khi dùng:

```bash
python3 scripts/dnse_session.py
```

Script sẽ:
1. Tự động login bằng username/password từ `.env`
2. Hiện prompt yêu cầu nhập OTP từ **SmartOTP app**
3. Cache JWT + Trading Token vào `.dnse_session.json`

**Output mẫu:**
```
══════════════════════════════════════════════════════
  📱 Mở SmartOTP để lấy mã 6 số
     Tài khoản: 064C220772
══════════════════════════════════════════════════════
  OTP (lần 1/3): 123456

✅ Session DNSE đã sẵn sàng!
   JWT    : eyJ0eXAiOiJKV1QiL...
   Trading: abc123...
══════════════════════════════════════════════════════
```

### Bước 2 — Khám phá endpoints (lần đầu hoặc sau update)

```bash
python3 scripts/dnse_deal_fetcher.py --explore
```

→ Tự động thử tất cả API endpoints với trading-token, in ra những endpoint nào hoạt động.

### Bước 3 — Fetch deal order history

```bash
# Ngày hôm qua (mặc định)
python3 scripts/dnse_deal_fetcher.py

# Ngày cụ thể
python3 scripts/dnse_deal_fetcher.py --date 2026-04-24

# Chỉ một số mã
python3 scripts/dnse_deal_fetcher.py --date 2026-04-24 --symbols SHB,POW,VND
```

### Kiểm tra trạng thái session

```bash
python3 scripts/dnse_session.py --check
```

### Bắt buộc làm mới session (OTP mới)

```bash
python3 scripts/dnse_session.py --refresh
# hoặc
python3 scripts/dnse_deal_fetcher.py --refresh
```

---

## Sử dụng trong script khác

```python
from scripts.dnse_session import get_jwt_headers, get_auth_headers

# Chỉ JWT (cho read-only, không cần OTP)
headers = get_jwt_headers()

# Đầy đủ JWT + Trading Token (cho lệnh và lịch sử)
headers = get_auth_headers()
# → {'Authorization': 'Bearer eyJ...', 'trading-token': 'abc...'}

import requests
r = requests.get(
    'https://services.entrade.com.vn/dnse-order-service/accounts/0001982402/deal-orders',
    headers=headers,
    timeout=10
)
```

---

## API Endpoints đã xác nhận

### ✅ Không cần auth (public)

| Endpoint | Mô tả | Resolution |
|----------|-------|------------|
| `GET https://services.entrade.com.vn/chart-api/v2/ohlcs/stock` | OHLCV bar data | 1,5,15,30m |
| Params: `symbol`, `resolution`, `from`, `to` | | |

**Ví dụ:**
```python
import requests
from datetime import datetime, timezone, timedelta
VN_TZ = timezone(timedelta(hours=7))

t_from = int(datetime(2026, 4, 24, 9,  0, tzinfo=VN_TZ).timestamp())
t_to   = int(datetime(2026, 4, 24, 15, 30, tzinfo=VN_TZ).timestamp())

r = requests.get(
    'https://services.entrade.com.vn/chart-api/v2/ohlcs/stock',
    params={'symbol':'SHB','resolution':'1','from':t_from,'to':t_to}
)
data = r.json()
# data = {'t': [timestamps], 'o': [opens], 'h': [highs], 'l': [lows],
#         'c': [closes], 'v': [volumes], 'nextTime': ...}
```

> [!WARNING]
> Volume trong bars 1m/5m/15m/30m = **nm_vol** (khớp lệnh liên tục) — KHÔNG bao gồm deal/thỏa thuận.

### ✅ Cần JWT (không cần OTP)

| Endpoint | Mô tả |
|----------|-------|
| `GET /dnse-user-service/api/me` | Thông tin cá nhân |
| `GET /dnse-order-service/accounts` | Danh sách tài khoản |

### ✅ Cần JWT + Trading Token (OTP)

| Endpoint | Mô tả | Status |
|----------|-------|--------|
| `POST /dnse-order-service/trading-token` | Lấy trading token từ OTP | Đã test ✅ |
| `GET /dnse-order-service/accounts/{id}/balance` | Số dư tiền | Cần explore |
| `GET /dnse-order-service/accounts/{id}/positions` | Danh mục cổ phiếu | Cần explore |
| `GET /dnse-order-service/accounts/{id}/orders` | Lịch sử lệnh khớp | Cần explore |
| `GET /dnse-order-service/accounts/{id}/deal-orders` | Lịch sử lệnh thỏa thuận | Cần explore |

---

## Token TTL

| Token | Thời hạn | Ghi chú |
|-------|:--------:|---------|
| JWT Token | ~8 giờ | Tự động reuse nếu còn hạn |
| Trading Token | ~4 giờ | Cần OTP mới nếu hết hạn |

→ Session file `.dnse_session.json` lưu cả hai token + timestamp. Script tự kiểm tra và chỉ yêu cầu OTP khi cần.

---

## Mục tiêu tích hợp

### Hiện tại (pt_vol từ Yahoo Finance)
```
DNSE chart-api → nm_vol (bars 1m)
Yahoo Finance  → total_vol 1D
pt_vol = total - nm  (±0.1% accuracy)
```

### Mục tiêu (pt_vol từ DNSE deal-orders)
```
DNSE chart-api   → nm_vol (bars 1m)
DNSE deal-orders → deal_vol với timestamp chính xác
pt_vol = sum(deal quantities)  (100% accuracy)
+ timestamp từng deal → tích hợp vào VWAP calculation intraday
```

### Tích hợp vào EOD Pipeline (kế hoạch)
```
Phase 2.8 (hiện tại): impute pt_vol từ Yahoo Finance
Phase 2.8 (tương lai): fetch pt_vol từ DNSE deal-orders
  → nếu DNSE deal-orders available: dùng DNSE (chính xác hơn)
  → fallback: Yahoo Finance (nếu OTP session không có)
```

---

## Lưu ý bảo mật

> [!CAUTION]
> - **Không commit** `.dnse_session.json` và `.env` lên Git
> - File session có **chmod 600** (chỉ owner đọc được)
> - OTP thay đổi mỗi 30 giây — không thể tự động hóa hoàn toàn
> - Trading Token cho phép đặt lệnh — bảo quản cẩn thận

---

## So sánh nguồn dữ liệu Volume

| Nguồn | nm_vol | pt_vol | Timestamp deal | Tự động hóa | Coverage |
|-------|:------:|:------:|:--------------:|:-----------:|---------|
| **DNSE chart-api** | ✅ | ❌ | ❌ | ✅ 100% | HOSE/HNX/UPCOM |
| **Yahoo Finance** | ❌ | ✅ ±0.1% | ❌ | ✅ 100% | HOSE ~90%, HNX ❌ |
| **TV Screener** | ❌ | ✅ snapshot | ❌ | ✅ 100% | HOSE/HNX/UPCOM |
| **DNSE deal-orders** | ❌ | ✅ 100% | ✅ | ⚠️ OTP/sáng | HOSE/HNX |

---

## Troubleshooting

### OTP không hợp lệ
```
❌ OTP không hợp lệ hoặc đã hết hạn. Hãy thử lại.
```
→ OTP SmartOTP thay đổi mỗi 30s. Nhập nhanh sau khi thấy mã.

### Session hết hạn giữa phiên
```bash
python3 scripts/dnse_session.py --refresh
```

### 500 REMOTE_SERVER_ERROR
→ Bình thường khi gọi endpoint lệnh mà không có trading-token. Chạy `dnse_session.py` trước.

### Module not found
```bash
# Đảm bảo đang trong venv
source venv/bin/activate
python3 scripts/dnse_session.py
```
