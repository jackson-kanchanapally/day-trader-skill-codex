"""Index option pricing, expiries and option-chain sources (paper research only).

Two price sources share one interface (`market.quote`, `market.expiry`,
`market.summary`):

  ModelChain   The default, free and account-free. Black-Scholes from the spot
               index and NSE's official India VIX, scaled per index by the
               ratio of 20-day realised volatilities. Every price is an
               ESTIMATE and is labelled that way.
  UpstoxChain  Opt-in only (chain_source "upstox"), for people who already
               have an Upstox account: live LTP, bid/ask, OI, IV and greeks.

NSE publishes no free official option-chain service, and its Terms of Use
restrict automated collection from the website, so nothing here scrapes it.

Expiry rules for the model are configuration (NSE changes them): NIFTY weekly
on Tuesday, BANK NIFTY monthly on the last Tuesday, each moved to the previous
NSE trading day when that day is a holiday. The Upstox source reads the real
expiry list and lot size instead.
"""
import datetime as dt
import gzip
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request

from .data import IST

CLOSE = dt.time(15, 30)
YEAR = 365.0 * 24 * 3600


# --------------------------------------------------------------------------- Black-Scholes

def _cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def years_to(expiry, now):
    """Time to the 15:30 IST expiry in years; a floor of 15 minutes keeps the maths finite."""
    end = dt.datetime.combine(expiry, CLOSE, IST)
    return max((end - now).total_seconds(), 900) / YEAR


def bs_price(spot, strike, t, sigma, kind, rate=0.065):
    if sigma <= 0 or t <= 0:
        return max(0.0, spot - strike) if kind == 'CE' else max(0.0, strike - spot)
    d1 = (math.log(spot / strike) + (rate + sigma * sigma / 2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    if kind == 'CE':
        return spot * _cdf(d1) - strike * math.exp(-rate * t) * _cdf(d2)
    return strike * math.exp(-rate * t) * _cdf(-d2) - spot * _cdf(-d1)


def bs_delta(spot, strike, t, sigma, kind, rate=0.065):
    if sigma <= 0 or t <= 0:
        return (1.0 if spot > strike else 0.0) if kind == 'CE' else (-1.0 if spot < strike else 0.0)
    d1 = (math.log(spot / strike) + (rate + sigma * sigma / 2) * t) / (sigma * math.sqrt(t))
    return _cdf(d1) if kind == 'CE' else _cdf(d1) - 1


def implied_vol(price, spot, strike, t, kind, rate=0.065):
    """Bisection IV; None when the price is below intrinsic or unpriceable."""
    lo, hi = 0.01, 3.0
    if price <= bs_price(spot, strike, t, lo, kind, rate) or price >= bs_price(spot, strike, t, hi, kind, rate):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(spot, strike, t, mid, kind, rate) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# --------------------------------------------------------------------------- expiries

def _last_weekday(year, month, weekday):
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    day = nxt - dt.timedelta(days=1)
    while day.weekday() != weekday:
        day -= dt.timedelta(days=1)
    return day


def _shift_to_trading(day, calendar):
    for _ in range(10):
        try:
            if calendar.trading_day(day, 'NSE'):
                return day
        except ValueError:
            return day if day.weekday() < 5 else day - dt.timedelta(days=day.weekday() - 4)
        day -= dt.timedelta(days=1)
    return day


def scheduled_expiries(contract, start, calendar, count=6):
    """Upcoming expiries on or after `start` from the configured weekday rule."""
    found, day = [], start
    wd = contract['expiry_weekday']
    while len(found) < count:
        if contract['monthly']:
            raw = _last_weekday(day.year, day.month, wd)
            day = dt.date(day.year + (day.month == 12), day.month % 12 + 1, 1)
        else:
            raw = day + dt.timedelta(days=(wd - day.weekday()) % 7)
            day = raw + dt.timedelta(days=1)
        expiry = _shift_to_trading(raw, calendar)
        if expiry >= start and expiry not in found:
            found.append(expiry)
    return found


def trading_days_between(start, end, calendar):
    """NSE sessions from start (exclusive) to end (inclusive); 0 on expiry day."""
    n, day = 0, start
    while day < end:
        day += dt.timedelta(days=1)
        try:
            n += calendar.trading_day(day, 'NSE')
        except ValueError:
            n += day.weekday() < 5
    return n


def pick_expiry(expiries, today, min_dte, calendar):
    """First expiry at least `min_dte` sessions away (avoids expiry-day theta and gamma)."""
    for expiry in sorted(expiries):
        if expiry >= today and trading_days_between(today, expiry, calendar) >= min_dte:
            return expiry
    raise ValueError('No expiry at least %d sessions away' % min_dte)


def round_strike(price, step):
    return int(round(price / step) * step)


# --------------------------------------------------------------------------- chain analytics

def chain_summary(rows, spot):
    """PCR, max pain and the OI walls from {strike: {'CE': {...}, 'PE': {...}}}."""
    strikes = sorted(rows)
    ce = {k: (rows[k].get('CE') or {}).get('oi') or 0 for k in strikes}
    pe = {k: (rows[k].get('PE') or {}).get('oi') or 0 for k in strikes}
    total_ce, total_pe = sum(ce.values()), sum(pe.values())
    if not strikes or not total_ce:
        return None
    pain = {k: sum(max(0, k - s) * ce[s] for s in strikes) + sum(max(0, s - k) * pe[s] for s in strikes) for k in strikes}
    above = [k for k in strikes if k > spot]
    below = [k for k in strikes if k < spot]
    return dict(pcr=round(total_pe / total_ce, 2), max_pain=min(pain, key=pain.get),
                call_wall=max(above, key=lambda k: ce[k]) if above else None,
                put_wall=max(below, key=lambda k: pe[k]) if below else None)


# --------------------------------------------------------------------------- sources

class ModelChain:
    """Black-Scholes estimates from spot and India VIX."""
    source = 'model'
    label = 'premiums estimated from India VIX'

    def __init__(self, cfg, calendar, vix):
        if not vix or vix <= 0:
            raise ValueError('India VIX unavailable: no model price')
        self.cfg, self.calendar, self.vix = cfg, calendar, vix

    def sigma(self, name):
        """VIX scaled by the realised-vol ratio to NIFTY when known (cfg['_iv_scale']), else the contract's iv_scale."""
        scale = (self.cfg.get('_iv_scale') or {}).get(name) or self.cfg['contracts'][name]['iv_scale']
        return self.vix / 100 * scale

    def expiry(self, name, today):
        c = self.cfg['contracts'][name]
        return pick_expiry(scheduled_expiries(c, today, self.calendar), today, self.cfg['min_dte'], self.calendar), c['lot']

    def quote(self, name, expiry, strike, kind, spot, now):
        sigma = self.sigma(name)
        t = years_to(expiry, now)
        price = bs_price(spot, strike, t, sigma, kind, self.cfg['rate'])
        return dict(ltp=round(price, 2), bid=None, ask=None, iv=round(sigma * 100, 2),
                    delta=round(bs_delta(spot, strike, t, sigma, kind, self.cfg['rate']), 3), oi=None)

    def summary(self, name, expiry, spot):
        return None


UPSTOX = 'https://api.upstox.com/v2/option/'


def _upstox_get(path, token, timeout=10):
    request = urllib.request.Request(UPSTOX + path, headers={
        'Accept': 'application/json', 'Authorization': 'Bearer ' + token, 'Accept-Encoding': 'gzip'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as exc:
        raise ValueError('Upstox option API HTTP %d' % exc.code) from None
    doc = json.loads(raw)
    if doc.get('status') != 'success':
        raise ValueError('Upstox option API: %s' % str(doc.get('errors') or doc)[:200])
    return doc['data']


def parse_upstox_chain(data):
    """Upstox /option/chain rows -> (spot, {strike: {'CE': quote, 'PE': quote}})."""
    rows, spot = {}, None
    for row in data or []:
        strike = row.get('strike_price')
        if not isinstance(strike, (int, float)):
            continue
        spot = row.get('underlying_spot_price') or spot
        entry = {}
        for side, key in (('CE', 'call_options'), ('PE', 'put_options')):
            leg = row.get(key) or {}
            md, greeks = leg.get('market_data') or {}, leg.get('option_greeks') or {}
            if md.get('ltp') is None:
                continue
            entry[side] = dict(ltp=md.get('ltp'), bid=md.get('bid_price') or None, ask=md.get('ask_price') or None,
                               oi=md.get('oi'), iv=greeks.get('iv'), delta=greeks.get('delta'),
                               key=leg.get('instrument_key'))
        if entry:
            rows[float(strike)] = entry
    return spot, rows


class UpstoxChain:
    """Live option chain from Upstox (read-only analytics token)."""
    source = 'upstox'
    label = 'Upstox option chain'

    def __init__(self, cfg, calendar, token, get=_upstox_get, clock=time.time):
        if not token:
            raise ValueError('No Upstox token')
        self.cfg, self.calendar, self.token, self.get, self.clock = cfg, calendar, token, get, clock
        self._chains, self._contracts = {}, {}

    def _key(self, name):
        return urllib.parse.quote(self.cfg['contracts'][name]['upstox_key'], safe='')

    def expiry(self, name, today):
        if name not in self._contracts:
            data = self.get('contract?instrument_key=%s' % self._key(name), self.token)
            self._contracts[name] = {dt.date.fromisoformat(r['expiry']): r.get('lot_size')
                                     for r in data if r.get('expiry')}
        expiries = self._contracts[name]
        expiry = pick_expiry(list(expiries), today, self.cfg['min_dte'], self.calendar)
        return expiry, expiries[expiry] or self.cfg['contracts'][name]['lot']

    def rows(self, name, expiry):
        cached = self._chains.get((name, expiry))
        if cached and self.clock() - cached[0] < 60:
            return cached[1]
        spot, rows = parse_upstox_chain(self.get('chain?instrument_key=%s&expiry_date=%s' % (
            self._key(name), expiry.isoformat()), self.token))
        if not rows:
            raise ValueError('Upstox returned an empty option chain for %s %s' % (name, expiry))
        self._chains[(name, expiry)] = (self.clock(), (spot, rows))
        return spot, rows

    def quote(self, name, expiry, strike, kind, spot, now):
        _, rows = self.rows(name, expiry)
        q = (rows.get(float(strike)) or {}).get(kind)
        if not q:
            raise ValueError('No %s %s %s quote in the Upstox chain' % (name, strike, kind))
        return dict(q)

    def summary(self, name, expiry, spot):
        live, rows = self.rows(name, expiry)
        return chain_summary(rows, live or spot)


def make_market(cfg, calendar, vix, token=None):
    """The configured source: the free model by default, Upstox only when chosen. Returns (market, warning)."""
    if cfg['chain_source'] == 'upstox':
        if not token:
            raise ValueError('index_fno.chain_source is "upstox" but UPSTOX_ANALYTICS_TOKEN is not set')
        return UpstoxChain(cfg, calendar, token), None
    return ModelChain(cfg, calendar, vix), None


# --------------------------------------------------------------------------- charges

def charges(buy_turnover, sell_turnover, orders):
    """Rough Indian index-option charges in rupees (flat-fee broker): brokerage + GST,
    STT 0.1% of sell premium, exchange 0.03503%, SEBI fee, stamp 0.003% on buys."""
    brokerage = 20.0 * orders
    exchange = 0.0003503 * (buy_turnover + sell_turnover)
    sebi = 0.000001 * (buy_turnover + sell_turnover)
    gst = 0.18 * (brokerage + exchange + sebi)
    return round(brokerage + exchange + sebi + gst + 0.001 * sell_turnover + 0.00003 * buy_turnover, 2)
