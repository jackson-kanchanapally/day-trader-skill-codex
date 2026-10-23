"""Indian exit tool; preserves the upstream command entrypoint."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow.cli import main
if __name__ == '__main__':
    raise SystemExit(main('exit'))
