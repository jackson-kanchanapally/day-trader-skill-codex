"""Indian counterpart of issuer facts: explicit sourced export adapters.

No SEC taxonomy is applied to Indian filings. A directory or multi-company
export supports batch discovery without guessing facts from PDF prose.
"""
import datetime as dt
from pathlib import Path

from .common import clock, envelope, exchange_of, number, read


def export_for(path, symbol):
    exchange_of(symbol)
    if isinstance(path, dict):
        doc = path
        if doc.get('symbol') != symbol:
            raise ValueError('Financial document symbol mismatch')
        return doc
    path = Path(path)
    if path.is_dir():
        path = path/(symbol+'.json')
    doc = read(path)
    if 'companies' in doc:
        matches = [row for row in doc['companies'] if row.get('symbol') == symbol]
        if len(matches) != 1:
            raise ValueError('Financial export must contain exactly one matching company')
        doc = matches[0]
    return doc


def financials(path, symbol, now, quarters_limit=None):
    doc = export_for(path, symbol)
    if doc['symbol'] != symbol or doc.get('currency') != 'INR' or doc.get('basis') not in ('consolidated', 'standalone'):
        raise ValueError('Financial export requires matching symbol, INR and explicit reporting basis')
    multiplier = {'INR': 1, 'INR_thousand': 1000, 'INR_lakh': 100000,
                  'INR_million': 1000000, 'INR_crore': 10000000}.get(doc.get('unit'))
    if multiplier is None:
        raise ValueError('Specify an explicit INR financial reporting unit')
    if quarters_limit is not None and (isinstance(quarters_limit, bool) or not isinstance(quarters_limit, int) or quarters_limit < 1):
        raise ValueError('quarters must be a positive integer')
    versions = {}
    for raw in doc['quarters']:
        published = clock(raw['published_at'])
        if published > now:
            continue
        row = dict(raw)
        start, end = dt.date.fromisoformat(row['period_start']), dt.date.fromisoformat(row['period_end'])
        kind = row.get('period_type', 'quarter')
        if kind not in ('quarter', 'ytd', 'annual') or end > published.date() or start > end:
            raise ValueError('Invalid completed financial period/type')
        if not str(row.get('source', '')).startswith('https://'):
            raise ValueError('Financial row requires the original HTTPS filing source')
        if kind == 'quarter' and not 75 <= (end-start).days <= 100:
            raise ValueError('Financial rows must be standalone quarters; explicitly label cumulative YTD/annual rows')
        key = (start, end, kind)
        row['published_at'] = published.isoformat()
        versions.setdefault(key, []).append(row)
    records = []
    for key, rows in versions.items():
        rows.sort(key=lambda r: r['published_at'])
        if len(rows) > 1:
            for previous, current in zip(rows, rows[1:]):
                if (not previous.get('revision_id') or not current.get('revision_id')
                        or current.get('supersedes') != previous['revision_id']
                        or current['published_at'] <= previous['published_at']):
                    raise ValueError('Duplicate period/restatement requires an explicit ordered revision_id/supersedes chain')
        records.append(rows[-1])
    quarters = []
    comparable_eps = doc.get('eps_ttm_method') == 'sum_comparable_quarters' and bool(doc.get('eps_share_basis'))
    for raw in sorted(records, key=lambda r: (r['period_end'], r['period_start'])):
        row = dict(raw)
        start, end = dt.date.fromisoformat(row['period_start']), dt.date.fromisoformat(row['period_end'])
        inputs = [row]
        if row.get('period_type', 'quarter') in ('ytd', 'annual'):
            length = (end-start).days+1
            if not 75 <= length <= 370:
                raise ValueError('Cumulative periods must be within one explicitly declared fiscal year')
            if length > 101:
                priors = [r for r in records if r['period_start'] == row['period_start']
                          and r['period_end'] < row['period_end'] and r.get('period_type') in ('ytd', 'annual')
                          and 75 <= (end-dt.date.fromisoformat(r['period_end'])).days <= 101
                          and r['published_at'] <= row['published_at']]
                if len(priors) != 1:
                    raise ValueError('Cumulative quarter derivation needs the matching prior fiscal YTD period')
                prior = priors[0]
                start = dt.date.fromisoformat(prior['period_end'])+dt.timedelta(days=1)
                for field in ('revenue', 'net_profit', 'gross_profit', 'operating_profit'):
                    row[field] = float(row[field])-float(prior[field]) if row.get(field) is not None and prior.get(field) is not None else None
                same_basis = comparable_eps and all(r.get('eps_share_basis', doc.get('eps_share_basis')) == doc['eps_share_basis'] for r in (row, prior))
                row['diluted_eps'] = float(row['diluted_eps'])-float(prior['diluted_eps']) if same_basis and row.get('diluted_eps') is not None and prior.get('diluted_eps') is not None else None
                inputs.append(prior)
        if not 75 <= (end-start).days <= 100:
            raise ValueError('Derived financial period is not a standalone quarter')
        revenue = number(row['revenue'], 'revenue', zero=True)*multiplier
        profit = float(row['net_profit'])*multiplier
        number(abs(profit), 'net_profit', zero=True)
        eps = float(row['diluted_eps']) if row.get('diluted_eps') is not None else None
        if eps is not None:
            number(abs(eps), 'diluted_eps', zero=True)
        q = dict(period_start=start.isoformat(), period_end=end.isoformat(),
                 published_at=max(r['published_at'] for r in inputs), source=raw['source'],
                 source_sha256=raw.get('source_sha256'),
                 unavailable_fields=raw.get('unavailable_fields', {}),
                 revenue=revenue, net_profit=profit, diluted_eps=eps,
                 net_margin_pct=100*profit/revenue if revenue else None,
                 derived=len(inputs)>1, derivation_sources=[dict(source=r['source'], period_start=r['period_start'], period_end=r['period_end'], published_at=r['published_at']) for r in inputs],
                 revision_id=raw.get('revision_id'), eps_share_basis=row.get('eps_share_basis', doc.get('eps_share_basis')))
        for field, margin in (('gross_profit', 'gross_margin_pct'), ('operating_profit', 'operating_margin_pct')):
            amount = float(row[field])*multiplier if row.get(field) is not None else None
            if amount is not None:
                number(abs(amount), field, zero=True)
            q[field], q[margin] = amount, 100*amount/revenue if amount is not None and revenue else None
        quarters.append(q)
    quarters.sort(key=lambda q: q['period_end'])
    for a, b in zip(quarters, quarters[1:]):
        if b['period_start'] <= a['period_end']:
            raise ValueError('Overlapping standalone financial quarters; reconcile input basis and cumulative rows')
    def growth(current, previous):
        return 100*(current/previous-1) if current is not None and previous is not None and previous > 0 else None
    changes = []
    for index, row in enumerate(quarters):
        end = dt.date.fromisoformat(row['period_end'])
        previous_year = next((q for q in quarters if dt.date.fromisoformat(q['period_end']).year == end.year-1 and q['period_end'][5:] == row['period_end'][5:]), None)
        previous = quarters[index-1] if index and (dt.date.fromisoformat(row['period_start'])-dt.date.fromisoformat(quarters[index-1]['period_end'])).days == 1 else None
        for field, label in (('revenue', 'revenue'), ('net_profit', 'profit'), ('diluted_eps', 'eps')):
            row[label+'_yoy_pct'] = growth(row[field], previous_year[field]) if previous_year else None
            row[label+'_qoq_pct'] = growth(row[field], previous[field]) if previous else None
        if previous:
            transition = 'profit' if row['net_profit'] > 0 and previous['net_profit'] <= 0 else 'loss' if row['net_profit'] < 0 and previous['net_profit'] >= 0 else None
            if transition:
                changes.append(dict(period_end=row['period_end'], finding='Turned to '+transition+' versus the preceding quarter'))
    trailing = quarters[-4:]
    contiguous = len(trailing) == 4 and all((dt.date.fromisoformat(b['period_start'])-dt.date.fromisoformat(a['period_end'])).days == 1 for a, b in zip(trailing, trailing[1:]))
    eps_ok = contiguous and comparable_eps and all(q['diluted_eps'] is not None and q['eps_share_basis'] == doc['eps_share_basis'] for q in trailing)
    latest = quarters[-1] if quarters else None
    known_filings = []
    for filing in doc.get('filings', []):
        if not str(filing.get('source', '')).startswith('https://'):
            raise ValueError('Filing index entries require original HTTPS source')
        if clock(filing['published_at']) <= now:
            known_filings.append(filing)
    newer = [f for f in known_filings if latest and clock(f['published_at']) > clock(latest['published_at']) and f.get('kind', 'results') == 'results']
    age = (now.date()-dt.date.fromisoformat(latest['period_end'])).days if latest else None
    return envelope('analyst', now, symbol=symbol, basis=doc['basis'], status='AVAILABLE' if quarters else 'UNAVAILABLE',
                    quarters=quarters[-quarters_limit:] if quarters_limit else quarters,
                    ttm_revenue=sum(q['revenue'] for q in trailing) if contiguous else None,
                    ttm_net_profit=sum(q['net_profit'] for q in trailing) if contiguous else None,
                    ttm_diluted_eps=sum(q['diluted_eps'] for q in trailing) if eps_ok else None,
                    ttm_eps_method='sum_comparable_quarters' if eps_ok else None,
                    ttm_eps_reason=None if eps_ok else 'Four contiguous quarters and explicit comparable EPS/share basis required',
                    changes=changes, latest_period_age_days=age, newer_results_filings=newer,
                    stale=bool(newer or (age is not None and age > 120)),
                    source_verification=doc.get('source_verification', 'USER_SUPPLIED_EXPORT_NOT_INDEPENDENT_AUTHENTICATION'),
                    acquisition=doc.get('acquisition'), acquisition_errors=doc.get('acquisition_errors', []),
                    limitations=['Reconcile exports, revisions and EPS share basis with original filings.',
                                 'No facts or EPS denominators are inferred from PDF prose or market prices.'])
