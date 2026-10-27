"""Bounded official NSE RSS/XBRL acquisition for Indian quarterly results."""
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import math
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from india.data import IST
from .common import clock, exchange_of

FEEDS = (
    'https://nsearchives.nseindia.com/content/RSS/Integrated_Filing_Financials.xml',
    'https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml',
)
HOSTS = {'nsearchives.nseindia.com', 'archives.nseindia.com'}
XBRL = '{http://www.xbrl.org/2003/instance}'
ISO4217 = '{http://www.xbrl.org/2003/iso4217}'
XSI = '{http://www.w3.org/2001/XMLSchema-instance}'


def _fetch(url, limit):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password:
        raise ValueError('Filing source must be an official NSE HTTPS host')
    request = urllib.request.Request(url, headers={'User-Agent': 'IndianTradingResearch/1.0',
                                                   'Accept': 'application/xml, text/xml'})
    with urllib.request.urlopen(request, timeout=20) as response:
        if urllib.parse.urlsplit(response.url).hostname not in HOSTS:
            raise ValueError('Filing redirected away from NSE')
        raw = response.read(limit+1)
    if len(raw) > limit:
        raise ValueError('Official filing exceeds bounded download size')
    return raw


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _integrated_index(symbol, max_filings):
    """NSE's paginated, symbol-specific Integrated Filing Financials index."""
    rows, total = [], None
    for page in range(1, 1+(max_filings+19)//20):
        query = urllib.parse.urlencode(dict(symbol=symbol.removesuffix('.NS'),
                                             type='Integrated Filing- Financials', page=page, size=20))
        url = 'https://www.nseindia.com/api/integrated-filing-results?'+query
        request = urllib.request.Request(url, headers={'User-Agent': 'IndianTradingResearch/1.0',
                                                       'Accept': 'application/json',
                                                       'Referer': 'https://www.nseindia.com/companies-listing/corporate-filings-financial-results'})
        with urllib.request.urlopen(request, timeout=20) as response:
            if urllib.parse.urlsplit(response.url).hostname != 'www.nseindia.com':
                raise ValueError('Integrated filing index redirected away from NSE')
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Integrated filing index exceeds 2 MB')
        doc = json.loads(raw)
        if not isinstance(doc, dict) or not isinstance(doc.get('data'), list) or not isinstance(doc.get('totalCount'), int):
            raise ValueError('Invalid NSE integrated filing index schema')
        if total is None:
            total = doc['totalCount']
        elif total != doc['totalCount']:
            raise ValueError('NSE integrated filing index changed during pagination')
        rows.extend(doc['data'])
        if len(rows) >= min(total, max_filings) or not doc['data']:
            break
    return rows[:max_filings], total or 0


def _parse_instance(raw):
    """Keep QName scopes: unit measures use namespace-qualified text values."""
    scopes, stack, pending, root = {}, [], {}, None
    for event, value in ET.iterparse(io.BytesIO(raw), events=('start-ns', 'start', 'end')):
        if event == 'start-ns':
            prefix, uri = value
            pending[prefix] = uri
        elif event == 'start':
            scope = dict(stack[-1]) if stack else {}
            scope.update(pending)
            pending.clear()
            scopes[value] = scope
            stack.append(scope)
            if root is None:
                root = value
        else:
            stack.pop()
    return root, scopes


def _element(root, name, namespace, context='OneD'):
    rows = [x for x in root if x.tag == '{'+namespace+'}'+name and x.get('contextRef') == context]
    if len(rows) > 1:
        raise ValueError('Ambiguous duplicate XBRL fact: '+name)
    return rows[0] if rows else None


def _fact(root, name, namespace, context='OneD'):
    row = _element(root, name, namespace, context)
    return (row.text or '').strip() if row is not None else None


def _measure(element, scopes):
    if element is None or element.tag != XBRL+'measure':
        raise ValueError('Missing XBRL unit measure')
    text = (element.text or '').strip()
    prefix, separator, local = text.partition(':')
    prefix, local = (prefix, local) if separator else ('', prefix)
    namespace = scopes[element].get(prefix)
    if not namespace or not local or ':' in local:
        raise ValueError('Unresolved XBRL unit QName')
    return '{'+namespace+'}'+local


def _unit_kind(unit, scopes):
    if len(unit) == 1 and unit[0].tag == XBRL+'measure':
        return 'INR' if _measure(unit[0], scopes) == ISO4217+'INR' else 'OTHER'
    if len(unit) == 1 and unit[0].tag == XBRL+'divide':
        divide = unit[0]
        numerator, denominator = divide.find(XBRL+'unitNumerator'), divide.find(XBRL+'unitDenominator')
        if (len(divide) == 2 and numerator is not None and denominator is not None
                and len(numerator) == len(denominator) == 1
                and _measure(numerator[0], scopes) == ISO4217+'INR'
                and _measure(denominator[0], scopes) == XBRL+'shares'):
            return 'INR_PER_SHARE'
    return 'OTHER'


def _numeric(root, name, namespace, units, scopes, kind='INR', optional=False):
    fact = _element(root, name, namespace)
    if fact is None or fact.get(XSI+'nil') in ('true', '1'):
        if optional:
            return None
        raise ValueError('XBRL current quarter lacks '+name)
    unit = units.get(fact.get('unitRef'))
    if unit is None or _unit_kind(unit, scopes) != kind:
        raise ValueError('Invalid XBRL unit for '+name+'; expected '+kind)
    text = (fact.text or '').strip()
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', text):
        raise ValueError('Invalid XBRL decimal for '+name)
    try:
        value = float(Decimal(text))
    except (InvalidOperation, OverflowError):
        raise ValueError('Invalid XBRL number for '+name) from None
    if not math.isfinite(value):
        raise ValueError('Nonfinite XBRL number for '+name)
    return value


def parse_xbrl(raw, symbol, source, published_at, basis='consolidated'):
    exchange_of(symbol)
    if symbol.endswith('.BO'):
        raise ValueError('NSE XBRL requires an NSE symbol')
    root, scopes = _parse_instance(raw)
    if root.tag != XBRL+'xbrl':
        raise ValueError('Expected XBRL instance')
    symbols = [x for x in root if _local(x.tag) == 'Symbol' and x.get('contextRef') == 'OneD']
    if len(symbols) != 1:
        raise ValueError('Missing or ambiguous XBRL issuer symbol')
    namespace = symbols[0].tag[1:].split('}', 1)[0]
    if not re.fullmatch(r'https?://www\.(?:sebi\.gov\.in/xbrl/[^ ]+/in-capmkt|bseindia\.com/xbrl/fin/[^ ]+/in-bse-fin)', namespace):
        raise ValueError('Unsupported Indian financial XBRL taxonomy')
    name = _fact(root, 'Symbol', namespace)
    if name != symbol.removesuffix('.NS'):
        raise ValueError('XBRL symbol mismatch')
    report = _fact(root, 'NatureOfReportStandaloneConsolidated', namespace)
    if report not in ('Standalone', 'Consolidated') or report.lower() != basis:
        return None
    contexts = [x for x in root if x.tag == XBRL+'context' and x.get('id') == 'OneD']
    context = contexts[0] if len(contexts) == 1 else None
    if (context is None or context.find(XBRL+'scenario') is not None
            or context.find('.//'+XBRL+'segment') is not None):
        raise ValueError('Missing undimensioned current period context')
    period = context.find(XBRL+'period')
    if period is None or sorted(child.tag for child in period) != sorted((XBRL+'startDate', XBRL+'endDate')):
        raise ValueError('Ambiguous XBRL reporting period')
    start = period.findtext(XBRL+'startDate') if period is not None else None
    end = period.findtext(XBRL+'endDate') if period is not None else None
    if not start or not end or not 75 <= (dt.date.fromisoformat(end)-dt.date.fromisoformat(start)).days <= 100:
        raise ValueError('Current XBRL context is not a standalone quarter')
    if dt.date.fromisoformat(end) > clock(published_at).date():
        raise ValueError('XBRL financial period ends after publication')
    explicit_start = _fact(root, 'DateOfStartOfReportingPeriod', namespace)
    explicit_end = _fact(root, 'DateOfEndOfReportingPeriod', namespace)
    if explicit_start and explicit_start != start or explicit_end and explicit_end != end:
        raise ValueError('Reported period differs from current XBRL context')
    unit_rows = [x for x in root if x.tag == XBRL+'unit']
    units = {x.get('id'): x for x in unit_rows}
    if len(units) != len(unit_rows) or None in units:
        raise ValueError('Missing or duplicate XBRL unit identifiers')
    revenue = _numeric(root, 'RevenueFromOperations', namespace, units, scopes)
    profit = _numeric(root, 'ProfitLossForPeriod', namespace, units, scopes)
    if revenue < 0:
        raise ValueError('Revenue from operations is negative; reconcile source before analysis')
    unavailable_fields = {}
    try:
        eps = _numeric(root, 'DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations',
                       namespace, units, scopes, 'INR_PER_SHARE', optional=True)
    except ValueError as exc:
        eps = None
        unavailable_fields['diluted_eps'] = str(exc)
    row = dict(period_start=start, period_end=end, period_type='quarter',
               published_at=clock(published_at).isoformat(), source=source, revenue=revenue,
               net_profit=profit, diluted_eps=eps, unavailable_fields=unavailable_fields,
               source_sha256=hashlib.sha256(raw).hexdigest(), xbrl_context='OneD',
               reporting_basis=report.lower(), unit='INR', taxonomy=namespace)
    return row


def collect(symbol, now, basis='consolidated', max_filings=40, feeds=None, issuer_name=None, cache=None):
    """Read current official feeds, validate every selected XBRL fact, report gaps."""
    exchange_of(symbol)
    if not symbol.endswith('.NS'):
        raise ValueError('Automatic NSE filings require a .NS symbol; supply --filing for BSE')
    if basis not in ('consolidated', 'standalone'):
        raise ValueError('Choose consolidated or standalone reporting basis')
    if isinstance(max_filings, bool) or not isinstance(max_filings, int) or not 1 <= max_filings <= 80:
        raise ValueError('max-filings must be 1..80')
    cache = {} if cache is None else cache
    def fetch(url, limit):
        key = url, limit
        if key not in cache:
            cache[key] = _fetch(url, limit)
        return cache[key]
    links, errors, later_sources = [], [], set()
    index_total, index_rows = None, []
    try:
        index_rows, index_total = _integrated_index(symbol, max_filings)
        for row in index_rows:
            try:
                if (row.get('symbol') != symbol.removesuffix('.NS') or
                        row.get('type') != 'Integrated Filing- Financials' or
                        str(row.get('consolidated', '')).lower() != basis):
                    continue
                published = max(dt.datetime.strptime(row[k], '%d-%b-%Y %H:%M:%S').replace(tzinfo=IST)
                                for k in ('broadcast_Date', 'creation_Date', 'revised_Date') if row.get(k))
                url = row.get('xbrl')
                if published > now and isinstance(url, str):
                    later_sources.add(url)
                if published <= now and isinstance(url, str) and url.startswith('https://'):
                    links.append(dict(source=url, published_at=published.isoformat(),
                                      title=row.get('cmName') or symbol, feed='NSE integrated filing index',
                                      description=row.get('type_Sub') or '',
                                      period_end=dt.datetime.strptime(row['qe_Date'], '%d-%b-%Y').date().isoformat()))
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                errors.append(dict(feed='NSE integrated filing index', error=str(exc)))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        errors.append(dict(feed='NSE integrated filing index', error=str(exc)))
    links = [row for row in links if row['source'] not in later_sources]
    for feed in feeds or FEEDS:
        try:
            root = ET.fromstring(fetch(feed, 2_000_000))
            for item in root.findall('.//item'):
                try:
                    published = dt.datetime.strptime((item.findtext('pubDate') or '').strip(), '%d-%b-%Y %H:%M:%S').replace(tzinfo=IST)
                except ValueError as exc:
                    errors.append(dict(feed=feed, error=str(exc)))
                    continue
                url = (item.findtext('link') or '').strip()
                title = (item.findtext('title') or '').strip()
                if published > now or not url or url in later_sources:
                    continue
                if links and not (title.casefold() == (issuer_name or '').strip().casefold()
                                  or symbol.removesuffix('.NS').lower() in (title+' '+url).lower()):
                    continue
                if url not in {r['source'] for r in links}:
                    links.append(dict(source=url, published_at=published.isoformat(), title=title,
                                      feed=feed, description=(item.findtext('description') or '').strip()))
        except (OSError, ValueError, ET.ParseError) as exc:
            errors.append(dict(feed=feed, error=str(exc)))
    # Exact issuer names prioritize reads. Directory names are often shortened,
    # so unmatched items remain eligible for XBRL symbol verification.
    links.sort(key=lambda r: (r['title'].casefold() == (issuer_name or '').strip().casefold(),
                              r['published_at']), reverse=True)
    rows, selected = [], []
    for filing in links[:max_filings]:
        try:
            raw = fetch(filing['source'], 6_000_000)
            row = parse_xbrl(raw, symbol, filing['source'], filing['published_at'], basis)
            if row:
                if filing.get('period_end') and filing['period_end'] != row['period_end']:
                    raise ValueError('XBRL period differs from NSE filing index')
                rows.append(row)
                selected.append(filing)
        except ValueError as exc:
            # Other issuers in a shared feed are expected; distinguish their
            # symbol mismatch from a broken matching filing.
            if str(exc) != 'XBRL symbol mismatch':
                errors.append(dict(source=filing['source'], error=str(exc)))
        except (OSError, ET.ParseError) as exc:
            errors.append(dict(source=filing['source'], error=str(exc)))
    # A revision replaces the previous publication for the exact period.
    versions, revisions, grouped = {}, {}, {}
    for row in sorted(rows, key=lambda r: r['published_at']):
        key = row['period_start'], row['period_end']
        revisions.setdefault(key, []).append(dict(source=row['source'], published_at=row['published_at'],
                                                    source_sha256=row['source_sha256']))
        grouped.setdefault(key, []).append(row)
    for key, history in grouped.items():
        signatures = {}
        ambiguous = False
        for row in history:
            signature = tuple(row[field] for field in ('revenue', 'net_profit', 'diluted_eps'))
            previous = signatures.setdefault(row['published_at'], signature)
            if previous != signature:
                ambiguous = True
        if ambiguous:
            errors.append(dict(period_start=key[0], period_end=key[1], error='Conflicting XBRL filings have the same publication time; quarter excluded'))
        else:
            versions[key] = history[-1]
    known = {(r['source'], r['published_at']): r for r in links if r['feed'] == 'NSE integrated filing index'}
    known.update({(r['source'], r['published_at']): r for r in selected})
    return dict(symbol=symbol, currency='INR', unit='INR', basis=basis,
                quarters=list(versions.values()), filings=[dict(source=r['source'],
                published_at=r['published_at'], kind='results') for r in known.values()],
                source_verification='DIRECT_NSE_INDEX_RSS_XBRL', acquisition_errors=errors,
                acquisition=dict(feeds=list(feeds or FEEDS), index_total=index_total,
                                 feed_filings=len(links),
                                 retrieved=min(len(links), max_filings), matched=len(rows),
                                 bounded_at=max_filings,
                                 excluded_later_revisions=len(later_sources),
                                 revisions=[dict(period_start=k[0], period_end=k[1], versions=v)
                                            for k, v in revisions.items() if len(v) > 1]))
