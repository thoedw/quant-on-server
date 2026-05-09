import paramiko
import time

def execute(ssh, command):
    print(f"\n[EXEC] {command}")
    stdin, stdout, stderr = ssh.exec_command(command)
    
    # We might need to wait for the command to finish.
    # WPA connection might take a few seconds
    while not stdout.channel.exit_status_ready():
        time.sleep(1)
        
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8')
    err = stderr.read().decode('utf-8')
    return exit_status, out, err

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect('192.168.2.3', username='tuanho', password='111111', timeout=10)
    
    # Check existing connections
    st, out, err = execute(ssh, "nmcli connection show")
    print(out)
    
    # Reset connection
    reset_cmd = "echo 111111 | sudo -S nmcli device wifi connect 'InnovateGUEST' password 'ITservices2025'"
    st, out, err = execute(ssh, reset_cmd)
    print("STDOUT:", out)
    print("STDERR:", err)
    
    # Test ping
    st, out, err = execute(ssh, "ping -c 3 8.8.8.8")
    print("PING:", out)
    
    ssh.close()
except Exception as e:
    print("ERROR:", e)
