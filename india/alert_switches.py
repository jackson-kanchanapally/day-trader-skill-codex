"""Dashboard on/off switches for Telegram alert streams.

One small JSON file next to the picks state (var/state/alert-switches.json by
default) is shared by the dashboard, which writes it, and the alert scheduler,
which reads it before every send. Both Docker services mount ./var, so a change
takes effect on the next scheduler tick without a restart.

A switch only controls Telegram messages. Research that must keep running
(the gold lab's forward-test ledger) is not paused by it.
"""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile

SWITCHES = {
    'gold': dict(default=True, label='Gold futures signals',
                 detail='MCX GOLD PETAL swing setups, live level updates and gold lab notices'),
    'index_fno': dict(default=False, label='Index F&O signals',
                      detail='NIFTY 50 and NIFTY BANK opening-range futures signals with stop/target updates'),
}
FILENAME = 'alert-switches.json'


def path_for(cfg):
    """The switch file for a picks config: beside its state_dir (var/state/ by default)."""
    return Path(cfg['state_dir']).parent / FILENAME


def read(path):
    """Current switches. A missing or unreadable file means every default."""
    values = {name: spec['default'] for name, spec in SWITCHES.items()}
    updated = None
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        doc = {}
    if isinstance(doc, dict):
        for name in SWITCHES:
            if isinstance(doc.get(name), bool):
                values[name] = doc[name]
        updated = doc.get('updated_at') if isinstance(doc.get('updated_at'), str) else None
    return dict(values, updated_at=updated)


def enabled(path, name):
    if name not in SWITCHES:
        raise ValueError('Unknown alert switch: %s' % name)
    return read(path)[name]


def write(path, name, value, now=None):
    """Set one switch atomically and return the full state."""
    if name not in SWITCHES:
        raise ValueError('Unknown alert switch: %s' % name)
    if type(value) is not bool:
        raise ValueError('Alert switch value must be true or false')
    path = Path(path)
    doc = read(path)
    doc[name] = value
    doc['updated_at'] = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec='seconds')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.alert-switches-')
    try:
        with os.fdopen(fd, 'w') as fh:
            json.dump(doc, fh, indent=2)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return doc


def describe(path):
    """Switch state plus labels, for the dashboard."""
    state = read(path)
    return dict(updated_at=state['updated_at'],
                switches=[dict(name=name, enabled=state[name], label=spec['label'], detail=spec['detail'])
                          for name, spec in SWITCHES.items()])
