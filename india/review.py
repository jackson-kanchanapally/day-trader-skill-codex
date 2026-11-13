"""Why each alert paper trade won or lost, and lessons fed back into scoring.

diagnose()  explains one paper trade's outcome with rule-based tags measured on
            the same 5-minute candles it was simulated on (opening gap, best and
            worst excursion in R, time to exit, entry slippage, NIFTY's move
            during the trade, whether the stock reached T1 after a stop).
traits()    describes the setup as it looked at ALERT time (RSI stretched, big
            prior-day move, volume spike, against the market...). Only facts
            known before the open, so a lesson can act on the next alert.
lessons()   groups closed paper trades by trait. A trait whose trades lose
            consistently -- at least ``min_trades`` trades, an average at or
            below ``threshold`` R and clearly worse than trades without it --
            becomes an ACTIVE lesson (when enough trades WITHOUT the trait exist to compare), and apply() lowers the score of future
            setups that carry it. Lessons only subtract points; they never add.
            Execution patterns (stops too tight, failed breakouts, gaps) are
            reported as suggestions: changing plan rules needs a backtest first.

Small samples mislead. Nothing becomes active below ``min_trades`` trades, and
every adjustment is recorded on the alert so it can be audited.
"""
import datetime as dt
import json
from collections import Counter
from pathlib import Path

FIVE = dt.timedelta(minutes=5)

TRAITS = {
    'side:BUY': 'Buy setups',
    'side:SELL': 'Short setups',
    'rsi_stretched': 'RSI stretched at the signal (≥65 for buys, ≤35 for shorts)',
    'big_prior_move': 'Signal day moved 3% or more',
    'volume_spike': 'Volume spike (2.5× average or more)',
    'high_volatility': 'High volatility (ATR 3% of price or more)',
    'extended': 'More than 1.2 ATR away from EMA20',
    'counter_trend': 'Against the NIFTY 50 trend',
    'neutral_market': 'NIFTY 50 trend neutral',
    'stale_macd': 'No fresh MACD crossover',
    'marginal_score': 'Base score below 65',
}

# Execution patterns seen in LOSING trades -> what to consider. Report-only.
SUGGESTIONS = {
    'STOP_TOO_TIGHT': 'Stopped out, then reached T1 anyway in {n} of {of} losses. A wider stop '
                      '(e.g. 0.75×ATR with a smaller quantity) may fit better. Backtest before changing.',
    'FAILED_BREAKOUT': 'Breakout failed within 30 minutes in {n} of {of} losses. Consider requiring a '
                       'second 5-minute close beyond the entry.',
    'REVERSED_NEAR_T1': 'Reached +0.7R or more, then reversed to the stop in {n} of {of} losses. Consider '
                        'moving the stop to breakeven at +0.7R.',
    'GAP_AGAINST': 'Opened against the trade in {n} of {of} losses. Consider skipping a setup that '
                   'opens more than 0.25 ATR against the signal close.',
    'MARKET_AGAINST': 'NIFTY moved against the trade in {n} of {of} losses. Consider skipping an entry '
                      'while NIFTY is moving against the trade.',
    'LATE_ENTRY': 'Triggered after 10:45 in {n} of {of} losses. Consider an earlier entry deadline.',
    'CHASED_ENTRY': 'Filled 0.4R or more past the entry in {n} of {of} losses. Consider a tighter no-chase level.',
    'NO_FOLLOW_THROUGH': 'Never reached T1 and closed at square-off in {n} of {of} losses. These setups lacked '
                         'intraday momentum.',
}


def traits(plan, bias=None):
    """Setup traits known at alert time."""
    f = plan.get('features') or {}
    sign = 1 if plan['side'] == 'BUY' else -1
    out = ['side:' + plan['side']]
    rsi = f.get('rsi')
    if rsi is not None and ((sign > 0 and rsi >= 65) or (sign < 0 and rsi <= 35)):
        out.append('rsi_stretched')
    if abs(f.get('change_pct') or 0) >= 3:
        out.append('big_prior_move')
    if (f.get('rel_volume') or 0) >= 2.5:
        out.append('volume_spike')
    if (f.get('atr_pct') or 0) >= 3:
        out.append('high_volatility')
    if f.get('atr') and f.get('ema20') is not None and sign * (f['close'] - f['ema20']) / f['atr'] > 1.2:
        out.append('extended')
    if plan.get('counter_trend') or any('Against the NIFTY' in x for x in plan.get('flags') or []):
        out.append('counter_trend')
    if (bias or plan.get('market_bias')) == 'neutral':
        out.append('neutral_market')
    if f and not f.get('macd_cross'):
        out.append('stale_macd')
    if plan.get('base_score', plan.get('score', 100)) < 65:
        out.append('marginal_score')
    return out


def _at(text):
    return dt.datetime.fromisoformat(text)


def _close_by(bars, when):
    """Close of the last candle that ended by `when` (the first open if none)."""
    prior = [b for b in bars if b[0] + FIVE <= when]
    return prior[-1][4] if prior else bars[0][1]


def _pct(x):
    return '%.1f%%' % abs(x)


def diagnose(plan, trade, bars, market=None):
    """Explain one simulated trade. Returns {outcome, tags, why, metrics}."""
    sign = 1 if plan['side'] == 'BUY' else -1
    f = plan.get('features') or {}
    atr = f.get('atr') or 0
    risk = plan.get('risk_per_share') or abs(plan['entry'] - plan['stop']) or 1
    status, m, tags, why = trade['status'], {}, [], []

    def tag(code, text):
        tags.append(code)
        why.append(text)
    gap_atr = 0.0
    if bars and f.get('close'):
        m['gap_pct'] = round(sign * (bars[0][1] / f['close'] - 1) * 100, 2)   # + = in the trade's favour
        gap_atr = sign * (bars[0][1] - f['close']) / atr if atr else 0.0
        m['gap_atr'] = round(gap_atr, 2)
    events = trade.get('events') or []
    window = None
    if status in ('CLOSED', 'OPEN') and trade.get('entry_time') and bars:
        t0, fill = _at(trade['entry_time']), trade['entry_price']
        last = events[-1] if status == 'CLOSED' and len(events) > 1 else None
        t_end = _at(last['time']) if last else bars[-1][0] + FIVE
        square = bool(last and last['reason'].startswith('Square-off'))
        held = [b for b in bars if t0 <= b[0] < t_end or (b[0] == t_end and not square)]
        moves = [sign * (x - fill) for b in held for x in (b[2], b[3])]
        mfe, mae = max([0.0] + moves) / risk, min([0.0] + moves) / risk
        minutes = int((t_end - t0).total_seconds() // 60)
        slip = sign * (fill - plan['entry']) / risk
        m.update(mfe_r=round(mfe, 2), mae_r=round(mae, 2), minutes_held=minutes, entry_slip_r=round(slip, 2))
        window = (t0, t_end)
        reason, net = trade.get('exit_reason') or '', trade.get('net_pnl') or 0
        if status == 'OPEN':
            why.append('Still open; best so far +%.1fR, worst %.1fR.' % (mfe, mae))
        elif reason.startswith('Stop-loss'):
            if minutes <= 30 and mfe < 0.5:
                tag('FAILED_BREAKOUT', 'Hit the stop %d min after entry without moving even 0.5R in favour, so '
                    'the breakout failed.' % minutes)
            elif mfe >= 0.7:
                tag('REVERSED_NEAR_T1', 'Reached +%.1fR, then reversed to the stop before T1.' % mfe)
            else:
                tag('STOPPED', 'Hit the stop after %d min; the best it did was +%.1fR.' % (minutes, mfe))
            square_at = dt.time.fromisoformat(plan.get('square_off', '15:10'))
            later = next((b for b in bars if b[0] > t_end and b[0].time() < square_at
                          and sign * ((b[2] if sign > 0 else b[3]) - plan['target1']) >= 0), None)
            if later:
                tag('STOP_TOO_TIGHT', 'After the stop it went on to reach T1 at %s, so the stop sat inside '
                    'normal intraday noise.' % (later[0] + FIVE).strftime('%H:%M'))
        elif reason == 'Target 1 + Target 2':
            tag('FOLLOW_THROUGH', 'Ran to T2: the breakout followed through.')
        elif reason.startswith('Target 1 then breakeven'):
            tag('PARTIAL_BREAKEVEN', 'Booked half at T1; the rest came back to the entry price.')
        elif reason.startswith('Target 1'):
            tag('PARTIAL_SQUARE_OFF', 'Booked half at T1; the rest closed at square-off.')
        elif net > 0:
            tag('DRIFT_WIN', 'Moved in favour (best +%.1fR) but never reached T1, then closed at square-off.' % mfe)
        else:
            tag('NO_FOLLOW_THROUGH', 'No follow-through: best +%.1fR, worst %.1fR, closed at square-off.' % (mfe, mae))
        if t0.time() > dt.time(10, 45):
            tag('LATE_ENTRY', 'Triggered late (%s), leaving less time for the move.' % t0.strftime('%H:%M'))
        if slip >= 0.4:
            tag('CHASED_ENTRY', 'Filled %.1fR past the entry level, which added risk and cut the reward.' % slip)
    elif status == 'SKIPPED':
        note = trade.get('note') or ''
        if 'Gapped' in note:
            tag('GAP_PAST_NO_CHASE', 'Opened %s %s the signal close, past the no-chase level; skipped rather '
                'than chased.' % (_pct(m.get('gap_pct', 0)), 'above' if sign > 0 else 'below'))
        else:
            tag('CHASE_SKIP', 'The trigger candle closed past the no-chase level; skipped rather than chased.')
    elif status == 'NOT_TRIGGERED' and bars:
        deadline = dt.time.fromisoformat(plan.get('entry_deadline', '11:30'))
        span = [b for b in bars if (b[0] + FIVE).time() <= deadline] or bars
        best = max(sign * x for b in span for x in (b[2], b[3]))
        short = (sign * plan['entry'] - best) / plan['entry'] * 100
        m['missed_by_pct'] = round(short, 2)
        if short > 0:
            tag('NO_BREAKOUT', 'Never broke the entry level by %s; it stayed %s short.' % (
                plan.get('entry_deadline', '11:30'), _pct(short)))
        else:
            tag('NO_CLOSE_BEYOND', 'Touched the entry level but no 5-minute candle closed beyond it by %s.' %
                plan.get('entry_deadline', '11:30'))
    if bars and gap_atr <= -0.25 and status != 'SKIPPED':
        tag('GAP_AGAINST', 'Opened %s %s the signal close, against the trade.' % (
            _pct(m['gap_pct']), 'below' if sign > 0 else 'above'))
    if market:
        try:
            a, b = (_close_by(market, window[0]), _close_by(market, window[1])) if window else (market[0][1], market[-1][4])
            raw = (b / a - 1) * 100
            m['nifty_pct'] = round(raw, 2)
            if sign * raw <= -0.3:
                tag('MARKET_AGAINST', 'NIFTY %s %s %s, against the trade.' % (
                    'rose' if raw > 0 else 'fell', _pct(raw), 'during the trade' if window else 'that day'))
            elif sign * raw >= 0.3 and window:
                tag('MARKET_TAILWIND', 'NIFTY %s %s during the trade, helping it.' % ('rose' if raw > 0 else 'fell', _pct(raw)))
        except (IndexError, TypeError, ZeroDivisionError):
            pass
    net = trade.get('net_pnl')
    outcome = ('open' if status == 'OPEN' else 'win' if net and net > 0 else 'loss' if net and net < 0 else 'flat') \
        if status in ('CLOSED', 'OPEN') else 'no_trade'
    return {'outcome': outcome, 'tags': tags, 'why': ' '.join(why), 'metrics': m}


def closed_trades(state_dir):
    """Closed trades from FINAL sessions that carry alert-time traits."""
    out = []
    for path in sorted(Path(state_dir).glob('results-*.json')):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or not doc.get('final'):
            continue
        for t in doc.get('trades') or []:
            if t.get('status') == 'CLOSED' and t.get('traits') and t.get('r_multiple') is not None:
                out.append(dict(t, session=doc.get('session')))
    return out


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def lessons(state_dir, min_trades=8, penalty=10, threshold=-0.25, margin=0.25):
    trades = closed_trades(state_dir)
    rows = []
    for code, label in TRAITS.items():
        group = [t for t in trades if code in t['traits']]
        if not group:
            continue
        rest = [t['r_multiple'] for t in trades if code not in t['traits']]
        avg, others = _mean([t['r_multiple'] for t in group]), _mean(rest)
        # Needs a comparison group: if (nearly) every trade has the trait, a loss is the
        # strategy's, not the trait's, and penalising everything would not help ranking.
        comparable = len(rest) >= max(3, min_trades // 2)
        active = len(group) >= min_trades and avg <= threshold and comparable and avg <= others - margin
        rows.append({
            'trait': code, 'label': label, 'trades': len(group),
            'win_rate': round(100 * sum(t['net_pnl'] > 0 for t in group) / len(group), 1),
            'avg_r': round(avg, 2), 'others_avg_r': round(others, 2) if others is not None else None,
            'net_pnl': round(sum(t['net_pnl'] for t in group), 2),
            'status': 'active' if active else 'collecting' if len(group) < min_trades else 'ok',
            'penalty': penalty if active else 0,
            'evidence': '%s averaged %+.2fR over %d paper trade(s)%s' % (
                label, avg, len(group), ' (others %+.2fR)' % others if others is not None else ''),
        })
    order = {'active': 0, 'collecting': 1, 'ok': 2}
    rows.sort(key=lambda r: (order[r['status']], r['avg_r'], r['trait']))
    losses = [t for t in trades if t['net_pnl'] < 0]
    counts = Counter(tag for t in losses for tag in (t.get('diagnosis') or {}).get('tags', []) if tag in SUGGESTIONS)
    patterns = [{'tag': k, 'count': n, 'of': len(losses), 'notable': n >= 3 and n / len(losses) >= 0.3,
                 'suggestion': SUGGESTIONS[k].format(n=n, of=len(losses))} for k, n in counts.most_common()]
    return {'trades': len(trades), 'min_trades': min_trades, 'penalty': penalty,
            'active': [r for r in rows if r['status'] == 'active'], 'traits': rows, 'patterns': patterns}


def apply(plan, learned, max_penalty=20):
    """Lower a plan's score for each active lesson it matches (capped). Mutates and returns plan."""
    hits = [r for r in (learned or {}).get('active', []) if r['trait'] in plan.get('traits', [])]
    total = min(max_penalty, sum(r['penalty'] for r in hits))
    plan['lessons'] = [{'trait': r['trait'], 'label': r['label'], 'penalty': r['penalty'], 'evidence': r['evidence']}
                       for r in hits]
    plan['lesson_penalty'] = total
    if total:
        plan['score'] = round(plan['score'] - total, 1)
    return plan
