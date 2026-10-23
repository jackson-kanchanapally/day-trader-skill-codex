"""Dated Indian news, issuer financial exports and public insider disclosures."""
import csv
import datetime as dt
from pathlib import Path
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .common import clock, digest, envelope, exchange_of, number, read, upstream


def news(symbols, now, state, hours=24, offline=None, feed_url=None, announcements=None, all_items=False):
    """Reuse the upstream RSS parser; maintain one ID per published item."""
    number(hours, 'hours')
    items, errors, seen = [], [], dict(state.get('seen', {}))
    m = upstream('news')
    for symbol in symbols:
        exchange_of(symbol)
        try:
            if offline:
                raw = (Path(offline)/('yahoo-'+symbol+'.xml')).read_bytes()
            else:
                template = feed_url or 'https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=IN&lang=en-IN'
                url = template.replace('{symbol}', urllib.parse.quote(symbol))
                if not url.startswith('https://'):
                    raise ValueError('News feed must be HTTPS')
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'IndianTradingResearch/1.0'}), timeout=20) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('News feed exceeds 2 MB')
            parsed = m.parse_rss(raw, symbol)
            for item in parsed:
                when = item.get('time')
                if when is None:
                    continue
                published = clock(when.isoformat())
                if when.tzinfo is None:
                    published = clock(when.isoformat()+'+00:00')
                item['published_at'] = published.isoformat()
                item.pop('time', None)
                item['source'] = 'Supplied RSS' if feed_url or offline else 'Yahoo Finance RSS (India symbols)'
                items.append(item)
        except (ValueError, OSError, ET.ParseError, AttributeError) as exc:
            errors.append(dict(symbol=symbol, error=str(exc)))
    if announcements:
        for row in read(announcements)['items']:
            if row['symbol'] in symbols:
                items.append(dict(id='announcement:'+digest(row), ticker=row['symbol'], title=row['title'],
                                  link=row['source'], published_at=clock(row['published_at']).isoformat(),
                                  source='User-supplied exchange/issuer announcement export', kind='filing'))
    fresh = []
    for item in items:
        published = clock(item['published_at'])
        if not now-dt.timedelta(hours=hours) <= published <= now:
            continue
        key = digest([item['ticker'], item['id']])
        if all_items or key not in seen:
            fresh.append(item)
        seen[key] = published.isoformat()
    seen = {key: value for key, value in seen.items() if clock(value) >= now-dt.timedelta(days=30)}
    fresh.sort(key=lambda item: item['published_at'], reverse=True)
    m.KEYWORDS = [(label, pattern) for label, pattern in m.KEYWORDS if label not in ('macro', 'regulatory')]
    m.KEYWORDS.extend([('macro', r'\bRBI\b|\bMPC\b|\brepo rate\b|\bCPI\b|\binflation\b'),
                       ('regulatory', r'\bSEBI\b|\bNSE\b|\bBSE\b|\bNCLT\b|\bCCI\b')])
    m.tag(fresh, {symbol: None for symbol in symbols})
    return envelope('news', now, as_of=now.isoformat(), items=fresh, errors=errors,
                    coverage='RSS headlines and supplied announcements; not complete exchange coverage'), {'seen': seen}


def financials(path, symbol, now):
    """Accept normalized, sourced quarters; never infer issuer facts from prices."""
    doc = read(path)
    if doc['symbol'] != symbol or doc.get('currency') != 'INR' or doc.get('basis') not in ('consolidated', 'standalone'):
        raise ValueError('Financial export requires matching symbol, INR and explicit reporting basis')
    multiplier = {'INR': 1, 'INR_thousand': 1000, 'INR_lakh': 100000,
                  'INR_million': 1000000, 'INR_crore': 10000000}.get(doc.get('unit'))
    if multiplier is None:
        raise ValueError('Specify an explicit INR financial reporting unit')
    quarters, seen = [], set()
    for row in doc['quarters']:
        published = clock(row['published_at'])
        if published > now:
            continue
        start, end = dt.date.fromisoformat(row['period_start']), dt.date.fromisoformat(row['period_end'])
        if not 75 <= (end-start).days <= 100 or end > published.date():
            raise ValueError('Financial rows must be completed standalone quarters, not cumulative YTD')
        if (start, end) in seen:
            raise ValueError('Duplicate period/restatement: select the version known at the cutoff')
        seen.add((start, end))
        if not str(row.get('source', '')).startswith('https://'):
            raise ValueError('Financial row requires the original HTTPS filing source')
        revenue = number(row['revenue'], 'revenue', zero=True)*multiplier
        profit, eps = float(row['net_profit'])*multiplier, float(row['diluted_eps'])
        number(abs(profit), 'net_profit', zero=True)
        number(abs(eps), 'diluted_eps', zero=True)
        quarters.append(dict(period_start=start.isoformat(), period_end=end.isoformat(),
                             published_at=published.isoformat(), source=row['source'], revenue=revenue,
                             net_profit=profit, diluted_eps=eps,
                             net_margin_pct=100*profit/revenue if revenue else None))
        for field, margin in (('gross_profit', 'gross_margin_pct'), ('operating_profit', 'operating_margin_pct')):
            amount = float(row[field])*multiplier if row.get(field) is not None else None
            if amount is not None:
                number(abs(amount), field, zero=True)
            quarters[-1][field] = amount
            quarters[-1][margin] = 100*amount/revenue if amount is not None and revenue else None
    quarters.sort(key=lambda q: q['period_end'])
    for row in quarters:
        end = dt.date.fromisoformat(row['period_end'])
        previous = next((q for q in quarters if dt.date.fromisoformat(q['period_end']).year == end.year-1 and q['period_end'][5:] == row['period_end'][5:]), None)
        row['revenue_yoy_pct'] = 100*(row['revenue']/previous['revenue']-1) if previous and previous['revenue'] else None
        row['profit_yoy_pct'] = 100*(row['net_profit']/previous['net_profit']-1) if previous and previous['net_profit'] > 0 else None
        row['eps_yoy_pct'] = 100*(row['diluted_eps']/previous['diluted_eps']-1) if previous and previous['diluted_eps'] > 0 else None
    trailing = quarters[-4:]
    contiguous = len(trailing) == 4 and all((dt.date.fromisoformat(b['period_start'])-dt.date.fromisoformat(a['period_end'])).days == 1 for a, b in zip(trailing, trailing[1:]))
    return envelope('analyst', now, symbol=symbol, basis=doc['basis'], status='AVAILABLE' if quarters else 'UNAVAILABLE',
                    quarters=quarters, ttm_revenue=sum(q['revenue'] for q in trailing) if contiguous else None,
                    ttm_net_profit=sum(q['net_profit'] for q in trailing) if contiguous else None,
                    limitations=['Input exports must be reconciled with original issuer/exchange filings.', 'EPS is in INR per share; no automatic split/restatement reconciliation.'])


def disclosures(path, now, state, symbols=None, all_items=False):
    """Public Indian insider/PIT exports, not a claimed Indian Congress feed."""
    with open(path, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    out, seen = [], dict(state.get('seen', {}))
    for row in rows:
        symbol = row['symbol']
        exchange_of(symbol)
        published = clock(row['published_at'])
        if published > now or (symbols and symbol not in symbols):
            continue
        if dt.date.fromisoformat(row['transaction_date']) > published.date():
            raise ValueError('Disclosure publication precedes transaction')
        if row['transaction_type'] not in ('buy', 'sell') or not row['person'] or not row['source'].startswith('https://'):
            raise ValueError('Disclosure requires person, buy/sell and original HTTPS source')
        quantity = number(row['quantity'], 'quantity')
        if quantity != int(quantity):
            raise ValueError('Disclosure quantity must be whole shares')
        row = dict(row, quantity=int(quantity), published_at=published.isoformat())
        key = digest(row)
        if key not in seen or all_items:
            out.append(row)
        seen[key] = published.isoformat()
    return envelope('pelosi', now, status='AVAILABLE', market='India', kind='PUBLIC_INSIDER_DISCLOSURES',
                    items=out, limitations=['User-supplied public PIT/insider exports; not comprehensive or real-time.', 'US congressional disclosure behavior is retained only in the archived US tool.']), {'seen': seen}
