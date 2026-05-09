#!/usr/bin/env python3
"""
scripts/ptvol_imputer.py
========================
Tính pt_vol (khối lượng thỏa thuận) từ dữ liệu DNSE đã có trong DB.

Công thức:
    pt_vol = DNSE_1D_volume  -  SUM(volume từ 1m bars cùng ngày)

Lý do:
  - DNSE /chart-api/v2/ohlcs/stock?resolution=1D trả về total_vol (nm + pt)
    đây là dữ liệu EOD chính thức từ HOSE/HNX.
  - Nến 1m từ MASVN/DNSE = nm_vol (khớp lệnh liên tục) only.
  - Hiệu số = pt_vol (thỏa thuận).

Ưu điểm so với Yahoo Finance cũ:
  ✅ Phủ 916 mã (toàn bộ DNSE) vs ~330 mã (Yahoo)
  ✅ Không cần HTTP call → chạy <1 giây
  ✅ Chính xác hơn (nguồn từ sàn) → VWAP QC gap giảm
  ✅ Không phụ thuộc vào uptime Yahoo API

Chạy:
  python3 scripts/ptvol_imputer.py --date 2026-04-28
  python3 scripts/ptvol_imputer.py --date 2026-04-28 --dry-run
  python3 scripts/ptvol_imputer.py --date 2026-04-28 --symbols SHB,POW,GAS
"""

import os
import sys
import sqlite3
import logging
import argparse
from datetime import datetime, timezone, timedelta
from typing import Optional

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
    format='%(asctime)s [%(levelname)s] [ptVol] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

VN_TZ   = timezone(timedelta(hours=7))
DB_PATH = os.environ.get('SMD_DB_PATH', os.path.join(PROJECT_ROOT, 'data', 'securities_master.db'))

CHUNK = 900  # SQLite IN() limit safe threshold


# ────────────────────────────────────────────────────────────────────────────
# CORE LOGIC
# ────────────────────────────────────────────────────────────────────────────

def _fetch_dnse_1d(conn: sqlite3.Connection, trade_date: str,
                   filter_sids: Optional[list] = None) -> dict:
    """
    Lấy DNSE 1D total volume từ stock_prices.
    DNSE 1D volume = nm_vol + pt_vol (total chính thức từ sàn).

    Returns: {security_id: dnse_1d_volume}
    """
    base = """
        SELECT security_id, volume
        FROM stock_prices
        WHERE interval = '1D'
          AND date(trade_time) = ?
          AND volume > 0
          {where_sid}
    """
    if filter_sids:
        rows = []
        for i in range(0, len(filter_sids), CHUNK):
            chunk = filter_sids[i:i + CHUNK]
            ph = ','.join('?' * len(chunk))
            rows += conn.execute(
                base.format(where_sid=f'AND security_id IN ({ph})'),
                [trade_date] + chunk,
            ).fetchall()
    else:
        rows = conn.execute(base.format(where_sid=''), [trade_date]).fetchall()

    return {r[0]: r[1] for r in rows}


def _fetch_nm_vol(conn: sqlite3.Connection, trade_date: str,
                  filter_sids: Optional[list] = None) -> dict:
    """
    Tổng khối lượng từ 1m bars = nm_vol (khớp lệnh liên tục).

    Returns: {security_id: nm_vol_sum}
    """
    base = """
        SELECT security_id, SUM(volume)
        FROM stock_prices
        WHERE interval = '1m'
          AND date(trade_time) = ?
          AND volume > 0
          {where_sid}
        GROUP BY security_id
    """
    if filter_sids:
        rows = []
        for i in range(0, len(filter_sids), CHUNK):
            chunk = filter_sids[i:i + CHUNK]
            ph = ','.join('?' * len(chunk))
            rows += conn.execute(
                base.format(where_sid=f'AND security_id IN ({ph})'),
                [trade_date] + chunk,
            ).fetchall()
    else:
        rows = conn.execute(base.format(where_sid=''), [trade_date]).fetchall()

    return {r[0]: r[1] for r in rows}


def run_impute(date_vn: str,
               filter_syms: Optional[list] = None,
               dry_run: bool = False,
               quiet: bool = False) -> dict:
    """
    Main entry point — tương thích với eod_daily_close.py.

    Tính pt_vol = DNSE_1D_vol - nm_vol_1m và cập nhật stock_prices[interval='1D'].

    Returns:
        dict: {
            'fetched'      : số mã có DNSE 1D data,
            'updated'      : số mã được update pt_vol,
            'total_pt_vol' : tổng pt_vol toàn thị trường (CP),
            'skipped'      : mã có DNSE 1D nhưng thiếu 1m bars,
            'zero_pt'      : mã tính ra pt_vol <= 0 (no block trades),
        }
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Build sid lookup nếu filter_syms được chỉ định
    filter_sids = None
    if filter_syms:
        sym_to_sid = {}
        for i in range(0, len(filter_syms), CHUNK):
            chunk = filter_syms[i:i + CHUNK]
            ph = ','.join('?' * len(chunk))
            rows = conn.execute(
                f"SELECT symbol, security_id FROM securities WHERE symbol IN ({ph})",
                chunk,
            ).fetchall()
            sym_to_sid.update({r[0]: r[1] for r in rows})
        filter_sids = list(sym_to_sid.values())

    if not quiet:
        logger.info(f"📐 pt_vol DNSE mode | date={date_vn} | dry_run={dry_run}")
        logger.info(f"   Symbols: {len(filter_sids) if filter_sids else 'ALL'}")

    # Fetch cả hai nguồn
    dnse_1d = _fetch_dnse_1d(conn, date_vn, filter_sids)
    nm_vols = _fetch_nm_vol(conn, date_vn, filter_sids)

    if not dnse_1d:
        logger.warning(f"⚠️  Không có DNSE 1D data cho ngày {date_vn}")
        conn.close()
        return {'fetched': 0, 'updated': 0, 'total_pt_vol': 0, 'skipped': 0, 'zero_pt': 0}

    if not quiet:
        logger.info(f"   DNSE 1D: {len(dnse_1d)} mã | nm_vol 1m bars: {len(nm_vols)} mã")

    # Tính pt_vol
    updates = []       # (pt_vol, security_id, date_vn)
    stats = {'fetched': len(dnse_1d), 'updated': 0, 'total_pt_vol': 0,
             'skipped': 0, 'zero_pt': 0}

    top_deals = []  # debug: top mã có pt_vol lớn

    for sid, total_vol in dnse_1d.items():
        nm_vol = nm_vols.get(sid)
        if nm_vol is None:
            stats['skipped'] += 1
            continue

        pt = max(0, total_vol - nm_vol)
        if pt == 0:
            stats['zero_pt'] += 1
            continue

        updates.append((pt, sid, date_vn))
        stats['total_pt_vol'] += pt
        top_deals.append((sid, pt, total_vol, nm_vol))

    if not quiet:
        top_deals.sort(key=lambda x: -x[1])
        logger.info(f"   Tính ra: {len(updates)} mã có pt_vol > 0 | tổng={stats['total_pt_vol']/1e6:.2f}M CP")
        if top_deals[:5]:
            # Map sid → symbol để in đẹp
            sids_to_show = [x[0] for x in top_deals[:5]]
            ph = ','.join('?' * len(sids_to_show))
            sym_map = dict(conn.execute(
                f"SELECT security_id, symbol FROM securities WHERE security_id IN ({ph})",
                sids_to_show,
            ).fetchall())
            logger.info("   Top 5 mã có pt_vol:")
            for sid, pt, total, nm in top_deals[:5]:
                sym = sym_map.get(sid, f'#{sid}')
                pt_pct = pt / total * 100 if total else 0
                logger.info(f"     {sym:<6} total={total/1e6:.2f}M nm={nm/1e6:.2f}M pt={pt/1e6:.2f}M ({pt_pct:.1f}%)")

    if dry_run:
        logger.info(f"   [DRY-RUN] Sẽ update {len(updates)} rows (không ghi DB)")
        conn.close()
        stats['updated'] = len(updates)
        return stats

    # Ghi DB theo batch
    if updates:
        BATCH = 500
        total_updated = 0
        for i in range(0, len(updates), BATCH):
            batch = updates[i:i + BATCH]
            cur = conn.executemany("""
                UPDATE stock_prices
                SET    pt_vol = ?
                WHERE  security_id = ?
                  AND  interval    = '1D'
                  AND  date(trade_time) = ?
            """, batch)
            total_updated += cur.rowcount
        conn.commit()
        stats['updated'] = total_updated
        if not quiet:
            logger.info(f"✅ pt_vol updated: {total_updated}/{len(updates)} rows")

    conn.close()
    return stats


# ────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='pt_vol Imputer — DNSE 1D mode (thay thế Yahoo Finance)'
    )
    parser.add_argument('--date',    type=str, default=None,
                        help='Ngày tính (YYYY-MM-DD). Mặc định: hôm nay VN')
    parser.add_argument('--symbols', type=str, default=None,
                        help='Lọc mã (HPG,SHB...). Mặc định: toàn bộ')
    parser.add_argument('--dry-run', action='store_true',
                        help='Xem trước, không ghi DB')
    args = parser.parse_args()

    if args.date:
        trade_date = args.date
    else:
        trade_date = datetime.now(VN_TZ).strftime('%Y-%m-%d')

    syms = [s.strip().upper() for s in args.symbols.split(',')] if args.symbols else None

    result = run_impute(
        date_vn=trade_date,
        filter_syms=syms,
        dry_run=args.dry_run,
        quiet=False,
    )

    print(f"\n📊 Kết quả pt_vol impute:")
    print(f"  Có DNSE 1D   : {result['fetched']:,} mã")
    print(f"  Đã update    : {result['updated']:,} mã")
    print(f"  pt_vol = 0   : {result['zero_pt']:,} mã (không có thỏa thuận)")
    print(f"  Thiếu 1m bars: {result['skipped']:,} mã")
    print(f"  Tổng pt_vol  : {result['total_pt_vol']/1e6:.2f}M CP")
