#!/usr/bin/env python3
"""
scripts/dnse_deal_fetcher.py
=============================
Lấy lịch sử lệnh thỏa thuận (put-through deal orders) từ DNSE LightSpeed API.
Dùng session DNSE (JWT + trading-token) để truy cập dữ liệu deal.

Mục tiêu:
  - Thay thế/bổ sung Yahoo Finance pt_vol với dữ liệu CHÍNH XÁC từ DNSE
  - Biết THỜI ĐIỂM từng deal xảy ra trong phiên (Yahoo không có)
  - Biết GIÁ từng deal → dùng cho weighted VWAP chính xác hơn

Chạy:
  python3 scripts/dnse_deal_fetcher.py              ← ngày hôm nay/hôm qua
  python3 scripts/dnse_deal_fetcher.py --date 2026-04-24
  python3 scripts/dnse_deal_fetcher.py --date 2026-04-24 --symbols SHB,POW
  python3 scripts/dnse_deal_fetcher.py --explore    ← khám phá endpoints có sẵn
"""

import os
import sys
import json
import sqlite3
import logging
import argparse
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

for _l in (PROJECT_ROOT / '.env').read_text().splitlines():
    _l = _l.strip()
    if _l and not _l.startswith('#') and '=' in _l:
        k, _, v = _l.partition('=')
        os.environ.setdefault(k.strip(), v.strip().strip('"'))

from scripts.dnse_session import get_jwt_headers, get_auth_headers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [DealFetch] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

VN_TZ    = timezone(timedelta(hours=7))
SVC_BASE = 'https://services.entrade.com.vn'
ACC_ID   = '0001982402'
DB_PATH  = os.environ.get('SMD_DB_PATH', str(PROJECT_ROOT / 'data' / 'securities_master.db'))


# ────────────────────────────────────────────────────────────────────────────
# EXPLORE MODE — Khám phá endpoints
# ────────────────────────────────────────────────────────────────────────────

def explore_endpoints() -> None:
    """Tự động khám phá tất cả endpoints có thể dùng với trading-token."""
    logger.info('🔍 Explore mode — thử tất cả endpoints...\n')

    try:
        jwt_h    = get_jwt_headers()
        trade_h  = get_auth_headers()
    except Exception as e:
        logger.error(f'Không thể lấy session: {e}')
        return

    eps = [
        # Account
        (jwt_h,   f'{SVC_BASE}/dnse-order-service/accounts'),
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/balance'),
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/ppse'),
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/positions'),
        # Orders
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/orders'),
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/orders?status=filled'),
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/orders?status=all'),
        # Deal orders (put-through)
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/deal-orders'),
        (trade_h, f'{SVC_BASE}/dnse-order-service/deal-orders'),
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/deal-orders?status=filled'),
        # History
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/order-history'),
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/deal-history'),
        # Cash + Asset
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/assets'),
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/cash'),
        # Derivative
        (trade_h, f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/derivative/positions'),
    ]

    print(f'\n{"="*70}')
    for hdrs, url in eps:
        try:
            rv = requests.get(url, headers=hdrs, timeout=8)
            ep_short = url[len(SVC_BASE):]
            if rv.status_code == 200:
                text = rv.text[:200].replace('\n', ' ')
                print(f'  ✅ [{rv.status_code}] {ep_short}')
                print(f'       {text}')
            elif rv.status_code not in (404,):
                print(f'  🟡 [{rv.status_code}] {ep_short}: {rv.text[:80]}')
            # skip 404
        except Exception as e:
            print(f'  ERR {url[-50:]}: {str(e)[:40]}')

    print(f'{"="*70}\n')


# ────────────────────────────────────────────────────────────────────────────
# FETCH DEAL ORDERS
# ────────────────────────────────────────────────────────────────────────────

def fetch_deal_orders(trade_date: str,
                      filter_syms: Optional[list[str]] = None) -> list[dict]:
    """
    Lấy danh sách lệnh thỏa thuận từ DNSE cho ngày trade_date.

    Returns:
        list[dict]: Mỗi dict là một lệnh deal với các field:
            symbol, quantity, price, side, trade_time, order_id, ...
    """
    headers = get_auth_headers()

    # Convert trade_date → timestamp range
    dt_from = datetime.strptime(trade_date, '%Y-%m-%d').replace(
        hour=9, minute=0, tzinfo=VN_TZ
    )
    dt_to = datetime.strptime(trade_date, '%Y-%m-%d').replace(
        hour=15, minute=30, tzinfo=VN_TZ
    )

    # Thử các endpoint deal
    candidate_eps = [
        f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/deal-orders',
        f'{SVC_BASE}/dnse-order-service/deal-orders',
        f'{SVC_BASE}/dnse-order-service/accounts/{ACC_ID}/order-history',
    ]

    params_variants = [
        {'status': 'filled'},
        {'status': 'all'},
        {'fromDate': trade_date, 'toDate': trade_date},
        {'date': trade_date},
        {},
    ]

    results = []
    for ep in candidate_eps:
        for params in params_variants:
            try:
                rv = requests.get(ep, headers=headers, params=params, timeout=10)
                if rv.status_code == 200:
                    data = rv.json()
                    logger.info(f'✅ Found: {ep} params={params}')
                    logger.info(f'   Response: {json.dumps(data)[:300]}')
                    if isinstance(data, list):
                        results = data
                    elif isinstance(data, dict):
                        results = data.get('data') or data.get('items') or data.get('orders') or []
                    if results:
                        break
            except Exception as e:
                logger.debug(f'  ERR {ep}: {e}')
        if results:
            break

    if not results:
        logger.warning('Không tìm thấy deal orders từ DNSE API')
        return []

    # Filter theo symbol và date nếu cần
    deals = []
    for r in results:
        sym = r.get('symbol') or r.get('secCode') or r.get('stockCode')
        if filter_syms and sym not in filter_syms:
            continue
        deals.append(r)

    logger.info(f'Tổng {len(deals)} deal records sau filter')
    return deals


def print_deal_report(deals: list[dict], trade_date: str) -> None:
    """In báo cáo deal orders đẹp."""
    if not deals:
        print(f'\n  ⚠️  Không có deal orders nào cho ngày {trade_date}')
        return

    # Gộp theo symbol
    by_sym: dict[str, dict] = {}
    for d in deals:
        sym = d.get('symbol') or d.get('secCode') or 'UNKNOWN'
        if sym not in by_sym:
            by_sym[sym] = {'qty': 0, 'val': 0.0, 'count': 0, 'items': []}
        qty = d.get('quantity') or d.get('qty') or d.get('matchedQty') or 0
        prc = d.get('price') or d.get('matchedPrice') or d.get('executedPrice') or 0
        by_sym[sym]['qty']   += qty
        by_sym[sym]['val']   += qty * prc
        by_sym[sym]['count'] += 1
        by_sym[sym]['items'].append(d)

    print(f'\n{"="*65}')
    print(f'  📋 DEAL ORDERS REPORT — {trade_date}')
    print(f'{"="*65}')
    print(f'  {"Mã":6} {"Deals":>6} {"Total Qty":>12} {"Avg Price":>10}')
    print(f'  {"-"*50}')
    for sym, info in sorted(by_sym.items()):
        avg_p = info['val'] / info['qty'] if info['qty'] > 0 else 0
        print(f'  {sym:6} {info["count"]:>6} {info["qty"]/1e6:>10.2f}M {avg_p:>10,.0f}')
    print(f'{"="*65}')
    print(f'  Tổng: {len(by_sym)} mã, {len(deals)} lệnh deal\n')


# ────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='DNSE Deal Order Fetcher')
    parser.add_argument('--date',      type=str, default=None,
                        help='Ngày fetch (YYYY-MM-DD). Mặc định: hôm qua')
    parser.add_argument('--symbols',   type=str, default=None,
                        help='Lọc mã (HPG,SHB...)')
    parser.add_argument('--explore',   action='store_true',
                        help='Explore mode: thử tất cả endpoints')
    parser.add_argument('--refresh',   action='store_true',
                        help='Bắt buộc làm mới session (OTP mới)')
    args = parser.parse_args()

    if args.refresh:
        from scripts.dnse_session import get_session
        get_session(force_refresh=True)

    if args.explore:
        explore_endpoints()
        sys.exit(0)

    # Date
    if args.date:
        trade_date = args.date
    else:
        yesterday = datetime.now(VN_TZ) - timedelta(days=1)
        while yesterday.weekday() >= 5:
            yesterday -= timedelta(days=1)
        trade_date = yesterday.strftime('%Y-%m-%d')

    syms = [s.strip().upper() for s in args.symbols.split(',')] if args.symbols else None

    logger.info(f'Fetching deal orders | date={trade_date} | symbols={syms or "all"}')
    deals = fetch_deal_orders(trade_date, filter_syms=syms)
    print_deal_report(deals, trade_date)
