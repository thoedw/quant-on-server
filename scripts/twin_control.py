#!/usr/bin/env python3
"""
twin_control.py — Start / Stop / Resume Data Pump jobs
Usage:
  python3 scripts/twin_control.py stop              # dừng tất cả gracefully
  python3 scripts/twin_control.py resume            # resume tất cả NOT RUNNING jobs
  python3 scripts/twin_control.py start VN          # start 1 schema mới
  python3 scripts/twin_control.py start VN,LI       # start nhiều schema
  python3 scripts/twin_control.py status            # xem trạng thái
"""
import oracledb, sys, time, logging

LOCAL_DSN  = "192.168.2.3:1521/ORCLPDB1"
LOCAL_USER = LOCAL_PASS = "vn"
DB_LINK    = "HQ_TWIN_PUBLIC"
LOG_DIR    = "DATAPUMP_DIR"

logging.basicConfig(format="%(asctime)s %(message)s",
                    datefmt="%H:%M:%S", level=logging.INFO)
log = logging.getLogger()

GR = "\033[32m"; YL = "\033[33m"; RD = "\033[31m"
CY = "\033[36m"; B  = "\033[1m";  R  = "\033[0m"


def connect():
    return oracledb.connect(user=LOCAL_USER, password=LOCAL_PASS, dsn=LOCAL_DSN)


# ── STATUS ────────────────────────────────────────────────────────
def cmd_status():
    con = connect(); cur = con.cursor()
    cur.execute("""
        SELECT owner_name, job_name, operation, job_mode, state, degree
        FROM dba_datapump_jobs ORDER BY owner_name, job_name
    """)
    rows = cur.fetchall()
    if not rows:
        print(f"  {GR}✅ Không có Data Pump job nào.{R}")
    else:
        print(f"\n  {B}{'OWNER':8s} {'JOB':32s} {'MODE':8s} {'STATE':14s} {'WORKERS'}{R}")
        print(f"  {'─'*72}")
        for r in rows:
            state_col = GR if r[4]=='EXECUTING' else YL if r[4]=='NOT RUNNING' else CY
            print(f"  {r[0]:8s} {r[1]:32s} {r[3]:8s} "
                  f"{state_col}{r[4]:14s}{R} {r[5]}")
    cur.close(); con.close()


# ── STOP ──────────────────────────────────────────────────────────
def cmd_stop():
    con = connect(); cur = con.cursor()
    cur.execute("""
        SELECT owner_name, job_name FROM dba_datapump_jobs
        WHERE state = 'EXECUTING'
    """)
    running = cur.fetchall()
    if not running:
        print(f"  {GR}✅ Không có job nào đang chạy.{R}")
        cur.close(); con.close(); return

    print(f"  Dừng {len(running)} job...\n")
    for owner, jname in running:
        try:
            hdl = cur.callfunc("DBMS_DATAPUMP.ATTACH",
                               oracledb.NUMBER, [jname, owner])
            # graceful stop, keep_master=1 để resume được
            cur.callproc("DBMS_DATAPUMP.STOP_JOB", [hdl, 0, 1, 0])
            con.commit()
            print(f"  {YL}⏸️  {jname}{R}  [{owner}] — stopped (resumable)")
        except Exception as e:
            print(f"  {RD}❌ {jname}: {e}{R}")

    time.sleep(2)
    print(f"\n  {GR}✅ Xong. Dùng {B}twin-resume{R}{GR} để tiếp tục sau.{R}")
    cur.close(); con.close()


# ── RESUME ────────────────────────────────────────────────────────
def cmd_resume():
    con = connect(); cur = con.cursor()
    cur.execute("""
        SELECT owner_name, job_name FROM dba_datapump_jobs
        WHERE state = 'NOT RUNNING'
          AND operation = 'IMPORT'
          AND owner_name = 'VN'
        ORDER BY job_name
    """)
    stopped = cur.fetchall()
    if not stopped:
        print(f"  {GR}✅ Không có job nào cần resume.{R}")
        cur.close(); con.close(); return

    print(f"  Resume {len(stopped)} job...\n")
    for owner, jname in stopped:
        try:
            hdl = cur.callfunc("DBMS_DATAPUMP.ATTACH",
                               oracledb.NUMBER, [jname, owner])
            cur.callproc("DBMS_DATAPUMP.START_JOB",
                         [hdl, 0, 0])   # skip_current=0, abort_step=0
            con.commit()
            print(f"  {GR}▶️  {jname}{R}  [{owner}] — RESUMED")
        except Exception as e:
            print(f"  {RD}❌ {jname}: {e}{R}")

    time.sleep(2)
    print(f"\n  {GR}✅ Xong. Dùng {B}twin-monitor{R}{GR} để theo dõi.{R}")
    cur.close(); con.close()


# ── START (schema mới) ────────────────────────────────────────────
def cmd_start(schemas_str: str, parallel: int = 2):
    schemas = [s.strip().upper() for s in schemas_str.split(",")]

    # VN cần parallel cao hơn
    if schemas == ["VN"]:
        parallel = 4

    con = connect(); cur = con.cursor()
    ts       = time.strftime("%m%d_%H%M%S")
    schema_in = ",".join(f"'{s}'" for s in schemas)
    tag       = "_".join(schemas[:2])
    job_name  = f"TWIN_{tag}_{ts}".upper()[:30]
    log_file  = f"{job_name}.log"

    print(f"  {B}Launch:{R} {', '.join(schemas)}")
    print(f"  Job   : {job_name}")
    print(f"  Link  : {DB_LINK}  |  Parallel: {parallel}")

    try:
        hdl = cur.callfunc("DBMS_DATAPUMP.OPEN", oracledb.NUMBER,
                           ["IMPORT", "SCHEMA", DB_LINK, job_name, "LATEST"])
        print(f"  Handle: {hdl}")

        # Log file (bỏ qua nếu lỗi)
        try:
            cur.callproc("DBMS_DATAPUMP.ADD_FILE",
                         [hdl, log_file, LOG_DIR, None, 3])
        except:
            pass

        cur.callproc("DBMS_DATAPUMP.SET_PARAMETER",
                     [hdl, "TABLE_EXISTS_ACTION", "REPLACE"])
        cur.callproc("DBMS_DATAPUMP.METADATA_FILTER",
                     [hdl, "SCHEMA_EXPR", f"IN ({schema_in})"])
        cur.callproc("DBMS_DATAPUMP.SET_PARALLEL", [hdl, parallel])
        cur.callproc("DBMS_DATAPUMP.START_JOB",    [hdl])
        con.commit()
        print(f"\n  {GR}✅ STARTED — dùng {B}twin-monitor{R}{GR} để theo dõi.{R}")
    except Exception as e:
        print(f"  {RD}❌ {e}{R}")

    cur.close(); con.close()


# ── Entry ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "status"

    print(f"\n{B}{CY}  Oracle Digital Twin Control{R}\n")

    if cmd == "stop":
        cmd_stop()
    elif cmd == "resume":
        cmd_resume()
    elif cmd == "start":
        schemas = sys.argv[2] if len(sys.argv) > 2 else "VN"
        cmd_start(schemas)
    elif cmd == "status":
        cmd_status()
    else:
        print(f"  Usage: twin_control.py [stop|resume|start SCHEMA|status]")

    print()
