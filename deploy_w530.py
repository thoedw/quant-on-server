import paramiko
import time
import sys

def execute(ssh, command, sudo=False):
    if sudo:
        command = f"echo 111111 | sudo -S {command}"
    
    print(f"\n[EXEC] {command}")
    stdin, stdout, stderr = ssh.exec_command(command)
    
    # Paramiko exec_command is non-blocking, so we wait and read
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8')
    err = stderr.read().decode('utf-8')
    
    if out:
        print("[STDOUT]", out)
    if err:
        print("[STDERR]", err)
    
    return exit_status

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    print("Connecting to W530...")
    ssh.connect('192.168.2.3', username='tuanho', password='111111', timeout=10)
    print("Connected successfully!")
    
    # 1. Install prerequisites if apt is available
    res = execute(ssh, "which apt")
    if res == 0:
        print("Debian/Ubuntu detected, installing tmux & git...")
        execute(ssh, "apt-get update", sudo=True)
        execute(ssh, "apt-get install -y tmux git python3-venv python3-pip", sudo=True)
    elif execute(ssh, "which yum") == 0:
        execute(ssh, "yum install -y tmux git python3", sudo=True)
        
    # 2. Check if quant exists
    res = execute(ssh, "ls ~/quant")
    if res != 0:
        print("Cloning quant repo...")
        execute(ssh, "git clone https://github.com/thoedw/quant.git ~/quant")
    else:
        print("Pulling latest code...")
        execute(ssh, "cd ~/quant && git stash && git pull origin master")
        
    # 3. Setup Python environment and Playwright
    # Dùng bash login shell để đảm bảo pip/uv hoạt động
    setup_cmd = """
    cd ~/quant
    python3 -m venv venv
    source venv/bin/activate
    pip install vnstock playwright beautifulsoup4
    playwright install chromium
    """
    execute(ssh, f"bash -c '{setup_cmd}'")
    
    # 4. Kill existing tmux and start new one
    print("Starting tmux session for crawler...")
    execute(ssh, "tmux kill-session -t crawler 2>/dev/null")
    
    tmux_launch_cmd = "cd ~/quant && source venv/bin/activate && export PYTHONPATH=. && python scripts/batch_history_news.py --years 10 --delay 1"
    execute(ssh, f"tmux new -d -s crawler \"{tmux_launch_cmd}\"")
    
    # Verify if tmux is running
    execute(ssh, "tmux ls")
    
    print("\n[DEPLOYMENT COMPLETE] Cỗ máy đã khởi động tại tmux:crawler trên W530.")
    
except Exception as e:
    print("Error:", e)
finally:
    ssh.close()
