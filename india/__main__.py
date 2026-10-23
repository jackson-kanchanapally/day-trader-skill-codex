import argparse
import datetime as dt
import json
import sys
from .data import CSVProvider, YahooProvider, IST, timestamp, symbol_name
from .analysis import report, backtest, indicators
from .upstox import UpstoxProvider

def main():
    p=argparse.ArgumentParser(description='NSE/BSE research only; no execution integration')
    p.add_argument('command',choices=['analyze','backtest','history'])
    p.add_argument('symbol')
    p.add_argument('--csv')
    p.add_argument('--provider',choices=['upstox','yahoo'],default='yahoo')
    p.add_argument('--instruments',help='Local Upstox instrument master JSON or JSON.gz')
    p.add_argument('--days',type=int,default=7,help='Upstox calendar-day lookback, 1-28')
    p.add_argument('--as-of',help='Explicit historical replay timestamp with timezone')
    p.add_argument('--tick',type=float,required=True,help='Verified instrument tick size in INR')
    p.add_argument('--capital',type=float,default=100000)
    p.add_argument('--risk',type=float,default=500)
    p.add_argument('--rr',type=float,default=2)
    p.add_argument('--cost-config',help='JSON override of cost rates')
    a=p.parse_args()
    try:
        symbol_name(a.symbol)
        now=timestamp(a.as_of) if a.as_of else dt.datetime.now(IST)
        source = CSVProvider(a.csv) if a.csv else (YahooProvider() if a.provider == 'yahoo' else UpstoxProvider(now,a.days,a.instruments))
        bars=source.history(a.symbol,'1d' if a.command=='history' else '5m')
        config=json.load(open(a.cost_config)) if a.cost_config else None
        if a.command=='analyze': result=report(bars,now,a.symbol,a.tick,a.capital,a.risk,a.rr,config)
        elif a.command=='backtest': result=backtest([b for b in bars if b.time+dt.timedelta(minutes=5)<=now],a.symbol,a.tick,a.capital,a.risk,a.rr,config)
        else:
            bars=[b for b in bars if b.time.date()<now.date()]
            if not bars: raise ValueError('No completed historical sessions')
            result={'mode':'historical','bars':[dict(time=b.time.isoformat(),open=b.open,high=b.high,low=b.low,close=b.close,volume=b.volume) for b in bars]}
        result.update(source='CSV export' if a.csv else ('Upstox V3 candles' if a.provider == 'upstox' else 'Yahoo unofficial historical feed; may be delayed'),historical_replay=bool(a.as_of))
        if isinstance(source, UpstoxProvider): result['instrument_key'] = source.instrument_key
        print(json.dumps(result,indent=2,allow_nan=False))
        return 0
    except (ValueError,OSError,KeyError,TypeError,IndexError) as e:
        print('Data/analysis unavailable: '+str(e),file=sys.stderr); return 1

if __name__=='__main__': sys.exit(main())
