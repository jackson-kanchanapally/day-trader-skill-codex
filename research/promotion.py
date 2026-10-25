"""Deterministic eligibility checklist; this never changes trading permissions."""
import math

from india.audit import digest

STATES = ('RESEARCH','BACKTESTED','OOS_VALIDATED','PAPER_TRADING','LIVE_ELIGIBLE')


def assess(report: dict, state: str, policy: dict) -> dict:
    required = {'min_market_days','min_trade_days','max_interval_width_r','max_drawdown_fraction',
                'min_positive_fold_fraction','rationale'}
    if state not in STATES or set(policy) != required or not isinstance(policy['rationale'],str) or not policy['rationale'].strip():
        raise ValueError('Promotion requires a known state and a complete documented policy')
    for name in ('min_market_days','min_trade_days'):
        if type(policy[name]) is not int or policy[name] < 1:
            raise ValueError(name+' must be a positive integer justified in rationale')
    for name in ('max_interval_width_r','max_drawdown_fraction','min_positive_fold_fraction'):
        if type(policy[name]) not in (int,float) or not math.isfinite(policy[name]) or policy[name] <= 0:
            raise ValueError(name+' must be positive and finite')
    if policy['max_drawdown_fraction'] > 1 or policy['min_positive_fold_fraction'] > 1:
        raise ValueError('Drawdown/fold fractions must not exceed one')
    payload = {k:v for k,v in report.items() if k != 'report_sha256'}
    if (report.get('report_sha256') != digest(payload) or report.get('mode') != 'walk_forward_research'
            or report.get('execution_enabled') is not False):
        raise ValueError('Promotion requires an intact paper-only walk-forward report')
    reasons = []
    variants = report['variants']
    if not variants:
        reasons.append('No completed backtests')
    if state != 'RESEARCH':
        if report['spec'].get('liquidity_config') is None:
            reasons.append('A documented liquidity scenario is required before OOS validation')
        if (report['spec'].get('cost_config') or {}).get('spread_bps',0) <= 0:
            reasons.append('An explicit positive spread scenario is required before OOS validation')
        regimes = report.get('regimes',{})
        coverage = regimes.get('coverage',{})
        if not report['spec'].get('regime_model') or regimes.get('model') != report['spec']['regime_model']:
            reasons.append('An explicit sourced regime model is required before OOS validation')
        elif not coverage.get('eligible_symbol_sessions') or coverage.get('unknown_symbol_sessions',1):
            reasons.append('Regime evidence must cover every eligible symbol session before OOS validation')
    for variant in variants:
        label = '%s rr=%s slippage=%s' % (variant.get('strategy',{}).get('name','baseline'),variant['rr'],variant['slippage_bps'])
        interval = variant['pooled_trade_r']
        if interval['market_days'] < policy['min_market_days'] or interval['trade_days'] < policy['min_trade_days']:
            reasons.append(label+': insufficient documented market/trade-day coverage')
        if interval.get('estimate') is None or interval['estimate'] <= 0:
            reasons.append(label+': net expectancy after costs is not positive')
        if state != 'RESEARCH':
            if interval.get('status') != 'AVAILABLE' or interval.get('lower') is None or interval['lower'] <= 0:
                reasons.append(label+': positive lower uncertainty bound unavailable')
            elif interval['upper']-interval['lower'] > policy['max_interval_width_r']:
                reasons.append(label+': uncertainty interval too wide')
        for symbol, row in variant['symbols'].items():
            folds = row['folds']
            for fold in folds:
                for partition in ('train','test'):
                    if any(t.get('liquidity',{}).get('status') == 'UNRESOLVED_EXIT_CAPACITY'
                           for t in fold[partition]['trade_details']):
                        reasons.append(label+' '+symbol+': unresolved exit capacity in '+partition+' fold '+str(fold['index']))
            positive = sum((f['test'].get('net_expectancy_r') or 0) > 0 for f in folds)/len(folds)
            if positive < policy['min_positive_fold_fraction']:
                reasons.append(label+' '+symbol+': weak fold robustness')
            if row['out_of_sample']['max_fold_drawdown_fraction'] > policy['max_drawdown_fraction']:
                reasons.append(label+' '+symbol+': drawdown exceeds documented limit')
    if state in ('OOS_VALIDATED','PAPER_TRADING'):
        reasons.append('Independent review of parameter stability, data and liquidity is required; sensitivity summaries alone do not satisfy it')
    if state == 'PAPER_TRADING':
        reasons.append('Forward paper-result validation is not implemented; LIVE_ELIGIBLE cannot be granted')
    if state == 'LIVE_ELIGIBLE':
        reasons.append('No further state; live execution remains disabled')
    target = STATES[STATES.index(state)+1] if state != STATES[-1] else None
    return dict(state=state,requested_next_state=target,eligible=not reasons,reasons=reasons,
                policy=policy,report_sha256=report['report_sha256'],execution_enabled=False,
                note='Research eligibility checklist only. No state, risk limit or execution permission is changed. Sample thresholds are policy assumptions, not proof of statistical adequacy.')
