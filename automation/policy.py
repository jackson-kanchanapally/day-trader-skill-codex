"""Explicit standing permission and bounded settings for simulated funds."""
import copy
import math

from workflow.common import config, digest

DEFAULT = dict(
    schema_version=1, paper=True, execution_enabled=False, exchange='NSE',
    enabled=True, poll_seconds=15, workers=4, batch_size=20,
    provider='yahoo', auto_fallback=False,
    risk=dict(budget=100000, max_loss_per_trade_inr=500, max_daily_loss_inr=1500,
              max_trades_per_day=3, max_orders_per_day=12, max_position_pct=25,
              max_order_inr=25000, cash_buffer_pct=10, min_reward_risk=1.5,
              no_entries_before='09:30', no_entries_after='14:45', flat_by='15:10',
              costs=dict(slippage_bps=5)),
    selection=dict(setups=['breakout_20d','bull_flag','reclaim_ma20','oversold_bounce'],
                   min_price=50, min_daily_turnover_inr=100000000,
                   min_session_turnover_inr=10000000, min_volume_x=1.2,
                   max_extension_pct=10, min_score=35,
                   max_participation=0.01, max_open_positions=3),
    exits=dict(stop='or-mid', target='2R'),
    max_data_delay_seconds=120, minimum_daily_coverage=0.95,
)


_UNSET = object()


def checked(raw=_UNSET):
    doc = copy.deepcopy(DEFAULT)
    raw = {} if raw is _UNSET else raw
    if not isinstance(raw, dict) or set(raw)-set(doc):
        raise ValueError('Unknown automatic paper settings')
    for key, value in raw.items():
        if isinstance(doc.get(key), dict):
            if not isinstance(value, dict) or set(value)-set(doc[key]):
                raise ValueError('Unknown automatic paper '+key+' settings')
            doc[key].update(value)
        else:
            doc[key] = value
    if type(doc['schema_version']) is not int or doc['schema_version'] != 1 or doc['paper'] is not True or doc['execution_enabled'] is not False or doc['exchange'] != 'NSE':
        raise ValueError('Automation supports only local NSE cash-equity paper simulation')
    if type(doc['enabled']) is not bool or type(doc['auto_fallback']) is not bool:
        raise ValueError('enabled/auto_fallback must be booleans')
    if doc['provider'] not in ('yahoo','nse','upstox'):
        raise ValueError('Unsupported candle provider')
    for key, low, high in (('poll_seconds',5,300),('workers',1,8),('batch_size',1,20),('max_data_delay_seconds',0,300)):
        if type(doc[key]) is not int or not low <= doc[key] <= high:
            raise ValueError(key+' is outside its supported range')
    if type(doc['minimum_daily_coverage']) not in (int,float) or not .5 <= doc['minimum_daily_coverage'] <= 1:
        raise ValueError('minimum_daily_coverage must be 0.5..1')
    cfg = config(**doc['risk'])
    from india.market import costs
    if cfg.get('costs') is not None and not isinstance(cfg['costs'],dict):
        raise ValueError('Costs must be an explicit fee-setting object')
    costs(1.,1.,1,'NSE',cfg.get('costs'))
    if cfg['max_loss_per_trade_inr'] > cfg['max_daily_loss_inr'] or cfg['max_daily_loss_inr'] > cfg['budget']:
        raise ValueError('Per-trade and daily risk must fit within paper capital')
    selection = doc['selection']
    allowed = set(DEFAULT['selection']['setups']) | {'oversold'}
    if not isinstance(selection['setups'], list) or not selection['setups'] or not all(isinstance(s,str) for s in selection['setups']) or set(selection['setups'])-allowed:
        raise ValueError('Choose supported daily setups')
    for key in ('min_price','min_daily_turnover_inr','min_session_turnover_inr','min_volume_x','max_extension_pct','min_score'):
        value = selection[key]
        if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
            raise ValueError('Invalid selection '+key)
    if type(selection['max_open_positions']) is not int or not 1 <= selection['max_open_positions'] <= 20:
        raise ValueError('max_open_positions must be 1..20')
    if type(selection['max_participation']) not in (int,float) or not 0 < selection['max_participation'] <= .1:
        raise ValueError('max_participation must be positive and no more than 10% of lagged volume')
    if not isinstance(doc['exits']['stop'],str) or not isinstance(doc['exits']['target'],str) or doc['exits']['stop'] not in ('or-mid','or-low') or doc['exits']['target'] not in ('2R','3R'):
        raise ValueError('Automatic exits support opening-range midpoint/low and 2R/3R targets')
    return doc


def mandate(settings):
    settings = checked(settings)
    cfg = config(**settings['risk'])
    return dict(schema_version=1, kind='STANDING_PAPER_AUTOMATION_POLICY',
                paper=True, execution_enabled=False, exchange='NSE',
                risk_config_sha256=digest(cfg), settings=settings,
                scope='Automatic simulated entries, protective exits and end-of-day liquidation; no broker transport')


def validate_mandate(document, cfg=None):
    if not isinstance(document, dict) or document.get('kind') != 'STANDING_PAPER_AUTOMATION_POLICY':
        raise ValueError('A standing paper automation policy is required')
    expected = mandate(document.get('settings'))
    if document != expected or not expected['settings']['enabled']:
        raise ValueError('Invalid or disabled paper automation policy')
    if cfg is not None and document['risk_config_sha256'] != digest(cfg):
        raise ValueError('Risk settings differ from the standing paper policy')
    return document
