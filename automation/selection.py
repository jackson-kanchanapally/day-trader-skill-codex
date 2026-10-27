"""Deterministic setup selection; scores express ordering, never profit odds."""
import math

STRENGTH = {'breakout_20d':40,'bull_flag':32,'reclaim_ma20':28,'oversold_bounce':20,'oversold':8}


def daily_score(row):
    tags = row.get('setups', [])
    relative = row.get('volume_x') or 0
    turnover = row['price']*row['volume']
    return round(max((STRENGTH.get(tag,0) for tag in tags), default=0)
                 + min(10,max(0,len(tags)-1)*5) + min(3,max(0,relative))*25/3
                 + 15*min(1,max(0,(math.log10(turnover)-5)/3)) if turnover > 0 else 0, 3)


def shortlist(daily, settings, master):
    rules = settings['selection']
    accepted, rejected = [], []
    for row in daily['hits']:
        reasons = []
        turnover = row['price']*row['volume']
        score = daily_score(row)
        if not set(row['setups']) & set(rules['setups']): reasons.append('DAILY_SETUP_FILTER')
        if row['price'] < rules['min_price']: reasons.append('PRICE_FILTER')
        if turnover < rules['min_daily_turnover_inr']: reasons.append('DAILY_LIQUIDITY_FILTER')
        if row.get('ma20') and (row['price']/row['ma20']-1)*100 > rules['max_extension_pct']: reasons.append('EXTENSION_FILTER')
        if score < rules['min_score']: reasons.append('DAILY_SCORE_FILTER')
        if row['symbol'] not in master: reasons.append('NO_DATED_TICK_METADATA')
        item = dict(symbol=row['symbol'], daily_score=score, turnover_inr=turnover, setups=row['setups'])
        (rejected if reasons else accepted).append(dict(item, reasons=reasons))
    accepted.sort(key=lambda row:(-row['daily_score'],-row['turnover_inr'],row['symbol']))
    return accepted, rejected


def rank_entries(results, daily_rows):
    scores = {row['symbol']:row for row in daily_rows}
    ranked = []
    for row in results:
        if not row.get('eligible') or row.get('data_complete') is not True or row['symbol'] not in scores:
            continue
        base = scores[row['symbol']]
        relative = (row.get('trigger') or {}).get('volume_x') or 0
        score = base['daily_score']+min(3,max(0,relative))*5
        ranked.append(dict(symbol=row['symbol'], score=round(score,3), daily_score=base['daily_score'],
                           relative_volume=relative, daily_setups=base['setups'], entry=row))
    return sorted(ranked,key=lambda row:(-row['score'],row['symbol']))
