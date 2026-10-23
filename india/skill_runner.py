"""Portable specialist CLI dispatch; independent agents live in Codex."""
import sys


def main(role, argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if args and args[0]=='evaluate':
        from .team import main as team_main
        if '--role' in args:
            raise ValueError('A specialist runner has a fixed role')
        return team_main(['evaluate','--role',role]+args[1:])
    from .__main__ import main as india_main
    return india_main(args)
