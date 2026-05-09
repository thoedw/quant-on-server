import paramiko

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
    
    st, out, err = execute(ssh, "node -v")
    print(out)
    
    ssh.close()
except Exception as e:
    print(e)
