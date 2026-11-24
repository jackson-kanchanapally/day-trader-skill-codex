"""The trading capital and risk per trade the user sets, shared by every alert stream (paper research only).

One small JSON file next to the alert switches (var/state/account.json by
default). The dashboard and the Telegram commands `/capital 50000` and
`/risk 1.5` write it. Every job reads it when it loads its config
(`picks.load_config` with a config file), so a change applies from the next
scheduler tick without a restart.

  capital    at least ₹5,000
  risk_pct   risk per trade as % of capital, 0.1-5% (default 1%); professionals
             mostly stay at 0.5-2%, so more than 2% carries a warning

Either one may be set alone: a saved risk % applies to the config's capital,
and a saved capital uses 1% until a risk % is set. They replace:

  picks       capital and risk_per_trade (stock alerts, and the options desk,
              whose index_fno.risk_per_trade then follows it)
  gold        gold.capital and gold.risk_per_trade

With no file, config/picks.json decides, as before.
"""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile

FILENAME = 'account.json'
MIN_CAPITAL = 5000
MAX_CAPITAL = 1e10
RISK_PCT = 1.0          # default risk per trade, % of capital
MIN_RISK_PCT = 0.1
MAX_RISK_PCT = 5.0
PRO_RISK_PCT = 2.0      # above this, a warning: most professionals risk 0.5-2% a trade


def path_for(cfg):
    """The account file for a picks config: beside its state_dir (var/state/ by default)."""
    return Path(cfg['state_dir']).parent / FILENAME


def _number(text):
    text = text.strip().replace(',', '').replace('₹', '').replace('Rs', '').replace('rs', '').strip().lower()
    scale = 1
    for suffix, mult in (('cr', 1e7), ('lakh', 1e5), ('l', 1e5), ('k', 1e3)):
        if text.endswith(suffix):
            text, scale = text[:-len(suffix)].strip(), mult
            break
    return float(text) * scale


def parse_capital(value):
    """A capital amount from a number or text like "50,000" / "₹1.5L"; raises ValueError when invalid."""
    if isinstance(value, bool):
        raise ValueError('Capital must be a number')
    if isinstance(value, str):
        try:
            value = _number(value)
        except ValueError:
            raise ValueError('Capital must be a number, e.g. 50000') from None
    if not isinstance(value, (int, float)) or value != value:
        raise ValueError('Capital must be a number')
    if value < MIN_CAPITAL:
        raise ValueError('Capital must be at least ₹{:,}'.format(MIN_CAPITAL))
    if value > MAX_CAPITAL:
        raise ValueError('Capital is too large')
    return int(round(value))


def parse_risk(value, capital=None):
    """Risk per trade as % of capital, from 1.5 / "1.5%" or, with a capital, a rupee amount like "₹1500"."""
    if isinstance(value, bool):
        raise ValueError('Risk must be a number')
    if isinstance(value, str):
        text = value.strip()
        rupees = '₹' in text or 'rs' in text.lower()
        try:
            amount = _number(text.replace('%', ''))
        except ValueError:
            raise ValueError('Risk must be a percent like 1.5, or rupees like ₹1500') from None
        if rupees:
            if not capital:
                raise ValueError('Set the capital first to give risk in rupees')
            value = amount / capital * 100
        else:
            value = amount
    if not isinstance(value, (int, float)) or value != value:
        raise ValueError('Risk must be a number')
    if not MIN_RISK_PCT <= value <= MAX_RISK_PCT:
        raise ValueError('Risk must be {:g}-{:g}% of capital per trade (₹ amounts with ₹, e.g. ₹1500)'.format(
            MIN_RISK_PCT, MAX_RISK_PCT))
    return round(float(value), 2)


def risk_for(capital, pct=RISK_PCT):
    return int(round(capital * pct / 100))


def read(path):
    """dict(capital, risk_pct, updated_at); a value is None when unset, unreadable or out of bounds."""
    out = dict(capital=None, risk_pct=None, updated_at=None)
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return out
    if not isinstance(doc, dict):
        return out
    try:
        out['capital'] = parse_capital(doc['capital']) if doc.get('capital') is not None else None
    except ValueError:
        pass
    try:
        out['risk_pct'] = parse_risk(doc['risk_pct']) if doc.get('risk_pct') is not None else None
    except ValueError:
        pass
    out['updated_at'] = doc.get('updated_at') if isinstance(doc.get('updated_at'), str) else None
    return out


def write(path, capital=None, risk_pct=None, now=None, source='dashboard', base_capital=None):
    """Save the capital and/or risk % atomically (the other is kept) and return the saved state.

    `base_capital` (the config's) lets a rupee risk be given before any capital is saved."""
    path = Path(path)
    current = read(path)
    doc = dict(capital=current['capital'], risk_pct=current['risk_pct'])
    if capital is not None:
        doc['capital'] = parse_capital(capital)
    if risk_pct is not None:
        doc['risk_pct'] = parse_risk(risk_pct, doc['capital'] or base_capital)
    if capital is None and risk_pct is None:
        raise ValueError('Nothing to save: give a capital or a risk %')
    doc.update(source=source, updated_at=(now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec='seconds'))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.account-')
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
    return read(path)


def resolve(state, cfg=None):
    """(capital, risk_pct, risk_per_trade) in use, from the saved state over the config."""
    cfg = cfg or {}
    capital = state['capital'] or cfg.get('capital')
    if state['risk_pct'] is not None:
        pct = state['risk_pct']
    elif state['capital']:
        pct = RISK_PCT
    else:
        pct = round(cfg['risk_per_trade'] / capital * 100, 2) if capital and cfg.get('risk_per_trade') else RISK_PCT
    risk = risk_for(capital, pct) if capital and (state['capital'] or state['risk_pct'] is not None) \
        else cfg.get('risk_per_trade')
    return capital, pct, risk


def apply(cfg, path=None):
    """A copy of a loaded picks config with the user's capital and risk applied (unchanged when neither is set)."""
    state = read(path or path_for(cfg))
    if state['capital'] is None and state['risk_pct'] is None:
        return cfg
    capital, pct, risk = resolve(state, cfg)
    out = dict(cfg, capital=capital, risk_per_trade=risk)
    if isinstance(cfg.get('gold'), dict):
        out['gold'] = dict(cfg['gold'], capital=capital, risk_per_trade=risk)
    if isinstance(cfg.get('index_fno'), dict):
        out['index_fno'] = dict(cfg['index_fno'], risk_per_trade=None)      # follows the picks risk
    out['_account'] = dict(capital=capital, risk_pct=pct, risk_per_trade=risk, source='account')
    return out


def warning(pct):
    if pct is not None and pct > PRO_RISK_PCT:
        return ('{:g}% a trade is above the {:g}% most professionals use: a run of 10 losers, normal for any '
                'strategy, costs about {:.0f}% of the capital.').format(pct, PRO_RISK_PCT, (1 - (1 - pct / 100) ** 10) * 100)
    return None


def describe(path, cfg=None):
    """For the dashboard: the capital and risk in use, where they come from, and the limits."""
    state = read(path)
    capital, pct, risk = resolve(state, cfg)
    return dict(capital=capital, risk_pct=pct, risk_per_trade=risk,
                capital_source='dashboard or Telegram' if state['capital'] else 'config/picks.json',
                risk_source='dashboard or Telegram' if state['risk_pct'] is not None else
                ('default 1% of the saved capital' if state['capital'] else 'config/picks.json'),
                updated_at=state['updated_at'], min_capital=MIN_CAPITAL, min_risk_pct=MIN_RISK_PCT,
                max_risk_pct=MAX_RISK_PCT, warning=warning(pct))


def text(path, cfg=None):
    """A Telegram reply describing the account."""
    d = describe(path, cfg)
    lines = ['💼 <b>Capital ₹{:,}</b> · risk per trade ₹{:,} ({:g}%)'.format(
        int(d['capital'] or 0), int(d['risk_per_trade'] or 0), d['risk_pct']),
        'Capital from {}; risk from {}.'.format(d['capital_source'], d['risk_source']),
        'Change: <code>/capital 50000</code> (min ₹{:,}) · <code>/risk 1.5</code> ({:g}-{:g}%) or <code>/risk ₹750</code>'.format(
            MIN_CAPITAL, MIN_RISK_PCT, MAX_RISK_PCT)]
    if d['warning']:
        lines.append('⚠️ ' + d['warning'])
    return '\n'.join(lines)
