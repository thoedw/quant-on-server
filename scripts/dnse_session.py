#!/usr/bin/env python3
"""
scripts/dnse_session.py
========================
DNSE LightSpeed API — Quản lý session (JWT + Trading Token).

Luồng:
  1. Login username/password  → JWT token
  2. Nhập SmartOTP 6 số      → Trading token (mã hóa phiên)
  3. Cache cả hai token vào   → ~/.dnse_session.json
  4. Tự động load lại token   → nếu còn hạn (< 8h)

Dùng trong các script khác:
  from scripts.dnse_session import get_session
  jwt, trading = get_session()
  headers = {'Authorization': f'Bearer {jwt}', 'trading-token': trading}

Chạy trực tiếp để khởi tạo session buổi sáng:
  python3 scripts/dnse_session.py
  python3 scripts/dnse_session.py --refresh     ← bắt buộc lấy token mới
"""

import os
import sys
import json
import time
import logging
import argparse
import getpass
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
_env = PROJECT_ROOT / '.env'
if _env.exists():
    for _l in _env.read_text().splitlines():
        _l = _l.strip()
        if _l and not _l.startswith('#') and '=' in _l:
            k, _, v = _l.partition('=')
            os.environ.setdefault(k.strip(), v.strip().strip('"'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [DNSE] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

VN_TZ       = timezone(timedelta(hours=7))
SESSION_FILE = PROJECT_ROOT / '.dnse_session.json'   # Không commit file này
AUTH_BASE    = 'https://api.dnse.com.vn'
SVC_BASE     = 'https://services.entrade.com.vn'

# Token TTL: JWT ~8h, trading-token ~4h (conservative)
JWT_TTL_SEC      = 8 * 3600
TRADING_TTL_SEC  = 4 * 3600


# ────────────────────────────────────────────────────────────────────────────
# SESSION CACHE
# ────────────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    SESSION_FILE.write_text(json.dumps(data, indent=2))
    SESSION_FILE.chmod(0o600)   # chỉ owner đọc được


def _is_valid(cache: dict, key: str, ttl: int) -> bool:
    """True nếu token còn hạn sử dụng."""
    ts = cache.get(f'{key}_ts', 0)
    return bool(cache.get(key)) and (time.time() - ts) < ttl


# ────────────────────────────────────────────────────────────────────────────
# AUTHENTICATION
# ────────────────────────────────────────────────────────────────────────────

def _login(username: str, password: str) -> str:
    """Bước 1: Login → JWT token."""
    logger.info(f'Đăng nhập DNSE: {username}')
    r = requests.post(
        f'{AUTH_BASE}/auth-service/login',
        json={'username': username, 'password': password},
        headers={
            'Content-Type': 'application/json',
            'Origin'       : 'https://entrade.com.vn',
            'User-Agent'   : 'Mozilla/5.0',
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f'Login thất bại [{r.status_code}]: {r.text[:200]}')

    token = r.json().get('token')
    if not token:
        raise RuntimeError(f'Không tìm thấy token trong response: {r.text[:200]}')

    logger.info('✅ JWT token obtained')
    return token


def _get_trading_token(jwt_token: str, otp: str) -> str:
    """Bước 2: Dùng OTP → Trading token."""
    r = requests.post(
        f'{SVC_BASE}/dnse-order-service/trading-token',
        json={'otp': otp},
        headers={
            'Authorization': f'Bearer {jwt_token}',
            'Content-Type' : 'application/json',
            'Origin'       : 'https://entrade.com.vn',
            'User-Agent'   : 'Mozilla/5.0',
        },
        timeout=10,
    )
    if r.status_code == 400:
        err = r.json().get('code', '')
        if 'OTP' in err.upper():
            raise ValueError('OTP không hợp lệ hoặc đã hết hạn. Hãy thử lại.')
        raise RuntimeError(f'400 Error: {r.text[:200]}')

    if r.status_code != 200:
        raise RuntimeError(f'Trading token thất bại [{r.status_code}]: {r.text[:200]}')

    d = r.json()
    trading_token = d.get('tradingToken') or d.get('trading_token') or d.get('token')
    if not trading_token:
        # Nếu response là string trực tiếp
        trading_token = r.text.strip().strip('"')

    if not trading_token:
        raise RuntimeError(f'Không tìm thấy trading token trong response: {r.text[:200]}')

    logger.info('✅ Trading token obtained')
    return trading_token


# ────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ────────────────────────────────────────────────────────────────────────────

def get_session(force_refresh: bool = False) -> tuple[str, str]:
    """
    Lấy (jwt_token, trading_token).
    - Tự load từ cache nếu còn hạn.
    - Nếu hết hạn hoặc force_refresh=True → yêu cầu đăng nhập lại.

    Returns:
        (jwt_token, trading_token)
    """
    cache = _load_cache()

    jwt_ok     = _is_valid(cache, 'jwt_token',     JWT_TTL_SEC)
    trading_ok = _is_valid(cache, 'trading_token', TRADING_TTL_SEC)

    if not force_refresh and jwt_ok and trading_ok:
        age_jwt = (time.time() - cache['jwt_token_ts']) / 60
        age_tr  = (time.time() - cache['trading_token_ts']) / 60
        logger.info(
            f'✅ Session cache hợp lệ | JWT: {age_jwt:.0f}m | Trading: {age_tr:.0f}m'
        )
        return cache['jwt_token'], cache['trading_token']

    # Cần làm mới
    username = os.environ.get('DNSE_USERNAME', '064C220772')
    password = os.environ.get('DNSE_PASSWORD', '')
    if not password:
        password = getpass.getpass('🔑 DNSE Password: ')

    # Bước 1: JWT (nếu cần)
    if force_refresh or not jwt_ok:
        jwt_token = _login(username, password)
        cache['jwt_token']    = jwt_token
        cache['jwt_token_ts'] = time.time()
        _save_cache(cache)
    else:
        jwt_token = cache['jwt_token']
        logger.info('  JWT token còn hạn, bỏ qua re-login')

    # Bước 2: Trading token
    print()
    print('═' * 55)
    print('  📱 Mở SmartOTP (hoặc Email OTP) để lấy mã 6 số')
    print('     Tài khoản: 064C220772')
    print('═' * 55)

    for attempt in range(3):
        otp = input(f'  OTP (lần {attempt+1}/3): ').strip()
        if not otp:
            print('  ⚠️  OTP trống, bỏ qua.')
            continue
        try:
            trading_token = _get_trading_token(jwt_token, otp)
            cache['trading_token']    = trading_token
            cache['trading_token_ts'] = time.time()
            cache['username']         = username
            cache['obtained_at']      = datetime.now(VN_TZ).strftime('%Y-%m-%d %H:%M:%S')
            _save_cache(cache)
            print()
            return jwt_token, trading_token
        except ValueError as e:
            print(f'  ❌ {e}')
            if attempt == 2:
                raise RuntimeError('Hết 3 lần thử OTP. Vui lòng chạy lại script.')

    raise RuntimeError('Không thể lấy trading token.')


def get_auth_headers(force_refresh: bool = False) -> dict:
    """Trả về dict headers đầy đủ cho tất cả DNSE API calls."""
    jwt, trading = get_session(force_refresh=force_refresh)
    return {
        'Authorization': f'Bearer {jwt}',
        'trading-token': trading,
        'Content-Type' : 'application/json',
        'Origin'       : 'https://entrade.com.vn',
        'User-Agent'   : 'Mozilla/5.0',
    }


def get_jwt_headers(force_refresh: bool = False) -> dict:
    """Chỉ JWT (không cần trading-token) — cho read-only calls."""
    jwt, _ = get_session(force_refresh=force_refresh)
    return {
        'Authorization': f'Bearer {jwt}',
        'Content-Type' : 'application/json',
        'Origin'       : 'https://entrade.com.vn',
        'User-Agent'   : 'Mozilla/5.0',
    }


# ────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DNSE Session Manager')
    parser.add_argument('--refresh', action='store_true',
                        help='Bắt buộc lấy token mới kể cả còn hạn')
    parser.add_argument('--check',  action='store_true',
                        help='Chỉ kiểm tra trạng thái token, không refresh')
    args = parser.parse_args()

    if args.check:
        cache = _load_cache()
        if not cache:
            print('❌ Chưa có session. Chạy: python3 scripts/dnse_session.py')
        else:
            jwt_ok     = _is_valid(cache, 'jwt_token',     JWT_TTL_SEC)
            trading_ok = _is_valid(cache, 'trading_token', TRADING_TTL_SEC)
            obtained   = cache.get('obtained_at', 'N/A')
            print(f'Session obtained: {obtained}')
            print(f'JWT Token    : {"✅ valid" if jwt_ok else "❌ expired"}')
            print(f'Trading Token: {"✅ valid" if trading_ok else "❌ expired"}')
        sys.exit(0)

    # Chạy interactive để lấy/làm mới session
    try:
        jwt, trading = get_session(force_refresh=args.refresh)
        print()
        print('═' * 55)
        print('✅ Session DNSE đã sẵn sàng!')
        print(f'   Tài khoản: {_load_cache().get("username","N/A")}')
        print(f'   JWT    : {jwt[:40]}...')
        print(f'   Trading: {trading[:40]}...')
        print()
        print('Các script có thể dùng session này:')
        print('  python3 scripts/dnse_deal_fetcher.py')
        print('  python3 scripts/dnse_deal_fetcher.py --date 2026-04-24')
        print('═' * 55)
    except Exception as e:
        logger.error(f'Lỗi: {e}')
        sys.exit(1)
