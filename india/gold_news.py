"""News flow, positioning and risk mood around gold, from free public sources.

Feeds (no API keys):

    Google News RSS   global gold, Fed/dollar/yields and geopolitics; Indian MCX,
                      rupee, RBI and import-duty searches
    Economic Times    commodities RSS
    CFTC              Commitments of Traders, COMEX gold managed-money positions
                      (publicreporting.cftc.gov, weekly)
    Yahoo             VIX and the S&P 500

Headlines are classified by a transparent rule list (``CUES``): each cue is a
pattern, a direction for gold (+1 supportive, -1 negative) and a driver tag.
Nothing here is learned or predicted; it is a summary of what the news says,
weighted toward recent headlines. Indian futures headlines that quote an MCX
price ("Rs 1.31 lakh") also give a free cross-check of the price basis.

News has no free history, so its effect on results cannot be backtested here.
Each alert records the news score with the paper trade, so the record can show
over time whether trading with or against the news flow did better.
"""
import datetime as dt
import email.utils
import html
import json
import re
import statistics
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UTC = dt.timezone.utc
UA = {'User-Agent': 'GoldResearch/1.0 (+rss reader)'}


def gnews(query, region):
    lang = {'IN': 'en-IN', 'US': 'en-US'}[region]
    return 'https://news.google.com/rss/search?%s' % urllib.parse.urlencode(
        {'q': query, 'hl': lang, 'gl': region, 'ceid': '%s:en' % region})


FEEDS = (
    ('global', gnews('gold price OR bullion OR "gold futures" when:2d', 'US')),
    ('global', gnews('"Federal Reserve" OR "Treasury yields" OR "dollar index" when:2d', 'US')),
    ('global', gnews('geopolitical tensions OR war OR ceasefire OR sanctions markets when:2d', 'US')),
    ('india', gnews('"MCX gold" when:2d', 'IN')),
    ('india', gnews('"gold futures" OR "gold prices" India when:2d', 'IN')),
    ('india', gnews('rupee dollar OR RBI gold OR "gold import duty" when:2d', 'IN')),
    ('india', 'https://economictimes.indiatimes.com/markets/commodities/rssfeeds/1808152121.cms'),
)

GOLD_WORDS = re.compile(r'\b(gold|bullion|yellow metal|precious metals?|xau|mcx)\b', re.I)
VERB_UP = (r'(rises?|rising|gains?|jumps?|surges?|climbs?|rall(?:y|ies)|soars?|edges? (?:up|higher)|rebounds?|recovers?'
           r'|breaks? above|hits? (?:a )?(?:fresh |new )?(?:record|lifetime|all-time|\d+-\w+) high|scales?|spikes?)')
VERB_DOWN = (r'(falls?|falling|drops?|dips?|slips?|declines?|tumbles?|plunges?|plummets?|slides?|eases?|retreats?|sheds?|loses?'
             r'|sinks?|crash(?:es)?|slumps?|breaks? below|extends? (?:losses|decline)|hits? (?:a )?(?:fresh |new )?(?:over )?(?:\S+ )?'
             r'(?:\d+-\w+|one-year|multi-month|\w+-week) low|hovers? near (?:an? )?(?:over )?(?:\S+ )?low)')
# The price verb must follow the subject within two words, so "Gold auctions likely to rise" is not a price move.
GOLD_SUBJECT = r'\b(?:spot |mcx |comex |world |global |domestic )?gold(?: prices?| futures| rates?)?(?:,? (?:and )?silver(?: prices?)?)?\s+(?:\S+\s+){0,2}?'

# (pattern, direction for gold, driver tag, gold headlines only?)
CUES = [
    (GOLD_SUBJECT + VERB_UP + r'\b', +1, 'gold price up', True),
    (GOLD_SUBJECT + VERB_DOWN + r'\b', -1, 'gold price down', True),
    (r'\b(?:sell-?off|bearish|downward pressure|under pressure)\b', -1, 'gold price down', True),
    (r'\bbullish\b|\bupward momentum\b', +1, 'gold price up', True),
    (r'\b(?:cuts?|lowers?|slashes?|trims?)\b[^.;:|]{0,30}?\bgold\b[^.;:|]{0,20}?\b(?:forecast|target|outlook)', -1, 'forecast cut', True),
    (r'\b(?:raises?|lifts?|hikes?|ups)\b[^.;:|]{0,30}?\bgold\b[^.;:|]{0,20}?\b(?:forecast|target|outlook)', +1, 'forecast raised', True),
    (r'\b(?:firm|firmer|strong|stronger|strengthening|rising|robust) (?:us )?dollar\b', -1, 'dollar up', False),
    (r'\b(?:dollar|greenback|dxy)\b[^.;:|]{0,25}?\b(?:firms?|strengthens?|rises?|gains?|jumps?|climbs?|rall(?:y|ies)|surges?|hits? \S+ high)', -1, 'dollar up', False),
    (r'\b(?:weak|weaker|weakening|softer|falling|sliding) (?:us )?dollar\b', +1, 'dollar down', False),
    (r'\b(?:dollar|greenback|dxy)\b[^.;:|]{0,25}?\b(?:weakens?|falls?|slips?|drops?|declines?|eases?|slides?|retreats?)', +1, 'dollar down', False),
    (r'\brate[- ]hikes?\b|\bhike (?:interest )?rates\b|\braise (?:interest )?rates\b|\bhawkish\b|\btightening\b', -1, 'Fed/rates hawkish', False),
    (r'\brate[- ]cuts?\b|\bcut (?:interest )?rates\b|\bdovish\b|\beasing cycle\b', +1, 'Fed/rates dovish', False),
    (r'\byields?\b[^.;:|]{0,20}?\b(?:rise|rises|climb|climbs|jump|jumps|surge|surges|higher|spike|spikes|hit \S+ high)', -1, 'yields up', False),
    (r'\byields?\b[^.;:|]{0,20}?\b(?:fall|falls|drop|drops|slip|slips|ease|eases|lower|retreat|retreats|decline|declines)', +1, 'yields down', False),
    (r'\b(?:(?<!pre-)(?<!post-)war|missiles?|airstrikes?|invasion|(?<!de-)(?<!de)escalat\w*|tensions?|conflict|sanctions?|geopolitic\w*|safe[- ]haven)\b', +1, 'geopolitical risk', False),
    (r'\b(?:ceasefire|truce|peace (?:deal|talks|agreement)|de-?escalat\w*)\b', -1, 'tensions easing', False),
    (r'\bcentral banks?\b[^.;:|]{0,30}?\b(?:buy|buying|purchases?|adds?|accumulat\w*)|\b(?:pboc|rbi|china)\b[^.;:|]{0,30}?\bgold (?:buying|purchases|reserves)', +1, 'central-bank buying', False),
    (r'\betfs?\b[^.;:|]{0,25}?\binflows?\b', +1, 'ETF inflows', True),
    (r'\betfs?\b[^.;:|]{0,25}?\boutflows?\b', -1, 'ETF outflows', True),
    (r'\bprofit[- ](?:taking|booking)\b', -1, 'profit-taking', True),
    (r'\b(?:recession|slowdown fears|financial crisis|market turmoil|risk[- ]off)\b', +1, 'risk-off', False),
    (r'\brisk[- ]on\b|\bstocks? (?:hit|at) (?:a )?record\b', -1, 'risk-on', False),
    (r'\brupee\b[^.;:|]{0,25}?\b(?:falls?|weakens?|slips?|drops?|declines?|depreciat\w*|(?:record|all-time|lifetime) low)', +1, 'rupee weak (MCX +)', False),
    (r'\brupee\b[^.;:|]{0,25}?\b(?:rises?|gains?|strengthens?|appreciat\w*|jumps?|climbs?)', -1, 'rupee strong (MCX -)', False),
    (r'\b(?:import )?duty\b[^.;:|]{0,20}?\b(?:cut|cuts|reduc\w*|lower\w*|slash\w*)'
     r'|\b(?:cut|cuts|reduc\w*|lower\w*|slash\w*)\b[^.;:|]{0,25}?\bduty\b', -1, 'import duty cut', True),
    (r'\b(?:import )?duty\b[^.;:|]{0,20}?\b(?:hike|hikes|raise\w*|increase\w*)'
     r'|\b(?:hike|hikes|raise\w*|increase\w*)\b[^.;:|]{0,25}?\bduty\b', +1, 'import duty hike', True),
    (r'\b(?:festive|festival|wedding|dhanteras|akshaya tritiya|diwali)\b[^.;:|]{0,25}?\bdemand\b', +1, 'Indian demand', True),
    (r'\bdemand\b[^.;:|]{0,15}?\b(?:weak|weakens|slumps?|falls?|subdued|dull)\b', -1, 'weak demand', True),
]
CUES = [(re.compile(p, re.I), sign, tag, gold_only) for p, sign, tag, gold_only in CUES]
# Only "to/at Rs X" quotes count: "fall 2% to Rs 1.31 lakh", not "lifetime high near Rs 1.80 lakh in January".
# Shown first when picking headlines for the message (the score uses every headline).
MAJOR_SOURCES = re.compile(
    r'reuters|bloomberg|cnbc|wsj|wall street journal|financial times|\bft\b|marketwatch|barron|associated press|\bap\b|kitco|'
    r'fxstreet|investing\.com|economic times|economictimes|moneycontrol|mint|business standard|hindu ?businessline|'
    r'financial express|ndtv profit|business today|times of india|the hindu|zee business|cnbc-?tv18|upstox', re.I)

LAKH_PRICE = re.compile(r'\b(?:to|at)\s(?:rs\.?|₹|inr)\s?(\d{1,2}(?:\.\d{1,3})?)\s?lakh', re.I)
FULL_PRICE = re.compile(r'\b(?:to|at)\s(?:rs\.?|₹|inr)\s?(\d{1,2},\d{2},\d{3}|\d{5,6})\b(?:[^.;|]{0,20}?(?:per|/)\s?10\s?(?:g|gm|gram))', re.I)


# --------------------------------------------------------------------------- news

def http_get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as response:
        return response.read(3_000_001)


def parse_rss(raw, region):
    items = []
    for it in ET.fromstring(raw).iter('item'):
        title = html.unescape((it.findtext('title') or '').strip())
        src = it.find('source')
        source = (src.text or '').strip() if src is not None else ''
        if source and title.endswith(' - ' + source):
            title = title[:-len(source) - 3].strip()
        try:
            published = email.utils.parsedate_to_datetime(it.findtext('pubDate') or '')
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        link = (it.findtext('link') or '').strip()
        if title and link.startswith('https://'):
            items.append({'title': title, 'source': source or 'Economic Times', 'link': link,
                          'published': published.astimezone(UTC).isoformat(), 'region': region})
    return items


def gather(now, fetch=None, hours=48, feeds=FEEDS):
    """Recent unique headlines from every feed. Returns (items, errors); one failing feed never stops the rest."""
    fetch = fetch or http_get
    items, errors, seen = [], [], set()
    oldest = now - dt.timedelta(hours=hours)
    for region, url in feeds:
        try:
            batch = parse_rss(fetch(url), region)
        except (OSError, ValueError, ET.ParseError) as exc:
            errors.append('%s: %s' % (url.split('?')[0], exc))
            continue
        for it in batch:
            t = dt.datetime.fromisoformat(it['published'])
            key = re.sub(r'[^a-z0-9]+', ' ', it['title'].lower()).strip()[:90]
            if not oldest <= t <= now + dt.timedelta(minutes=10) or key in seen:
                continue
            seen.add(key)
            items.append(it)
    items.sort(key=lambda it: it['published'], reverse=True)
    return items, errors


def classify(title):
    """(direction for gold: -1/0/+1, [tags], mentions gold?)."""
    gold = bool(GOLD_WORDS.search(title))
    total, tags = 0, []
    for pattern, sign, tag, gold_only in CUES:
        if gold_only and not gold:
            continue
        if pattern.search(title):
            total += sign
            tags.append(tag)
    return (1 if total > 0 else -1 if total < 0 else 0), tags, gold


def sentiment(items, now, half_life_hours=24):
    """Recency-weighted news score from -100 (negative for gold) to +100 (supportive).

    Gold headlines weigh 1, macro headlines without gold 0.5, halving every
    ``half_life_hours``. A pseudo-weight of 4 shrinks thin samples toward 0:
    one headline alone scores at most +/-20 (mixed)."""
    num = den = 0.0
    drivers, scored = {}, []
    for it in items:
        sign, tags, gold = classify(it['title'])
        it.update(sign=sign, tags=tags, gold=gold)
        if not sign:
            continue
        age = (now - dt.datetime.fromisoformat(it['published'])).total_seconds() / 3600
        w = (1.0 if gold else 0.5) * 0.5 ** (max(age, 0) / half_life_hours)
        num += w * sign
        den += w
        for tag in tags:
            drivers[tag] = drivers.get(tag, 0) + 1
        scored.append(it)
    score = 100 * num / (den + 4.0)
    label = 'supportive' if score >= 25 else 'negative' if score <= -25 else 'mixed'
    return {'score': round(score, 1), 'label': label, 'headlines': len(items), 'scored': len(scored),
            'drivers': dict(sorted(drivers.items(), key=lambda kv: -kv[1])), 'top': pick_top(scored)}


def pick_top(scored, now=None):
    """Up to six headlines for the message: major sources and more drivers first, then recency;
    three global gold, two Indian, one macro."""
    def rank(it):
        return (bool(MAJOR_SOURCES.search(it['source'])), len(it['tags']), it['published'])
    ordered = sorted(scored, key=rank, reverse=True)
    picks, seen = [], set()
    for want, test in ((3, lambda it: it['gold'] and it['region'] == 'global'),
                       (2, lambda it: it['region'] == 'india'),
                       (1, lambda it: not it['gold'])):
        for it in ordered:
            if want and test(it) and id(it) not in seen:
                picks.append(it)
                seen.add(id(it))
                want -= 1
    return sorted(picks, key=lambda it: it['published'], reverse=True)




def implied_mcx(items, now, hours=24):
    """MCX gold futures prices (INR per gram) quoted in recent Indian headlines."""
    found = []
    for it in items:
        title = it['title']
        if not re.search(r'\b(mcx|futures)\b', title, re.I) or not re.search(r'\bgold\b', title, re.I):
            continue
        if now - dt.datetime.fromisoformat(it['published']) > dt.timedelta(hours=hours):
            continue
        title = re.split(r'\bsilver\b', title, flags=re.I)[0]     # "gold ... to Rs X, silver ... to Rs Y"
        m = FULL_PRICE.search(title)
        per10 = float(m.group(1).replace(',', '')) if m else None
        if per10 is None:
            m = LAKH_PRICE.search(title)
            per10 = float(m.group(1)) * 1e5 if m else None
        if per10 and 30_000 <= per10 <= 500_000:
            found.append({'per_gram': per10 / 10, 'title': title, 'source': it['source'], 'published': it['published']})
    return found


# --------------------------------------------------------------------------- positioning and risk mood

COT_URL = 'https://publicreporting.cftc.gov/resource/72hh-3qpy.json'
COT_GOLD = '088691'


def cot(fetch=None, weeks=156):
    """Managed-money net position in COMEX gold and its percentile over ``weeks`` reports."""
    fetch = fetch or http_get
    query = urllib.parse.urlencode({
        '$where': "cftc_contract_market_code='%s'" % COT_GOLD, '$order': 'report_date_as_yyyy_mm_dd DESC',
        '$limit': str(weeks), '$select': 'report_date_as_yyyy_mm_dd,m_money_positions_long_all,m_money_positions_short_all,open_interest_all'})
    rows = json.loads(fetch('%s?%s' % (COT_URL, query)))
    nets = [int(float(r['m_money_positions_long_all'])) - int(float(r['m_money_positions_short_all'])) for r in rows]
    if len(nets) < 52:
        raise ValueError('CFTC returned %d gold reports' % len(nets))
    net = nets[0]
    pct = 100 * sum(1 for x in nets if x <= net) / len(nets)
    change = net - nets[1]
    return {'date': rows[0]['report_date_as_yyyy_mm_dd'][:10], 'net': net, 'change': change,
            'percentile': round(pct), 'weeks': len(nets), 'open_interest': int(float(rows[0]['open_interest_all'])),
            'state': 'crowded long' if pct >= 85 else 'washed out' if pct <= 15 else 'neutral'}


def risk_mood(fetch_daily, now):
    """VIX level and 5-session change, S&P 500 against its EMA20."""
    start = now.date() - dt.timedelta(days=90)
    vix = [b.close for b in fetch_daily('^VIX', start, now)]
    spx = [b.close for b in fetch_daily('^GSPC', start, now)]
    if len(vix) < 6 or len(spx) < 21:
        raise ValueError('not enough VIX/S&P 500 history')
    k, e = 2 / 21, sum(spx[:20]) / 20
    for c in spx[20:]:
        e = c * k + e * (1 - k)
    change = (vix[-1] / vix[-6] - 1) * 100
    state = 'risk-off' if vix[-1] >= 22 or change >= 20 else 'calm' if vix[-1] <= 15 else 'normal'
    return {'vix': round(vix[-1], 2), 'vix_change_pct': round(change, 1), 'spx_above_ema20': spx[-1] > e, 'state': state}


# --------------------------------------------------------------------------- backdrop

def backdrop(f, news=None, positioning=None, mood=None):
    """Votes on the backdrop for a LONG: each factor is +1 supportive, -1 against, 0 neutral/unknown."""
    votes = {}
    trend = f['weekly'] + (1 if f['close'] > f['ema20'] > f['ema50'] else -1 if f['close'] < f['ema20'] < f['ema50'] else 0)
    votes['trend'] = (1 if trend > 0 else -1 if trend < 0 else 0,
                      'weekly %s, daily %s' % ({1: 'up', 0: 'flat', -1: 'down'}[f['weekly']],
                                               'up' if trend - f['weekly'] > 0 else 'down' if trend - f['weekly'] < 0 else 'flat'))
    macro = 0
    if f.get('dxy_change_pct') is not None:
        macro -= 1 if f['dxy_change_pct'] > 0.5 else -1 if f['dxy_change_pct'] < -0.5 else 0
    if f.get('us10y_change') is not None:
        macro -= 1 if f['us10y_change'] > 0.10 else -1 if f['us10y_change'] < -0.10 else 0
    votes['macro'] = (max(-1, min(1, macro)), 'dollar %+.1f%%, US 10Y %+.2f pts (20 sessions)' % (
        f.get('dxy_change_pct') or 0, f.get('us10y_change') or 0))
    if news:
        votes['news'] = (1 if news['label'] == 'supportive' else -1 if news['label'] == 'negative' else 0,
                         'score %+.0f' % news['score'])
    if positioning:
        votes['positioning'] = ({'crowded long': -1, 'washed out': 1}.get(positioning['state'], 0),
                                'managed money %s (%d%% pct)' % (positioning['state'], positioning['percentile']))
    if mood:
        votes['risk mood'] = (1 if mood['state'] == 'risk-off' else 0, 'VIX %.1f (%+.0f%% 5d)' % (mood['vix'], mood['vix_change_pct']))
    total = sum(v for v, _ in votes.values())
    label = 'bullish' if total >= 2 else 'bearish' if total <= -2 else 'mixed'
    return {'votes': votes, 'total': total, 'label': label}
