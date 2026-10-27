"""Configure, run and supervise the local automatic paper account."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

from workflow.common import clock, read, write
from .policy import checked


def configure(workspace, enabled=True):
    root = Path(workspace).resolve()
    path = root/'automation/settings.json'
    settings = checked(read(path,{}) if path.is_file() else {})
    settings['enabled'] = enabled
    write(path,settings)
    return settings


def install(workspace):
    """Launchd keeps the worker independent of dashboard and terminal lifetimes."""
    if sys.platform != 'darwin':
        raise ValueError('Automatic installation is macOS launchd only; run python -m automation run under your process supervisor on Linux')
    import hashlib
    root = Path(workspace).resolve()
    label = 'com.daytrader.paper.'+hashlib.sha256(str(root).encode()).hexdigest()[:12]
    repository = Path(__file__).resolve().parents[1]
    auto = root/'automation'
    auto.mkdir(parents=True,exist_ok=True)
    path = Path.home()/'Library/LaunchAgents'/(label+'.plist')
    path.parent.mkdir(parents=True,exist_ok=True)
    payload = dict(Label=label,ProgramArguments=[sys.executable,'-m','automation','run','--workspace',str(root)],
                   WorkingDirectory=str(repository),RunAtLoad=True,KeepAlive=True,ThrottleInterval=30,
                   StandardOutPath=str(auto/'worker.log'),StandardErrorPath=str(auto/'worker-error.log'),
                   EnvironmentVariables={'PYTHONUNBUFFERED':'1'})
    serialized = plistlib.dumps(payload)
    unchanged = path.is_file() and path.read_bytes() == serialized
    path.write_bytes(serialized)
    os.chmod(path,0o600)
    domain = 'gui/'+str(os.getuid())
    service = domain+'/'+label
    loaded = subprocess.run(['launchctl','print',service],capture_output=True).returncode == 0
    # Restart the same job directly; bootout is asynchronous and immediately
    # bootstrapping its label can otherwise fail while removal is in progress.
    if loaded and unchanged:
        result = subprocess.run(['launchctl','kickstart','-k',service],capture_output=True,text=True)
    else:
        if loaded:
            subprocess.run(['launchctl','bootout',service],capture_output=True)
        for attempt in range(20):
            result = subprocess.run(['launchctl','bootstrap',domain,str(path)],capture_output=True,text=True)
            if not result.returncode or 'Input/output error' not in result.stderr:
                break
            time.sleep(.25)
    if result.returncode:
        raise ValueError('Launchd installation failed: '+result.stderr.strip())
    write(auto/'service.json',dict(kind='launchd',label=label,path=str(path),installed_at=clock().isoformat(),paper=True,execution_enabled=False))
    return dict(status='INSTALLED',label=label,path=str(path),paper=True,execution_enabled=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['enable','run','install','pause','resume','status','resize'])
    parser.add_argument('--workspace',default='trader')
    parser.add_argument('--once',action='store_true',help='Complete one scan/management cycle, then exit')
    parser.add_argument('--capital',type=float,help='New simulated capital for resize; applied only when flat')
    parser.add_argument('--daily-loss',type=float,help='Daily INR loss limit for resize')
    a = parser.parse_args(argv)
    try:
        if a.command == 'resize':
            if a.capital is None or a.daily_loss is None:raise ValueError('resize requires --capital and --daily-loss')
            from .capital import request_change
            result=request_change(a.workspace,a.capital,a.daily_loss)
        elif a.command in ('enable','pause','resume'):
            settings = configure(a.workspace,a.command != 'pause')
            result = dict(status='ENTRIES_PAUSED_EXITS_ACTIVE' if a.command == 'pause' else 'ENABLED',settings=settings,paper=True,execution_enabled=False)
        elif a.command == 'install':
            configure(a.workspace,True)
            result = install(a.workspace)
        elif a.command == 'status':
            from .view import snapshot
            result = snapshot(a.workspace)
        else:
            from .service import run
            run(a.workspace,once=a.once)
            return 0
        print(json.dumps(result,indent=2,allow_nan=False))
        return 0
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps(dict(status='UNAVAILABLE',error=str(exc),paper=True,execution_enabled=False)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
