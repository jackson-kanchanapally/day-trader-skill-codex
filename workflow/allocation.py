"""Original weighted allocation arithmetic with INR cash and entry-cost caps."""
import math

from .common import clock, digest, envelope, exchange_of, number, upstream
from .paper import market_fingerprint, one_way_cost, validate_portfolio


def allocate(rows, now, cfg, prices, portfolio=None, top=None):
    if top is not None and (isinstance(top, bool) or not isinstance(top, int) or top < 1):
        raise ValueError('top must be a positive integer')
    for row in rows:
        exchange_of(row['ticker'])
        number(abs(row['weight']), 'weight', zero=True)
    if not math.isfinite(sum(r['weight'] for r in rows)):
        raise ValueError('Total allocation weight must be finite')
    keep, dropped = upstream('budget').normalise(rows, cfg.get('blocked'), top)
    capital = number(cfg['budget'], 'budget')
    cash = capital
    holdings = {}
    if portfolio is not None:
        validate_portfolio(portfolio)
        cash = float(portfolio['cash'])
        holdings = {r.get('symbol', r.get('ticker')): r.get('shares', r.get('qty')) for r in portfolio.get('positions', [])}
    investable = max(0, min(cash, capital)-capital*cfg['cash_buffer_pct']/100)
    values, caps = {}, {}
    for row in keep:
        symbol = row['ticker']
        info = prices.get('detail', {}).get(symbol, {})
        if not info.get('time'):
            raise ValueError('Weighted allocation needs a dated price for '+symbol)
        stamp = clock(info['time'])
        if stamp.date() != now.date() or not 0 <= (now-stamp).total_seconds() <= 900:
            raise ValueError('Stale or future allocation price: '+symbol)
        values[symbol] = number(prices.get('prices', {}).get(symbol), 'price')
        caps[symbol] = max(0, min(cfg['max_order_inr'], capital*cfg['max_position_pct']/100-holdings.get(symbol, 0)*values[symbol]))
    exact, active, remaining = {}, list(keep), investable
    while active:
        total = sum(r['weight'] for r in active)
        trial = {r['ticker']: remaining*r['weight']/total for r in active}
        capped = [r for r in active if trial[r['ticker']] > caps[r['ticker']]]
        if not capped:
            exact.update(trial)
            break
        for row in capped:
            symbol = row['ticker']
            exact[symbol] = caps[symbol]
            remaining -= caps[symbol]
        active = [r for r in active if r not in capped]
    def debit(symbol, qty):
        return qty*values[symbol]+one_way_cost(values[symbol], qty, 'buy', exchange_of(symbol), cfg.get('costs'))['total']
    quantities = {}
    for row in keep:
        symbol = row['ticker']
        lo, hi = 0, math.floor(exact[symbol]/values[symbol])
        while lo < hi:
            mid = (lo+hi+1)//2
            if debit(symbol, mid) <= exact[symbol]+1e-8:
                lo = mid
            else:
                hi = mid-1
        quantities[symbol] = lo
    spent = sum(debit(s, q) for s, q in quantities.items())
    # Redistribute whole-share residuals by the original target deficit order.
    while True:
        choices = [s for s in quantities if debit(s, quantities[s]+1) <= caps[s]+1e-8
                   and debit(s, quantities[s]+1)-debit(s, quantities[s]) <= investable-spent+1e-8]
        if not choices:
            break
        symbol = min(choices, key=lambda s: (-(exact[s]-debit(s, quantities[s])), s))
        spent += debit(symbol, quantities[symbol]+1)-debit(symbol, quantities[symbol])
        quantities[symbol] += 1
    allocations = [dict(symbol=r['ticker'], weight_pct=r['weight'], target_inr=exact[r['ticker']],
                        qty=quantities[r['ticker']], reference_price=values[r['ticker']],
                        estimated_debit_inr=debit(r['ticker'], quantities[r['ticker']])) for r in keep]
    return envelope('budget', now, mode='weighted-allocation', allocations=allocations, dropped=dropped,
                    investable_inr=investable, estimated_debit_inr=spent, idle_cash_inr=cash-spent,
                    cash_basis='supplied-paper-snapshot' if portfolio is not None else 'configured-capital-estimate',
                    prices_sha256=market_fingerprint(prices), portfolio_sha256=digest(portfolio),
                    ready=False, orders=[],
                    note='Allocation amounts only. Each intraday trade still needs an entry, stop/target, verified tick and exact-order risk PASS; no allocation is assumed filled.')
