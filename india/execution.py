"""Canonical long-only candle execution; calculations require no provider or LLM.

Prices are unadjusted candle prices. Slippage and optional spread are cash
costs in costs(), not second price adjustments. Entry buffer is a sizing reserve.
"""
import datetime as dt
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Optional

from .data import Bar, IST, timestamp, validate
from .market import costs
from .liquidity import capacity, normalize as liquidity_settings

POLICY = 'next-available-open-v1'


def flatten_slot(plan: dict, minutes: int = 5) -> int:
    """Strict completed-bar boundary; never round an off-grid clock forward."""
    at = dt.time.fromisoformat(plan.get('flat_by','15:10'))
    offset = at.hour*60+at.minute-9*60-15
    if at.tzinfo is not None or at.second or at.microsecond or offset % minutes:
        raise ValueError('Flat-by must align with the candle interval')
    if not dt.time(9,15) < at <= dt.time(15,15):
        raise ValueError('Flat-by must follow the open and not exceed 15:15 IST')
    return offset//minutes


def exit_event(plan: dict, opening: float, high: float, low: float, close: float,
               *, flatten: bool = False) -> Optional[dict]:
    """Shared price-event rule for validated observed or generated OHLC paths."""
    if opening <= plan['stop']:
        return dict(exit=opening,outcome='stop')
    if opening >= plan['target']:
        return dict(exit=plan['target'],outcome='target')
    if low <= plan['stop']:
        return dict(exit=plan['stop'],outcome='stop')
    if high >= plan['target']:
        return dict(exit=plan['target'],outcome='target')
    return dict(exit=close,outcome='close') if flatten else None


def tick_price(value: float, tick: float, up: bool = False) -> float:
    if not all(math.isfinite(x) and x > 0 for x in (value, tick)):
        raise ValueError('Price and tick must be positive finite numbers')
    units = Decimal(str(value))/Decimal(str(tick))
    # Provider floats such as 102.30000000000001 must not add a whole tick.
    nearest = units.to_integral_value()
    if abs(units-nearest) < Decimal('0.00000001'):
        units = nearest
    return float(units.to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)*Decimal(str(tick)))


def estimate_entry(plan: dict, buffer_bps: float = 5.) -> float:
    if not isinstance(buffer_bps, (int, float)) or isinstance(buffer_bps, bool) or not math.isfinite(buffer_bps) or buffer_bps < 0:
        raise ValueError('entry_buffer_bps must be finite and nonnegative')
    value = plan['entry']*(1+buffer_bps/10000)
    return tick_price(value, plan['tick'], True) if plan.get('tick') else value


def position_values(plan: dict, entry: float, qty: int, exchange: str, config=None) -> dict:
    stop_cost = costs(entry, plan['stop'], qty, exchange, config)['total']
    target_cost = costs(entry, plan['target'], qty, exchange, config)
    loss = qty*(entry-plan['stop'])+stop_cost
    reward = qty*(plan['target']-entry)-target_cost['total']
    return dict(max_loss_estimate=loss, net_reward=reward,
                net_reward_risk=reward/loss if loss > 0 else 0,
                costs_at_target=target_cost)


def size_position(plan: dict, capital: float, risk: float, exchange: str,
                  config=None, buffer_bps: float = 5.) -> dict:
    if not all(math.isfinite(x) and x > 0 for x in (capital, risk, plan['entry'], plan['stop'], plan['target'])):
        raise ValueError('Sizing requires positive finite prices, capital and risk')
    if not plan['stop'] < plan['entry'] < plan['target']:
        raise ValueError('Sizing requires stop < entry < target')
    e = estimate_entry(plan, buffer_bps)
    lo, hi = 0, int(min(capital/e, risk/(e-plan['stop']))) if e < plan['target'] else 0
    while lo < hi:
        q = (lo+hi+1)//2
        fee = costs(e, plan['stop'], q, exchange, config)['total']
        if q*(e-plan['stop'])+fee <= risk and q*e+fee <= capital:
            lo = q
        else:
            hi = q-1
    return dict(plan, qty=lo, estimated_entry=e, entry_buffer_bps=buffer_bps,
                sizing_basis='conservative estimate before fill',
                **position_values(plan, e, lo, exchange, config))


def entry_fill(plan: dict, bar: Bar, submitted_at: dt.datetime) -> dict:
    """Use the first supplied open at/after submission, never a past close.

    Caller must deliver events in order. A delayed submission cannot use an
    earlier open. Limit orders fill only when that open is within the limit;
    intrabar limit queues and partial fills are intentionally unsupported.
    """
    validate([bar])
    signal = timestamp(plan['signal_time'])
    minutes = 1 if plan.get('interval') == '1m' else 5
    confirmation = signal+dt.timedelta(minutes=minutes)
    if submitted_at.tzinfo is None or submitted_at.utcoffset() is None:
        raise ValueError('Submission time requires a timezone')
    if submitted_at < confirmation or bar.time < submitted_at or bar.time.date() != confirmation.date():
        raise ValueError('Fill open must follow completed signal and order submission')
    if bar.time > confirmation+dt.timedelta(minutes=minutes):
        raise ValueError('Entry signal expired before fill open')
    opening = bar.time.astimezone(IST).replace(hour=9, minute=15, second=0, microsecond=0)
    if bar.time < opening or (bar.time-opening).total_seconds() % (minutes*60):
        raise ValueError('Fill candle is off the regular-session grid')
    slots = math.ceil((submitted_at-opening).total_seconds()/(minutes*60))
    first_open = opening+dt.timedelta(minutes=minutes*slots)
    if bar.time != first_open:
        raise ValueError('Fill requires the first available candle open after submission')
    flat = dt.datetime.combine(signal.date(), dt.time.fromisoformat(plan.get('flat_by', '15:10')), IST)
    if flat.second or flat.microsecond or (flat-opening).total_seconds() % (minutes*60):
        raise ValueError('Flat-by must align with the candle interval')
    if bar.time+dt.timedelta(minutes=minutes) > flat or flat.time() > dt.time(15, 15):
        raise ValueError('No entry at or beyond flatten/session boundary')
    if bar.volume <= 0:
        raise ValueError('Cannot fill a zero-volume candle')
    price = tick_price(bar.open, plan['tick'], True)
    if not plan['stop'] < price < plan['target']:
        raise ValueError('Fill open lies outside planned stop/target')
    return dict(entry=price, signal_time=signal.isoformat(),
                signal_confirmed_at=confirmation.isoformat(), submitted_at=submitted_at.isoformat(),
                entry_time=bar.time.isoformat(), execution_policy=POLICY)


def validate_fill(plan: dict, price: float, qty: int, capital: float, risk: float,
                  exchange: str, config=None, min_rr: float = 1.) -> dict:
    if not plan['stop'] < price < plan['target']:
        raise ValueError('Fill price must lie between approved stop and target')
    values = position_values(plan, price, qty, exchange, config)
    cash_cost = costs(price, plan['stop'], qty, exchange, config)['total']
    if (qty <= 0 or values['max_loss_estimate'] > risk+1e-8
            or values['net_reward_risk'] < min_rr or qty*price+cash_cost > capital+1e-8):
        raise ValueError('Actual fill breaches cost, cash or risk limits; no hindsight resizing')
    return values


def exit_fill(plan: dict, bar: Bar, minutes: int = 5) -> Optional[dict]:
    """Known open gaps first, then stop-first for ambiguous intrabar touches."""
    validate([bar])
    if bar.volume <= 0:
        raise ValueError('Cannot simulate exits on zero-volume candles')
    flatten = (bar.time+dt.timedelta(minutes=minutes)).time() == dt.time.fromisoformat(plan.get('flat_by','15:10'))
    event = exit_event(plan,bar.open,bar.high,bar.low,bar.close,flatten=flatten)
    if event is None:
        return None
    return dict(event, exit_bar=bar.time.isoformat(),
                exit_time=(bar.time+dt.timedelta(minutes=minutes)).isoformat() if event['outcome'] == 'close' else None)


def replay_trade(plan: dict, future: list, capital: float, risk: float, exchange: str,
                 config=None, min_rr: float = 1., buffer_bps: float = 5.,
                 *, submitted_at: Optional[dt.datetime] = None, liquidity_config=None,
                 prior_bar: Optional[Bar] = None, diagnostics: Optional[dict] = None) -> Optional[dict]:
    """Size before seeing the fill; optionally screen lagged-volume capacity.

    Legacy incomplete paths return None. With a capacity model, missing history
    after an accepted entry raises: an entered exposure cannot be a skipped trade.
    """
    liquidity = liquidity_settings(liquidity_config)
    if not future:
        return None
    minutes = 1 if plan.get('interval') == '1m' else 5
    confirmation = timestamp(plan['signal_time'])+dt.timedelta(minutes=minutes)
    submission = submitted_at or confirmation
    future = [b for b in future if b.time >= submission]
    sized = size_position(plan, capital, risk, exchange, config, buffer_bps)
    if not sized['qty'] or sized['net_reward_risk'] < min_rr or not future:
        return None
    entry_capacity = None
    if liquidity is not None:
        entry_capacity = capacity(sized['qty'], prior_bar, future[0].time, minutes, liquidity)
        if entry_capacity['status'] != 'PROXY_PASSED':
            if diagnostics is not None:
                diagnostics.update(reason='ENTRY_CAPACITY_LIMIT', capacity=entry_capacity)
            return None
    try:
        fill = entry_fill(plan, future[0], submission)
        values = validate_fill(plan, fill['entry'], sized['qty'], capital, risk, exchange, config, min_rr)
    except ValueError:
        return None

    def incomplete(reason: str) -> None:
        if liquidity is not None:
            if diagnostics is not None:
                diagnostics.update(reason='UNRESOLVED_POSITION', detail=reason,
                                   entry_time=fill['entry_time'], quantity=sized['qty'])
            raise ValueError('Unresolved entered position at '+fill['entry_time']+': '+reason+
                             '; no complete liquidity scenario can be reported')
        return None

    previous = future[0].time-dt.timedelta(minutes=minutes)
    previous_bar = prior_bar
    for bar in future:
        if bar.time.date() != confirmation.date() or bar.time != previous+dt.timedelta(minutes=minutes):
            return incomplete('Missing or noncontiguous candle after entry')
        if bar.time.time() >= dt.time.fromisoformat(plan.get('flat_by', '15:10')):
            return incomplete('No modeled exit before the flatten boundary')
        try:
            exit_doc = exit_fill(plan, bar, minutes)
        except ValueError:
            return incomplete('No tradable exit candle after entry')
        previous = bar.time
        if exit_doc:
            fee = costs(fill['entry'], exit_doc['exit'], sized['qty'], exchange, config)['total']
            result = dict(sized, **values, **fill, **exit_doc,
                          net_pnl=(exit_doc['exit']-fill['entry'])*sized['qty']-fee)
            if liquidity is not None:
                exit_capacity = capacity(sized['qty'], previous_bar, bar.time, minutes, liquidity)
                # Keep the marked exit and loss visible. An inability to exit
                # cannot turn a position already entered into a skipped trade.
                result['liquidity'] = dict(entry=entry_capacity, exit=exit_capacity,
                    status='PROXY_PASSED' if exit_capacity['status'] == 'PROXY_PASSED' else 'UNRESOLVED_EXIT_CAPACITY',
                    interpretation='Lagged-volume scenario; no observed quote, queue or execution guarantee')
            return result
        previous_bar = bar
    return incomplete('History ends before a modeled exit')  # no invented close
