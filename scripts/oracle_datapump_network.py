#!/usr/bin/env python3
"""
oracle_datapump_network.py
===========================
Chạy Oracle Data Pump IMPORT qua NETWORK_LINK bằng DBMS_DATAPUMP PL/SQL.
Không cần sudo oracle, không cần file dump trung gian.

Cơ chế:
  impdp vn/vn@LOCAL  NETWORK_LINK=HQ_TWIN_LINK  SCHEMAS=VN,LI,...
  → Kéo trực tiếp từ HQ qua DB link: tables, indexes, procedures,
    functions, packages, views, triggers, sequences, synonyms, grants.

Usage:
  python3 scripts/oracle_datapump_network.py --schemas VN,LI
  python3 scripts/oracle_datapump_network.py --schemas VN --mode FULL
  python3 scripts/oracle_datapump_network.py --status JOB_NAME
  python3 scripts/oracle_datapump_network.py --list-jobs
"""
import oracledb, time, argparse, sys, logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_DSN    = "192.168.2.3:1521/ORCLPDB1"
LOCAL_USER   = "vn"
LOCAL_PASS   = "vn"
DB_LINK      = "HQ_TWIN_LINK"           # VN-owned link → 172.16.21.40/vietnam
LOG_DIR_OBJ  = "DATAPUMP_DIR"          # = /data_128g/oracle_backup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("DataPump")


# ─── Core: Launch impdp job via DBMS_DATAPUMP ─────────────────────────────────
def launch_import(schemas: list[str], job_suffix: str = "",
                  table_exists_action: str = "REPLACE",
                  include_data: bool = True,
                  data_only: bool = False,
                  parallel: int = 2) -> str:
    """
    Khởi động Oracle Data Pump IMPORT via NETWORK_LINK.
    Trả về job_name để theo dõi.
    """
    con = oracledb.connect(user=LOCAL_USER, password=LOCAL_PASS, dsn=LOCAL_DSN)
    cur = con.cursor()

    ts        = time.strftime("%m%d_%H%M%S")
    job_name  = f"TWIN_{job_suffix or '_'.join(schemas[:2])}_{ts}".upper()[:30]
    log_file  = f"{job_name}.log"
    schema_list = ",".join(schemas)

    log.info(f"Launching Data Pump job: {job_name}")
    log.info(f"  Schemas   : {schema_list}")
    log.info(f"  DB Link   : {DB_LINK} → HQ")
    log.info(f"  Parallel  : {parallel}")
    log.info(f"  Log       : {LOG_DIR_OBJ}:{log_file}")

    # ── Mở job IMPORT với remote_link = DB_LINK ──────────────────
    handle = cur.callfunc(
        "DBMS_DATAPUMP.OPEN",
        oracledb.NUMBER,
        ["IMPORT", "SCHEMA", DB_LINK, job_name, "LATEST"]  # remote_link = DB_LINK
    )
    log.info(f"  Job handle: {handle}")


    # ── ADD log file (optional, bỏ qua nếu lỗi) ────────────────────
    try:
        cur.callproc("DBMS_DATAPUMP.ADD_FILE", [
            handle, log_file, LOG_DIR_OBJ, None, 3  # 3 = KU$_FILE_TYPE_LOG_FILE
        ])
        log.info(f"  Log: {LOG_DIR_OBJ}:{log_file}")
    except Exception as e:
        log.warning(f"  Log file skip (ORA-39002 ok): {str(e)[:60]}")

    # (NETWORK_LINK được truyền qua remote_link trong OPEN — không cần SET_PARAMETER)

    # ── TABLE_EXISTS_ACTION ───────────────────────────────────────
    cur.callproc("DBMS_DATAPUMP.SET_PARAMETER", [
        handle, "TABLE_EXISTS_ACTION", table_exists_action
    ])

    # ── Schemas filter ────────────────────────────────────────────
    schema_in = ",".join(f"'{s}'" for s in schemas)
    cur.callproc("DBMS_DATAPUMP.METADATA_FILTER", [
        handle, "SCHEMA_EXPR", f"IN ({schema_in})"
    ])

    # ── Content filter (DDL_ONLY / DATA_ONLY / FULL) ─────────────
    if not include_data:           # DDL_ONLY: objects only, no rows
        cur.callproc("DBMS_DATAPUMP.SET_PARAMETER", [handle, "INCLUDE_METADATA", 1])
        cur.callproc("DBMS_DATAPUMP.DATA_FILTER",   [handle, "INCLUDE_ROWS", 0])
    elif data_only:                # DATA_ONLY: rows only, skip DDL recreation
        cur.callproc("DBMS_DATAPUMP.SET_PARAMETER", [handle, "INCLUDE_METADATA", 0])
        cur.callproc("DBMS_DATAPUMP.DATA_FILTER",   [handle, "INCLUDE_ROWS", 1])

    # ── Parallel ─────────────────────────────────────────────────
    cur.callproc("DBMS_DATAPUMP.SET_PARALLEL", [handle, parallel])

    # ── Start ─────────────────────────────────────────────────────
    cur.callproc("DBMS_DATAPUMP.START_JOB", [handle])
    con.commit()

    log.info(f"  ✅ Job {job_name} STARTED")
    cur.close(); con.close()
    return job_name


# ─── Monitor: theo dõi job đang chạy ─────────────────────────────────────────
def monitor_job(job_name: str, poll_secs: int = 15):
    """Poll status cho đến khi job hoàn tất hoặc lỗi."""
    con = oracledb.connect(user=LOCAL_USER, password=LOCAL_PASS, dsn=LOCAL_DSN)
    cur = con.cursor()

    log.info(f"Monitoring job: {job_name}")
    prev_pct = -1

    while True:
        cur.execute("""
            SELECT state, job_mode
            FROM dba_datapump_jobs
            WHERE job_name = :jn AND owner_name = 'VN'
        """, {"jn": job_name})
        row = cur.fetchone()

        if not row:
            log.info(f"  ✅ Job {job_name} COMPLETED")
            break

        state, mode = row
        log.info(f"  [{state:12s}] mode={mode}")

        if state in ("COMPLETED", "COMPLETING"):
            log.info(f"  ✅ Job {job_name} {state}")
            break
        if state in ("STOPPED", "STOP_PENDING"):
            log.warning(f"  ⚠️  Job {job_name} {state}")
            break

        time.sleep(poll_secs)

    cur.close(); con.close()


# ─── List: xem các jobs hiện tại ─────────────────────────────────────────────
def list_jobs():
    con = oracledb.connect(user=LOCAL_USER, password=LOCAL_PASS, dsn=LOCAL_DSN)
    cur = con.cursor()
    cur.execute("""
        SELECT owner_name, job_name, operation, job_mode, state, degree
        FROM dba_datapump_jobs
        ORDER BY owner_name, job_name
    """)
    rows = cur.fetchall()
    if not rows:
        log.info("Không có Data Pump job nào đang chạy.")
    else:
        log.info(f"{'OWNER':8s} {'JOB':30s} {'OP':8s} {'MODE':10s} {'STATE':12s}")
        log.info("-" * 72)
        for r in rows:
            log.info(f"  {str(r[0]):8s} {str(r[1]):30s} {str(r[2]):8s} "
                     f"{str(r[3]):10s} {str(r[4]):12s}")
    cur.close(); con.close()


# ─── Entry point ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Oracle Data Pump via Network Link")
    ap.add_argument("--schemas",  help="Schemas CSV: VN,LI,LI_DEV")
    ap.add_argument("--mode",     default="FULL",
                    choices=["FULL","DDL_ONLY","DATA_ONLY"],
                    help="FULL=DDL+data | DDL_ONLY=objects only | DATA_ONLY=rows only")
    ap.add_argument("--action",   default="REPLACE",
                    choices=["REPLACE","TRUNCATE","SKIP","APPEND"])
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--monitor",  action="store_true",
                    help="Monitor job sau khi launch")
    ap.add_argument("--list-jobs",  action="store_true")
    ap.add_argument("--status",   help="Tên job cần check status")
    args = ap.parse_args()

    if args.list_jobs:
        list_jobs(); return

    if args.status:
        monitor_job(args.status); return

    if not args.schemas:
        ap.print_help(); sys.exit(1)

    schemas      = [s.strip().upper() for s in args.schemas.split(",")]
    include_data = (args.mode != "DDL_ONLY")
    data_only    = (args.mode == "DATA_ONLY")

    job_name = launch_import(
        schemas=schemas,
        job_suffix=args.mode,
        table_exists_action=args.action,
        include_data=include_data,
        data_only=data_only,
        parallel=args.parallel,
    )

    if args.monitor:
        time.sleep(3)
        monitor_job(job_name)
    else:
        log.info(f"\nĐể theo dõi: python3 {__file__} --status {job_name}")
        log.info(f"Hoặc xem log: /data_128g/oracle_backup/{job_name}.log")

if __name__ == "__main__":
    main()
