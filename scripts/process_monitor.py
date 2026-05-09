#!/usr/bin/env python3
"""
scripts/process_monitor.py
══════════════════════════════════════════════════════════════
🖥  QUANT DATA PIPELINE — Unified Process Monitor

Theo dõi tất cả tiến trình kéo data trong 1 màn hình:
  • Realtime workers   : intraday_engine, watchlist_masvn, dnse_refill
  • Oracle Data Pump   : job state, elapsed, log tail
  • Batch jobs         : oracle_pull_hq, daily_vwap_builder, bvc_imputer
  • SQLite DB          : last write time, size

Usage:
  python3 scripts/process_monitor.py               # refresh 30s
  python3 scripts/process_monitor.py --interval 10 # refresh 10s
  python3 scripts/process_monitor.py --once        # chạy 1 lần rồi thoát

Alias (thêm vào .zshrc trên server):
  alias qmon='cd ~/quant && python3 scripts/process_monitor.py'
"""

import os, sys, re, time, shutil, argparse, sqlite3, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import oracledb
    HAS_ORACLE = True
except ImportError:
    HAS_ORACLE = False

# ── Paths ────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR      = PROJECT_ROOT / "logs"
DB_PATH      = PROJECT_ROOT / "data" / "securities_master.db"
DP_LOG_DIR   = Path("/data_128g/oracle_backup")
ORACLE_DSN   = "localhost:1521/ORCLPDB1"
ORACLE_USER  = "vn"
ORACLE_PASS  = "vn"
VN_TZ        = timezone(timedelta(hours=7))

# ── ANSI Colors ──────────────────────────────────────────────
G  = "\033[92m";  R  = "\033[91m";  Y  = "\033[93m"
C  = "\033[96m";  M  = "\033[95m";  W  = "\033[97m"
DIM= "\033[2m";   B  = "\033[1m";   E  = "\033[0m"
BG_RED  = "\033[41m"; BG_GRN = "\033[42m"; BG_YEL = "\033[43m"

# ── Process definitions ──────────────────────────────────────
REALTIME_PROCS = [
    {
        "name"   : "intraday_engine",
        "match"  : "intraday_engine.py",
        "log"    : None,                      # không có log file riêng
        "stale_m": 5,                         # cảnh báo nếu log im lặng > N phút
        "critical": True,                     # P0 — dừng là critical
    },
    {
        "name"   : "watchlist_masvn",
        "match"  : "watchlist_masvn_worker.py",
        "log"    : None,
        "stale_m": 10,
        "critical": True,
    },
    {
        "name"   : "dnse_refill",
        "match"  : "dnse_refill_worker.py",
        "log"    : None,
        "stale_m": 15,
        "critical": True,
    },
]

BATCH_LOGS = [
    {"name": "daily_vwap_builder",  "log": "daily_vwap_builder.log",  "stale_h": 25},
    {"name": "bvc_imputer",         "log": "bvc_imputer.log",         "stale_h": 25},
    {"name": "eod_daily_close",     "log": "eod_daily_close.log",     "stale_h": 25},
    {"name": "index_refresh",       "log": "index_refresh.log",       "stale_h": 25},
    # oracle_pull_hq: one-time historical (pre-2012), KHÔNG chạy hàng ngày
    # datapump: Oracle Digital Twin, chạy tay khi cần
    {"name": "datapump_oracle",     "log": "datapump_network_*.log",  "stale_h": 48},
]

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def now_vn() -> datetime:
    return datetime.now(VN_TZ)

def _strip(s: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*m', '', s)

def _vis(s: str) -> int:
    clean = _strip(s)
    w = 0
    for ch in clean:
        cp = ord(ch)
        if cp == 0xFE0F: pass
        elif cp >= 0x2E80: w += 2
        else: w += 1
    return w

def _pad(s: str, width: int) -> str:
    """Pad string to visual width."""
    return s + " " * max(0, width - _vis(s))

def _ago(ts: float) -> str:
    """Trả về chuỗi '2m ago', '3h ago'..."""
    diff = time.time() - ts
    if diff < 60:    return f"{int(diff)}s ago"
    if diff < 3600:  return f"{int(diff/60)}m ago"
    return f"{diff/3600:.1f}h ago"

def _log_stat(log_path: Path):
    """Trả về (mtime_ts, last_line, size_mb) hoặc None."""
    if not log_path.exists():
        return None
    stat = log_path.stat()
    mtime = stat.st_mtime
    size_mb = stat.st_size / 1024 / 1024
    # Đọc dòng cuối (không đọc toàn file)
    try:
        result = subprocess.run(
            ["tail", "-1", str(log_path)],
            capture_output=True, text=True, timeout=2
        )
        last = result.stdout.strip()
    except Exception:
        last = ""
    return mtime, last, size_mb

def _glob_latest(pattern: str) -> Path | None:
    """Trả về file mới nhất match glob pattern trong LOG_DIR."""
    files = sorted(LOG_DIR.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None

# ─────────────────────────────────────────────────────────────
# PROCESS CHECK
# ─────────────────────────────────────────────────────────────

def check_processes() -> list[dict]:
    """Kiểm tra tất cả realtime processes bằng ps."""
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )
        ps_out = result.stdout
    except Exception:
        ps_out = ""

    results = []
    for p in REALTIME_PROCS:
        match = p["match"]
        found_lines = [l for l in ps_out.splitlines() if match in l and "grep" not in l]
        if found_lines:
            parts   = found_lines[0].split()
            pid     = parts[1]
            cpu     = parts[2]
            mem     = parts[3]
            elapsed = parts[9] if len(parts) > 9 else "?"
            status  = "RUNNING"
            color   = G
        else:
            pid = mem = cpu = elapsed = "—"
            status = "STOPPED"
            color  = R if p["critical"] else Y

        results.append({
            **p,
            "pid": pid, "cpu": cpu, "mem": mem,
            "elapsed": elapsed, "status": status, "color": color,
        })
    return results

# ─────────────────────────────────────────────────────────────
# ORACLE DATA PUMP CHECK
# ─────────────────────────────────────────────────────────────

def check_oracle_dp() -> dict:
    """Trả về dict trạng thái Oracle Data Pump."""
    result = {
        "connected": False,
        "jobs": [],
        "log_tail": [],
        "log_file": None,
        "error": None,
    }

    # 1. Kiểm tra log file Oracle DP gần nhất
    if DP_LOG_DIR.exists():
        dp_logs = sorted(DP_LOG_DIR.glob("TWIN_*.log"),
                         key=lambda f: f.stat().st_mtime, reverse=True)
        if dp_logs:
            latest = dp_logs[0]
            result["log_file"] = latest
            try:
                proc = subprocess.run(
                    ["tail", "-8", str(latest)],
                    capture_output=True, text=True, timeout=3
                )
                result["log_tail"] = proc.stdout.strip().splitlines()
                result["log_mtime"] = latest.stat().st_mtime
                result["log_name"]  = latest.name
            except Exception as e:
                result["error"] = str(e)

    # 2. Query Oracle DB
    if not HAS_ORACLE:
        result["error"] = "oracledb not installed"
        return result
    try:
        conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS,
                                dsn=ORACLE_DSN)
        cur  = conn.cursor()
        cur.execute("""
            SELECT job_name, operation, job_mode, state, degree, attached_sessions
            FROM dba_datapump_jobs
            ORDER BY job_name
        """)
        rows = cur.fetchall()
        result["jobs"]      = rows
        result["connected"] = True
        conn.close()
    except Exception as e:
        result["error"] = str(e)[:80]

    return result

# ─────────────────────────────────────────────────────────────
# BATCH LOGS CHECK
# ─────────────────────────────────────────────────────────────

def check_batch_logs() -> list[dict]:
    results = []
    for cfg in BATCH_LOGS:
        pattern = cfg["log"]
        if "*" in pattern:
            log_path = _glob_latest(pattern)
        else:
            log_path = LOG_DIR / pattern

        entry = {"name": cfg["name"], "stale_h": cfg["stale_h"]}
        if not log_path or not log_path.exists():
            entry.update({"status": "NO_LOG", "color": DIM, "ago": "—",
                          "last_line": "—", "size_mb": 0})
        else:
            stat = _log_stat(log_path)
            if stat:
                mtime, last_line, size_mb = stat
                age_h = (time.time() - mtime) / 3600
                stale = age_h > cfg["stale_h"]
                # Check if last line contains ERROR
                has_err = any(kw in last_line.upper()
                              for kw in ("ERROR", "FAIL", "EXCEPTION", "CRITICAL"))
                if has_err:
                    color, status = R, "ERROR"
                elif stale:
                    color, status = Y, "STALE"
                else:
                    color, status = G, "OK"
                entry.update({
                    "status": status, "color": color,
                    "ago": _ago(mtime),
                    "last_line": last_line[-90:] if last_line else "—",
                    "size_mb": size_mb,
                    "log_name": log_path.name,
                })
            else:
                entry.update({"status": "NO_LOG", "color": DIM, "ago": "—",
                              "last_line": "—", "size_mb": 0})
        results.append(entry)
    return results

# ─────────────────────────────────────────────────────────────
# SQLITE DB CHECK
# ─────────────────────────────────────────────────────────────

def check_sqlite() -> dict:
    result = {"ok": False, "size_gb": 0, "ago": "—", "last_write": "—"}
    if not DB_PATH.exists():
        return result
    stat    = DB_PATH.stat()
    size_gb = stat.st_size / 1024**3
    mtime   = stat.st_mtime
    age_min = (time.time() - mtime) / 60
    result.update({
        "ok": age_min < 30,        # coi là OK nếu write trong 30 phút qua (sau 15:30 market close)
        "size_gb": size_gb,
        "ago": _ago(mtime),
        "stale": age_min > 10,
    })
    # Kiểm tra số candles hôm nay
    try:
        date_vn = now_vn().strftime("%Y-%m-%d")
        conn = sqlite3.connect(str(DB_PATH), timeout=3)
        cur  = conn.cursor()
        cur.execute(
            "SELECT COUNT(*), MAX(trade_time) FROM stock_prices "
            "WHERE interval='1m' AND date(trade_time)=?", (date_vn,)
        )
        r = cur.fetchone()
        result["candles_today"] = r[0] or 0
        result["last_candle"]   = str(r[1] or "—")[11:16]
        conn.close()
    except Exception:
        result["candles_today"] = "?"
        result["last_candle"]   = "?"
    return result

# ─────────────────────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────────────────────

def _status_badge(status: str, color: str) -> str:
    labels = {
        "RUNNING": f"{BG_GRN}{B}  RUNNING {E}",
        "STOPPED": f"{BG_RED}{B}  STOPPED {E}",
        "OK":      f"{G}✔ OK    {E}",
        "ERROR":   f"{R}✖ ERROR {E}",
        "STALE":   f"{Y}⚠ STALE {E}",
        "NO_LOG":  f"{DIM}– NO LOG{E}",
        "EXECUTING": f"{G}⚡ EXEC  {E}",
        "NOT RUNNING": f"{DIM}– DONE  {E}",
    }
    return labels.get(status, f"{color}{status:8s}{E}")


def render(procs, oracle, batches, db, cycle, interval):
    term   = shutil.get_terminal_size(fallback=(160, 45))
    W_TERM = term.columns
    now    = now_vn().strftime("%Y-%m-%d %H:%M:%S")

    lines = []

    # ── HEADER ──────────────────────────────────────────────
    title = f"🖥  QUANT DATA PIPELINE MONITOR  —  {now}"
    lines.append(f"{B}{C}{'═'*min(W_TERM,80)}{E}")
    lines.append(f"{B}{W}  {title}{E}")
    lines.append(f"{B}{C}{'═'*min(W_TERM,80)}{E}")

    # ── SECTION 1: REALTIME PROCESSES ───────────────────────
    lines.append(f"\n{B}{Y}  ▶ REALTIME WORKERS{E}  {DIM}(core data ingestion){E}")
    lines.append(f"  {'PROCESS':<22} {'STATUS':^12} {'PID':>7}  {'CPU':>4}  {'MEM':>4}  {'UPTIME':>8}")
    lines.append(f"  {'─'*65}")
    alerts = []
    for p in procs:
        badge = _status_badge(p["status"], p["color"])
        line  = (f"  {p['name']:<22} {badge}  "
                 f"{p['pid']:>7}  {p['cpu']:>4}  {p['mem']:>4}  {p['elapsed']:>8}")
        lines.append(line)
        if p["status"] == "STOPPED" and p["critical"]:
            alerts.append(f"🔴 {p['name']} đã DỪNG — cần khởi động lại!")

    # ── SECTION 2: ORACLE DATA PUMP ─────────────────────────
    lines.append(f"\n{B}{Y}  ▶ ORACLE DATA PUMP{E}  {DIM}(schema import từ HQ 172.16.21.40){E}")

    if oracle["connected"]:
        active = [j for j in oracle["jobs"] if j[3] != "NOT RUNNING"]
        done   = [j for j in oracle["jobs"] if j[3] == "NOT RUNNING"]
        if active:
            lines.append(f"  {G}DB: ORCLPDB1 ✔  Connected{E}  |  {G}{B}{len(active)} job đang chạy{E}")
            lines.append(f"  {'JOB NAME':<35} {'STATE':^12} {'MODE':^10} {'DEG':>3}")
            lines.append(f"  {'─'*62}")
            for j in active:
                badge = _status_badge(j[3], G)
                lines.append(f"  {j[0]:<35} {badge} {j[2]:^10} {j[4]:>3}")
        else:
            lines.append(f"  {G}DB: Connected{E}  |  {DIM}Không có job đang chạy  ({len(done)} jobs cũ){E}")
    else:
        err = oracle.get("error", "unknown")
        lines.append(f"  {Y}DB: Không kết nối được Oracle  ({err}){E}")

    # Oracle DP log tail
    if oracle.get("log_tail"):
        log_age = _ago(oracle.get("log_mtime", 0))
        log_stale = (time.time() - oracle.get("log_mtime", 0)) > 300  # 5 min
        age_col = Y if log_stale else G
        lines.append(f"\n  {DIM}📄 {oracle.get('log_name','?')}  "
                     f"({age_col}{log_age}{E}{DIM}){E}")
        for l in oracle["log_tail"][-5:]:
            # Highlight ORA-err vs progress
            if "ORA-" in l and "31684" not in l:   # 31684 = expected "already exists"
                lines.append(f"  {R}  {l[:W_TERM-4]}{E}")
                alerts.append(f"🟡 Oracle DP: {l[:60]}")
            elif "Processing object" in l or "%" in l or "xong" in l.lower():
                lines.append(f"  {G}  {l[:W_TERM-4]}{E}")
            else:
                lines.append(f"  {DIM}  {l[:W_TERM-4]}{E}")
        log_stale = (time.time() - oracle.get("log_mtime", 0)) > 1200  # 20 phút
        if log_stale:
            alerts.append(f"🟡 Oracle DP log im lặng > 20 phút ({log_age}) — có thể bị treo")

    # ── SECTION 3: BATCH JOBS LOG ────────────────────────────
    lines.append(f"\n{B}{Y}  ▶ BATCH JOBS{E}  {DIM}(scheduled, EOD){E}")
    lines.append(f"  {'JOB':<22} {'STATUS':^10} {'LAST RUN':>10}  {'SIZE':>6}  LAST LINE")
    lines.append(f"  {'─'*90}")
    for b in batches:
        badge    = _status_badge(b["status"], b["color"])
        size_str = f"{b['size_mb']:.1f}MB" if b.get("size_mb") else "—"
        last     = b.get("last_line", "—")[:50]
        lines.append(f"  {b['name']:<22} {badge} {b['ago']:>10}  {size_str:>6}  {DIM}{last}{E}")
        if b["status"] == "ERROR":
            alerts.append(f"🔴 {b['name']}: ERROR trong log — {last[:50]}")

    # ── SECTION 4: SQLITE DB ─────────────────────────────────
    lines.append(f"\n{B}{Y}  ▶ SQLITE DATABASE{E}")
    db_col   = G if not db.get("stale") else Y
    candles  = db.get("candles_today", "?")
    last_c   = db.get("last_candle", "?")
    lines.append(
        f"  {db_col}securities_master.db{E}  "
        f"size={db['size_gb']:.2f} GB  "
        f"last_write={db['ago']}  "
        f"candles_today={G}{candles:,}{E}  "
        f"last_1m={G}{last_c}{E}"
    )
    if db.get("stale") and now_vn().hour < 15:  # chỉ cảnh báo trong giờ giao dịch
        alerts.append(f"🟡 SQLite không có write mới trong 10+ phút — engine có thể bị dừng?")

    # ── ALERTS ───────────────────────────────────────────────
    lines.append(f"\n{B}{'─'*min(W_TERM,80)}{E}")
    if alerts:
        lines.append(f"  {B}{R}⚠  ALERTS ({len(alerts)}){E}")
        for a in alerts:
            lines.append(f"  {R}{a}{E}")
    else:
        lines.append(f"  {G}{B}✔  Tất cả tiến trình hoạt động bình thường{E}")

    # ── FOOTER ───────────────────────────────────────────────
    lines.append(f"\n  {DIM}Refresh mỗi {interval}s  |  Ctrl+C để thoát  "
                 f"|  Cycle #{cycle}{E}")
    lines.append(f"{B}{C}{'═'*min(W_TERM,80)}{E}")

    return lines


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def _collect():
    """Thu thập toàn bộ metrics."""
    procs   = check_processes()
    oracle  = check_oracle_dp()
    batches = check_batch_logs()
    db      = check_sqlite()
    return procs, oracle, batches, db


def main():
    ap = argparse.ArgumentParser(description="Quant Pipeline Monitor")
    ap.add_argument("--interval", type=int, default=30,
                    help="Giây giữa mỗi refresh (mặc định: 30)")
    ap.add_argument("--once", action="store_true",
                    help="Chạy 1 lần rồi thoát")
    args = ap.parse_args()

    cycle = 0
    try:
        while True:
            cycle += 1
            procs, oracle, batches, db = _collect()
            lines = render(procs, oracle, batches, db, cycle, args.interval)

            # Clear screen + print
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()

            if args.once:
                break

            # Countdown
            for remaining in range(args.interval, 0, -1):
                now_s = now_vn().strftime("%H:%M:%S")
                sys.stdout.write(
                    f"\r  ⏱  [{now_s}]  Refresh trong {remaining:3d}s "
                    f"(Ctrl+C để thoát)   "
                )
                sys.stdout.flush()
                time.sleep(1)
            print()

    except KeyboardInterrupt:
        print(f"\n\n  👋 Monitor đã dừng.\n")


if __name__ == "__main__":
    main()
