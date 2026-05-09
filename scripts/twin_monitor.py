#!/usr/bin/env python3
"""
twin_monitor.py — Oracle Digital Twin Sync Monitor
Refresh mỗi 30s, hiển thị HQ vs Local theo từng schema.

Usage:
    python3 scripts/twin_monitor.py
    python3 scripts/twin_monitor.py --interval 15
    python3 scripts/twin_monitor.py --once
"""
import oracledb, time, os, argparse
from datetime import datetime

LOCAL_DSN  = os.environ.get("ORACLE_DSN", "localhost:1521/ORCLPDB1")
LOCAL_USER = LOCAL_PASS = "vn"
LOG_DIR    = "/data_128g/oracle_backup"

# Schemas công ty — không lấy Oracle internal
COMPANY_SCHEMAS = [
    "VN", "LI_DEV", "LI_DBZUSER", "LI", "LITEST", "LI_REMISIER",
    "TRADING", "CF", "GATEWAY", "BOS", "HR",
    "ADMIN", "DIRTUX", "TEST", "VNMON", "VNTUX",
]

# ── ANSI ──────────────────────────────────────────────────────────────────────
R   = "\033[0m"
B   = "\033[1m"
DM  = "\033[2m"
GR  = "\033[32m"
YL  = "\033[33m"
OR  = "\033[38;5;208m"
RD  = "\033[31m"
CY  = "\033[36m"
BLD = "\033[1m"
BG_HEADER = "\033[48;5;235m"

def clear():
    os.system("clear")

def fmt_gb(gb):
    if gb >= 1:   return f"{gb:.2f} GB"
    if gb >= 0.001: return f"{gb*1000:.1f} MB"
    return "0 GB"

def get_schema_sizes(cur, via_link=False):
    """Lấy actual GB và object count theo schema từ dba_segments."""
    link = "@HQ_TWIN_LINK" if via_link else ""
    try:
        cur.execute(f"""
            SELECT owner,
                   ROUND(SUM(bytes)/1024/1024/1024, 4) gb,
                   COUNT(*) obj_count
            FROM dba_segments{link}
            WHERE owner IN ({','.join(f"'{s}'" for s in COMPANY_SCHEMAS)})
            GROUP BY owner
        """)
        return {r[0]: (float(r[1]), int(r[2])) for r in cur.fetchall()}
    except Exception as e:
        return {}

def get_active_jobs(cur):
    """Lấy tất cả Data Pump jobs hiện tại (kể cả NOT RUNNING)."""
    import glob
    jobs = {}
    # Scan log files để detect job–schema mapping
    for f in sorted(glob.glob(f"{LOG_DIR}/TWIN_*.log"), key=os.path.getmtime, reverse=True):
        job_name = os.path.basename(f).replace(".log", "")
        try:
            with open(f, errors="replace") as fh:
                content = fh.read()
            # Detect schemas có trong log
            schemas_in_log = set()
            for line in content.splitlines():
                if ". . imported" in line or "SCHEMA_EXPORT" in line:
                    for s in COMPANY_SCHEMAS:
                        if f'"{s}"' in line:
                            schemas_in_log.add(s)
            mtime = os.path.getmtime(f)
            age_min = (time.time() - mtime) / 60
            completed = (
                "completed" in content.lower() and "error" in content.lower()
            ) or ("job successfully completed" in content.lower())
            # Job "active" nếu log < 30 phút và chưa completed
            is_active = age_min < 30 and not completed
            for s in schemas_in_log:
                if s not in jobs or jobs[s]["mtime"] < mtime:
                    jobs[s] = {
                        "job_name": job_name,
                        "mtime": mtime,
                        "age_min": age_min,
                        "completed": completed,
                        "is_active": is_active,
                    }
        except:
            pass
    return jobs

def get_running_jobs_db(cur):
    """Check dba_datapump_jobs."""
    try:
        cur.execute("""
            SELECT job_name, state FROM dba_datapump_jobs
            WHERE owner_name = 'VN'
        """)
        return {r[0]: r[1] for r in cur.fetchall()}
    except:
        return {}

def render(local_con, cycle):
    cur = local_con.cursor()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Lấy data sizes
    local_sizes = get_schema_sizes(cur, via_link=False)
    hq_sizes    = get_schema_sizes(cur, via_link=True)
    schema_jobs = get_active_jobs(cur)
    db_jobs     = get_running_jobs_db(cur)

    # Build rows
    rows = []
    total_hq = total_local = 0
    for schema in COMPANY_SCHEMAS:
        hq_gb,    hq_obj    = hq_sizes.get(schema,    (0.0, 0))
        local_gb, local_obj = local_sizes.get(schema, (0.0, 0))
        total_hq    += hq_gb
        total_local += local_gb

        job_info = schema_jobs.get(schema)

        # Determine status
        if hq_gb == 0 and local_gb == 0:
            pct    = 100
            status = "empty"
            job_label = ""
        elif hq_gb == 0:
            pct    = 100
            status = "ok"
            job_label = ""
        else:
            pct = min(local_gb / hq_gb * 100, 100)
            if job_info and job_info["is_active"]:
                status = "running"
                jname = job_info["job_name"]
                # Chỉ lấy timestamp phần (6 số cuối)
                job_label = jname[-6:] if len(jname) >= 6 else jname
            elif pct >= 95:
                status = "done"
                job_label = ""
            elif pct >= 50:
                status = "partial"
                job_label = ""
            else:
                status = "low"
                job_label = ""

        rows.append({
            "schema": schema,
            "hq_gb": hq_gb, "local_gb": local_gb,
            "hq_obj": hq_obj, "local_obj": local_obj,
            "pct": pct, "status": status,
            "job_label": job_label,
        })

    # ── Vẽ ───────────────────────────────────────────────────────────
    clear()
    W = 90

    # Header
    print(f"\n  {BLD}{CY}ORACLE DIGITAL TWIN — Schema Sync Monitor{R}  "
          f"{DM}{now}  |  Cycle #{cycle}{R}")
    print(f"  {DM}{'─'*W}{R}")

    # Column headers
    print(f"  {BLD}"
          f"{'Schema':16s}  {'HQ':>10s}  {'Local':>10s}  {'Sync':>7s}  "
          f"{'Progress':32s}  Status{R}")
    print(f"  {DM}{'─'*W}{R}")

    for r in rows:
        pct = r["pct"]
        schema  = r["schema"]
        hq_str  = fmt_gb(r["hq_gb"])
        lo_str  = fmt_gb(r["local_gb"])
        pct_str = f"{pct:.0f}%"

        # Progress bar (20 chars)
        filled = int(20 * pct / 100)
        if r["status"] == "running":
            bar_col = YL
        elif pct >= 95:
            bar_col = GR
        elif pct >= 50:
            bar_col = OR
        else:
            bar_col = RD
        bar = f"{bar_col}{'█' * filled}{'░' * (20 - filled)}{R}"

        # Status string
        if r["status"] == "running":
            jlabel = r["job_label"]
            status_str = f"{YL}🟡 Job {jlabel} đang chạy{R}"
        elif r["status"] in ("done", "empty", "ok"):
            if r["hq_gb"] == 0 and r["local_gb"] == 0:
                status_str = f"{DM}✅ Empty/sync{R}"
            else:
                status_str = f"{GR}✅ Done{R}"
        elif r["status"] == "partial":
            status_str = f"{OR}🟠 Partial{R}"
        else:
            status_str = f"{RD}🔴 Low{R}"

        # Sync% color
        if pct >= 95:    pct_col = GR
        elif pct >= 50:  pct_col = OR
        else:            pct_col = RD
        if pct >= 95 and r["hq_gb"] > 0:
            pct_str = f"{B}{pct_col}{pct:.0f}%{R}"
        else:
            pct_str = f"{pct_col}{pct:.0f}%{R}"

        print(f"  {BLD}{schema:16s}{R}  "
              f"{DM}{hq_str:>10s}{R}  "
              f"{lo_str:>10s}  "
              f"{pct_str:>7s}  "
              f"{bar}  "
              f"{status_str}")

    # Footer
    print(f"  {DM}{'─'*W}{R}")
    total_pct = (total_local / total_hq * 100) if total_hq > 0 else 0
    filled = int(20 * total_pct / 100)
    total_bar = f"{GR}{'█'*filled}{'░'*(20-filled)}{R}"
    print(f"  {BLD}{'TOTAL':16s}{R}  "
          f"{DM}{fmt_gb(total_hq):>10s}{R}  "
          f"{fmt_gb(total_local):>10s}  "
          f"{OR}{total_pct:.0f}%{R:>7s}  "
          f"{total_bar}")

    # Active DB jobs
    if db_jobs:
        print(f"\n  {BLD}Active DB Jobs:{R}")
        for jname, state in db_jobs.items():
            col = YL if state == "EXECUTING" else DM
            print(f"    {col}▶ {jname:35s} {state}{R}")

    # Countdown footer — được update mỗi giây bên ngoài hàm render
    print(f"\n  ", end="", flush=True)
    cur.close()


def main():
    ap = argparse.ArgumentParser(description="Oracle Digital Twin Sync Monitor")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--once",     action="store_true")
    args = ap.parse_args()

    print("Đang kết nối Oracle Local...")
    try:
        local_con = oracledb.connect(user=LOCAL_USER, password=LOCAL_PASS, dsn=LOCAL_DSN)
    except Exception as e:
        print(f"❌ Kết nối thất bại: {e}")
        return

    cycle = 1
    try:
        while True:
            try:
                render(local_con, cycle)
            except Exception as e:
                print(f"\n[Monitor error] {e}")
            if args.once:
                break
            # Countdown đếm xuống, update mỗi giây
            for remaining in range(args.interval, 0, -1):
                bar_len = 20
                filled  = int(bar_len * remaining / args.interval)
                cbar    = f"\033[36m{'█' * filled}\033[2m{'░' * (bar_len - filled)}\033[0m"
                print(
                    f"\r  \033[2mRefresh trong \033[0m\033[1m{remaining:2d}s\033[0m  "
                    f"{cbar}  \033[2mCtrl+C để thoát\033[0m   ",
                    end="", flush=True
                )
                time.sleep(1)
            cycle += 1
    except KeyboardInterrupt:
        print("\n\nMonitor thoát. Pipeline vẫn đang chạy trên server.")
    finally:
        local_con.close()

if __name__ == "__main__":
    main()
