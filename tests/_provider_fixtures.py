"""Original synthetic provider payloads; no downloaded prices or issuer data.

Prices follow a small repeating sequence below the opening-range high. NSE
timestamps deliberately encode IST wall time as UTC, matching the adapter's
wire format. Yahoo timestamps use actual epoch seconds and include an all-null
15:15 slot plus an off-grid update to exercise auction/live-tick exclusion.
"""
import datetime as dt

from india.data import IST


def _intraday_rows():
    opening = dt.datetime(2026, 10, 23, 9, 15, tzinfo=IST)
    for index in range(75):
        when = opening + dt.timedelta(minutes=5 * index)
        close = round(100 + (index % 9) / 10, 2)
        yield when, dict(open=100, high=103 if index < 3 else 101,
                         low=99, close=close, volume=1000 + 17 * index)


def nse_intraday():
    rows = []
    for when, prices in _intraday_rows():
        wall = (when + dt.timedelta(seconds=59)).replace(tzinfo=dt.timezone.utc)
        rows.append(dict(time=int(wall.timestamp() * 1000), **prices))
    return dict(status=True, fixture=True, data=rows)


def nse_daily():
    days = (1, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 19, 21, 22, 23)
    rows = []
    for index, day in enumerate(days):
        when = dt.datetime(2026, 10, day, 15, 30, tzinfo=dt.timezone.utc)
        close = 100 + index / 2
        rows.append(dict(time=int(when.timestamp() * 1000), open=close,
                         high=close + 1, low=close - 1, close=close,
                         volume=2000 + index * 100))
    return dict(status=True, fixture=True, data=rows)


def yahoo_intraday():
    times, quote = [], {key: [] for key in ('open', 'high', 'low', 'close', 'volume')}
    for index, (when, prices) in enumerate(_intraday_rows()):
        times.append(int(when.timestamp()))
        for key in quote:
            quote[key].append(None if index == 72 else prices[key])
    times.append(int(dt.datetime(2026, 10, 23, 15, 29, 27, tzinfo=IST).timestamp()))
    for values in quote.values():
        values.append(None)
    return dict(fixture=True, chart=dict(error=None, result=[dict(
        meta=dict(symbol='DEMO.NS', currency='INR', dataGranularity='5m',
                  exchangeTimezoneName='Asia/Kolkata'),
        timestamp=times, indicators=dict(quote=[quote]))]))
