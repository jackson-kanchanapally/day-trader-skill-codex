"""Dated Indian news, issuer financial exports and public insider disclosures."""
import csv
import datetime as dt
import hashlib
import re
from pathlib import Path
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from india.data import IST

from .common import clock, digest, envelope, exchange_of, number, read, upstream
from .financials import financials


def news(symbols, now, state, hours=24, offline=None, feed_url=None, announcements=None, all_items=False, names=None):
    """Original story-level semantics with Indian issuer/venue attribution."""
    number(hours, 'hours')
    items, errors, seen = [], [], dict(state.get('seen', {}))
    m = upstream('news')
    templates = feed_url if isinstance(feed_url, (list, tuple)) else [feed_url]
    for symbol in symbols:
        exchange_of(symbol)
        for template in templates:
            try:
                if offline:
                    raw = (Path(offline)/('yahoo-'+symbol+'.xml')).read_bytes()
                    origin = 'Supplied RSS'
                else:
                    template = template or 'https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=IN&lang=en-IN'
                    url = template.replace('{symbol}', urllib.parse.quote(symbol))
                    if not url.startswith('https://'):
                        raise ValueError('News feed must be HTTPS')
                    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'IndianTradingResearch/1.0'}), timeout=20) as response:
                        raw = response.read(2_000_001)
                    if len(raw) > 2_000_000:
                        raise ValueError('News feed exceeds 2 MB')
                    origin = url
                for item in m.parse_rss(raw, symbol):
                    when = item.pop('time', None)
                    if when is None:
                        continue
                    published = clock(when.isoformat() if when.tzinfo else when.isoformat()+'+00:00')
                    item.update(published_at=published.isoformat(), source=origin)
                    items.append(item)
            except (ValueError, OSError, ET.ParseError, AttributeError) as exc:
                errors.append(dict(symbol=symbol, feed=template, error=str(exc)))
            if offline:
                break
    if not offline:
        from .nse_filings import FEEDS, _fetch
        exchange_feeds = (
            'https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml',
            *FEEDS,
            'https://nsearchives.nseindia.com/content/RSS/InsiderTrading.xml',
        )
        for feed in exchange_feeds:
            try:
                root = ET.fromstring(_fetch(feed, 2_000_000))
                for item in root.findall('.//item'):
                    title = (item.findtext('title') or '').strip()
                    summary = (item.findtext('description') or '').strip()
                    link = (item.findtext('link') or '').strip()
                    stamp = (item.findtext('pubDate') or '').strip()
                    published = dt.datetime.strptime(stamp, '%d-%b-%Y %H:%M:%S').replace(tzinfo=IST)
                    if published > now or not now-dt.timedelta(hours=hours) <= published or not link.startswith('https://'):
                        continue
                    for symbol in symbols:
                        if not symbol.endswith('.NS'):
                            continue
                        issuer = (names or {}).get(symbol, '')
                        stem = symbol.removesuffix('.NS')
                        source_file = urllib.parse.urlsplit(link).path.rsplit('/', 1)[-1].upper()
                        title_words = re.sub(r'[^A-Z0-9]+', ' ', title.upper()).strip()
                        issuer_words = re.sub(r'[^A-Z0-9]+', ' ', issuer.upper()).strip()
                        matched = (source_file.startswith(stem+'_') or
                                   bool(issuer_words and title_words == issuer_words))
                        if not matched:
                            continue
                        items.append(dict(id='nse:'+hashlib.sha256(link.encode()).hexdigest(),
                                          ticker=symbol, title=title+' — '+summary[:180],
                                          summary=summary, link=link, published_at=published.isoformat(),
                                          source=feed, kind='filing', touches=[symbol]))
            except (OSError, ValueError, ET.ParseError) as exc:
                errors.append(dict(feed=feed, error=str(exc)))
    if announcements:
        for row in read(announcements)['items']:
            if row['symbol'] in symbols:
                items.append(dict(id=row.get('id') or 'announcement:'+digest(row), ticker=row['symbol'], title=row['title'],
                                  link=row['source'], published_at=clock(row['published_at']).isoformat(),
                                  source=row['source'], kind='filing'))
    m.KEYWORDS = [(label, pattern) for label, pattern in m.KEYWORDS if label not in ('macro', 'regulatory')]
    m.KEYWORDS.extend([('macro', r'\bRBI\b|\bMPC\b|\brepo rate\b|\bCPI\b|\binflation\b'),
                       ('regulatory', r'\bSEBI\b|\bNSE\b|\bBSE\b|\bNCLT\b|\bCCI\b')])
    fresh, stories = [], []
    for item in items:
        published = clock(item['published_at'])
        if not now-dt.timedelta(hours=hours) <= published <= now:
            continue
        m.tag([item], {s: None for s in symbols})
        text = item['title']+' '+(item.get('summary') or '')
        touches = set(item['touches'])
        for symbol in symbols:
            stem, venue = symbol.rsplit('.', 1)
            exchange = 'NSE' if venue == 'NS' else 'BSE'
            patterns = [r'(?<![A-Z0-9])'+re.escape(symbol)+r'(?![A-Z0-9])',
                        r'\b'+exchange+r'\s*:\s*'+re.escape(stem)+r'\b']
            company = (names or {}).get(symbol)
            if isinstance(company, str) and len(company.strip()) >= 4:
                patterns.append(r'\b'+re.escape(company.strip()).replace(r'\ ', r'\s+')+r'\b')
            if any(re.search(pattern, text, re.I) for pattern in patterns):
                touches.add(symbol)
        item['touches'] = sorted(touches)
        title = re.sub(r'\W+', ' ', item['title'].casefold()).strip()
        link = urllib.parse.urlsplit(item.get('link') or '')
        normalized_link = urllib.parse.urlunsplit((link.scheme.lower(), link.netloc.lower(), link.path.rstrip('/'), link.query, ''))
        aliases = {digest(['title', title, published.date().isoformat()])}
        if item.get('id'):
            aliases.add(digest(['id', item['id']]))
        if normalized_link:
            aliases.add(digest(['link', normalized_link]))
        # Components are merged transitively: a third feed can bridge an id
        # and a changed title/link already seen in two earlier feed items.
        matches = [story for story in stories if story['_aliases'] & aliases]
        was_seen = any(key in seen for key in aliases) or digest([item['ticker'], item['id']]) in seen
        item.update(sources=[item['source']], publications=[dict(published_at=item['published_at'], source=item['source'])],
                    _aliases=aliases, _seen=was_seen)
        for match in matches:
            item['_aliases'].update(match['_aliases'])
            item['_seen'] = item['_seen'] or match['_seen']
            item['touches'] = sorted(set(item['touches']) | set(match['touches']))
            item['sources'] = sorted(set(item['sources']) | set(match['sources']))
            item['publications'].extend(match['publications'])
            item['published_at'] = min(item['published_at'], match['published_at'])
            item['tags'] = sorted(set(item['tags']) | set(match['tags']))
            stories.remove(match)
        stories.append(item)
    for item in stories:
        aliases, already = item.pop('_aliases'), item.pop('_seen')
        for key in aliases:
            seen[key] = item['published_at']
        if all_items or not already:
            fresh.append(item)
    seen = {key: value for key, value in seen.items() if clock(value) >= now-dt.timedelta(days=30)}
    fresh.sort(key=lambda item: (item['published_at'], item['title']), reverse=True)
    return envelope('news', now, as_of=now.isoformat(), items=fresh, errors=errors,
                    coverage='Merged Yahoo headlines, current official NSE RSS filings and supplied issuer announcements; RSS windows are bounded and not complete archive coverage'), {'seen': seen}




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
