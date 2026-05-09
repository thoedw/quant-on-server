#!/usr/bin/env python3
"""
twin_watchdog.py — Hourly watchdog cho Oracle Digital Twin import
Chạy mỗi 1h qua cron. Kiểm tra job TWIN_FULL còn sống không,
nếu chết thì restart với SKIP mode để giữ progress đã có.

Cron: 0 * * * * cd /home/tuanho/quant && source venv_py11/bin/activate && python3 scripts/twin_watchdog.py >> logs/twin_watchdog.log 2>&1
"""
import oracledb, os, time, glob, subprocess, logging
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
LOCAL_DSN   = "192.168.2.3:1521/ORCLPDB1"
LOCAL_USER  = LOCAL_PASS = "vn"
LOG_DIR     = Path("/data_128g/oracle_backup")
SCRIPT_DIR  = Path("/home/tuanho/quant")
VENV_PYTHON = "/home/tuanho/quant/venv_py11/bin/python3"
STALE_MIN   = 35          # log im lặng > 35 phút → job coi như dead
DONE_PCT    = 95          # schema >= 95% → coi là done

# Schemas cần import đầy đủ (sorted by priority: lớn trước)
SCHEMAS_ALL = [
    ("VN,LI,LI_DEV", "SKIP"),      # lớn, dùng SKIP giữ progress
    ("LI_DBZUSER,LITEST,LI_REMISIER", "SKIP"),
    ("TRADING,BOS,CF,GATEWAY", "SKIP"),
    ("HR,ADMIN,DIRTUX,TEST,VNMON,VNTUX", "REPLACE"),
]

# ── Logger ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger()


# ── Helpers ───────────────────────────────────────────────────────────────────
def get_schema_sizes(cur, via_link=False):
    link = "@HQ_TWIN_LINK" if via_link else ""
    schemas = [
        "VN","LI","LI_DEV","LI_DBZUSER","LI_REMISIER","LITEST",
        "TRADING","CF","GATEWAY","BOS","HR",
        "ADMIN","DIRTUX","TEST","VNMON","VNTUX","TRADING",
    ]
    try:
        cur.execute(f"""
            SELECT owner, ROUND(SUM(bytes)/1024/1024/1024,3)
            FROM dba_segments{link}
            WHERE owner IN ({','.join(f"'{s}'" for s in schemas)})
            GROUP BY owner
        """)
        return {r[0]: float(r[1]) for r in cur.fetchall()}
    except Exception as e:
        log.warning(f"get_schema_sizes(link={via_link}) error: {e}")
        return {}


def find_active_log():
    """Tìm TWIN_FULL log mới nhất còn đang update."""
    logs = sorted(
        glob.glob(str(LOG_DIR / "TWIN_FULL_*.log")),
        key=os.path.getmtime, reverse=True
    )
    if not logs:
        return None, None, None
    latest = logs[0]
    mtime  = os.path.getmtime(latest)
    age_m  = (time.time() - mtime) / 60
    return latest, mtime, age_m


def is_job_completed(log_path):
    """Kiểm tra log có dòng 'completed' không."""
    try:
        with open(log_path, errors="replace") as f:
            content = f.read()
        return "job successfully completed" in content.lower() or (
            "completed with" in content.lower() and "error(s)" in content.lower()
        )
    except:
        return False


def get_last_imported(log_path):
    """Lấy table cuối cùng được import."""
    try:
        with open(log_path, errors="replace") as f:
            lines = f.readlines()
        for line in reversed(lines):
            if ". . imported" in line:
                return line.strip()[-80:]
    except:
        pass
    return ""


def launch_job(schemas_csv, action="SKIP", parallel=4):
    """Launch Data Pump job."""
    cmd = [
        VENV_PYTHON,
        str(SCRIPT_DIR / "scripts/oracle_datapump_network.py"),
        "--schemas", schemas_csv,
        "--mode", "FULL",
        "--action", action,
        "--parallel", str(parallel),
    ]
    log.info(f"  Launching: {schemas_csv} [{action}] parallel={parallel}")
    try:
        result = subprocess.run(
            cmd, cwd=str(SCRIPT_DIR),
            capture_output=True, text=True, timeout=60
        )
        for line in result.stdout.splitlines():
            if any(k in line for k in ["INFO", "WARNING", "STARTED", "ERROR"]):
                log.info(f"    {line.strip()}")
        return "STARTED" in result.stdout
    except Exception as e:
        log.error(f"  Launch failed: {e}")
        return False


def check_all_done(local_sizes, hq_sizes):
    """Kiểm tra tất cả schemas đã sync đủ chưa."""
    undone = []
    for schema in hq_sizes:
        hq_gb  = hq_sizes.get(schema, 0)
        loc_gb = local_sizes.get(schema, 0)
        if hq_gb <= 0.001:
            continue  # skip empty schemas
        pct = (loc_gb / hq_gb * 100) if hq_gb > 0 else 100
        if pct < DONE_PCT:
            undone.append((schema, hq_gb, loc_gb, pct))
    return undone


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("Twin Watchdog START")

    # Kết nối Oracle Local
    try:
        conn = oracledb.connect(user=LOCAL_USER, password=LOCAL_PASS, dsn=LOCAL_DSN)
        cur  = conn.cursor()
    except Exception as e:
        log.error(f"Không kết nối được Oracle Local: {e}")
        return

    # Lấy sizes
    log.info("Đang lấy dung lượng HQ vs Local...")
    local_sizes = get_schema_sizes(cur, via_link=False)
    hq_sizes    = get_schema_sizes(cur, via_link=True)

    # Report sizes
    log.info("Schema sync status:")
    total_hq = total_local = 0
    for s in sorted(hq_sizes, key=lambda x: hq_sizes[x], reverse=True):
        hq  = hq_sizes[s]
        loc = local_sizes.get(s, 0)
        pct = min(loc / hq * 100, 100) if hq > 0 else 100
        icon = "✅" if pct >= DONE_PCT else "🟠" if pct >= 50 else "🔴"
        log.info(f"  {icon} {s:18s}  HQ={hq:.2f}GB  Local={loc:.2f}GB  {pct:.0f}%")
        total_hq    += hq
        total_local += loc
    log.info(f"  TOTAL: {total_local:.2f} / {total_hq:.2f} GB  ({total_local/total_hq*100:.0f}%)")

    # Kiểm tra xem đã xong chưa
    undone = check_all_done(local_sizes, hq_sizes)
    if not undone:
        log.info("✅ TẤT CẢ SCHEMAS ĐÃ SYNC ĐỦ — Watchdog hoàn thành nhiệm vụ!")
        cur.close(); conn.close()
        return

    log.info(f"⚠️  {len(undone)} schemas chưa đủ: {[u[0] for u in undone]}")

    # Kiểm tra log job hiện tại
    log_path, mtime, age_m = find_active_log()
    if log_path:
        completed = is_job_completed(log_path)
        last_line = get_last_imported(log_path)
        log.info(f"Log mới nhất: {os.path.basename(log_path)}")
        log.info(f"  Age: {age_m:.1f} phút | Completed: {completed}")
        if last_line:
            log.info(f"  Last: {last_line}")
    else:
        age_m     = 9999
        completed = False
        log.info("Không tìm thấy TWIN_FULL log nào.")

    # Quyết định có cần restart không
    need_restart = False
    if completed:
        log.info("Job cũ đã completed nhưng vẫn còn schemas chưa đủ → cần restart.")
        need_restart = True
    elif age_m > STALE_MIN:
        log.info(f"Job log im lặng {age_m:.0f} phút (> {STALE_MIN}m) → coi là dead, restart.")
        need_restart = True
    else:
        log.info(f"Job đang sống (log {age_m:.0f}m ago) — không cần restart.")

    # Restart
    if need_restart:
        log.info("─" * 40)
        log.info("Launching restart jobs...")
        for schemas_csv, action in SCHEMAS_ALL:
            # Chỉ restart nếu có ít nhất 1 schema trong nhóm chưa done
            schemas = [s.strip() for s in schemas_csv.split(",")]
            needs = any(
                s in [u[0] for u in undone] for s in schemas
                if hq_sizes.get(s, 0) > 0.001
            )
            if needs:
                ok = launch_job(schemas_csv, action=action, parallel=4)
                status = "✅ STARTED" if ok else "❌ FAILED"
                log.info(f"  {schemas_csv}: {status}")
                time.sleep(3)  # stagger launches
            else:
                log.info(f"  {schemas_csv}: skip (đã done)")

    log.info("Twin Watchdog END")
    log.info("=" * 60)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
