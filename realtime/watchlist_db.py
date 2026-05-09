"""
realtime/watchlist_db.py
────────────────────────
Module dùng chung để đọc watchlist từ SQLite DB.
Tất cả worker, script đều import hàm này thay vì hardcode.

Sử dụng:
    from realtime.watchlist_db import load_watchlist

    symbols = load_watchlist()                    # list 'default'
    symbols = load_watchlist(list_name='vip')     # list khác
    symbols = load_watchlist(db_path='/path/...')  # DB tùy chỉnh
"""

import os
import sqlite3
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB   = os.getenv(
    "SMD_DB_PATH",
    os.path.join(_PROJECT_ROOT, "data", "securities_master.db"),
)

_SQL_LOAD = """
    SELECT w.symbol
    FROM   watchlists w
    LEFT JOIN securities s ON s.symbol = w.symbol
    WHERE  w.list_name = ?
      AND  w.active    = 1
      AND  (s.asset_type IN ('EQUITY','INDEX') OR s.symbol IS NULL)
    ORDER BY w.symbol
"""


def load_watchlist(
    list_name: str = "default",
    db_path: Optional[str] = None,
) -> List[str]:
    """
    Đọc danh sách mã cổ phiếu từ bảng watchlists.

    Args:
        list_name: tên danh sách (default = 'default')
        db_path:   đường dẫn DB (mặc định theo SMD_DB_PATH env)

    Returns:
        List[str]: danh sách symbol đang active, ví dụ ['ACB','HPG',...]
        Trả về [] nếu DB không tồn tại hoặc bảng chưa có.
    """
    path = db_path or _DEFAULT_DB
    try:
        conn = sqlite3.connect(path, check_same_thread=False)
        rows = conn.execute(_SQL_LOAD, (list_name,)).fetchall()
        conn.close()
        symbols = [r[0] for r in rows]
        if symbols:
            logger.debug(f"watchlist[{list_name}]: {len(symbols)} mã từ DB")
        else:
            logger.warning(f"watchlist[{list_name}]: không tìm thấy mã nào trong DB")
        return symbols
    except Exception as e:
        logger.error(f"load_watchlist lỗi: {e}")
        return []


def list_names(db_path: Optional[str] = None) -> List[str]:
    """Trả về tất cả list_name đang có trong bảng watchlists."""
    path = db_path or _DEFAULT_DB
    try:
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT DISTINCT list_name FROM watchlists WHERE active=1 ORDER BY list_name"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception as e:
        logger.error(f"list_names lỗi: {e}")
        return []
