"""Prepare mounted volume as root, then drop privileges before serving.

For cloud providers that mount a fresh /data as root-owned. Never read API keys or trade.
"""
import os
from pathlib import Path
import pwd


def main():
    name='app'
    user=pwd.getpwnam(name)
    data=Path(os.getenv('DATA_DIR','/data'))
    data.mkdir(parents=True,exist_ok=True)
    if os.geteuid()==0:
        os.chown(data,user.pw_uid,user.pw_gid)
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    if not os.access(data,os.W_OK):
        raise SystemExit(f'DATA_DIR not writable: {data}; check the volume mount permissions')
    port=os.getenv('PORT','8000')
    if not port.isdigit() or not 1<=int(port)<=65535:
        raise SystemExit('PORT must be a valid integer')
    os.execvp('uvicorn',['uvicorn','webapp:app','--host','0.0.0.0','--port',port,'--workers','1'])


if __name__=='__main__': main()
