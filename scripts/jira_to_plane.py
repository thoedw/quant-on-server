#!/usr/bin/env python3
"""
jira_to_plane.py — Import Jira Twin (SQLite) → Plane
══════════════════════════════════════════════════════════════════
Import đầy đủ: issues + comments + worklogs (as comments) + attachments

Usage:
    python3 scripts/jira_to_plane.py --full          # Import tất cả
    python3 scripts/jira_to_plane.py                 # Incremental
    python3 scripts/jira_to_plane.py --project SHS   # 1 project
    python3 scripts/jira_to_plane.py --status        # Thống kê
"""

import sqlite3, requests, logging, sys, time, argparse, json, re, os
from datetime import datetime
from pathlib import Path
from html import escape

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════
PLANE_URL   = "http://100.85.45.77:8090"
PLANE_TOKEN = "plane_api_00f02a6a099443f6aa8b047da58bced2"
WORKSPACE   = "lhpt"

JIRA_DB     = Path.home() / "twins" / "jira" / "jira_twin.db"
ATT_DIR     = Path.home() / "twins" / "jira" / "attachments"
STATE_FILE  = Path.home() / "twins" / "jira" / "plane_import_state.json"

MAX_RETRIES = 3
ISSUE_SLEEP = 0.05   # rate-limit delay between issues

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [J→PLANE] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger()

SESSION = requests.Session()
SESSION.headers.update({"X-Api-Key": PLANE_TOKEN})


# ══════════════════════════════════════════════════════════════════
# STATE
# ══════════════════════════════════════════════════════════════════
def load_state():
    base = {"projects": {}, "issues": {}, "comments_done": {}, "attach_done": {}, "last_run": None}
    if STATE_FILE.exists():
        saved = json.loads(STATE_FILE.read_text())
        base.update(saved)
    return base

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ══════════════════════════════════════════════════════════════════
# PLANE API HELPERS
# ══════════════════════════════════════════════════════════════════
def api(method, path, **kwargs):
    url = f"{PLANE_URL}/api/v1/workspaces/{WORKSPACE}{path}"
    for attempt in range(MAX_RETRIES):
        try:
            r = SESSION.request(method, url, timeout=30, **kwargs)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (400, 422):
                log.warning(f"  API {method} {path}: {r.status_code} {r.text[:120]}")
                return None
            r.raise_for_status()
            return r.json() if r.content else {}
        except requests.HTTPError as e:
            if attempt == MAX_RETRIES - 1:
                log.warning(f"  API {method} {path}: {e}")
                return None
            time.sleep(1)
    return None


# ══════════════════════════════════════════════════════════════════
# PROJECT
# ══════════════════════════════════════════════════════════════════
def get_or_create_project(jira_key, jira_name, state):
    if jira_key in state["projects"]:
        return state["projects"][jira_key]

    resp = api("GET", "/projects/?per_page=100")
    if resp:
        for p in resp.get("results", []):
            if p.get("identifier") == re.sub(r'[^A-Z]', '', jira_key.upper())[:12]:
                state["projects"][jira_key] = p["id"]
                save_state(state)
                return p["id"]

    identifier = re.sub(r'[^A-Z]', '', jira_key.upper())[:12]
    if len(identifier) < 2:
        identifier = re.sub(r'[^A-Z0-9]', '', jira_key.upper())[:12]

    safe_name = re.sub(r'[^\w\s\-]', ' ', jira_name).strip()[:255] or jira_key
    data = {
        "name": safe_name,
        "identifier": identifier,
        "description": f"Imported from Jira project {jira_key}",
        "network": 2,
    }
    resp = api("POST", "/projects/", json=data, headers={"Content-Type": "application/json"})
    if resp and "id" in resp:
        pid = resp["id"]
        state["projects"][jira_key] = pid
        save_state(state)
        log.info(f"  ✅ Project: {jira_key} [{identifier}] → {pid}")
        return pid
    log.warning(f"  ❌ Failed project {jira_key}")
    return None


def get_state_map(project_id):
    resp = api("GET", f"/projects/{project_id}/states/")
    if not resp:
        return {}
    return {s["name"].lower(): s["id"] for s in resp.get("results", [])}


PRIORITY_MAP = {
    "Highest": "urgent", "High": "high", "Medium": "medium",
    "Low": "low", "Lowest": "low",
}
STATUS_MAP = {
    "open": "Backlog", "to do": "Backlog", "backlog": "Backlog",
    "in progress": "In Progress", "in review": "In Progress",
    "done": "Done", "closed": "Done", "resolved": "Done",
}


# ══════════════════════════════════════════════════════════════════
# ISSUE
# ══════════════════════════════════════════════════════════════════
def push_issue(project_id, issue, state_map, state):
    key = issue["key"]
    summary = (issue.get("summary") or key)[:255]
    desc = issue.get("description") or ""

    # Build rich description
    lines = [f"<p><strong>Jira: {key}</strong></p>"]
    if issue.get("reporter"):
        lines.append(f"<p>Reporter: {escape(issue['reporter'])}</p>")
    if issue.get("assignee"):
        lines.append(f"<p>Assignee: {escape(issue['assignee'])}</p>")
    if issue.get("labels"):
        lines.append(f"<p>Labels: {escape(issue['labels'])}</p>")
    if issue.get("fix_versions"):
        lines.append(f"<p>Fix Version: {escape(issue['fix_versions'])}</p>")
    if desc:
        lines.append(f"<hr/><p>{escape(desc)}</p>")
    description_html = "\n".join(lines)

    jira_status = (issue.get("status") or "").lower()
    plane_state_name = STATUS_MAP.get(jira_status, "Backlog")
    state_id = state_map.get(plane_state_name.lower())

    data = {
        "name": summary,
        "description_html": description_html,
        "priority": PRIORITY_MAP.get(issue.get("priority", ""), "none"),
        "state": state_id,
    }
    if issue.get("due_date"):
        data["due_date"] = issue["due_date"][:10]

    plane_id = state["issues"].get(key)
    headers = {"Content-Type": "application/json"}

    if plane_id:
        resp = api("PATCH", f"/projects/{project_id}/issues/{plane_id}/", json=data, headers=headers)
    else:
        resp = api("POST", f"/projects/{project_id}/issues/", json=data, headers=headers)
        if resp and "id" in resp:
            state["issues"][key] = resp["id"]
            plane_id = resp["id"]

    return plane_id


# ══════════════════════════════════════════════════════════════════
# COMMENTS
# ══════════════════════════════════════════════════════════════════
def push_comments(project_id, plane_issue_id, issue_key, conn, state):
    if state["comments_done"].get(issue_key):
        return 0

    rows = conn.execute(
        "SELECT author, body, created FROM comments WHERE issue_key = ? ORDER BY created ASC",
        (issue_key,)
    ).fetchall()

    ok = 0
    for row in rows:
        author  = escape(row[0] or "Unknown")
        body    = escape(row[1] or "")
        created = row[2] or ""
        html = f'<p><em>{author} · {created[:16]}</em></p><p>{body}</p>'
        resp = api("POST", f"/projects/{project_id}/issues/{plane_issue_id}/comments/",
                   json={"comment_html": html},
                   headers={"Content-Type": "application/json"})
        if resp:
            ok += 1
        time.sleep(0.02)

    state["comments_done"][issue_key] = True
    return ok


# ══════════════════════════════════════════════════════════════════
# WORKLOGS → comments
# ══════════════════════════════════════════════════════════════════
def push_worklogs(project_id, plane_issue_id, issue_key, conn, state):
    wl_key = f"wl_{issue_key}"
    if state["comments_done"].get(wl_key):
        return 0

    rows = conn.execute(
        "SELECT author, time_spent_sec, started, comment FROM worklogs WHERE issue_key = ? ORDER BY started ASC",
        (issue_key,)
    ).fetchall()

    if not rows:
        state["comments_done"][wl_key] = True
        return 0

    lines = ["<p><strong>⏱ Work Logs</strong></p><ul>"]
    for row in rows:
        author  = escape(row[0] or "")
        secs    = row[1] or 0
        h, m    = divmod(secs // 60, 60)
        spent   = f"{h}h {m}m" if h else f"{m}m"
        started = (row[2] or "")[:10]
        comment = escape(row[3] or "")
        lines.append(f"<li>{author} · {spent} · {started}{(' — ' + comment) if comment else ''}</li>")
    lines.append("</ul>")

    resp = api("POST", f"/projects/{project_id}/issues/{plane_issue_id}/comments/",
               json={"comment_html": "\n".join(lines)},
               headers={"Content-Type": "application/json"})

    state["comments_done"][wl_key] = True
    return len(rows) if resp else 0


# ══════════════════════════════════════════════════════════════════
# ATTACHMENTS
# ══════════════════════════════════════════════════════════════════
SKIP_EXTS = {".exe", ".rar", ".dmp"}
MAX_ATTACH_MB = 50


def push_attachments(project_id, plane_issue_id, issue_key, conn, state):
    att_key = f"att_{issue_key}"
    if state["attach_done"].get(att_key):
        return 0

    rows = conn.execute(
        "SELECT id, filename, local_path, downloaded FROM attachments WHERE issue_key = ?",
        (issue_key,)
    ).fetchall()

    ok = 0
    for row in rows:
        att_id    = row[0]
        filename  = row[1] or ""
        local_path = row[2]
        downloaded = row[3]

        ext = Path(filename).suffix.lower()
        if ext in SKIP_EXTS:
            continue

        # Use local downloaded file if available
        fpath = None
        if downloaded and local_path and Path(local_path).exists():
            fpath = Path(local_path)
        else:
            # Try default path
            candidate = ATT_DIR / issue_key / f"{att_id}_{filename}"
            if candidate.exists():
                fpath = candidate

        if not fpath:
            continue

        if fpath.stat().st_size > MAX_ATTACH_MB * 1024 * 1024:
            continue

        try:
            with open(fpath, "rb") as f:
                resp = SESSION.post(
                    f"{PLANE_URL}/api/v1/workspaces/{WORKSPACE}/projects/{project_id}/issues/{plane_issue_id}/issue-attachments/",
                    files={"asset": (filename, f)},
                    timeout=60,
                )
            if resp.status_code in (200, 201):
                ok += 1
            time.sleep(0.05)
        except Exception as e:
            log.warning(f"  Attach fail {filename}: {e}")

    state["attach_done"][att_key] = True
    return ok


# ══════════════════════════════════════════════════════════════════
# MAIN IMPORT
# ══════════════════════════════════════════════════════════════════
def run_import(full=False, project_filter=None):
    conn = sqlite3.connect(str(JIRA_DB))
    conn.row_factory = sqlite3.Row
    state = load_state()
    t0    = time.time()

    log.info("═" * 60)
    log.info(f"Jira → Plane  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Mode: {'FULL' if full else 'INCREMENTAL'}")
    log.info("═" * 60)

    if project_filter:
        projects = conn.execute(
            "SELECT key, name FROM projects WHERE key = ?", (project_filter.upper(),)
        ).fetchall()
    else:
        projects = conn.execute("SELECT key, name FROM projects ORDER BY key").fetchall()

    log.info(f"Projects: {len(projects)}")
    total_issues = total_comments = total_attach = 0

    for proj in projects:
        jira_key = proj["key"]
        jira_name = proj["name"]

        plane_pid = get_or_create_project(jira_key, jira_name, state)
        if not plane_pid:
            continue

        state_map = get_state_map(plane_pid)

        # Issues to sync
        if full:
            issues = conn.execute(
                "SELECT * FROM issues WHERE project_key = ? ORDER BY created ASC",
                (jira_key,)
            ).fetchall()
        else:
            last = state.get("_synced_projects", {}).get(jira_key, "2000-01-01")
            issues = conn.execute(
                "SELECT * FROM issues WHERE project_key = ? AND updated >= ? ORDER BY updated ASC",
                (jira_key, last)
            ).fetchall()

        if not issues:
            log.info(f"  [{jira_key}] up to date")
            continue

        log.info(f"  [{jira_key}] {jira_name}: {len(issues):,} issues")
        ok = err = n_comments = n_attach = 0

        for i, row in enumerate(issues):
            issue = dict(row)
            key   = issue["key"]

            # 1. Issue
            plane_iid = push_issue(plane_pid, issue, state_map, state)
            if plane_iid:
                ok += 1
                # 2. Comments
                nc = push_comments(plane_pid, plane_iid, key, conn, state)
                n_comments += nc
                # 3. Worklogs → comment
                push_worklogs(plane_pid, plane_iid, key, conn, state)
                # 4. Attachments
                na = push_attachments(plane_pid, plane_iid, key, conn, state)
                n_attach += na
            else:
                err += 1

            if (i + 1) % 50 == 0:
                save_state(state)
                elapsed = round(time.time() - t0)
                log.info(f"    [{jira_key}] {i+1}/{len(issues)} | issues={ok} comments={n_comments} attach={n_attach} err={err} | {elapsed}s")

            time.sleep(ISSUE_SLEEP)

        save_state(state)
        if "_synced_projects" not in state:
            state["_synced_projects"] = {}
        state["_synced_projects"][jira_key] = datetime.now().strftime("%Y-%m-%d %H:%M")
        save_state(state)

        log.info(f"    [{jira_key}] ✅ issues={ok} comments={n_comments} attach={n_attach} err={err}")
        total_issues += ok
        total_comments += n_comments
        total_attach += n_attach

    state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_state(state)
    conn.close()

    elapsed = round(time.time() - t0)
    log.info("═" * 60)
    log.info(f"✅ Done: {total_issues:,} issues | {total_comments:,} comments | {total_attach:,} attachments | {elapsed}s")


# ══════════════════════════════════════════════════════════════════
# STATUS
# ══════════════════════════════════════════════════════════════════
def print_status():
    state = load_state()
    log.info("═" * 50)
    log.info("Jira → Plane Import Status")
    log.info(f"  Projects synced : {len(state.get('projects', {}))}")
    log.info(f"  Issues synced   : {len(state.get('issues', {})):,}")
    log.info(f"  Comments done   : {sum(1 for k,v in state.get('comments_done',{}).items() if v and not k.startswith('wl_') and not k.startswith('att_')):,}")
    log.info(f"  Worklogs done   : {sum(1 for k,v in state.get('comments_done',{}).items() if k.startswith('wl_') and v):,}")
    log.info(f"  Attachments done: {len(state.get('attach_done', {})):,}")
    log.info(f"  Last run        : {state.get('last_run', 'never')}")
    resp = api("GET", "/projects/?per_page=100")
    if resp:
        log.info(f"  Plane projects  : {resp.get('total_count','?')}")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="Jira Twin → Plane Importer")
    ap.add_argument("--full",    action="store_true")
    ap.add_argument("--project", type=str)
    ap.add_argument("--status",  action="store_true")
    args = ap.parse_args()

    if args.status:
        print_status(); return

    run_import(full=args.full, project_filter=args.project)


if __name__ == "__main__":
    main()
