"""Declared ORB experiments using existing signal, indicator, exit and fill rules."""
from dataclasses import asdict, dataclass, replace
import datetime as dt
import math
import re
from typing import Optional

from india.analysis import ema, proposal, session_vwap
from india.audit import digest
from india.data import IST, timestamp, symbol_name, validate
from india.execution import replay_trade
from india.market import DEFAULT_CALENDAR
from india.volume import MODES, relative_volume
from workflow.common import config, upstream
from workflow.strategy import entry, plan


@dataclass(frozen=True)
class Strategy:
    name: str = 'baseline'
    or_minutes: int = 15
    confirmation_bps: float = 0.
    require_vwap: bool = False
    ema_fast: Optional[int] = None
    ema_slow: Optional[int] = None
    min_rvol: Optional[float] = None
    rvol_mode: str = 'same_time'
    rvol_lookback: int = 20
    rvol_min_samples: int = 5
    min_atr_pct: Optional[float] = None
    max_atr_pct: Optional[float] = None
    stop: str = 'or-low'
    entry_start: str = '09:15'
    entry_cutoff: str = '15:05'
    flat_by: str = '15:10'

    def parameters(self) -> dict:
        return {k:v for k,v in asdict(self).items() if k != 'name'}

    def document(self) -> dict:
        return dict(asdict(self), strategy_id=digest(self.parameters()))


BASELINE = Strategy()


def normalize(records=None) -> list:
    """Always include the original baseline. Reject duplicate/unbounded trials."""
    if records is None:
        return [BASELINE]
    if not isinstance(records,list) or not records or len(records) > 12:
        raise ValueError('Strategies must be a nonempty JSON array of at most 12 declared configurations')
    selected = [BASELINE]
    for row in records:
        if not isinstance(row,dict) or not isinstance(row.get('name'),str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',row['name']):
            raise ValueError('Each strategy needs a unique name (letters, digits, underscore or hyphen)')
        if set(row)-set(asdict(BASELINE)):
            raise ValueError('Unknown strategy fields: '+', '.join(sorted(set(row)-set(asdict(BASELINE)))))
        value = Strategy(**row)
        if type(value.or_minutes) is not int or value.or_minutes not in (5,15,30):
            raise ValueError('Opening range must be 5, 15 or 30 minutes')
        if type(value.require_vwap) is not bool:
            raise ValueError('require_vwap must be a JSON boolean')
        for name in ('confirmation_bps','min_rvol','min_atr_pct','max_atr_pct'):
            number = getattr(value,name)
            if number is None and name != 'confirmation_bps':
                continue
            if type(number) not in (int,float) or not math.isfinite(number) or number < 0 or (name != 'confirmation_bps' and number == 0):
                raise ValueError(name+' must be finite and '+('nonnegative' if name == 'confirmation_bps' else 'positive'))
        if value.min_atr_pct is not None and value.max_atr_pct is not None and value.min_atr_pct > value.max_atr_pct:
            raise ValueError('min_atr_pct cannot exceed max_atr_pct')
        if (value.ema_fast is None) != (value.ema_slow is None):
            raise ValueError('Set both ema_fast and ema_slow, or neither')
        if value.ema_fast is not None and (any(type(v) is not int for v in (value.ema_fast,value.ema_slow)) or not 1 <= value.ema_fast < value.ema_slow <= 72):
            raise ValueError('EMA periods require 1 <= fast < slow <= 72 completed session bars')
        if value.rvol_mode not in MODES or any(type(v) is not int for v in (value.rvol_lookback,value.rvol_min_samples)) or not 1 <= value.rvol_min_samples <= value.rvol_lookback <= 252:
            raise ValueError('RVOL requires a supported mode and 1 <= min_samples <= lookback <= 252')
        if any(not isinstance(t,str) for t in (value.entry_start,value.entry_cutoff,value.flat_by)):
            raise ValueError('Entry/flatten clocks must be local IST HH:MM strings')
        clocks = [dt.time.fromisoformat(t) for t in (value.entry_start,value.entry_cutoff,value.flat_by)]
        if any(t.tzinfo is not None or t.second or t.microsecond for t in clocks) or clocks[-1].minute % 5:
            raise ValueError('Entry clocks must be local IST HH:MM; flat_by must match the five-minute grid')
        config(no_entries_before=value.entry_start,no_entries_after=value.entry_cutoff,flat_by=value.flat_by)
        if clocks[1].hour*60+clocks[1].minute < 9*60+15+value.or_minutes+5:
            raise ValueError('Entry cutoff precedes the first possible completed ORB signal')
        if not isinstance(value.stop,str):
            raise ValueError('Stop must be or-low, or-mid, pct:X or atr:X')
        m = upstream('exit')
        rule, parameter = m.parse_stop(value.stop)
        if rule not in ('or_low','or_mid','pct','atr'):
            raise ValueError('Research stop must be or-low, or-mid, pct:X or atr:X')
        numeric = {key:float(getattr(value,key)) if getattr(value,key) is not None else None
                   for key in ('confirmation_bps','min_rvol','min_atr_pct','max_atr_pct')}
        canonical_stop = {'or_low':'or-low','or_mid':'or-mid'}.get(rule) or rule+':'+format(parameter.normalize(),'f')
        value = replace(value,stop=canonical_stop,entry_start=clocks[0].strftime('%H:%M'),
                        entry_cutoff=clocks[1].strftime('%H:%M'),flat_by=clocks[2].strftime('%H:%M'),**numeric)
        if value.min_rvol is None:
            value = replace(value,rvol_mode=BASELINE.rvol_mode,rvol_lookback=BASELINE.rvol_lookback,rvol_min_samples=BASELINE.rvol_min_samples)
        if value.name == 'baseline':
            if value.parameters() != BASELINE.parameters():
                raise ValueError('The baseline name is reserved for the unchanged reference strategy')
            continue
        if any(value.name == other.name or value.parameters() == other.parameters() for other in selected):
            raise ValueError('Duplicate strategy name or parameters; renaming is not a new experiment')
        selected.append(value)
    if len(selected) > 12:
        raise ValueError('At most 12 strategies including the automatic baseline')
    return selected


def volume_history(prior: list, day: dt.date, actions: list) -> list:
    """Express old share volume in current share units using known actions only.

    Price observations remain raw. Actions are supplied already resolved at the
    session-open publication cutoff by CandleStore.actions(known_at=...).
    """
    if not actions:
        return prior
    opening = dt.datetime.combine(day,dt.time(9,15),IST)
    result = []
    for bar in prior:
        factor = 1.
        for action in actions:
            if bar.time.date().isoformat() < action['effective_date'] <= day.isoformat() and timestamp(action['published_at']) <= opening:
                factor *= action['price_factor']
        if not math.isfinite(factor) or factor <= 0 or not math.isfinite(bar.volume/factor):
            raise ValueError('Corporate actions produce invalid RVOL share units')
        result.append(replace(bar,volume=bar.volume/factor))
    return result


def filters(rows: list, strategy: Strategy, *, prior=None, actions=None, calendar=None, symbol=None) -> dict:
    """Only completed current-session bars and strictly earlier history enter."""
    if not rows:
        raise ValueError('Filters require completed current-session candles')
    validate(rows)
    day, last = rows[-1].time.date(), rows[-1]
    if any(b.time.date() != day for b in rows) or any(b.time.date() >= day for b in prior or []):
        raise ValueError('Filter history must be strictly earlier sessions')
    level = max(b.high for b in rows[:strategy.or_minutes//5])
    checks = {'confirmation': dict(value=10000*(last.close/level-1),minimum=strategy.confirmation_bps,
                                   passed=last.close > level and last.close >= level*(1+strategy.confirmation_bps/10000))}
    if strategy.require_vwap:
        value = session_vwap(rows)
        checks['vwap'] = dict(value=value,passed=value is not None and last.close >= value)
    if strategy.ema_fast is not None:
        ready = len(rows) >= strategy.ema_slow
        fast = ema([b.close for b in rows],strategy.ema_fast) if ready else None
        slow = ema([b.close for b in rows],strategy.ema_slow) if ready else None
        checks['ema'] = dict(fast=fast,slow=slow,bars=len(rows),required=strategy.ema_slow,
                             passed=ready and last.close >= fast > slow,reason=None if ready else 'INSUFFICIENT_EMA_WARMUP')
    if strategy.min_rvol is not None:
        if symbol is None:
            raise ValueError('RVOL requires an explicit NSE/BSE symbol')
        symbol_name(symbol)
        history = volume_history(prior or [],day,actions or [])
        result = relative_volume(history+rows,mode=strategy.rvol_mode,lookback=strategy.rvol_lookback,
                                 min_samples=strategy.rvol_min_samples,as_of=last.time+dt.timedelta(minutes=5),
                                 calendar=calendar,exchange='NSE' if symbol.endswith('.NS') else 'BSE')
        checks['rvol'] = dict(result,minimum=strategy.min_rvol,
                              passed=result['value'] is not None and result['value'] >= strategy.min_rvol)
    if strategy.stop.startswith('atr:') and len(rows) < 3:
        checks['atr_stop_warmup'] = dict(bars=len(rows),required=3,passed=False,reason='INSUFFICIENT_ATR_STOP_WARMUP')
    if strategy.min_atr_pct is not None or strategy.max_atr_pct is not None:
        ready = len(rows) >= 14
        value, used = upstream('exit',calendar).atr_of([dict(h=b.high,l=b.low,c=b.close) for b in rows]) if ready else (None,len(rows))
        pct = 100*value/last.close if value is not None else None
        passed = ready and (strategy.min_atr_pct is None or pct >= strategy.min_atr_pct) and (strategy.max_atr_pct is None or pct <= strategy.max_atr_pct)
        checks['atr'] = dict(value=value,pct=pct,bars=used,required=14,passed=bool(passed),reason=None if ready else 'INSUFFICIENT_ATR_WARMUP')
    return dict(passed=all(c['passed'] for c in checks.values()),checks=checks,
                reasons=[name for name,check in checks.items() if not check['passed']])


def prepare(rows: list, symbol: str, tick: float, strategy: Strategy, *, prior=None, actions=None, calendar=None) -> dict:
    """Select first qualified completed close; no outcome enters selection.

    Failed filters may be followed by a later qualified close. Once selected,
    a rejected next-open fill does not create a second entry opportunity.
    """
    symbol_name(symbol)
    validate(rows)
    day = rows[0].time.date()
    if (rows[0].time.time() != dt.time(9,15) or any(b.time.date() != day or b.time.time() >= dt.time(15,15) for b in rows)
            or any(b.time-a.time != dt.timedelta(minutes=5) for a,b in zip(rows,rows[1:]))):
        raise ValueError('Strategy preparation requires a continuous regular session from 09:15')
    if any(b.time.date() >= day for b in prior or []):
        raise ValueError('Strategy history must be strictly earlier sessions')
    if strategy.min_rvol is not None:
        days = set(sorted({bar.time.date() for bar in prior or []})[-strategy.rvol_lookback:])
        prior = volume_history([bar for bar in prior or [] if bar.time.date() in days],rows[0].time.date(),actions or [])
        actions = []
    cal = calendar or DEFAULT_CALENDAR
    cfg = config(no_entries_before=strategy.entry_start,no_entries_after=strategy.entry_cutoff,flat_by=strategy.flat_by)
    candidates = []
    for index in range(strategy.or_minutes//5,len(rows)):
        prefix, now = rows[:index+1], rows[index].time+dt.timedelta(minutes=5)
        if now.time() < dt.time.fromisoformat(strategy.entry_start):
            continue
        if now.time() > dt.time.fromisoformat(strategy.entry_cutoff):
            break
        # Keep the exact original signal path for the reference configuration.
        if strategy.parameters() == BASELINE.parameters():
            base = proposal(prefix,tick,2.)
            if base is not None:
                return dict(index=index,signal=None,candidates=[],baseline_plan=base,status='SELECTED')
            continue
        info = entry(prefix,symbol,now,cfg,or_minutes=strategy.or_minutes,calendar=cal)
        if not info.get('trigger') or info.get('signal_time') != rows[index].time.isoformat():
            continue
        qualified = filters(prefix,strategy,prior=prior,actions=actions,calendar=cal,symbol=symbol)
        record = dict(signal_time=rows[index].time.isoformat(),signal_confirmed_at=now.isoformat(),**qualified)
        candidates.append(record)
        if not info['eligible']:
            record.update(passed=False,reasons=['ZERO_SIGNAL_VOLUME_OR_INELIGIBLE'])
            return dict(index=None,signal=None,candidates=candidates,status='INELIGIBLE_SIGNAL')
        if qualified['passed']:
            # Exit calculations use raw signal close, never public two-decimal
            # display values, so sub-paise tick precision is not discarded.
            info['price'] = rows[index].close
            opening = prefix[:strategy.or_minutes//5]
            info['opening_range'] = dict(info['opening_range'],high=max(b.high for b in opening),low=min(b.low for b in opening))
            return dict(index=index,signal=info,candidates=candidates,status='SELECTED')
        cfg['no_entries_before'] = (now+dt.timedelta(minutes=1)).strftime('%H:%M')
    return dict(index=None,signal=None,candidates=candidates,status='NO_QUALIFIED_SIGNAL' if candidates else 'NO_BREAKOUT')


def execute(prepared: dict, rows: list, symbol: str, tick: float, capital: float, risk: float,
            rr: float, cost_config=None, calendar=None, *, strategy: Strategy, liquidity_config=None) -> dict:
    if prepared['index'] is None:
        return dict(trades=[],net_pnl=0.,rejection=prepared['status'])
    index = prepared['index']
    if prepared.get('baseline_plan') is not None:
        p = proposal(rows[:index+1],tick,rr)
    else:
        cfg = config(no_entries_before=strategy.entry_start,no_entries_after=strategy.entry_cutoff,flat_by=strategy.flat_by)
        try:
            p = plan(prepared['signal'],rows[index].time+dt.timedelta(minutes=5),tick,cfg,strategy.stop,str(rr)+'R',
                     rows[:index+1],calendar,flat_by=strategy.flat_by)
        except ValueError as error:
            return dict(trades=[],net_pnl=0.,rejection='INVALID_EXIT_PLAN',detail=str(error))
    diagnostics = {}
    trade = replay_trade(p,rows[index+1:],capital,risk,'NSE' if symbol.endswith('.NS') else 'BSE',cost_config,
                         liquidity_config=liquidity_config,prior_bar=rows[index],diagnostics=diagnostics)
    if trade is None:
        return dict(trades=[],net_pnl=0.,rejection=diagnostics.get('reason','EXECUTION_OR_RISK_LIMIT'),
                    **(dict(detail=diagnostics['capacity']) if diagnostics else {}))
    return dict(trades=[trade],net_pnl=trade['net_pnl'],rejection=None)
