"""Walk-forward declared ORB experiments; no fitting or portfolio simulation."""
import datetime as dt
import hashlib
import math
from pathlib import Path

from india.audit import digest
from india.data import IST
from india.market import DEFAULT_CALENDAR, costs
from india.liquidity import normalize as liquidity_settings
from india.research import complete_sessions, evaluate
from .metrics import enrich, expectancy_interval, performance, paired_daily_difference, liquidity_summary, regime_breakdown
from .strategies import normalize, prepare, execute
from .store import instant
from .regimes import snapshot as regime_snapshot


def folds(dates: list, train_sessions: int, test_sessions: int, embargo_sessions: int = 0) -> list:
    if (any(type(v) is not int or v < 1 for v in (train_sessions,test_sessions))
            or type(embargo_sessions) is not int or embargo_sessions < 0):
        raise ValueError('Training/test windows must be positive whole sessions; embargo must be nonnegative')
    if dates != sorted(set(dates)):
        raise ValueError('Walk-forward dates must be unique and increasing')
    if len(dates) <= train_sessions+embargo_sessions:
        raise ValueError('Insufficient sessions for training plus an out-of-sample test')
    result, boundary = [], train_sessions
    while boundary+embargo_sessions < len(dates):
        start = boundary+embargo_sessions
        end = min(len(dates),start+test_sessions)
        result.append(dict(train=dates[:boundary], embargo=dates[boundary:start], test=dates[start:end]))
        boundary = end
    return result


def fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    files = sorted(p for name in ('research','india','workflow') for p in (root/name).glob('*.py'))
    files += sorted((root/'legacy').glob('trader-*/*.py'))
    h = hashlib.sha256()
    for path in files:
        h.update(str(path.relative_to(root)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def experiment(store, *, start: dt.date, end: dt.date, as_of, vintage,
               train_sessions: int = 20, test_sessions: int = 5, embargo_sessions: int = 0,
               capital: float = 100000., risk: float = 500., risk_fraction: float = .005,
               rr_values: tuple = (2.,), cost_config=None, calendar=None,
               bootstrap_samples: int = 1000, block_days: int = 5, seed: int = 17,
               strategy_configs=None, liquidity_config=None, regime_model=None) -> dict:
    """Evaluate symbols independently; dates/universe/settings are frozen first.

    Strategy/RR candidates are declared sensitivity experiments. No winner is selected
    using test results. Every fold/partition starts with the same capital.
    """
    strategies = normalize(strategy_configs)
    liquidity = liquidity_settings(liquidity_config)
    cutoff, vintage_at = instant(as_of), instant(vintage)
    if start > end or cutoff > dt.datetime.now(IST) or dt.datetime.combine(end,dt.time(15,15),IST) > cutoff:
        raise ValueError('Use an ordered historical range whose end session has completed by as_of')
    if (not all(type(x) in (int,float) and math.isfinite(x) and x > 0 for x in (capital,risk,risk_fraction))
            or risk_fraction > 1):
        raise ValueError('Capital/risk must be positive finite; risk_fraction must be in (0,1]')
    if (not rr_values or len(rr_values) > 9 or len(set(rr_values)) != len(rr_values)
            or any(type(x) not in (int,float) or not math.isfinite(x) or x <= 0 for x in rr_values)):
        raise ValueError('Declare 1..9 distinct positive R/R values; no unbounded parameter sweep')
    if len(strategies)*len(rr_values) > 24:
        raise ValueError('At most 24 declared strategy/RR combinations before cost stress; reduce the explicit experiment list')
    if cost_config is not None and not isinstance(cost_config, dict):
        raise ValueError('Cost configuration must be a JSON object')
    costs(1,1,0,'NSE',cost_config)
    # Validate uncertainty options even if all strategies produce zero trades.
    expectancy_interval([], [], samples=bootstrap_samples, block_days=block_days, seed=seed)
    cal = calendar or DEFAULT_CALENDAR
    timeline, metadata, day = [], {}, start
    while day <= end:
        universe = store.universe(day, vintage=vintage_at)
        eligible = {}
        for symbol, row in universe.items():
            exchange = 'NSE' if symbol.endswith('.NS') else 'BSE'
            if cal.trading_day(day,exchange):
                cal.require_strategy_session(day,exchange)
                eligible[symbol] = row
        # Calendar days remain in validation even when the supplied universe is
        # empty. Missing whole sessions cannot disappear by taking a file union.
        open_exchanges = [exchange for exchange in ('NSE','BSE') if exchange in cal.document and cal.trading_day(day,exchange)]
        if open_exchanges:
            timeline.append(day)
            metadata[day] = eligible
        day += dt.timedelta(days=1)
    windows = folds(timeline,train_sessions,test_sessions,embargo_sessions)
    if not any(metadata.values()):
        raise ValueError('No eligible point-in-time instruments; import dated instrument evidence')
    symbols = sorted({symbol for members in metadata.values() for symbol in members})
    regimes = regime_snapshot(store.regime_records(vintage=vintage_at) if regime_model is not None else [],
                              timeline,metadata,regime_model)
    datasets, provenance, actions = {}, {}, {}
    for symbol in symbols:
        wanted = [day for day in timeline if symbol in metadata[day]]
        bars, provenance[symbol] = store.load(symbol,start=start,end=end,vintage=vintage_at)
        bars = [b for b in bars if b.time.date() in set(wanted)]
        if not bars:
            raise ValueError('Missing all eligible historical sessions for '+symbol)
        exchange = 'NSE' if symbol.endswith('.NS') else 'BSE'
        datasets[symbol] = complete_sessions(bars,cutoff,cal,exchange,expected_dates=wanted)
        actions[symbol] = store.actions(symbol,vintage=vintage_at,through=end)
    prepared, decisions = {}, {}
    for strategy in strategies:
        prepared[strategy.name], decisions[strategy.name] = {}, {}
        for symbol in symbols:
            prior = []
            prepared[strategy.name][symbol], decisions[strategy.name][symbol] = {}, {}
            for day, rows in datasets[symbol]:
                known_actions = store.actions(symbol,vintage=vintage_at,through=day,
                                              known_at=dt.datetime.combine(day,dt.time(9,15),IST))
                candidate = prepare(rows,symbol,metadata[day][symbol]['tick'],strategy,prior=prior,
                                    actions=known_actions,calendar=cal)
                prepared[strategy.name][symbol][day] = candidate
                decisions[strategy.name][symbol][day.isoformat()] = dict(status=candidate['status'],
                    signal_time=rows[candidate['index']].time.isoformat() if candidate['index'] is not None else None,
                    candidates=candidate['candidates'],known_actions=known_actions if strategy.min_rvol is not None else [],
                    regime=regimes['sessions'][day.isoformat()]['NSE' if symbol.endswith('.NS') else 'BSE'])
                prior.extend(rows)
    baseline_slippage = (cost_config or {}).get('slippage_bps',5.)
    scenarios = sorted(set((baseline_slippage,max(5.,baseline_slippage*2),max(10.,baseline_slippage*4))))
    spec = dict(strategy='fixed-15m-range-low-ORB' if len(strategies) == 1 else 'declared-ORB-configurations',
                strategies=[strategy.document() for strategy in strategies], start=start.isoformat(), end=end.isoformat(),
                as_of=cutoff.isoformat(), vintage=vintage_at.isoformat(), train_sessions=train_sessions,
                test_sessions=test_sessions, embargo_sessions=embargo_sessions, capital=capital,
                risk=risk, risk_fraction=risk_fraction, rr_values=list(rr_values), cost_config=cost_config,
                calendar=cal.document, bootstrap_samples=bootstrap_samples, block_days=block_days,seed=seed,
                liquidity_config=liquidity,regime_model=regime_model,regime_decision_policy=regimes['decision_policy'])
    variants = []
    bootstrap = dict(samples=bootstrap_samples,block_days=block_days,seed=seed)
    for strategy, rr in ((strategy,rr) for strategy in strategies for rr in rr_values):
        for slippage in scenarios:
            config = dict(cost_config or {},slippage_bps=slippage)
            symbol_reports, pooled = {}, []
            for symbol in symbols:
                exchange = 'NSE' if symbol.endswith('.NS') else 'BSE'
                costs(1,1,0,exchange,config)
                days = datasets[symbol]
                row_map = dict(days)
                dated = {day:dict(metadata[day][symbol],regime=regimes['sessions'][day.isoformat()][exchange]['label']) for day,_ in days}
                fold_reports, oos, oos_dates = [], [], []
                for index, window in enumerate(windows):
                    partitions = {}
                    for label in ('train','test'):
                        selected = [(day,row_map[day]) for day in window[label] if day in row_map]
                        rejections = []
                        def backtester(rows, sym, tick, cash, loss_limit, target_rr, charges, session_calendar):
                            day = rows[0].time.date()
                            result = execute(prepared[strategy.name][sym][day],rows,sym,tick,cash,loss_limit,
                                             target_rr,charges,session_calendar,strategy=strategy,liquidity_config=liquidity)
                            if result.get('rejection'):
                                rejections.append(dict(date=day.isoformat(),reason=result['rejection'],detail=result.get('detail')))
                            return result
                        raw = evaluate(selected,symbol,dated[selected[0][0] if selected else next(iter(dated))]['tick'],
                                       capital,risk,rr,config,cal,risk_fraction,day_metadata=dated,backtester=backtester)
                        trades = [dict(enrich(t,row_map[dt.date.fromisoformat(t['entry_time'][:10])],exchange,config),strategy_id=strategy.document()['strategy_id'],strategy_name=strategy.name) for t in raw['trade_details']]
                        partition_regimes = {day.isoformat():[dated[day]['regime']] for day,_ in selected}
                        partitions[label] = dict(performance(trades,capital,[day for day,_ in selected],regime_sessions=partition_regimes,**bootstrap),
                                                 trade_details=trades, sessions=len(selected),rejections=rejections)
                        if label == 'test':
                            oos.extend(trades)
                            oos_dates.extend(day for day,_ in selected)
                    fold_reports.append(dict(index=index,train_start=window['train'][0].isoformat(),train_end=window['train'][-1].isoformat(),
                                             test_start=window['test'][0].isoformat(),test_end=window['test'][-1].isoformat(),
                                             embargo=[d.isoformat() for d in window['embargo']],**partitions))
                # Fold accounts reset. R summaries pool trades; cash summaries
                # explicitly sum fold P&L without pretending to compound folds.
                summary = performance(oos,capital,oos_dates,
                                      regime_sessions={day.isoformat():[dated[day]['regime']] for day in oos_dates},**bootstrap)
                for field in ('ending_equity','return_fraction','equity_curve','max_drawdown_fraction'):
                    summary.pop(field)
                summary['account_basis'] = 'Independent fold accounts reset to initial capital; net P&L is summed attribution, not a compounded return'
                summary['max_fold_drawdown_fraction'] = max(f['test']['max_drawdown_fraction'] for f in fold_reports)
                symbol_reports[symbol] = dict(folds=fold_reports,out_of_sample=summary,trades=oos,
                                              eligible_sessions=len(days),oos_sessions=len(oos_dates))
                pooled.extend(oos)
            pooled.sort(key=lambda t:(t['entry_time'],t['symbol']))
            oos_timeline = [day for window in windows for day in window['test']]
            pooled_regimes = {day.isoformat():sorted({row['label'] for row in regimes['sessions'][day.isoformat()].values()}) for day in oos_timeline}
            variants.append(dict(strategy=strategy.document(),rr=rr,slippage_bps=slippage,symbols=symbol_reports,
                                 liquidity=liquidity_summary(pooled),
                                 by_regime=regime_breakdown(pooled,oos_timeline,pooled_regimes,**bootstrap),
                                 pooled_trade_r=expectancy_interval(pooled,oos_timeline,**bootstrap),
                                 pooled_trades=len(pooled),
                                 capital_basis='Independent per-symbol/fold accounts; pooled R is not a portfolio return'))
    comparisons = []
    evaluation_dates = [day for window in windows for day in window['test']]
    baseline_by_parameters = {(v['rr'],v['slippage_bps']):v for v in variants if v['strategy']['name'] == 'baseline'}
    for variant in variants:
        if variant['strategy']['name'] == 'baseline':
            continue
        baseline = baseline_by_parameters[variant['rr'],variant['slippage_bps']]
        comparison = paired_daily_difference([t for row in variant['symbols'].values() for t in row['trades']],
                                             [t for row in baseline['symbols'].values() for t in row['trades']],
                                             evaluation_dates,**bootstrap)
        comparisons.append(dict(strategy=variant['strategy']['name'],rr=variant['rr'],slippage_bps=variant['slippage_bps'],**comparison))
    sensitivity = []
    for strategy in strategies:
        scenarios_for_strategy = [v for v in variants if v['strategy']['name'] == strategy.name]
        estimates = [v['pooled_trade_r']['estimate'] for v in scenarios_for_strategy if v['pooled_trade_r']['estimate'] is not None]
        sensitivity.append(dict(strategy=strategy.name,evaluated_scenarios=len(scenarios_for_strategy),
                                scenarios_with_trades=len(estimates),positive_scenarios=sum(e > 0 for e in estimates),
                                minimum_expectancy_r=min(estimates) if estimates else None,
                                maximum_expectancy_r=max(estimates) if estimates else None,
                                interpretation='Descriptive cost/RR sensitivity; no winner selection or multiplicity correction'))
    report = dict(schema_version=1,mode='walk_forward_research',execution_enabled=False,spec=spec,
                  experiment_sha256=digest(spec),code_sha256=fingerprint(),
                  data=provenance,instruments={day.isoformat():members for day,members in metadata.items()},
                  corporate_actions=actions,regimes=regimes,variants=variants,decisions=decisions,
                  baseline_comparisons=comparisons,parameter_sensitivity=sensitivity,
                  limitations=['Declared long ORB configurations, bounded explicit sensitivity and no test-driven strategy selection.',
                               'Independent per-symbol and fold accounts; no shared-capital portfolio simulation.',
                               'Raw session prices for execution; RVOL share volumes use only actions published by the decision session open. Action completeness is unverified.',
                               'Source vintages, eligibility and corporate actions are supplied evidence, not independently authenticated.',
                               'Regime labels are optional supplied evidence frozen at 09:15 IST. Missing evidence stays UNKNOWN; no classifier or historical availability is independently verified.',
                               'Intervals are conditional, block-length-sensitive and do not correct repeated experiments or parameter selection.',
                               'Spread is a declared full-spread cash cost; no observed bid/ask quotes are inferred from OHLCV.',
                               'Optional entry/exit capacity uses only the preceding completed candle volume. It is a proxy, not executable order-book depth.',
                               'Unresolved exit-capacity trades remain in metrics and block promotion; their marked P&L does not validate executable expectancy.',
                               'Real liquidity, spread, capacity and corporate-action completeness are not validated.'])
    return dict(report,report_sha256=digest(report))
