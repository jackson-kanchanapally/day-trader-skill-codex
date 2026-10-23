"""Install Codex skill links after checking all destinations for conflicts."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def install(destination, dry_run=False):
    destination = Path(destination).expanduser().absolute()
    folders = sorted(p for p in ROOT.glob('trader-*') if (p/'SKILL.md').is_file())
    pending = []
    for folder in folders:
        target = destination/folder.name
        if target.is_symlink() and target.resolve() == folder.resolve():
            continue
        if target.exists() or target.is_symlink():
            raise ValueError('Existing skill not replaced: '+str(target))
        pending.append((target,folder))
    if not dry_run:
        destination.mkdir(parents=True,exist_ok=True)
        for target,folder in pending:
            target.symlink_to(folder,target_is_directory=True)
    return [str(t) for t,_ in pending]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest',default=str(Path.home()/'.agents/skills'))
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    try:
        for path in install(args.dest,args.dry_run): print(path)
    except (ValueError,OSError) as exc:
        parser.exit(1,str(exc)+'\n')

if __name__=='__main__': main()
