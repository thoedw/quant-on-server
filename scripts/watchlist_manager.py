#!/usr/bin/env python3
"""
scripts/watchlist_manager.py
═════════════════════════════
CLI quản lý danh sách watchlist trong SQLite DB.

Cách dùng:
  python3 scripts/watchlist_manager.py list                   # xem danh sách 'default'
  python3 scripts/watchlist_manager.py list --name vip        # xem danh sách 'vip'
  python3 scripts/watchlist_manager.py all                    # xem tất cả lists

  python3 scripts/watchlist_manager.py add HPG SHB MBB        # thêm vào 'default'
  python3 scripts/watchlist_manager.py add TCB --name vip --note "Techcombank"
  python3 scripts/watchlist_manager.py add HPG --note "Thep Hoa Phat"

  python3 scripts/watchlist_manager.py remove HPG SHB         # xóa khỏi 'default'
  python3 scripts/watchlist_manager.py remove TCB --name vip

  python3 scripts/watchlist_manager.py new vip                # tạo list mới (rỗng)
  python3 scripts/watchlist_manager.py copy default vip        # copy list

  Alias gợi ý (~/.zshrc):
    alias qwl="ssh $QSERVER 'cd ~/quant && source venv_py11/bin/activate && PYTHONPATH=. python3 scripts/watchlist_manager.py'"
    Dùng: qwl list | qwl add HPG | qwl remove SHB
"""

import os
import sys
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = Path(os.environ.get("SMD_DB_PATH", PROJECT_ROOT / "data" / "securities_master.db"))

RESET  = "\033[0m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"


# ── DB helpers ───────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlists (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            list_name TEXT    NOT NULL DEFAULT 'default',
            symbol    TEXT    NOT NULL,
            added_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
            note      TEXT,
            active    INTEGER NOT NULL DEFAULT 1,
            UNIQUE(list_name, symbol)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlists_name ON watchlists(list_name, active)")
    conn.commit()


def validate_symbols(conn: sqlite3.Connection, symbols: List[str]) -> dict:
    """Kiểm tra symbol có tồn tại trong bảng securities không."""
    result = {"valid": [], "invalid": []}
    for sym in symbols:
        row = conn.execute(
            "SELECT symbol FROM securities WHERE symbol=? AND asset_type='EQUITY'",
            (sym.upper(),)
        ).fetchone()
        if row:
            result["valid"].append(sym.upper())
        else:
            result["invalid"].append(sym.upper())
    return result


# ── Commands ─────────────────────────────────────────────────────

def cmd_list(args):
    """Hiển thị danh sách watchlist."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT w.symbol, s.exchange, w.note, w.added_at
        FROM watchlists w
        LEFT JOIN securities s ON s.symbol = w.symbol
        WHERE w.list_name = ? AND w.active = 1
        ORDER BY w.symbol
    """, (args.name,)).fetchall()
    conn.close()

    print(f"\n{BOLD}{CYAN}📋 Watchlist [{args.name}] — {len(rows)} mã{RESET}")
    print(f"  DB: {DB_PATH}")
    print(f"  {'SYM':<6}  {'Exchange':<8}  {'Note':<30}  Added")
    print(f"  {'─'*70}")
    for r in rows:
        note = r["note"] or "—"
        ex   = r["exchange"] or "?"
        print(f"  {GREEN}{r['symbol']:<6}{RESET}  {ex:<8}  {note:<30}  {r['added_at'][:10]}")
    print()


def cmd_all(args):
    """Hiển thị tất cả lists."""
    conn = get_conn()
    lists = conn.execute(
        "SELECT DISTINCT list_name FROM watchlists WHERE active=1 ORDER BY list_name"
    ).fetchall()

    for lst in lists:
        ln = lst["list_name"]
        rows = conn.execute(
            "SELECT symbol FROM watchlists WHERE list_name=? AND active=1 ORDER BY symbol",
            (ln,)
        ).fetchall()
        syms = ", ".join(r["symbol"] for r in rows)
        print(f"  {BOLD}{CYAN}[{ln}]{RESET} ({len(rows)} mã): {syms}")
    conn.close()
    print()


def cmd_add(args):
    """Thêm symbol(s) vào watchlist."""
    conn = get_conn()
    ensure_table(conn)

    symbols = [s.upper() for s in args.symbols]
    check   = validate_symbols(conn, symbols)

    if check["invalid"]:
        print(f"\n{YELLOW}⚠️  Không tìm thấy trong DB:{RESET} {check['invalid']}")
        print(f"   (kiểm tra lại tên mã — chỉ nhận EQUITY)")

    added  = []
    skipped = []
    for sym in check["valid"]:
        try:
            conn.execute("""
                INSERT INTO watchlists (list_name, symbol, added_at, note, active)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(list_name, symbol) DO UPDATE SET active=1, note=COALESCE(excluded.note, note)
            """, (args.name, sym, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), args.note))
            if conn.total_changes > 0:
                added.append(sym)
            else:
                skipped.append(sym)
        except Exception as e:
            print(f"  {RED}❌ {sym}: {e}{RESET}")

    conn.commit()
    conn.close()

    if added:
        print(f"\n{GREEN}✅ Đã thêm vào [{args.name}]:{RESET} {added}")
    if skipped:
        print(f"{YELLOW}⏭️  Đã có sẵn (skip):{RESET} {skipped}")
    print(f"   Dùng `python3 scripts/watchlist_manager.py list` để xem.\n")


def cmd_remove(args):
    """Xóa (deactivate) symbol(s) khỏi watchlist."""
    conn = get_conn()
    symbols = [s.upper() for s in args.symbols]
    removed = []

    for sym in symbols:
        cur = conn.execute(
            "UPDATE watchlists SET active=0 WHERE list_name=? AND symbol=? AND active=1",
            (args.name, sym)
        )
        if cur.rowcount > 0:
            removed.append(sym)
        else:
            print(f"  {YELLOW}⚠️  {sym} không có trong [{args.name}] (hoặc đã inactive){RESET}")

    conn.commit()
    conn.close()

    if removed:
        print(f"\n{RED}🗑️  Đã xóa khỏi [{args.name}]:{RESET} {removed}")
        print(f"   Worker sẽ tự reload watchlist sau chu kỳ tiếp theo.\n")


def cmd_new(args):
    """Tạo danh sách mới (rỗng)."""
    conn = get_conn()
    ensure_table(conn)
    existing = conn.execute(
        "SELECT COUNT(*) FROM watchlists WHERE list_name=?", (args.list_name,)
    ).fetchone()[0]
    conn.close()

    if existing:
        print(f"{YELLOW}⚠️  List [{args.list_name}] đã tồn tại.{RESET}")
    else:
        print(f"{GREEN}✅ List [{args.list_name}] đã sẵn sàng — dùng `add` để thêm mã.{RESET}")


def cmd_copy(args):
    """Copy toàn bộ symbols từ list gốc sang list đích."""
    conn = get_conn()
    ensure_table(conn)
    rows = conn.execute(
        "SELECT symbol, note FROM watchlists WHERE list_name=? AND active=1",
        (args.src,)
    ).fetchall()
    if not rows:
        print(f"{RED}❌ List [{args.src}] trống hoặc không tồn tại.{RESET}")
        conn.close()
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    copied = 0
    for r in rows:
        try:
            conn.execute("""
                INSERT INTO watchlists (list_name, symbol, added_at, note, active)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(list_name, symbol) DO UPDATE SET active=1
            """, (args.dst, r["symbol"], now, r["note"]))
            copied += 1
        except Exception as e:
            print(f"  {YELLOW}skip {r['symbol']}: {e}{RESET}")

    conn.commit()
    conn.close()
    print(f"{GREEN}✅ Đã copy {copied} mã từ [{args.src}] → [{args.dst}]{RESET}\n")


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Quản lý Watchlist trong SQLite DB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="Xem danh sách watchlist")
    p_list.add_argument("--name", default="default", help="Tên list (mặc định: default)")

    # all
    sub.add_parser("all", help="Xem tất cả lists")

    # add
    p_add = sub.add_parser("add", help="Thêm mã vào watchlist")
    p_add.add_argument("symbols", nargs="+", help="Tên mã (VD: HPG SHB MBB)")
    p_add.add_argument("--name", default="default", help="Tên list (mặc định: default)")
    p_add.add_argument("--note", default=None, help="Ghi chú")

    # remove
    p_rm = sub.add_parser("remove", help="Xóa mã khỏi watchlist")
    p_rm.add_argument("symbols", nargs="+", help="Tên mã (VD: HPG SHB)")
    p_rm.add_argument("--name", default="default", help="Tên list (mặc định: default)")

    # new
    p_new = sub.add_parser("new", help="Tạo list mới")
    p_new.add_argument("list_name", help="Tên list mới")

    # copy
    p_copy = sub.add_parser("copy", help="Copy một list sang list khác")
    p_copy.add_argument("src", help="List nguồn")
    p_copy.add_argument("dst", help="List đích")

    args = parser.parse_args()

    dispatch = {
        "list":   cmd_list,
        "all":    cmd_all,
        "add":    cmd_add,
        "remove": cmd_remove,
        "new":    cmd_new,
        "copy":   cmd_copy,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
