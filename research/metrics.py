"""Net-R performance with reproducible moving-block market-day intervals."""
import datetime as dt
import math
import random
from statistics import mean

from india.data import timestamp
from india.market import costs
from india.research import metrics as cash_metrics


def enrich(trade: dict, bars: list, exchange: str, config=None) -> dict:
    """Excursions are lower bounds: omit unknown exit-bar price ordering."""
    risk = trade['max_loss_estimate']
    if risk <= 0:
        raise ValueError('Trade requires positive initial cost-inclusive risk')
    start, end = timestamp(trade['entry_time']), timestamp(trade['exit_bar'])
    prior = [b for b in bars if start <= b.time < end]
    high = max([trade['entry'], trade['exit']]+[b.high for b in prior])
    low = min([trade['entry'], trade['exit']]+[b.low for b in prior])
    charges = costs(trade['entry'], trade['exit'], trade['qty'], exchange, config)
    return dict(trade, initial_risk_inr=risk, net_r=trade['net_pnl']/risk,
                mae_r=max(0, trade['entry']-low)*trade['qty']/risk,
                mfe_r=max(0, high-trade['entry'])*trade['qty']/risk,
                excursion_method='lower bounds; exit-bar extrema omitted',
                modeled_slippage_inr=charges['slippage'], modeled_spread_inr=charges.get('spread',0.),
                realized_cost_estimate=charges)


def quantile(values: list, fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered)-1)*fraction
    low = int(position)
    return ordered[low]+(ordered[min(low+1,len(ordered)-1)]-ordered[low])*(position-low)


def expectancy_interval(trades: list, sessions: list, *, samples: int = 1000,
                        block_days: int = 5, seed: int = 17, confidence: float = .95) -> dict:
    """Resample contiguous day blocks, keeping every same-day trade together.

    Sessions include zero-trade dates. Estimate is trade-weighted mean net R.
    These percentile intervals do not correct strategy selection/multiple tests.
    """
    if (any(type(x) is not int or x < 1 for x in (samples, block_days))
            or samples > 100000 or type(seed) is not int or not 0 < confidence < 1):
        raise ValueError('Bootstrap requires 1..100000 samples, positive block_days, integer seed and 0<confidence<1')
    days = [d.isoformat() if isinstance(d, dt.date) else d for d in sessions]
    if days != sorted(set(days)):
        raise ValueError('Bootstrap sessions must be unique and chronological')
    grouped = {day: [] for day in days}
    for trade in trades:
        day = timestamp(trade['entry_time']).date().isoformat()
        if day not in grouped or not math.isfinite(trade['net_r']):
            raise ValueError('Trade does not belong to the supplied session set or has invalid R')
        grouped[day].append(trade['net_r'])
    result = dict(method='moving-block market-day percentile bootstrap', confidence=confidence,
                  block_days=block_days, samples=samples, seed=seed, market_days=len(days),
                  trade_days=sum(bool(v) for v in grouped.values()), lower=None, upper=None,
                  estimate=mean(t['net_r'] for t in trades) if trades else None,
                  valid_resamples=0, limitations=['Conditional on the supplied sample and chosen block length; not a guarantee.',
                                                'No multiple-comparison or strategy-selection correction.'])
    if samples*(1-confidence)/2 < 10:
        return dict(result, status='UNAVAILABLE', reason='Expected fewer than 10 bootstrap draws in each tail; increase resamples for quantile precision')
    if len(days) < 2*block_days or result['trade_days'] < 2:
        return dict(result, status='UNAVAILABLE', reason='Need at least two day blocks and two trade days; this is a calculability floor, not statistical adequacy')
    rng, estimates = random.Random(seed), []
    starts = list(range(len(days)-block_days+1))
    for _ in range(samples):
        selected = []
        while len(selected) < len(days):
            first = rng.choice(starts)
            selected.extend(days[first:first+block_days])
        values = [r for day in selected[:len(days)] for r in grouped[day]]
        if values:
            estimates.append(mean(values))
    if len(estimates) < samples*.95:
        return dict(result, status='UNAVAILABLE', reason='Too many resamples contain no trades', valid_resamples=len(estimates))
    alpha = (1-confidence)/2
    return dict(result, status='AVAILABLE', lower=quantile(estimates,alpha), upper=quantile(estimates,1-alpha),
                valid_resamples=len(estimates), effective_blocks=len(days)/block_days,
                caution='Few independent blocks; interval is unstable' if len(days)/block_days < 20 else 'Block length remains an assumption; inspect sensitivity')


def performance(trades: list, capital: float, sessions: list, *, regime_sessions=None, **bootstrap) -> dict:
    base = cash_metrics(trades, capital)
    winners, losers = [t['net_pnl'] for t in trades if t['net_pnl'] > 0], [t['net_pnl'] for t in trades if t['net_pnl'] < 0]
    groups = {}
    for label in ('time_of_day', 'sector', 'regime'):
        buckets = {}
        for trade in trades:
            key = timestamp(trade['entry_time']).strftime('%H:00') if label == 'time_of_day' else trade.get(label,'UNKNOWN')
            buckets.setdefault(key,[]).append(trade)
        groups[label] = {key:dict(trades=len(rows), net_pnl=sum(t['net_pnl'] for t in rows),
                                 net_expectancy_r=mean(t['net_r'] for t in rows)) for key,rows in sorted(buckets.items())}
    if regime_sessions is not None:
        groups['regime'] = regime_breakdown(trades,sessions,regime_sessions,**bootstrap)
    return dict(base, average_win_inr=mean(winners) if winners else None,
                average_loss_inr=mean(losers) if losers else None,
                net_expectancy_r=mean(t['net_r'] for t in trades) if trades else None,
                average_mae_r=mean(t['mae_r'] for t in trades) if trades else None,
                average_mfe_r=mean(t['mfe_r'] for t in trades) if trades else None,
                average_modeled_slippage_inr=mean(t['modeled_slippage_inr'] for t in trades) if trades else None,
                average_modeled_spread_inr=mean(t.get('modeled_spread_inr',0.) for t in trades) if trades else None,
                liquidity=liquidity_summary(trades),
                expectancy_interval=expectancy_interval(trades,sessions,**bootstrap), by=groups)


def regime_breakdown(trades: list, sessions: list, labels_by_day: dict, **bootstrap) -> dict:
    """Keep original day adjacency when bootstrapping conditional regime R.

    Non-regime days contain no selected trades but remain in the resampling
    timeline. Selecting only matching dates would join disjoint regime spells.
    A day may have different NSE and BSE labels in a pooled independent account.
    """
    days = [day.isoformat() if isinstance(day,dt.date) else day for day in sessions]
    expectancy_interval([],days,**bootstrap)  # Validate empty/zero-trade groupings too.
    if set(days) != set(labels_by_day) or any(not isinstance(labels,list) or any(not isinstance(v,str) or not v for v in labels)
                                             for labels in labels_by_day.values()):
        raise ValueError('Regime grouping requires explicit label lists for every evaluation session')
    for trade in trades:
        day = timestamp(trade['entry_time']).date().isoformat()
        if day not in labels_by_day or trade.get('regime','UNKNOWN') not in labels_by_day[day]:
            raise ValueError('Trade regime does not match the supplied session evidence')
    result = {}
    for label in sorted({v for labels in labels_by_day.values() for v in labels}):
        selected = [t for t in trades if t.get('regime','UNKNOWN') == label]
        matching = [day for day in days if label in labels_by_day[day]]
        traded_days = {timestamp(t['entry_time']).date().isoformat() for t in selected}
        winners,losers = [t['net_pnl'] for t in selected if t['net_pnl'] > 0], [t['net_pnl'] for t in selected if t['net_pnl'] < 0]
        result[label] = dict(trades=len(selected),eligible_market_days=len(matching),zero_trade_days=len(matching)-len(traded_days),
            net_pnl=sum(t['net_pnl'] for t in selected),net_expectancy_r=mean(t['net_r'] for t in selected) if selected else None,
            win_rate=len(winners)/len(selected) if selected else None,
            average_win_inr=mean(winners) if winners else None,average_loss_inr=mean(losers) if losers else None,
            profit_factor=-sum(winners)/sum(losers) if losers else None,
            expectancy_interval=expectancy_interval(selected,days,**bootstrap),liquidity=liquidity_summary(selected),
            interpretation='Conditional regime attribution; full evaluation-day blocks retained. No selection correction or independent regime validation.')
    return result


def liquidity_summary(trades: list) -> dict:
    screened = [t for t in trades if 'liquidity' in t]
    unresolved = sum(t['liquidity']['status'] != 'PROXY_PASSED' for t in screened)
    return dict(screened_trades=len(screened), unscreened_trades=len(trades)-len(screened),
                unresolved_exit_trades=unresolved,
                inference_status='UNRESOLVED_CAPACITY' if unresolved else 'CONDITIONAL_SCENARIO',
                note='Unresolved exits remain in cash/R results; these results cannot validate executable expectancy')


def paired_daily_difference(candidate: list, baseline: list, sessions: list, **bootstrap) -> dict:
    """Compare daily sums of R, including zeros, with matched day-block draws.

    Internal observations represent paired days, never additional market trades.
    Across symbols this is an independent-account attribution, not a return.
    """
    days = [day.isoformat() if isinstance(day,dt.date) else day for day in sessions]
    values = {day:0. for day in days}
    for sign, trades in ((1,candidate),(-1,baseline)):
        for trade in trades:
            day = timestamp(trade['entry_time']).date().isoformat()
            if day not in values:
                raise ValueError('Comparison trade outside evaluation dates')
            values[day] += sign*trade['net_r']
    observations = [dict(entry_time=day+'T09:15:00+05:30',net_r=values[day]) for day in days]
    interval = expectancy_interval(observations,days,**bootstrap)
    interval.pop('trade_days')
    interval['paired_days'] = len(days)
    interval['method'] = 'paired moving-block percentile interval of daily sums of net R'
    unresolved = any(t.get('liquidity',{}).get('status') == 'UNRESOLVED_EXIT_CAPACITY' for t in candidate+baseline)
    return dict(interval,metric='candidate minus baseline daily sum of net R; independent-account attribution',
                execution_status='UNRESOLVED_CAPACITY' if unresolved else 'CONDITIONAL_SCENARIO',
                daily_difference=values,baseline_trades=len(baseline),candidate_trades=len(candidate))
