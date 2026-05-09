import paramiko
import sys

def execute(ssh, command):
    print(f"\n[EXEC] {command}")
    stdin, stdout, stderr = ssh.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8')
    err = stderr.read().decode('utf-8')
    return exit_status, out, err

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect('192.168.2.3', username='tuanho', password='111111', timeout=10)
    
    scripts = [
        "cd ~/quant && source venv/bin/activate && pip install --upgrade pip",
        "cd ~/quant && source venv/bin/activate && pip install vnstock beautifulsoup4 playwright",
        "cd ~/quant && source venv/bin/activate && playwright install chromium",
        "tmux new -d -s crawler 'cd ~/quant && source venv/bin/activate && export PYTHONPATH=. && python scripts/batch_history_news.py --years 10 --delay 1'",
        "tmux ls"
    ]
    
    for cmd in scripts:
        st, out, err = execute(ssh, cmd)
        if out: print(out.strip())
        if err: print("STDERR:", err.strip())
        
    ssh.close()
except Exception as e:
    print(e)
