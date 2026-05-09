#!/usr/bin/env python3
"""
twin_loop.py — Autonomous Oracle Digital Twin Sync Loop
════════════════════════════════════════════════════════
Chạy liên tục trên server, tự động:
  • Monitor tiến độ sync HQ → Local mỗi POLL_INTERVAL phút
  • Auto-fix ORA-01691 (tablespace full): thêm datafile 30GB khi cần
  • Detect job chết (stale log > STALE_MIN phút hoặc error) → restart
  • Kiểm tra job thật sự đang chạy qua dba_datapump_jobs (không chỉ log)
  • Thoát khi tất cả schemas đạt TARGET_PCT

Usage:
    nohup python3 scripts/twin_loop.py >> logs/twin_loop.log 2>&1 &
    python3 scripts/twin_loop.py --once     # 1 lần check rồi exit
    python3 scripts/twin_loop.py --status   # xem tiến độ, không restart
"""

import oracledb, os, time, glob, subprocess, logging, sys, argparse
from datetime import datetime
from pathlib import Path

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════
LOCAL_DSN    = "192.168.2.3:1521/ORCLPDB1"
VN_USER      = VN_PASS = "vn"
SYS_USER     = "system"
SYS_PASS     = "adminpwd"

LOG_DIR      = Path("/data_128g/oracle_backup")
TS_DATA_DIR  = Path("/data_128g/oracle_data/ORCLPDB1")
VENV_PYTHON  = "/home/tuanho/quant/venv_py11/bin/python3"
SCRIPT_DIR   = Path("/home/tuanho/quant")

POLL_INTERVAL = 10      # phút giữa 2 lần check
STALE_MIN     = 35      # log im lặng > N phút → job bị chết
TARGET_PCT    = 99.0    # % đạt mục tiêu (theo GB segment)
TS_WARN_PCT   = 82      # VIETNAM_TAB dùng > N% → thêm datafile
MAX_PARALLEL  = 4       # workers / job
TS_NAME       = "VIETNAM_TAB"

# Schema groups theo thứ tự ưu tiên — lớn trước
# (csv_schemas, action_khi_restart)
# TRUNCATE = xóa data cũ, import lại (tránh SKIP bỏ qua tables đã tồn tại)
SCHEMA_GROUPS = [
    ("VN",                               "TRUNCATE"),
    ("LI,LI_DEV",                        "TRUNCATE"),
    ("LI_DBZUSER,LITEST,LI_REMISIER",    "TRUNCATE"),
    ("CF,TRADING,GATEWAY",               "TRUNCATE"),
    ("BOS,HR,ADMIN,DIRTUX,TEST,VNMON,VNTUX", "TRUNCATE"),
]

COMPANY_SCHEMAS = [s for grp, _ in SCHEMA_GROUPS for s in grp.split(",")]

# ══════════════════════════════════════════════════════════════════
# LOGGER
# ══════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TWIN-LOOP] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger()


# ══════════════════════════════════════════════════════════════════
# DB CONNECTIONS
# ══════════════════════════════════════════════════════════════════
def vn_conn():
    return oracledb.connect(user=VN_USER, password=VN_PASS, dsn=LOCAL_DSN)

def sys_conn():
    return oracledb.connect(user=SYS_USER, password=SYS_PASS, dsn=LOCAL_DSN)


# ══════════════════════════════════════════════════════════════════
# SCHEMA SYNC STATUS
# ══════════════════════════════════════════════════════════════════
def get_sizes(cur, via_link=False):
    """GB segment size mỗi schema (local hoặc HQ qua link)."""
    link = "@HQ_TWIN_LINK" if via_link else ""
    schema_in = ",".join(f"'{s}'" for s in COMPANY_SCHEMAS)
    try:
        cur.execute(f"""
            SELECT owner, ROUND(SUM(bytes)/1024/1024/1024, 4)
            FROM   dba_segments{link}
            WHERE  owner IN ({schema_in})
            GROUP  BY owner
        """)
        return {r[0]: float(r[1]) for r in cur.fetchall()}
    except Exception as e:
        log.warning(f"get_sizes(link={via_link}): {e}")
        return {}


def sync_report(local, hq):
    """Trả về list (schema, hq_gb, local_gb, pct) sắp xếp theo hq_gb desc."""
    rows = []
    for s in COMPANY_SCHEMAS:
        hq_gb  = hq.get(s, 0)
        loc_gb = local.get(s, 0)
        if hq_gb < 0.001:
            pct = 100.0
        else:
            pct = min(loc_gb / hq_gb * 100, 100.0)
        rows.append((s, hq_gb, loc_gb, pct))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def schemas_needing_work(report):
    return [s for s, hq, loc, pct in report if hq >= 0.001 and pct < TARGET_PCT]


def get_ddl_done_schemas(cur):
    """Schemas đã có objects local (DDL đã import xong)."""
    schema_in = ",".join(f"'{s}'" for s in COMPANY_SCHEMAS)
    try:
        cur.execute(f"""
            SELECT DISTINCT owner FROM dba_tables
            WHERE owner IN ({schema_in})
        """)
        return {r[0] for r in cur.fetchall()}
    except Exception as e:
        log.warning(f"get_ddl_done_schemas: {e}")
        return set()


# ══════════════════════════════════════════════════════════════════
# JOB STATUS (Oracle catalog + log files)
# ══════════════════════════════════════════════════════════════════
def get_oracle_jobs(cur):
    """Lấy Data Pump jobs đang active trong Oracle catalog."""
    try:
        cur.execute("""
            SELECT job_name, state, degree
            FROM   dba_datapump_jobs
            WHERE  state NOT IN ('NOT RUNNING', 'COMPLETED', 'COMPLETING')
        """)
        return {r[0]: {'state': r[1], 'degree': r[2]} for r in cur.fetchall()}
    except Exception as e:
        log.warning(f"get_oracle_jobs: {e}")
        return {}


def scan_logs():
    """
    Scan TWIN_FULL logs trong 3 giờ gần nhất.
    Trả về list dicts: name, age_min, completed, has_ts_error, last_line.
    """
    now = time.time()
    result = []
    for path in sorted(glob.glob(str(LOG_DIR / "TWIN_FULL_*.log")),
                       key=os.path.getmtime, reverse=True):
        mtime    = os.path.getmtime(path)
        age_min  = (now - mtime) / 60
        if age_min > 180:
            break  # logs cũ hơn 3h → bỏ
        try:
            with open(path, errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        lines    = content.splitlines()
        completed = any(
            ("job successfully completed" in l.lower() or
             ("completed with" in l.lower() and "error(s)" in l.lower()))
            for l in lines[-30:]
        )
        has_ts_error = "ORA-01691" in content or "ORA-01536" in content
        last_import  = next(
            (l.strip() for l in reversed(lines) if ". . imported" in l), ""
        )
        result.append({
            "name":         Path(path).stem,
            "path":         path,
            "age_min":      age_min,
            "completed":    completed,
            "has_ts_error": has_ts_error,
            "last_import":  last_import[-80:],
        })
    return result


def count_active_jobs(oracle_jobs, logs):
    """Đếm số jobs thực sự đang chạy (còn trong Oracle catalog VÀ log còn fresh)."""
    active = set(oracle_jobs.keys())
    # Job trong catalog nhưng log stale → coi là zombie
    for info in logs:
        if info["age_min"] > STALE_MIN and not info["completed"]:
            active.discard(info["name"])
    return len(active), active


# ══════════════════════════════════════════════════════════════════
# TABLESPACE MANAGEMENT
# ══════════════════════════════════════════════════════════════════
def check_tablespace(sys_cur):
    """
    Trả về (pct_used, free_gb, max_gb).
    Nếu pct_used > TS_WARN_PCT → auto thêm datafile 30GB.
    """
    sys_cur.execute("""
        SELECT ROUND((df.total - NVL(fs.free,0)) / df.total * 100, 1),
               ROUND(NVL(fs.free,0) / 1024/1024/1024, 2),
               ROUND(df.maxbytes / 1024/1024/1024, 2)
        FROM (
            SELECT SUM(bytes) total,
                   SUM(CASE WHEN autoextensible='YES' THEN maxbytes ELSE bytes END) maxbytes
            FROM dba_data_files WHERE tablespace_name = :ts
        ) df,
        (SELECT NVL(SUM(bytes),0) free FROM dba_free_space WHERE tablespace_name = :ts) fs
    """, ts=TS_NAME)
    row = sys_cur.fetchone()
    pct_used, free_gb, max_gb = float(row[0] or 0), float(row[1]), float(row[2])

    if pct_used >= TS_WARN_PCT:
        _add_datafile(sys_cur)

    return pct_used, free_gb, max_gb


def _add_datafile(sys_cur):
    """Thêm datafile 30GB mới vào VIETNAM_TAB."""
    # Tìm filename tiếp theo chưa tồn tại trên disk
    n = 10
    while n < 50:
        candidate = TS_DATA_DIR / f"{TS_NAME}_{n:02d}.dbf"
        if not candidate.exists():
            break
        n += 1
    else:
        log.error("Không tìm được tên datafile mới (>50 files)!")
        return

    try:
        sys_cur.execute(f"""
            ALTER TABLESPACE {TS_NAME}
            ADD DATAFILE '{candidate}'
            SIZE 30720M AUTOEXTEND ON NEXT 1024M MAXSIZE UNLIMITED
        """)
        log.info(f"🗄  Auto-expanded {TS_NAME}: thêm {candidate.name} (30GB)")
    except Exception as e:
        log.error(f"Thêm datafile thất bại: {e}")


# ══════════════════════════════════════════════════════════════════
# JOB LAUNCHER
# ══════════════════════════════════════════════════════════════════
def launch_group(schemas_csv, action, mode="FULL"):
    """Gọi oracle_datapump_network.py, trả về True nếu STARTED.
    mode: FULL | DDL_ONLY | DATA_ONLY
    """
    cmd = [
        VENV_PYTHON,
        str(SCRIPT_DIR / "scripts/oracle_datapump_network.py"),
        "--schemas", schemas_csv,
        "--mode",    mode,
        "--action",  action,
        "--parallel", str(MAX_PARALLEL),
    ]
    try:
        r = subprocess.run(cmd, cwd=str(SCRIPT_DIR),
                           capture_output=True, text=True, timeout=90)
        output = r.stdout + r.stderr   # logging ghi vào stderr theo mặc định
        started = "STARTED" in output
        for line in output.splitlines():
            if any(k in line for k in ("INFO", "WARNING", "ERROR", "STARTED")):
                log.info(f"    {line.strip()}")
        return started
    except Exception as e:
        log.error(f"  Launch error [{schemas_csv}]: {e}")
        return False


# ══════════════════════════════════════════════════════════════════
# DETECT STALE / ZOMBIE JOBS (và kill nếu cần)
# ══════════════════════════════════════════════════════════════════
def handle_stale_jobs(cur, oracle_jobs, logs):
    """
    Jobs còn trong Oracle catalog nhưng log ngừng update → STOP để giải phóng
    worker slots, sau đó sẽ được restart ở vòng kế tiếp.
    """
    log_names = {info["name"]: info for info in logs}
    for job_name, job_info in oracle_jobs.items():
        log_info = log_names.get(job_name)
        if log_info and log_info["age_min"] > STALE_MIN and not log_info["completed"]:
            log.warning(f"  ⚠️  Job {job_name} log stale {log_info['age_min']:.0f}m → STOP")
            try:
                handle = cur.callfunc("DBMS_DATAPUMP.ATTACH",
                                      oracledb.NUMBER, [job_name, VN_USER])
                cur.callproc("DBMS_DATAPUMP.STOP_JOB", [handle, 1, 0])
                log.info(f"  ✅ Stopped {job_name}")
            except Exception as e:
                log.warning(f"  Stop failed (ok): {e}")


# ══════════════════════════════════════════════════════════════════
# ONE ITERATION
# ══════════════════════════════════════════════════════════════════
def run_once(status_only=False):
    ts_start = time.time()
    log.info("═" * 64)
    log.info(f"Twin Loop  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("═" * 64)

    # ── Kết nối ──────────────────────────────────────────────────
    try:
        vn  = vn_conn();  vc  = vn.cursor()
        sys = sys_conn(); sc  = sys.cursor()
    except Exception as e:
        log.error(f"Không kết nối được Oracle: {e}")
        return False

    # ── Tablespace check ──────────────────────────────────────────
    pct_used, free_gb, max_gb = check_tablespace(sc)
    ts_icon = "✅" if pct_used < TS_WARN_PCT else ("🟡" if pct_used < 93 else "🔴")
    log.info(f"{ts_icon} {TS_NAME}: {pct_used:.1f}% used | {free_gb:.1f} GB free | max {max_gb:.0f} GB")

    # ── Schema sizes ──────────────────────────────────────────────
    log.info("Đang lấy kích thước schemas...")
    local_sz = get_sizes(vc, via_link=False)
    hq_sz    = get_sizes(vc, via_link=True)
    report   = sync_report(local_sz, hq_sz)

    total_hq  = sum(r[1] for r in report)
    total_loc = sum(r[2] for r in report)
    overall   = total_loc / total_hq * 100 if total_hq > 0 else 0

    log.info(f"{'SCHEMA':20s} {'HQ':>9s} {'LOCAL':>9s} {'PCT':>7s}  STATUS")
    log.info("─" * 58)
    for schema, hq_gb, loc_gb, pct in report:
        if hq_gb < 0.001:
            icon = "⬜"
        elif pct >= TARGET_PCT:
            icon = "✅"
        elif pct >= 70:
            icon = "🟡"
        elif pct >= 30:
            icon = "🟠"
        else:
            icon = "🔴"
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        log.info(f"  {icon} {schema:18s} {hq_gb:>7.2f}GB {loc_gb:>7.2f}GB {pct:>6.1f}%  [{bar}]")

    log.info("─" * 58)
    overall_bar = "█" * int(overall / 5) + "░" * (20 - int(overall / 5))
    log.info(f"  {'TOTAL':18s} {total_hq:>7.2f}GB {total_loc:>7.2f}GB {overall:>6.1f}%  [{overall_bar}]")

    # ── Kiểm tra hoàn thành ───────────────────────────────────────
    undone = schemas_needing_work(report)
    if not undone:
        log.info("🎉 TẤT CẢ SCHEMAS ĐẠT MỤC TIÊU — Digital Twin hoàn chỉnh!")
        vc.close(); vn.close(); sc.close(); sys.close()
        return True  # done signal

    log.info(f"📋 {len(undone)} schemas chưa xong: {undone}")

    if status_only:
        vc.close(); vn.close(); sc.close(); sys.close()
        return False

    # ── Job monitoring ────────────────────────────────────────────
    oracle_jobs = get_oracle_jobs(vc)
    logs        = scan_logs()
    n_active, active_names = count_active_jobs(oracle_jobs, logs)

    log.info(f"⚙️  Jobs đang chạy: {n_active}  {list(active_names)}")

    # Xử lý stale jobs
    handle_stale_jobs(vc, oracle_jobs, logs)

    # ── Quyết định launch — 2-phase: DDL trước, DATA sau ────────────
    ddl_done = get_ddl_done_schemas(vc)
    schemas_need_ddl  = [s for s in undone if s not in ddl_done]
    schemas_have_ddl  = [s for s in undone if s in ddl_done]

    if schemas_need_ddl:
        log.info(f"🏗️  Phase 1 (DDL): {len(schemas_need_ddl)} schemas chưa có objects → DDL_ONLY trước")
    if schemas_have_ddl:
        log.info(f"📦 Phase 2 (DATA): {len(schemas_have_ddl)} schemas đã có DDL → sync data")

    running_schemas: set[str] = set()
    for jn in active_names:
        log_path = next((i["path"] for i in logs if i["name"] == jn), None)
        if log_path:
            try:
                with open(log_path, errors="replace") as f:
                    content = f.read(65536)
                for s in COMPANY_SCHEMAS:
                    if f'"{s}"' in content or f"'{s}'" in content:
                        running_schemas.add(s)
            except OSError:
                pass

    MAX_CONCURRENT = len(SCHEMA_GROUPS)
    if n_active >= MAX_CONCURRENT:
        log.info(f"  ⏳ {n_active} jobs đang chạy (>= max {MAX_CONCURRENT}) — không launch thêm")
    else:
        launched = 0
        slots_free = MAX_CONCURRENT - n_active

        for grp_csv, action in SCHEMA_GROUPS:
            if slots_free <= 0:
                break
            grp_schemas = [s.strip() for s in grp_csv.split(",")]
            already_running = any(s in running_schemas for s in grp_schemas)
            if already_running:
                log.info(f"  ⏳ {grp_csv}: đang chạy, bỏ qua")
                continue

            # Phase 1: nhóm có schema chưa có DDL → chạy DDL_ONLY
            grp_needs_ddl = [s for s in grp_schemas if s in schemas_need_ddl]
            if grp_needs_ddl:
                log.info(f"  🏗️  DDL_ONLY: {grp_csv} (schemas cần DDL: {grp_needs_ddl})")
                ok = launch_group(grp_csv, "REPLACE", mode="DDL_ONLY")
                if ok:
                    launched += 1; slots_free -= 1
                    for s in grp_schemas: running_schemas.add(s)
                else:
                    log.error(f"  ❌ DDL_ONLY thất bại: {grp_csv}")
                time.sleep(4)
                continue

            # Phase 2: nhóm đã có DDL → sync data
            grp_needs_data = [s for s in grp_schemas if s in schemas_have_ddl]
            if grp_needs_data:
                # Dùng DATA_ONLY nếu tất cả schemas trong nhóm đã có DDL,
                # FULL nếu mix (1 số có DDL, 1 số chưa — không nên xảy ra)
                mode = "DATA_ONLY" if all(s in ddl_done for s in grp_schemas if s in undone) else "FULL"
                log.info(f"  🚀 Launch: {grp_csv} [{action}] mode={mode}")
                ok = launch_group(grp_csv, action, mode=mode)
                if ok:
                    launched += 1; slots_free -= 1
                    for s in grp_schemas: running_schemas.add(s)
                else:
                    log.error(f"  ❌ Launch thất bại: {grp_csv}")
                time.sleep(4)

        if launched == 0 and n_active == 0:
            log.warning("Không có job nào đang chạy và không launch được — thử lại sau.")

    elapsed = round(time.time() - ts_start, 1)
    log.info(f"Vòng lặp hoàn tất trong {elapsed}s | Tiếp theo: {POLL_INTERVAL} phút")

    vc.close(); vn.close(); sc.close(); sys.close()
    return False


# ══════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="Oracle Digital Twin Sync Loop")
    ap.add_argument("--once",   action="store_true", help="Chạy 1 lần rồi exit")
    ap.add_argument("--status", action="store_true", help="Chỉ xem tiến độ, không restart")
    args = ap.parse_args()

    if args.once or args.status:
        run_once(status_only=args.status)
        return

    log.info("🔄 Twin Loop khởi động — sẽ chạy liên tục đến khi sync 100%")
    log.info(f"   Poll interval : {POLL_INTERVAL} phút")
    log.info(f"   Target        : {TARGET_PCT}% mỗi schema")
    log.info(f"   TS warn       : {TS_WARN_PCT}% → auto-expand")

    iteration = 0
    while True:
        iteration += 1
        log.info(f"\n{'━'*64}")
        log.info(f"  Iteration #{iteration}")
        try:
            done = run_once()
            if done:
                log.info("✅ Loop hoàn thành. Thoát.")
                break
        except Exception as e:
            log.error(f"Lỗi không mong muốn: {e}", exc_info=True)

        log.info(f"💤 Ngủ {POLL_INTERVAL} phút...")
        time.sleep(POLL_INTERVAL * 60)


if __name__ == "__main__":
    main()
