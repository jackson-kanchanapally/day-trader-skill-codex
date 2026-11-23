import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from india import gold as G
from india import gold_news as N
from india.data import IST, Bar

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 18, 6, 0, tzinfo=UTC)


def rss(*items):
    body = ''.join('<item><title>%s</title><link>https://news.example/%d</link><pubDate>%s</pubDate>%s</item>' % (
        title, i, pub, '<source url="https://x">%s</source>' % src if src else '') for i, (title, pub, src) in enumerate(items))
    return ('<?xml version="1.0"?><rss><channel>%s</channel></rss>' % body).encode()


def item(title, hours=1, region='global', source='Reuters'):
    return {'title': title, 'source': source, 'link': 'https://news.example/x?a=1&b=2', 'region': region,
            'published': (NOW - dt.timedelta(hours=hours)).isoformat()}


class ClassifyTests(unittest.TestCase):
    def test_directions_and_drivers(self):
        cases = {
            'Gold slides to over one-year low on firm dollar, US rate outlook': (-1, {'gold price down', 'dollar up'}),
            'Gold, silver prices tumble as stronger dollar, rate-hike fears weigh': (-1, {'gold price down', 'dollar up', 'Fed/rates hawkish'}),
            'Spot gold breaks below $3,800': (-1, {'gold price down'}),
            'Goldman Sachs cuts gold price forecast again': (-1, {'forecast cut'}),
            'Gold hits record high as Fed signals rate cuts': (1, {'gold price up', 'Fed/rates dovish'}),
            'War fears lift safe-haven gold': (1, {'geopolitical risk'}),
            'Rupee hits all-time low against the dollar': (1, {'rupee weak (MCX +)'}),
            'Government cuts gold import duty to 6%': (-1, {'import duty cut'}),
            'Dhanteras demand lifts gold sales': (1, {'Indian demand'}),
            'Ceasefire agreed; markets calm': (-1, {'tensions easing'}),
        }
        for title, (sign, tags) in cases.items():
            got_sign, got_tags, _ = N.classify(title)
            self.assertEqual((got_sign, set(got_tags)), (sign, tags), title)

    def test_false_positives_stay_neutral(self):
        for title in ('Pledged Gold Auctions Likely to Rise in Q3', 'Nifty reclaims pre-war levels intraday',
                      'Gold price today: check 24K and 22K rates in Delhi', 'Druckenmiller buys gold miners'):
            self.assertEqual(N.classify(title)[0], 0, title)
        # gold-only cues need a gold headline
        self.assertEqual(N.classify('Profit-taking hits tech stocks')[0], 0)


class FeedTests(unittest.TestCase):
    def test_parse_rss_strips_source_suffix_and_needs_https(self):
        raw = rss(('Gold slides - Reuters', 'Wed, 18 Nov 2026 03:10:00 GMT', 'Reuters'),
                  ('No date', 'garbage', ''))
        items = N.parse_rss(raw, 'global')
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]['title'], items[0]['source']), ('Gold slides', 'Reuters'))
        self.assertEqual(items[0]['published'], '2026-11-18T03:10:00+00:00')

    def test_gather_dedupes_windows_and_reports_failures(self):
        feeds = [('global', 'https://a'), ('india', 'https://b'), ('india', 'https://c')]
        pages = {'https://a': rss(('Gold falls', 'Wed, 18 Nov 2026 05:00:00 GMT', 'Reuters'),
                                  ('Old gold news', 'Sat, 14 Nov 2026 05:00:00 GMT', 'Reuters')),
                 'https://b': rss(('Gold Falls!', 'Wed, 18 Nov 2026 04:00:00 GMT', 'ET'))}

        def fetch(url):
            if url not in pages:
                raise OSError('down')
            return pages[url]
        items, errors = N.gather(NOW, fetch, feeds=feeds)
        self.assertEqual([it['title'] for it in items], ['Gold falls'])
        self.assertEqual(errors, ['https://c: down'])


class SentimentTests(unittest.TestCase):
    def test_score_label_weights_and_top_picks(self):
        items = [item('Gold slides on firm dollar'), item('Gold falls as yields rise', 2),
                 item('Gold rises', 40, source='Tiny Blog'), item('Rupee falls to record low', 3, 'india', 'Mint'),
                 item('Gold rate today in Delhi', 1)] + [item('Gold drops as dollar firms', 4 + i, source='Tiny Blog') for i in range(6)]
        s = N.sentiment(items, NOW)
        self.assertEqual(s['label'], 'negative')
        self.assertLess(s['score'], -25)
        self.assertEqual(s['scored'], 10)
        self.assertEqual(s['drivers']['gold price down'], 8)
        self.assertEqual(s['top'][0]['title'], 'Gold slides on firm dollar')
        self.assertIn('Rupee falls to record low', [it['title'] for it in s['top']])
        self.assertEqual(N.sentiment([], NOW)['score'], 0)

    def test_one_headline_is_shrunk(self):
        self.assertEqual(N.sentiment([item('Gold falls', 0)], NOW)['label'], 'mixed')

    def test_implied_mcx_reads_only_current_futures_quotes(self):
        items = [item('MCX gold futures fall nearly 2% to Rs 1.31 lakh, silver slide 3% to Rs 1.70 lakh', 2, 'india'),
                 item('Gold futures rise Rs 171 to Rs 1,34,000 per 10 grams', 3, 'india'),
                 item('Gold hit a lifetime high near Rs 1.80 lakh per 10 grams in January', 1, 'india'),
                 item('Gold rate dips below ₹1.40 lakh', 1, 'india'),
                 item('MCX gold futures at Rs 1.29 lakh', 30, 'india')]
        found = N.implied_mcx(items, NOW)
        self.assertEqual([x['per_gram'] for x in found], [13100, 13400])


class PositioningTests(unittest.TestCase):
    def test_cot_percentile_and_state(self):
        rows = [{'report_date_as_yyyy_mm_dd': '2026-11-%02dT00:00:00.000' % (10 - i % 10), 'm_money_positions_long_all': str(300 - i),
                 'm_money_positions_short_all': '0', 'open_interest_all': '1000'} for i in range(100)]
        doc = N.cot(lambda url: json.dumps(rows).encode())
        self.assertEqual((doc['net'], doc['percentile'], doc['state'], doc['change']), (300, 100, 'crowded long', 1))
        with self.assertRaises(ValueError):
            N.cot(lambda url: json.dumps(rows[:10]).encode())

    def test_risk_mood(self):
        def fetch(sym, start, now):
            vals = [15.0] * 30 + [19.0] if sym == '^VIX' else [100.0 + i for i in range(40)]
            return [Bar(dt.datetime(2026, 1, 1, tzinfo=UTC) + dt.timedelta(days=i), v, v, v, v, 0) for i, v in enumerate(vals)]
        mood = N.risk_mood(fetch, NOW)
        self.assertEqual((mood['state'], mood['spx_above_ema20'], mood['vix_change_pct']), ('risk-off', True, 26.7))

    def test_backdrop_votes(self):
        f = {'weekly': -1, 'close': 90, 'ema20': 95, 'ema50': 100, 'dxy_change_pct': 1.5, 'us10y_change': 0.0}
        bd = N.backdrop(f, {'label': 'negative', 'score': -40}, {'state': 'washed out', 'percentile': 10},
                        {'state': 'risk-off', 'vix': 25, 'vix_change_pct': 20})
        self.assertEqual({k: v for k, (v, _) in bd['votes'].items()},
                         {'trend': -1, 'macro': -1, 'news': -1, 'positioning': 1, 'risk mood': 1})
        self.assertEqual((bd['total'], bd['label']), (-1, 'mixed'))


def cfg(**over):
    return G.settings(dict({'risk_per_trade': 5000}, **over))


def feat(**over):
    from test_gold import feat as base
    return base(**over)


class IntegrationTests(unittest.TestCase):
    def test_implied_basis_uses_the_proxy_when_each_headline_was_published(self):
        gc = [Bar(dt.datetime(2026, 11, 18, 3, 0, tzinfo=UTC), 0, 0, 0, 3110.34768, 0)]
        fx = [Bar(dt.datetime(2026, 11, 17, tzinfo=UTC), 0, 0, 0, 100.0, 0)]
        found = [{'per_gram': 11500, 'published': '2026-11-18T04:00:00+00:00', 'title': 't', 'source': 's'},
                 {'per_gram': 11600, 'published': '2026-11-18T05:00:00+00:00', 'title': 't', 'source': 's'},
                 {'per_gram': 99999, 'published': '2026-11-18T05:00:00+00:00', 'title': 'bad', 'source': 's'},
                 {'per_gram': 11500, 'published': '2026-11-18T02:00:00+00:00', 'title': 'before data', 'source': 's'}]
        out = G.implied_basis(found, fx, NOW, fetch_bars=lambda s, n: gc)
        self.assertEqual(out['basis'], 1.155)
        self.assertEqual(len(out['quotes']), 2)

    def test_choose_basis_precedence(self):
        now = dt.datetime(2026, 11, 18, 9, 0, tzinfo=IST)
        implied = {'basis': 1.154, 'quotes': [{}, {}]}
        with tempfile.TemporaryDirectory() as d:
            c = cfg(state_dir=d)
            self.assertEqual(G.choose_basis(c, now)[:2], (1.15, 'default, not calibrated'))
            self.assertEqual(G.choose_basis(c, now, implied)[:2], (1.154, 'from 2 MCX quotes in the news'))
            doc = {'basis': 1.16, 'calibrated_at': (now - dt.timedelta(days=2)).isoformat()}
            (Path(d) / 'calibration.json').write_text(json.dumps(doc))
            self.assertEqual(G.choose_basis(c, now, implied)[:2], (1.16, 'calibrated 2 d ago'))
            doc['calibrated_at'] = (now - dt.timedelta(days=30)).isoformat()
            (Path(d) / 'calibration.json').write_text(json.dumps(doc))
            self.assertEqual(G.choose_basis(c, now, implied)[0], 1.154)
            self.assertEqual(G.choose_basis(c, now)[:2], (1.16, 'calibrated 30 d ago, stale'))

    def test_backdrop_sets_conviction_flags_and_optional_skip(self):
        c = cfg()
        f = feat()
        base = G.build_plan(f, G.evaluate(f, c)[0], c, 1.15)
        news = {'label': 'negative', 'score': -40, 'drivers': {'dollar up': 5, 'Fed/rates hawkish': 3}}
        ctx = {'news': news, 'positioning': {'state': 'crowded long', 'percentile': 92}}
        p = dict(base, flags=list(base['flags']))
        G.apply_backdrop(p, {'total': -2, 'label': 'bearish'}, ctx, c)
        self.assertEqual(p['conviction'], 'Low')
        self.assertIn('News flow is negative for gold (score -40: dollar up 5', p['flags'][0])
        self.assertIn('crowded long (92%', p['flags'][-1])
        self.assertNotIn('suppressed', p)
        p = dict(base, flags=[])
        G.apply_backdrop(p, {'total': -2, 'label': 'bearish'}, ctx, cfg(news='skip'))
        self.assertIn('news flow against the trade', p['suppressed'])
        p = dict(base, flags=[])
        G.apply_backdrop(p, {'total': 3, 'label': 'bullish'}, {}, c)
        self.assertEqual(p['conviction'], 'High' if base['score'] >= 70 else 'Medium')

    def test_run_with_context_uses_news_basis_and_renders_the_block(self):
        from test_gold import market, fake_fetch, NO_QUOTE
        data = market()
        last = data[G.GOLD][-1].time.date()
        now = dt.datetime.combine(last + dt.timedelta(days=1), dt.time(8, 30), IST)
        news = N.sentiment([item('Gold <slides> on firm dollar & yields'), item('Gold falls', 2)], NOW)
        context = {'news': news, 'implied': {'basis': 1.2, 'quotes': [{'per_gram': 13100, 'source': 'Moneycontrol',
                                                                        'published': NOW.isoformat()}]},
                   'positioning': {'date': '2026-11-10', 'net': 112341, 'change': 8727, 'percentile': 33, 'state': 'neutral'},
                   'mood': {'vix': 22.9, 'vix_change_pct': 15.0, 'spx_above_ema20': False, 'state': 'risk-off'}}
        with tempfile.TemporaryDirectory() as d:
            ctx = G.run(cfg(state_dir=d), now, fetch=fake_fetch(data), fetch_quote=NO_QUOTE,
                        context_fn=lambda *a: context)
            self.assertEqual((ctx['basis'], ctx['basis_note']), (1.2, 'from 1 MCX quote in the news'))
            text = '\n'.join(G.format_messages(ctx, cfg()))
            self.assertIn('▼ News -32: strong dollar, gold falling', text)
            self.assertIn('Gold &lt;slides&gt; on firm dollar &amp; yields', text)
            self.assertIn('href="https://news.example/x?a=1&amp;b=2"', text)
            self.assertIn('▬ Funds neutral (33% of 3 y)', text)
            self.assertIn('▲ Fear high: VIX 23', text)
            self.assertIn('Backdrop for buyers: MIXED (+1)', text)
            self.assertIn('Basis 1.200 (1 MCX quote in news)', text)
            detail = G.format_context(ctx)                     # /news keeps the full brief
            self.assertIn('Positioning (CFTC, 10 Nov): managed money net long 112,341', detail)
            self.assertTrue((Path(d) / 'basis.json').exists() is False)   # a past-date run does not store a live basis

    def test_news_off_and_past_dates_skip_the_network(self):
        from test_gold import market, fake_fetch, NO_QUOTE
        data = market()
        now = dt.datetime.combine(data[G.GOLD][-1].time.date() + dt.timedelta(days=1), dt.time(8, 30), IST)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(G, 'gather_context', side_effect=AssertionError):
            ctx = G.run(cfg(state_dir=d), now, fetch=fake_fetch(data), fetch_quote=NO_QUOTE)
            self.assertIn('News, positioning and risk mood are skipped for a past date', ctx['warnings'])
            ctx = G.run(cfg(state_dir=d, news='off'), now, fetch=fake_fetch(data), fetch_quote=NO_QUOTE)
            self.assertEqual(ctx['context'], {})


if __name__ == '__main__':
    unittest.main()
