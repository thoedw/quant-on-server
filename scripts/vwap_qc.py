#!/usr/bin/env python3
"""
scripts/vwap_qc.py
==================
QC tự động: so sánh VWAP trong daily_vwap_summary (DB) với VWAP từ TradingView Screener.

Nguồn sự thật: TradingView Screener (dùng total_vol = nm + pt)
DB hiện tại:   daily_vwap_summary.vwap (chỉ dùng nm_vol từ DNSE 1m bars)

Gap kỳ vọng:
  - Mã ít deal (<5% pt_vol): gap < 0.5% → OK
  - Mã deal nặng (SHB, POW): gap có thể 1-3% → cần ghi nhận nhưng không flag lỗi

Chạy: python3 scripts/vwap_qc.py [--date YYYY-MM-DD] [--symbols HPG,SHB] [--threshold 2.0]
      python3 scripts/vwap_qc.py  ← chạy cho toàn bộ mã ngày hôm qua
"""

import os
import sys
import sqlite3
import logging
import argparse
from datetime import datetime, timezone, timedelta
from typing import Optional

import warnings
warnings.filterwarnings('ignore')

from tradingview_screener import Query, Column

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

_env = os.path.join(PROJECT_ROOT, '.env')
if os.path.exists(_env):
    for _line in open(_env).read().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _, _v = _line.partition('=')
            os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [VWAP-QC] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

VN_TZ   = timezone(timedelta(hours=7))
DB_PATH = os.environ.get('SMD_DB_PATH', os.path.join(PROJECT_ROOT, 'data', 'securities_master.db'))

# Ngưỡng flag
THRESHOLD_WARN  = 1.0   # % gap: cảnh báo nhẹ
THRESHOLD_ERROR = 3.0   # % gap: cần điều tra

# Batch size khi query TV (1 lần call lấy nhiều mã)
TV_BATCH_SIZE = 200     # TV Screener hỗ trợ tới 1500 mã/call


# ────────────────────────────────────────────────────────────────────────────
# DATA FETCH
# ────────────────────────────────────────────────────────────────────────────

def fetch_tv_vwap(filter_syms: Optional[list[str]] = None) -> dict[str, dict]:
    """
    Lấy VWAP (D, 1W, 1M), volume, close từ TradingView Screener.

    Returns:
        dict: {symbol: {'vwap_d': float, 'vwap_1w': float, 'vwap_1m': float,
                        'volume': int, 'close': float, 'exchange': str}}
    """
    q = Query().select(
        'name', 'close', 'volume', 'exchange',
        'VWAP',       # Daily VWAP
        'VWAP|1W',    # Weekly VWAP
        'VWAP|1M',    # Monthly VWAP
        'Value.Traded',
        'relative_volume_10d_calc',
    ).set_markets('vietnam')

    if filter_syms:
        q = q.where(Column('name').isin(filter_syms))

    q = q.limit(1500)

    try:
        (count, df) = q.get_scanner_data()
        logger.info(f'TV Screener: {count} mã trả về')
    except Exception as e:
        logger.error(f'TV Screener ERR: {e}')
        return {}

    result = {}
    for _, row in df.iterrows():
        sym = row['name']
        result[sym] = {
            'vwap_d'    : row.get('VWAP'),
            'vwap_1w'   : row.get('VWAP|1W'),
            'vwap_1m'   : row.get('VWAP|1M'),
            'volume'    : row.get('volume'),
            'close'     : row.get('close'),
            'exchange'  : row.get('exchange'),
            'rel_vol'   : row.get('relative_volume_10d_calc'),
            'value_traded': row.get('Value.Traded'),
        }
    return result


def fetch_db_vwap(conn: sqlite3.Connection, trade_date: str,
                  filter_syms: Optional[list[str]] = None) -> dict[str, dict]:
    """
    Lấy VWAP từ daily_vwap_summary trong DB cho ngày trade_date.

    Notes:
        - DB lưu giá đơn vị nghìn VND (14.75 = 14,750 đồng)
        - TV Screener trả về đơn vị VND đầy đủ (14750.0)
        - Cần scale DB × 1000 khi so sánh
    """
    CHUNK = 900
    base_sql = """
        SELECT s.symbol, s.exchange,
               dvs.vwap, dvs.vwap_std,
               dvs.cum_volume, dvs.buy_vol, dvs.sell_vol, dvs.side_cov_pct,
               dvs.session_open, dvs.session_close
        FROM daily_vwap_summary dvs
        JOIN securities s ON dvs.security_id = s.security_id
        WHERE dvs.trade_date = ? {where_sym}
        ORDER BY s.symbol
    """
    if filter_syms:
        rows = []
        for i in range(0, len(filter_syms), CHUNK):
            chunk = filter_syms[i:i + CHUNK]
            ph = ','.join('?' * len(chunk))
            rows += conn.execute(
                base_sql.format(where_sym=f'AND s.symbol IN ({ph})'),
                [trade_date] + chunk,
            ).fetchall()
    else:
        rows = conn.execute(base_sql.format(where_sym=''), [trade_date]).fetchall()

    result = {}
    for r in rows:
        sym = r[0]
        vwap_raw = r[2]
        # Phát hiện scale: nếu vwap < 1000 → đơn vị nghìn VND, cần × 1000
        scale = 1000 if vwap_raw and vwap_raw < 1000 else 1
        result[sym] = {
            'vwap'       : vwap_raw * scale if vwap_raw else None,
            'vwap_std'   : (r[3] or 0) * scale,
            'cum_volume' : r[4],
            'buy_vol'    : r[5],
            'sell_vol'   : r[6],
            'side_cov'   : r[7],
            'open'       : (r[8] or 0) * scale,
            'close'      : (r[9] or 0) * scale,
        }
    return result


def fetch_pt_vol_info(conn: sqlite3.Connection, trade_date: str,
                      filter_syms: Optional[list[str]] = None) -> dict[str, dict]:
    """Lấy pt_vol và volume từ stock_prices 1D để tính deal ratio."""
    CHUNK = 900
    base_sql = """
        SELECT s.symbol, sp.volume, COALESCE(sp.pt_vol, 0)
        FROM stock_prices sp
        JOIN securities s ON sp.security_id = s.security_id
        WHERE sp.interval = '1D' AND date(sp.trade_time) = ? {where_sym}
    """
    if filter_syms:
        rows = []
        for i in range(0, len(filter_syms), CHUNK):
            chunk = filter_syms[i:i + CHUNK]
            ph = ','.join('?' * len(chunk))
            rows += conn.execute(
                base_sql.format(where_sym=f'AND s.symbol IN ({ph})'),
                [trade_date] + chunk,
            ).fetchall()
    else:
        rows = conn.execute(base_sql.format(where_sym=''), [trade_date]).fetchall()
    return {r[0]: {'total_vol': r[1], 'pt_vol': r[2]} for r in rows}


# ────────────────────────────────────────────────────────────────────────────
# QC LOGIC
# ────────────────────────────────────────────────────────────────────────────

def run_qc(
    trade_date: str,
    filter_syms: Optional[list[str]] = None,
    threshold_warn: float = THRESHOLD_WARN,
    threshold_error: float = THRESHOLD_ERROR,
    quiet: bool = False,
) -> dict:
    """
    Chạy QC VWAP: so sánh DB vs TradingView Screener.

    Returns:
        dict: {'ok': int, 'warn': int, 'error': int, 'missing': int,
               'details': list[dict]}
    """
    conn = sqlite3.connect(DB_PATH)

    logger.info(f'📐 VWAP QC | date={trade_date} | threshold warn={threshold_warn}% error={threshold_error}%')

    # Fetch từ TV Screener (chỉ lấy mã cần filter)
    tv_data  = fetch_tv_vwap(filter_syms)
    db_data  = fetch_db_vwap(conn, trade_date, filter_syms)
    pt_data  = fetch_pt_vol_info(conn, trade_date, filter_syms)
    conn.close()

    if not db_data:
        logger.warning(f'  Không có dữ liệu trong daily_vwap_summary cho {trade_date}')
        return {'ok': 0, 'warn': 0, 'error': 0, 'missing': 0, 'details': []}

    stats = {'ok': 0, 'warn': 0, 'error': 0, 'missing': 0, 'details': []}
    rows = []

    for sym, db_row in db_data.items():
        tv_row = tv_data.get(sym)
        pt_row = pt_data.get(sym, {})

        db_vwap = db_row.get('vwap')
        tv_vwap = tv_row.get('vwap_d') if tv_row else None

        if db_vwap is None or tv_vwap is None:
            stats['missing'] += 1
            rows.append({
                'sym': sym, 'status': '❓', 'db_vwap': db_vwap, 'tv_vwap': tv_vwap,
                'gap_pct': None, 'pt_pct': None, 'tv_1w': None, 'tv_1m': None,
            })
            continue

        gap_pct = (db_vwap - tv_vwap) / tv_vwap * 100

        # Deal ratio
        total_vol = pt_row.get('total_vol', 0) or 0
        pt_vol    = pt_row.get('pt_vol', 0) or 0
        pt_pct    = pt_vol / total_vol * 100 if total_vol > 0 else 0

        # Status
        abs_gap = abs(gap_pct)
        if abs_gap >= threshold_error:
            status = '🔴'
            stats['error'] += 1
        elif abs_gap >= threshold_warn:
            status = '🟡'
            stats['warn'] += 1
        else:
            status = '✅'
            stats['ok'] += 1

        rows.append({
            'sym'    : sym,
            'status' : status,
            'db_vwap': db_vwap,
            'tv_vwap': tv_vwap,
            'gap_pct': gap_pct,
            'pt_pct' : pt_pct,
            'tv_1w'  : tv_row.get('vwap_1w') if tv_row else None,
            'tv_1m'  : tv_row.get('vwap_1m') if tv_row else None,
            'tv_vol' : tv_row.get('volume') if tv_row else None,
            'db_vol' : db_row.get('cum_volume'),
            'side_cov': db_row.get('side_cov'),
            'rel_vol' : tv_row.get('rel_vol') if tv_row else None,
        })

    stats['details'] = rows

    # Print report
    if not quiet:
        _print_report(rows, stats, trade_date, threshold_warn, threshold_error)

    return stats


def _print_report(rows: list, stats: dict, trade_date: str,
                  threshold_warn: float, threshold_error: float) -> None:
    """In bảng QC ra stdout."""
    # Sắp xếp: lỗi → cảnh báo → OK, nội bộ sort theo |gap_pct| giảm dần
    def sort_key(r):
        prty = {'🔴': 0, '🟡': 1, '✅': 2, '❓': 3}
        abs_gap = abs(r['gap_pct']) if r['gap_pct'] is not None else -1
        return (prty.get(r['status'], 4), -abs_gap)

    rows_sorted = sorted(rows, key=sort_key)

    logger.info(f'\n{"="*82}')
    logger.info(f'  📐 VWAP QC REPORT — {trade_date}')
    logger.info(f'  Warn threshold: >{threshold_warn}%  |  Error threshold: >{threshold_error}%')
    logger.info(f'{"="*82}')
    logger.info(
        f'  {"Mã":6} {"DB_VWAP":>9} {"TV_VWAP":>9} {"Gap%":>6} '
        f'{"ptDeal%":>8} {"VWAP1W":>9} {"VWAP1M":>9} {"SideCov":>8}  St'
    )
    logger.info(f'  {"-"*78}')

    for r in rows_sorted:
        if r['gap_pct'] is None:
            logger.info(f'  {r["sym"]:6} {str(r["db_vwap"] or "N/A"):>9} {str(r["tv_vwap"] or "N/A"):>9}  ❓ missing')
            continue

        vw1 = f'{r["tv_1w"]:,.0f}' if r['tv_1w'] else 'N/A'
        vm1 = f'{r["tv_1m"]:,.0f}' if r['tv_1m'] else 'N/A'
        sc  = f'{r["side_cov"]:.0f}%' if r['side_cov'] is not None else 'N/A'

        logger.info(
            f'  {r["sym"]:6} {r["db_vwap"]:>9,.0f} {r["tv_vwap"]:>9,.0f} '
            f'{r["gap_pct"]:>+5.2f}% {r["pt_pct"]:>7.1f}% '
            f'{vw1:>9} {vm1:>9} {sc:>8}  {r["status"]}'
        )

    logger.info(f'{"="*82}')
    total = stats['ok'] + stats['warn'] + stats['error'] + stats['missing']
    logger.info(
        f'  Kết quả: {stats["ok"]} ✅ OK | {stats["warn"]} 🟡 WARN | '
        f'{stats["error"]} 🔴 ERROR | {stats["missing"]} ❓ MISSING | Total={total}'
    )
    logger.info(f'{"="*82}\n')


# ────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='VWAP QC — DB vs TradingView Screener')
    parser.add_argument('--date',      type=str, default=None,
                        help='Ngày QC (YYYY-MM-DD). Mặc định: hôm qua VN')
    parser.add_argument('--symbols',   type=str, default=None,
                        help='Lọc mã (HPG,SHB...). Mặc định: toàn bộ mã có trong DB ngày đó')
    parser.add_argument('--threshold-warn',  type=float, default=THRESHOLD_WARN,
                        help=f'% gap cảnh báo (mặc định: {THRESHOLD_WARN})')
    parser.add_argument('--threshold-error', type=float, default=THRESHOLD_ERROR,
                        help=f'% gap lỗi (mặc định: {THRESHOLD_ERROR})')
    parser.add_argument('--quiet', action='store_true',
                        help='Không in bảng chi tiết, chỉ print summary')
    args = parser.parse_args()

    # Mặc định: hôm qua (vì hôm nay chưa có daily_vwap_summary)
    if args.date:
        trade_date = args.date
    else:
        yesterday = datetime.now(VN_TZ) - timedelta(days=1)
        # Nếu hôm nay là T2 thì hôm qua là T6
        while yesterday.weekday() >= 5:
            yesterday -= timedelta(days=1)
        trade_date = yesterday.strftime('%Y-%m-%d')

    syms = [s.strip().upper() for s in args.symbols.split(',')] if args.symbols else None

    run_qc(
        trade_date=trade_date,
        filter_syms=syms,
        threshold_warn=args.threshold_warn,
        threshold_error=args.threshold_error,
        quiet=args.quiet,
    )
