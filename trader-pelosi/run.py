"""Run the Indian paper CLI from any directory, including installed symlinks."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow.cli import dispatch as main
if __name__ == '__main__':
    raise SystemExit(main('pelosi'))
