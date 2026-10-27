"""Original two-stage scanner: batched close screening, full OHLCV confirmation.

Close-only spark data is never converted into invented OHLCV candles. Only
the ordinary chart/provider path can confirm a signal or supply a daily hit.
"""
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request
import urllib.error

from india.data import IST
from .common import digest, exchange_of, upstream, write


def summaries(names, now, mode, calendar, batch_size=20, or_minutes=15):
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 20:
        raise ValueError('batch-size must be 1..20')
    if mode == 'orb' and (or_minutes < 5 or or_minutes > 120 or or_minutes % 5):
        raise ValueError('Opening range must be a multiple of 5 from 5..120 minutes')
    interval, rng = ('1d', '3mo') if mode == 'daily' else ('5m', '1d')
    found, errors, requests = {}, [], 0
    for offset in range(0, len(names), batch_size):
        batch = names[offset:offset+batch_size]
        path = '/v7/finance/spark?'+urllib.parse.urlencode(dict(symbols=','.join(batch), range=rng, interval=interval))
        payload = None
        for host in ('query2', 'query1'):
            requests += 1
            try:
                request = urllib.request.Request('https://'+host+'.finance.yahoo.com'+path, headers={'User-Agent':'Mozilla/5.0'})
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = json.load(response)
                if not isinstance(payload.get('spark', {}).get('result'), list):
                    raise ValueError('Incomplete Yahoo spark response')
                break
            except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
                errors.append(dict(symbols=batch, provider='yahoo-spark', error=str(exc)))
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                payload = None
        if payload is None:
            continue  # ordinary whole-chart retrieval will account for each name
        seen = set()
        for item in payload['spark']['result']:
            if not isinstance(item, dict):
                errors.append(dict(symbols=batch, provider='yahoo-spark', error='Malformed summary item'))
                continue
            symbol = str(item.get('symbol', '')).upper()
            if symbol not in batch:
                continue
            if symbol in seen:
                found.pop(symbol, None)
                errors.append(dict(symbol=symbol, provider='yahoo-spark', error='Duplicate summary symbol'))
                continue
            seen.add(symbol)
            try:
                raw = item['response'][0]
                meta = raw['meta']
                if meta.get('symbol', '').upper() != symbol or meta.get('currency') != 'INR':
                    raise ValueError('Summary symbol/currency mismatch')
                if meta.get('dataGranularity', interval) != interval:
                    raise ValueError('Summary interval mismatch')
                quotes = raw['indicators']['quote'][0]
                rows = []
                for epoch, close in zip(raw.get('timestamp', []), quotes.get('close', [])):
                    if epoch is None or close is None:
                        continue
                    when = dt.datetime.fromtimestamp(epoch, IST)
                    if isinstance(close, bool) or not isinstance(close, (float, int)) or not 0 < close < float('inf'):
                        raise ValueError('Invalid summary close')
                    if mode == 'daily':
                        from india.market import daily_completed
                        if not daily_completed(when.date(), now, calendar, exchange_of(symbol)):
                            continue
                        if not calendar.trading_day(when.date(), exchange_of(symbol)):
                            raise ValueError('Unexpected non-session daily summary')
                    elif (when.date() != now.date() or when.second or when.minute % 5
                          or not dt.time(9, 15) <= when.time() < dt.time(15, 15)
                          or when+dt.timedelta(minutes=5) > now):
                        continue
                    rows.append((when, float(close)))
                if any(a[0] >= b[0] for a, b in zip(rows, rows[1:])):
                    raise ValueError('Duplicate/unordered summary timestamps')
                if mode == 'daily' and len({r[0].date() for r in rows}) != len(rows):
                    raise ValueError('Duplicate daily summary dates')
                if mode == 'daily':
                    expected = calendar.last_completed_session(now, exchange_of(symbol))
                    complete = len(rows) >= 30 and rows[-1][0].date() == expected
                    tags = upstream('scanner', calendar).find_setups([r[1] for r in rows])[0] if complete else []
                    candidate = bool(tags) if complete else True
                else:
                    opening = now.replace(hour=9, minute=15, second=0, microsecond=0)
                    count = max(0, int((min(now, opening.replace(hour=15, minute=15))-opening).total_seconds()//300))
                    complete = [r[0] for r in rows] == [opening+dt.timedelta(minutes=5*i) for i in range(count)]
                    n = or_minutes//5
                    candidate = any(c >= max(x[1] for x in rows[:n]) for _, c in rows[n:]) if complete and len(rows) > n else not complete
                # Incomplete/absent summaries always take the full-data path.
                observed = meta.get('regularMarketTime')
                volume = meta.get('regularMarketVolume')
                dated_volume = (isinstance(observed, (int, float)) and not isinstance(observed, bool)
                                and dt.datetime.fromtimestamp(observed, IST) <= now
                                and dt.datetime.fromtimestamp(observed, IST).date() == now.date()
                                and isinstance(volume, (int, float)) and not isinstance(volume, bool) and 0 <= volume < float('inf'))
                found[symbol] = dict(candidate=candidate, close=rows[-1][1] if rows else None,
                                     as_of=rows[-1][0].isoformat() if rows else None,
                                     source='Yahoo spark unofficial; close-only screening', complete=complete,
                                     turnover_proxy=rows[-1][1]*volume if dated_volume and rows else 0)
            except (ValueError, OSError, KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
                errors.append(dict(symbol=symbol, provider='yahoo-spark', error=str(exc)))
    return found, errors, requests


def batch_scan(data, names, now, cfg, mode, calendar, min_price, min_volume,
               workers, max_confirm, eligible_only, batch_size, options):
    from .strategy import scan
    started = time.monotonic()
    summary, errors, requests = summaries(names, now, mode, calendar, batch_size, options.get('or_minutes', 15))
    full = [s for s in names if s not in summary or summary[s]['candidate']]
    capped = []
    if mode == 'orb' and max_confirm is not None:
        full.sort(key=lambda s: (-summary.get(s, {}).get('turnover_proxy', 0), s))
        full, capped = full[:max_confirm], full[max_confirm:]
    doc = scan(data, full, now, cfg, mode, calendar, min_price, min_volume,
               workers, None, eligible_only, batch_size=batch_size,
               use_summaries=False, **options)
    for symbol in names:
        if symbol not in full:
            row = summary.get(symbol, {})
            doc['results'].append(dict(symbol=symbol, status='SKIPPED_CONFIRMATION_CAP' if symbol in capped else 'NO_SETUP' if mode == 'daily' else 'NO_CANDIDATE',
                                       price=row.get('close'), as_of=row.get('as_of'), volume=None, setups=[],
                                       source=row.get('source', 'Summary unavailable'), eligible=False, close_only_screen=True))
    doc['skipped'].extend(dict(symbol=s, reason='confirmation cap; summary candidate or unavailable summary') for s in capped)
    doc['universe'] = len(names)
    doc['scanned'] += sum(s not in full and s not in capped for s in summary)
    returned = set(summary) | {r['symbol'] for r in doc['results'] if r.get('status') not in ('FAILED', 'SKIPPED_CONFIRMATION_CAP')}
    returned -= {r['symbol'] for r in doc['failed'] if r['symbol'] not in summary}
    doc['counts'].update(requested=len(names), scanned=doc['scanned'], fetched=len(returned),
                         screened=sum(r['complete'] for r in summary.values()), full_snapshots=len(full),
                         full_fetched=len(full)-len(doc['failed']), skipped=len(capped),
                         candidates=doc['counts']['candidates']+len(capped))
    doc['fetch'] = dict(doc.get('fetch', {}), summary_requests=requests, batch_size=batch_size,
                        summary_errors=errors, total_elapsed_seconds=round(time.monotonic()-started, 4))
    doc['source'] = 'See per-symbol sources'
    doc['confirmation_rank_basis'] = 'Dated current-session summary close × volume proxy, then symbol; unavailable dated volume ties at zero. This is not executable liquidity.'
    order = {symbol: i for i, symbol in enumerate(names)}
    doc['results'].sort(key=lambda r: order[r['symbol']])
    return doc


def archive_scan(workspace, doc, data):
    """Save the exact scan and its already-fetched daily hit evidence."""
    root = Path(workspace)
    suffix = digest(doc)[:12]
    run = root/'scans'/(doc['mode']+'-'+doc['as_of'].replace(':', '').replace('+', '_')+'-'+suffix)
    write(run/'watchlist.json', doc)
    if doc['mode'] == 'daily':
        write(root/'daily-watchlist.json', doc)
        for hit in doc['hits']:
            # history() returns the captured cache; it must not create a new
            # snapshot to attach a chart to an earlier decision.
            rows = data._cache.get((hit['symbol'], '1d'))
            if rows:
                write(run/'candles'/(hit['symbol']+'.json'), dict(symbol=hit['symbol'], cutoff=doc['as_of'], source=hit['source'],
                      bars=[dict(time=b.time.isoformat(), open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume) for b in rows]))
    elif doc['mode'] == 'orb':
        from .intraday_index import DailySourceChanged, publish
        from india.data import timestamp
        evidence = {}
        cutoff = timestamp(doc['as_of'])
        for symbol in {r['symbol'] for r in doc['results']} | {r['symbol'] for r in doc['failed']}:
            rows = getattr(data, '_cache', {}).get((symbol, '5m'))
            if not rows:
                continue  # summary-only screening has no OHLCV to archive
            captured = [b for b in rows if b.time+dt.timedelta(minutes=5) <= cutoff]
            source = getattr(data, 'sources', {}).get((symbol, '5m'), data.source)
            snapshot = dict(symbol=symbol, interval='5m', cutoff=doc['as_of'], source=source,
                            bars=[dict(time=b.time.isoformat(), open=b.open, high=b.high, low=b.low,
                                       close=b.close, volume=b.volume) for b in captured],
                            provider_attempts=getattr(data, 'attempts', {}).get((symbol, '5m'), []))
            encoded = gzip.compress(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode(), mtime=0)
            path = run/'candles'/(symbol+'.json.gz')
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_bytes() != encoded:
                raise ValueError('Captured ORB candle evidence differs from its existing archive')
            path.write_bytes(encoded)
            evidence[symbol] = dict(path='candles/'+path.name, sha256=hashlib.sha256(encoded).hexdigest(),
                                    bars=len(captured), source=source)
        write(run/'evidence.json', dict(cutoff=doc['as_of'], interval='5m', symbols=evidence,
                                       note='Captured whole provider snapshots; no OHLCV invented from close summaries'))
        # A separate scan workspace can live anywhere beneath the dashboard
        # workspace. Its immutable archive is bound to that exact daily scan.
        receipt = dict(status='NO_DAILY_SOURCE_BINDING')
        for parent in (root, *root.parents) if doc.get('daily_origin') else ():
            if (parent/'daily-watchlist.json').is_file():
                try:
                    published = publish(parent, run/'watchlist.json')
                    receipt = dict(status='PUBLISHED', snapshot=published)
                except DailySourceChanged as exc:
                    receipt = dict(status='SOURCE_CHANGED', reason=str(exc), daily_origin=doc['daily_origin'])
                except (ValueError, OSError) as exc:
                    receipt = dict(status='REJECTED', reason=str(exc), daily_origin=doc['daily_origin'])
                break
        write(run/'publication.json', receipt)
    return run
