#!/usr/bin/env python3
"""
jira_twin.py — Jira Digital Twin
══════════════════════════════════════════════════════════════════
Pull toàn bộ Jira (projects, issues, comments, attachments, worklogs)
về SQLite local. Hỗ trợ full sync + incremental sync.

Nguồn  : https://support.lottehpt.com.vn:8843   (Jira 7.11.2 Server)
Auth   : Basic Auth (vnjvtuan / TuanHo@2023)
Storage: data/jira_twin.db

Usage:
    python3 scripts/jira_twin.py --full            # Full sync tất cả
    python3 scripts/jira_twin.py                   # Incremental (kể từ lần cuối)
    python3 scripts/jira_twin.py --project LOT     # Chỉ 1 project
    python3 scripts/jira_twin.py --status          # Xem thống kê
    python3 scripts/jira_twin.py --loop 30         # Lặp mỗi 30 phút
"""

import sqlite3, requests, logging, sys, time, argparse, json, os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════
JIRA_URL   = "https://support.lottehpt.com.vn:8843"
JIRA_USER  = "vnjvtuan"
JIRA_PASS  = "TuanHo@2023"
AUTH       = (JIRA_USER, JIRA_PASS)

DB_PATH = Path.home() / "twins" / "jira" / "jira_twin.db"
ATT_DIR = Path.home() / "twins" / "jira" / "attachments"

PAGE_SIZE    = 100     # issues per API call
MAX_RETRIES  = 3
RETRY_DELAY  = 5       # seconds between retries

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [JIRA-TWIN] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger()


# ══════════════════════════════════════════════════════════════════
# DATABASE SCHEMA
# ══════════════════════════════════════════════════════════════════
SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    key         TEXT PRIMARY KEY,
    name        TEXT,
    description TEXT,
    lead        TEXT,
    url         TEXT,
    synced_at   TEXT
);

CREATE TABLE IF NOT EXISTS issues (
    key           TEXT PRIMARY KEY,
    id            TEXT,
    project_key   TEXT,
    issue_type    TEXT,
    summary       TEXT,
    description   TEXT,
    status        TEXT,
    priority      TEXT,
    assignee      TEXT,
    reporter      TEXT,
    labels        TEXT,   -- JSON array
    components    TEXT,   -- JSON array
    fix_versions  TEXT,   -- JSON array
    parent_key    TEXT,
    resolution    TEXT,
    environment   TEXT,
    created       TEXT,
    updated       TEXT,
    resolved      TEXT,
    story_points  REAL,
    synced_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_issues_project ON issues(project_key);
CREATE INDEX IF NOT EXISTS idx_issues_updated ON issues(updated);
CREATE INDEX IF NOT EXISTS idx_issues_status  ON issues(status);

CREATE TABLE IF NOT EXISTS comments (
    id         TEXT PRIMARY KEY,
    issue_key  TEXT NOT NULL,
    author     TEXT,
    body       TEXT,
    created    TEXT,
    updated    TEXT,
    FOREIGN KEY(issue_key) REFERENCES issues(key)
);
CREATE INDEX IF NOT EXISTS idx_comments_issue ON comments(issue_key);

CREATE TABLE IF NOT EXISTS attachments (
    id          TEXT PRIMARY KEY,
    issue_key   TEXT NOT NULL,
    filename    TEXT,
    size_bytes  INTEGER,
    mime_type   TEXT,
    author      TEXT,
    created     TEXT,
    url         TEXT,
    local_path  TEXT,
    downloaded  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_att_issue ON attachments(issue_key);

CREATE TABLE IF NOT EXISTS worklogs (
    id               TEXT PRIMARY KEY,
    issue_key        TEXT NOT NULL,
    author           TEXT,
    time_spent_sec   INTEGER,
    started          TEXT,
    comment          TEXT
);
CREATE INDEX IF NOT EXISTS idx_wl_issue ON worklogs(issue_key);

CREATE TABLE IF NOT EXISTS sync_state (
    key        TEXT PRIMARY KEY,
    value      TEXT
);
"""


def get_db(path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ══════════════════════════════════════════════════════════════════
# API HELPERS
# ══════════════════════════════════════════════════════════════════
SESSION = requests.Session()
SESSION.auth    = AUTH
SESSION.verify  = False
SESSION.headers.update({"Content-Type": "application/json"})


def api_get(path, params=None, retries=MAX_RETRIES):
    url = f"{JIRA_URL}{path}"
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 10))
                log.warning(f"Rate limited — chờ {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            log.warning(f"API error (retry {attempt+1}/{retries}): {e}")
            time.sleep(RETRY_DELAY)
    return {}


# ══════════════════════════════════════════════════════════════════
# SYNC: PROJECTS
# ══════════════════════════════════════════════════════════════════
def sync_projects(conn):
    log.info("Syncing projects...")
    data = api_get("/rest/api/2/project")
    now  = datetime.utcnow().isoformat()
    rows = []
    for p in data:
        rows.append((
            p["key"], p.get("name",""),
            p.get("description",""),
            p.get("lead",{}).get("displayName","") if p.get("lead") else "",
            p.get("self",""), now
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO projects VALUES (?,?,?,?,?,?)", rows
    )
    conn.commit()
    log.info(f"  ✅ {len(rows)} projects synced")
    return [r[0] for r in rows]


# ══════════════════════════════════════════════════════════════════
# SYNC: ISSUES (incremental hoặc full)
# ══════════════════════════════════════════════════════════════════
ISSUE_FIELDS = (
    "summary,description,status,priority,assignee,reporter,"
    "issuetype,labels,components,fixVersions,parent,resolution,"
    "environment,created,updated,resolutiondate,story_points,"
    "customfield_10016,comment,attachment,worklog,project"
)


def _parse_issue(issue):
    f = issue["fields"]
    def _name(obj): return obj.get("displayName","") if obj else ""
    def _jlist(lst): return json.dumps([x.get("name","") for x in (lst or [])])

    story_points = (
        f.get("customfield_10016") or
        f.get("story_points") or
        f.get("customfield_10028") or
        None
    )

    return {
        "key":          issue["key"],
        "id":           issue["id"],
        "project_key":  f.get("project",{}).get("key",""),
        "issue_type":   f.get("issuetype",{}).get("name",""),
        "summary":      f.get("summary",""),
        "description":  f.get("description",""),
        "status":       f.get("status",{}).get("name",""),
        "priority":     f.get("priority",{}).get("name","") if f.get("priority") else "",
        "assignee":     _name(f.get("assignee")),
        "reporter":     _name(f.get("reporter")),
        "labels":       json.dumps(f.get("labels",[])),
        "components":   _jlist(f.get("components",[])),
        "fix_versions": _jlist(f.get("fixVersions",[])),
        "parent_key":   f.get("parent",{}).get("key","") if f.get("parent") else "",
        "resolution":   f.get("resolution",{}).get("name","") if f.get("resolution") else "",
        "environment":  f.get("environment",""),
        "created":      f.get("created",""),
        "updated":      f.get("updated",""),
        "resolved":     f.get("resolutiondate",""),
        "story_points": float(story_points) if story_points else None,
        "synced_at":    datetime.utcnow().isoformat(),
    }


def _upsert_issue(conn, issue_data):
    cols = list(issue_data.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO issues ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
        list(issue_data.values())
    )


def _sync_comments(conn, issue_key, fields):
    comment_data = fields.get("comment",{})
    comments = comment_data.get("comments",[])
    total    = comment_data.get("total",0)
    if total > len(comments):
        # Fetch remaining comments
        result = api_get(f"/rest/api/2/issue/{issue_key}/comment",
                         params={"maxResults": 500})
        comments = result.get("comments", comments)
    rows = []
    for c in comments:
        rows.append((
            c["id"], issue_key,
            c.get("author",{}).get("displayName",""),
            c.get("body",""), c.get("created",""), c.get("updated","")
        ))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO comments VALUES (?,?,?,?,?,?)", rows
        )
    return len(rows)


def _sync_attachments(conn, issue_key, fields):
    attachments = fields.get("attachment",[])
    rows = []
    for a in attachments:
        rows.append((
            a["id"], issue_key,
            a.get("filename",""),
            a.get("size",0),
            a.get("mimeType",""),
            a.get("author",{}).get("displayName",""),
            a.get("created",""),
            a.get("content",""),
            None, 0
        ))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO attachments VALUES (?,?,?,?,?,?,?,?,?,?)", rows
        )
    return len(rows)


def _sync_worklogs(conn, issue_key, fields):
    wl_data   = fields.get("worklog",{})
    worklogs  = wl_data.get("worklogs",[])
    total     = wl_data.get("total",0)
    if total > len(worklogs):
        result = api_get(f"/rest/api/2/issue/{issue_key}/worklog")
        worklogs = result.get("worklogs", worklogs)
    rows = []
    for w in worklogs:
        rows.append((
            w["id"], issue_key,
            w.get("author",{}).get("displayName",""),
            w.get("timeSpentSeconds",0),
            w.get("started",""),
            w.get("comment","")
        ))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO worklogs VALUES (?,?,?,?,?,?)", rows
        )
    return len(rows)


def sync_issues(conn, jql, label=""):
    """Sync issues theo JQL. Trả về số issues đã sync."""
    start       = 0
    total_synced = 0

    while True:
        data = api_get("/rest/api/2/search", params={
            "jql":        jql,
            "startAt":    start,
            "maxResults": PAGE_SIZE,
            "fields":     ISSUE_FIELDS,
        })
        issues = data.get("issues", [])
        total  = data.get("total", 0)

        if not issues:
            break

        n_comments = n_att = n_wl = 0
        for issue in issues:
            f = issue["fields"]
            issue_data = _parse_issue(issue)
            _upsert_issue(conn, issue_data)
            n_comments += _sync_comments(conn, issue["key"], f)
            n_att      += _sync_attachments(conn, issue["key"], f)
            n_wl       += _sync_worklogs(conn, issue["key"], f)

        conn.commit()
        total_synced += len(issues)
        pct = total_synced / total * 100 if total > 0 else 100
        log.info(
            f"  {label} {total_synced:,}/{total:,} ({pct:.0f}%) "
            f"| +{len(issues)} issues  {n_comments} comments  {n_att} att  {n_wl} worklogs"
        )

        if total_synced >= total:
            break
        start += PAGE_SIZE
        time.sleep(0.3)  # rate limit courtesy

    return total_synced


# ══════════════════════════════════════════════════════════════════
# SYNC STATE
# ══════════════════════════════════════════════════════════════════
def get_last_sync(conn, key="last_full_sync"):
    row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_last_sync(conn, key="last_full_sync", value=None):
    value = value or datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    conn.execute("INSERT OR REPLACE INTO sync_state VALUES (?,?)", (key, value))
    conn.commit()


# ══════════════════════════════════════════════════════════════════
# ATTACHMENT DOWNLOAD
# ══════════════════════════════════════════════════════════════════
SKIP_EXTS = {".exe", ".rar", ".dmp"}
DOWNLOAD_WORKERS = 4


def download_attachments(conn):
    rows = conn.execute("""
        SELECT id, issue_key, filename, size_bytes, url
        FROM attachments
        WHERE downloaded = 0 AND url IS NOT NULL AND url != ''
    """).fetchall()

    skip = [r for r in rows if Path(r[2]).suffix.lower() in SKIP_EXTS]
    todo = [r for r in rows if Path(r[2]).suffix.lower() not in SKIP_EXTS]

    total_mb = sum(r[3] or 0 for r in todo) / 1024 / 1024
    log.info(f"Attachments to download : {len(todo):,} files  ({total_mb:,.0f} MB)")
    log.info(f"Skipped ({', '.join(SKIP_EXTS)})  : {len(skip):,} files")

    done = ok = err = 0
    for att_id, issue_key, filename, size_bytes, url in todo:
        dest_dir  = ATT_DIR / issue_key
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"{att_id}_{filename}"

        if dest_path.exists() and dest_path.stat().st_size > 0:
            conn.execute("UPDATE attachments SET downloaded=1, local_path=? WHERE id=?",
                         (str(dest_path), att_id))
            done += 1
            continue

        for attempt in range(MAX_RETRIES):
            try:
                r = SESSION.get(url, stream=True, timeout=60)
                r.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        f.write(chunk)
                conn.execute("UPDATE attachments SET downloaded=1, local_path=? WHERE id=?",
                             (str(dest_path), att_id))
                ok += 1
                break
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    log.warning(f"  FAIL {filename}: {e}")
                    err += 1

        done += 1
        if done % 500 == 0:
            conn.commit()
            pct = done / len(todo) * 100
            mb_done = sum(
                (ATT_DIR / r[1] / f"{r[0]}_{r[2]}").stat().st_size
                for r in todo[:done]
                if (ATT_DIR / r[1] / f"{r[0]}_{r[2]}").exists()
            ) / 1024 / 1024
            log.info(f"  [{pct:5.1f}%] {done:,}/{len(todo):,} | ok={ok} err={err} | ~{mb_done:,.0f} MB downloaded")

    conn.commit()
    log.info(f"Download xong: ok={ok:,}  err={err:,}  skip={len(skip):,}")


# ══════════════════════════════════════════════════════════════════
# STATUS
# ══════════════════════════════════════════════════════════════════
def print_status(conn):
    log.info("═" * 60)
    log.info("JIRA TWIN — Status")
    log.info("═" * 60)
    n_proj  = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    n_iss   = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    n_com   = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    n_att   = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
    n_wl    = conn.execute("SELECT COUNT(*) FROM worklogs").fetchone()[0]
    last    = get_last_sync(conn)
    inc     = get_last_sync(conn, "last_incremental_sync")
    log.info(f"  Projects    : {n_proj:,}")
    log.info(f"  Issues      : {n_iss:,}")
    log.info(f"  Comments    : {n_com:,}")
    n_dl    = conn.execute("SELECT COUNT(*) FROM attachments WHERE downloaded=1").fetchone()[0]
    att_mb  = conn.execute("SELECT SUM(size_bytes) FROM attachments WHERE size_bytes IS NOT NULL").fetchone()[0] or 0
    skip_mb = conn.execute(
        "SELECT SUM(size_bytes) FROM attachments WHERE LOWER(SUBSTR(filename, INSTR(filename,'.'))) IN ('.exe','.rar','.dmp')"
    ).fetchone()[0] or 0
    log.info(f"  Attachments : {n_att:,}  (downloaded: {n_dl:,} / {n_att:,} | server total: {att_mb/1024/1024:,.0f} MB | skip: {skip_mb/1024/1024:,.0f} MB)")
    log.info(f"  Worklogs    : {n_wl:,}")
    log.info(f"  Last full   : {last or 'never'}")
    log.info(f"  Last incr.  : {inc or 'never'}")
    log.info(f"  DB size     : {DB_PATH.stat().st_size/1024/1024:.1f} MB")

    log.info("\n  Issues per project (top 10):")
    rows = conn.execute("""
        SELECT project_key, COUNT(*) as n, MAX(updated) as latest
        FROM issues GROUP BY project_key ORDER BY n DESC LIMIT 10
    """).fetchall()
    for proj, n, latest in rows:
        log.info(f"    [{proj:10s}]  {n:5,} issues  latest={latest[:10] if latest else 'N/A'}")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def run(full=False, project=None):
    conn = get_db()
    t0   = time.time()

    log.info("═" * 60)
    log.info(f"Jira Twin  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Mode: {'FULL' if full else 'INCREMENTAL'}")
    log.info("═" * 60)

    # Sync projects
    project_keys = sync_projects(conn)
    if project:
        project_keys = [k for k in project_keys if k == project.upper()]
        if not project_keys:
            log.error(f"Project '{project}' không tìm thấy")
            return

    if full:
        # Full sync: tất cả issues
        jql = "ORDER BY created ASC"
        if project:
            jql = f"project={project_keys[0]} ORDER BY created ASC"
        total = sync_issues(conn, jql, label="[FULL]")
        set_last_sync(conn, "last_full_sync")
        set_last_sync(conn, "last_incremental_sync")
        log.info(f"✅ Full sync xong: {total:,} issues | {time.time()-t0:.0f}s")
    else:
        # Incremental: chỉ issues updated sau lần sync trước
        last = get_last_sync(conn, "last_incremental_sync")
        if not last:
            log.info("Chưa có incremental state — chạy full sync lần đầu")
            jql = "ORDER BY created ASC"
        else:
            # Lùi lại 5 phút để tránh missed updates
            dt   = datetime.strptime(last, "%Y-%m-%d %H:%M") - timedelta(minutes=5)
            jql  = f'updated >= "{dt.strftime("%Y/%m/%d %H:%M")}" ORDER BY updated ASC'
        if project:
            base = jql.replace("ORDER BY", f"AND project={project_keys[0]} ORDER BY")
            jql  = f"project={project_keys[0]} AND {jql}" if "ORDER BY" in jql[:15] else base

        total = sync_issues(conn, jql, label="[INCR]")
        set_last_sync(conn, "last_incremental_sync")
        log.info(f"✅ Incremental sync xong: {total:,} issues updated | {time.time()-t0:.0f}s")

    conn.close()


def main():
    ap = argparse.ArgumentParser(description="Jira Digital Twin")
    ap.add_argument("--full",     action="store_true", help="Full sync toàn bộ")
    ap.add_argument("--project",  type=str,            help="Chỉ sync 1 project key")
    ap.add_argument("--status",   action="store_true", help="Xem thống kê")
    ap.add_argument("--download", action="store_true", help="Download attachments (bỏ .exe .rar .dmp)")
    ap.add_argument("--loop",     type=int, metavar="MIN", help="Lặp mỗi N phút")
    args = ap.parse_args()

    if args.status:
        print_status(get_db()); return

    if args.download:
        conn = get_db()
        download_attachments(conn)
        conn.close()
        return

    if args.loop:
        log.info(f"🔄 Loop mode: mỗi {args.loop} phút")
        while True:
            try:
                run(full=False, project=args.project)
            except Exception as e:
                log.error(f"Lỗi: {e}", exc_info=True)
            log.info(f"💤 Ngủ {args.loop} phút...")
            time.sleep(args.loop * 60)
    else:
        run(full=args.full, project=args.project)


if __name__ == "__main__":
    main()
