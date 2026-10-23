"""Pure health classification; no order or messaging transport."""
import datetime as dt
from .data import IST, timestamp
from .market import trading_day


def health(analysis, as_of, calendar=None):
    day=as_of.astimezone(IST).date()
    if as_of.astimezone(IST).time() < dt.time(9,20):
        day -= dt.timedelta(days=1)
    exchange='NSE' if analysis['symbol'].endswith('.NS') else 'BSE'
    while not trading_day(day,calendar,exchange):
        day -= dt.timedelta(days=1)
    checks={'data_complete':analysis['data_quality']['complete'],
            'latest_session':timestamp(analysis['last_bar']).date()==day,
            'fresh_when_strategy_open':not analysis['strategy_open'] or not analysis['stale']}
    return {'mode':'health','status':'PASS' if all(checks.values()) else 'FAIL',
            'checks':checks,'analysis':analysis,'execution_enabled':False}
