import paramiko

def execute(ssh, command):
    print(f"\n[EXEC] {command}")
    stdin, stdout, stderr = ssh.exec_command(command)
    return stdout.read().decode('utf-8') + stderr.read().decode('utf-8')

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect('192.168.2.3', username='tuanho', password='111111', timeout=10)
    
    print(execute(ssh, "ping -c 2 8.8.8.8"))
    print(execute(ssh, "ping -c 2 github.com"))
    print(execute(ssh, "curl -I -v https://github.com"))
    
    ssh.close()
except Exception as e:
    print(e)
