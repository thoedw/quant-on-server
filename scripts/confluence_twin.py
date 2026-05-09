#!/usr/bin/env python3
"""
confluence_twin.py — Confluence Digital Twin
══════════════════════════════════════════════════════════════════
Pull toàn bộ Confluence (spaces, pages, body, attachments, comments)
về SQLite local. Chỉ chạy được khi ở mạng VP (10.179.104.112).

Nguồn  : http://10.179.104.112:8090  (Confluence Server)
Auth   : Basic Auth (vnjvtuan / TuanHo@2023)
Storage: data/confluence_twin.db + data/confluence_attachments/

Usage:
    python3 scripts/confluence_twin.py --full             # Full sync
    python3 scripts/confluence_twin.py                    # Incremental
    python3 scripts/confluence_twin.py --space SD         # 1 space
    python3 scripts/confluence_twin.py --status           # Thống kê
    python3 scripts/confluence_twin.py --loop 60          # Mỗi 60 phút
"""

import sqlite3, requests, logging, sys, time, argparse, os, hashlib
from datetime import datetime, timedelta
from pathlib import Path

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════
# Khi chạy trên w530: dùng SSH tunnel qua Mac (confluence_tunnel.sh)
# Khi chạy trên Mac trực tiếp: reach thẳng 10.179.104.112
import socket as _socket
_ON_W530 = _socket.gethostname() == "w530-bos3-worker"
CONF_URL  = "http://localhost:18090" if _ON_W530 else "http://10.179.104.112:8090"
CONF_USER = "vnjvtuan"
CONF_PASS = "TuanHo@2023"
AUTH      = (CONF_USER, CONF_PASS)

DB_PATH = Path.home() / "twins" / "confluence" / "confluence_twin.db"
ATT_DIR = Path.home() / "twins" / "confluence" / "attachments"

PAGE_SIZE    = 25   # nhỏ hơn để tránh timeout trên page lớn
MAX_RETRIES  = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CONF-TWIN] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger()


# ══════════════════════════════════════════════════════════════════
# DATABASE SCHEMA
# ══════════════════════════════════════════════════════════════════
SCHEMA = """
CREATE TABLE IF NOT EXISTS spaces (
    key         TEXT PRIMARY KEY,
    name        TEXT,
    type        TEXT,
    status      TEXT,
    description TEXT,
    homepage_id TEXT,
    url         TEXT,
    synced_at   TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    id            TEXT PRIMARY KEY,
    space_key     TEXT NOT NULL,
    title         TEXT,
    parent_id     TEXT,
    version       INTEGER,
    author        TEXT,
    created       TEXT,
    updated       TEXT,
    body_storage  TEXT,   -- Confluence storage format (XML-like)
    body_view     TEXT,   -- Rendered HTML
    url           TEXT,
    labels        TEXT,   -- JSON
    synced_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_pages_space   ON pages(space_key);
CREATE INDEX IF NOT EXISTS idx_pages_updated ON pages(updated);
CREATE INDEX IF NOT EXISTS idx_pages_parent  ON pages(parent_id);

CREATE TABLE IF NOT EXISTS page_comments (
    id        TEXT PRIMARY KEY,
    page_id   TEXT NOT NULL,
    author    TEXT,
    body      TEXT,
    created   TEXT,
    updated   TEXT
);
CREATE INDEX IF NOT EXISTS idx_pc_page ON page_comments(page_id);

CREATE TABLE IF NOT EXISTS attachments (
    id            TEXT PRIMARY KEY,
    page_id       TEXT NOT NULL,
    space_key     TEXT,
    filename      TEXT,
    size_bytes    INTEGER,
    media_type    TEXT,
    author        TEXT,
    created       TEXT,
    download_url  TEXT,
    local_path    TEXT,
    downloaded    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_att_page ON attachments(page_id);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ══════════════════════════════════════════════════════════════════
# API HELPERS
# ══════════════════════════════════════════════════════════════════
SESSION = requests.Session()
SESSION.auth    = AUTH
SESSION.headers.update({"Content-Type": "application/json"})


def api_get(path, params=None):
    url = f"{CONF_URL}{path}" if path.startswith("/") else path
    for attempt in range(MAX_RETRIES):
        try:
            r = SESSION.get(url, params=params, timeout=120)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 10)))
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.ReadTimeout:
            wait = 15 * (attempt + 1)
            log.warning(f"Timeout {attempt+1}/{MAX_RETRIES} — retry sau {wait}s")
            if attempt == MAX_RETRIES - 1:
                log.error(f"Bỏ qua {url} sau {MAX_RETRIES} lần timeout")
                return {}
            time.sleep(wait)
        except requests.RequestException as e:
            if attempt == MAX_RETRIES - 1:
                log.error(f"API error {url}: {e}")
                return {}
            log.warning(f"API retry {attempt+1}: {e}")
            time.sleep(5)
    return {}


def paginate(path, params=None, key="results"):
    """Tự động paginate qua tất cả kết quả."""
    params = dict(params or {})
    params.setdefault("limit", PAGE_SIZE)
    params["start"] = 0
    while True:
        data    = api_get(path, params)
        results = data.get(key, [])
        yield from results
        if "next" not in data.get("_links", {}):
            break
        params["start"] += len(results)
        if not results:
            break
        time.sleep(0.2)


# ══════════════════════════════════════════════════════════════════
# SYNC: SPACES
# ══════════════════════════════════════════════════════════════════
def sync_spaces(conn, space_filter=None):
    log.info("Syncing spaces...")
    now   = datetime.utcnow().isoformat()
    rows  = []
    for s in paginate("/rest/api/space", {"expand": "description.plain,homepage"}):
        if space_filter and s["key"] != space_filter.upper():
            continue
        rows.append((
            s["key"], s.get("name",""),
            s.get("type",""), s.get("status",""),
            s.get("description",{}).get("plain",{}).get("value","")[:500],
            s.get("homepage",{}).get("id","") if s.get("homepage") else "",
            s.get("_links",{}).get("webui",""), now
        ))
    conn.executemany("INSERT OR REPLACE INTO spaces VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    log.info(f"  ✅ {len(rows)} spaces synced")
    return [r[0] for r in rows]


# ══════════════════════════════════════════════════════════════════
# SYNC: PAGES
# ══════════════════════════════════════════════════════════════════
def sync_pages_for_space(conn, space_key, since=None):
    """
    2-pass sync:
      Pass 1 — metadata nhẹ (title, version, ancestors, labels) cho tất cả pages
      Pass 2 — body.storage từng page một (tránh timeout khi fetch nhiều pages lớn)
    """
    n_pages = n_comments = n_att = 0

    # ── Pass 1: metadata (không có body — nhanh) ──────────────────
    meta_params = {
        "spaceKey": space_key,
        "expand":   "version,ancestors,metadata.labels",
        "limit":    PAGE_SIZE,
        "type":     "page",
    }
    page_ids = []
    for page in paginate("/rest/api/content", meta_params):
        if page.get("type") != "page":
            continue

        updated = page.get("version", {}).get("when", "")
        if since and updated and updated[:16] < since[:16]:
            existing = conn.execute("SELECT version FROM pages WHERE id=?", (page["id"],)).fetchone()
            if existing and existing[0] == page.get("version", {}).get("number", 0):
                continue

        author = page.get("version", {}).get("by", {}).get("displayName", "")
        labels = [lbl.get("name", "") for lbl in
                  page.get("metadata", {}).get("labels", {}).get("results", [])]
        ancestors = page.get("ancestors", [])
        parent_id = ancestors[-1].get("id", "") if ancestors else ""

        # Insert metadata row (body = empty for now)
        conn.execute(
            "INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                page["id"], space_key, page.get("title", ""), parent_id,
                page.get("version", {}).get("number", 0), author, "",
                updated, "", "",
                page.get("_links", {}).get("webui", ""),
                str(labels), datetime.utcnow().isoformat(),
            )
        )
        page_ids.append(page["id"])
        n_pages += 1

    conn.commit()
    log.info(f"    [{space_key}] pass1 metadata: {n_pages} pages")

    # ── Pass 2: body + comments + attachments từng page ──────────
    for i, pid in enumerate(page_ids):
        try:
            data = api_get(f"/rest/api/content/{pid}", {"expand": "body.storage,body.view"})
            conn.execute(
                "UPDATE pages SET body_storage=?, body_view=? WHERE id=?",
                (
                    data.get("body", {}).get("storage", {}).get("value", ""),
                    data.get("body", {}).get("view",    {}).get("value", ""),
                    pid,
                )
            )
        except Exception as e:
            log.warning(f"    body skip {pid}: {e}")

        n_comments += _sync_page_comments(conn, pid)
        n_att      += _sync_page_attachments(conn, pid, space_key)

        if (i + 1) % 100 == 0:
            conn.commit()
            log.info(f"    [{space_key}] pass2: {i+1}/{len(page_ids)} | comments={n_comments} att={n_att}")

    conn.commit()
    return n_pages, n_comments, n_att


def _sync_page_comments(conn, page_id):
    rows = []
    for c in paginate(f"/rest/api/content/{page_id}/child/comment",
                      {"expand": "body.view,version,descendants.comment.body.view,descendants.comment.version",
                       "depth": "all"}):
        rows.append((
            c["id"], page_id,
            c.get("version",{}).get("by",{}).get("displayName",""),
            c.get("body",{}).get("view",{}).get("value",""),
            c.get("version",{}).get("when",""),
            c.get("version",{}).get("when",""),
        ))
        # Collect nested replies from descendants
        for rc in c.get("descendants",{}).get("comment",{}).get("results",[]):
            rows.append((
                rc["id"], page_id,
                rc.get("version",{}).get("by",{}).get("displayName",""),
                rc.get("body",{}).get("view",{}).get("value",""),
                rc.get("version",{}).get("when",""),
                rc.get("version",{}).get("when",""),
            ))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO page_comments VALUES (?,?,?,?,?,?)", rows
        )
    return len(rows)


def _sync_page_attachments(conn, page_id, space_key):
    rows = []
    for a in paginate(f"/rest/api/content/{page_id}/child/attachment",
                      {"expand": "version"}):
        ext   = a.get("extensions",{})
        rows.append((
            a["id"], page_id, space_key,
            a.get("title",""),
            ext.get("fileSize",0),
            ext.get("mediaType",""),
            a.get("version",{}).get("by",{}).get("displayName",""),
            a.get("version",{}).get("when",""),
            CONF_URL + a.get("_links",{}).get("download",""),
            None, 0
        ))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO attachments VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
        )
    return len(rows)


# ══════════════════════════════════════════════════════════════════
# DOWNLOAD ATTACHMENTS (optional)
# ══════════════════════════════════════════════════════════════════
SKIP_EXTS = {".exe", ".rar", ".dmp"}


def download_attachments(conn, space_key=None):
    """Download all attachments not yet downloaded, skip .exe/.rar/.dmp."""
    where = "downloaded=0 AND size_bytes > 0"
    params_list = []
    if space_key:
        where += " AND space_key=?"
        params_list = [space_key]

    rows = conn.execute(
        f"SELECT id, space_key, filename, size_bytes, download_url FROM attachments WHERE {where}",
        params_list
    ).fetchall()

    # Filter out skipped extensions
    rows = [(aid, sk, fn, sz, url) for aid, sk, fn, sz, url in rows
            if Path(fn).suffix.lower() not in SKIP_EXTS]

    total_mb = sum(sz for _, _, _, sz, _ in rows) / 1024 / 1024
    log.info(f"📎 Downloading {len(rows)} attachments ({total_mb:.0f} MB total)...")
    done = 0
    for att_id, sk, filename, size, url in rows:
        safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in filename)
        dest_dir  = ATT_DIR / (sk or "unknown")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest      = dest_dir / f"{att_id}_{safe_name}"
        if dest.exists():
            conn.execute("UPDATE attachments SET downloaded=1, local_path=? WHERE id=?", (str(dest), att_id))
            done += 1
            continue
        try:
            r = SESSION.get(url, timeout=120, stream=True)
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
            conn.execute(
                "UPDATE attachments SET downloaded=1, local_path=? WHERE id=?",
                (str(dest), att_id)
            )
            done += 1
            if done % 20 == 0:
                conn.commit()
                log.info(f"  Downloaded {done}/{len(rows)} ({size/1024/1024:.1f} MB latest)...")
        except Exception as e:
            log.warning(f"  Skip {filename}: {e}")
    conn.commit()
    log.info(f"  ✅ Downloaded {done} attachments")


# ══════════════════════════════════════════════════════════════════
# SYNC STATE
# ══════════════════════════════════════════════════════════════════
def get_last_sync(conn, key="last_sync"):
    row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_last_sync(conn, key="last_sync", value=None):
    value = value or datetime.utcnow().isoformat()
    conn.execute("INSERT OR REPLACE INTO sync_state VALUES (?,?)", (key, value))
    conn.commit()


# ══════════════════════════════════════════════════════════════════
# STATUS
# ══════════════════════════════════════════════════════════════════
def print_status(conn):
    log.info("═" * 60)
    log.info("CONFLUENCE TWIN — Status")
    log.info("═" * 60)
    n_sp  = conn.execute("SELECT COUNT(*) FROM spaces").fetchone()[0]
    n_pg  = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    n_cm  = conn.execute("SELECT COUNT(*) FROM page_comments").fetchone()[0]
    n_att = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
    n_dl  = conn.execute("SELECT COUNT(*) FROM attachments WHERE downloaded=1").fetchone()[0]
    last  = get_last_sync(conn)
    log.info(f"  Spaces      : {n_sp:,}")
    log.info(f"  Pages       : {n_pg:,}")
    log.info(f"  Comments    : {n_cm:,}")
    log.info(f"  Attachments : {n_att:,}  (downloaded: {n_dl:,})")
    log.info(f"  Last sync   : {last or 'never'}")
    log.info(f"  DB size     : {DB_PATH.stat().st_size/1024/1024:.1f} MB" if DB_PATH.exists() else "  DB: not created yet")

    log.info("\n  Pages per space:")
    rows = conn.execute("""
        SELECT s.key, s.name, COUNT(p.id) as n, MAX(p.updated) as latest
        FROM spaces s LEFT JOIN pages p ON s.key=p.space_key
        GROUP BY s.key ORDER BY n DESC
    """).fetchall()
    for key, name, n, latest in rows:
        log.info(f"    [{key:15s}]  {n:4,} pages  {name[:30]}")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def run(full=False, space=None, download=False):
    # Kiểm tra connectivity
    try:
        r = SESSION.get(f"{CONF_URL}/rest/api/space?limit=1", timeout=5)
        r.raise_for_status()
    except Exception as e:
        log.error(f"❌ Không kết nối được Confluence ({CONF_URL}): {e}")
        log.error("   Confluence chỉ accessible từ mạng VP (10.179.104.x)")
        return

    conn = get_db()
    t0   = time.time()
    since = None if full else get_last_sync(conn)

    log.info("═" * 60)
    log.info(f"Confluence Twin  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Mode : {'FULL' if full else f'INCREMENTAL (since {since})'}")
    log.info("═" * 60)

    space_keys = sync_spaces(conn, space)

    total_pages = total_comments = total_att = 0
    for sk in space_keys:
        log.info(f"  Space [{sk}]...")
        pg, cm, att = sync_pages_for_space(conn, sk, since=since)
        log.info(f"    → {pg} pages  {cm} comments  {att} attachments")
        total_pages    += pg
        total_comments += cm
        total_att      += att

    set_last_sync(conn)

    # Always download after sync (skip .exe/.rar/.dmp, no size limit)
    download_attachments(conn, space_key=space)

    elapsed = round(time.time() - t0, 1)
    log.info("═" * 60)
    log.info(f"✅ Sync xong: {total_pages:,} pages | {total_comments:,} comments | {total_att:,} att | {elapsed}s")
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="Confluence Digital Twin")
    ap.add_argument("--full",     action="store_true", help="Full sync")
    ap.add_argument("--space",    type=str,            help="Chỉ sync 1 space key")
    ap.add_argument("--status",   action="store_true", help="Thống kê")
    ap.add_argument("--download", action="store_true", help="Download attachments")
    ap.add_argument("--loop",     type=int, metavar="MIN")
    args = ap.parse_args()

    if args.status:
        print_status(get_db()); return

    if args.loop:
        log.info(f"🔄 Loop mode: mỗi {args.loop} phút")
        while True:
            try:
                run(full=False, space=args.space, download=args.download)
            except Exception as e:
                log.error(f"Lỗi: {e}", exc_info=True)
            log.info(f"💤 Ngủ {args.loop} phút...")
            time.sleep(args.loop * 60)
    else:
        run(full=args.full, space=args.space, download=args.download)


if __name__ == "__main__":
    main()
