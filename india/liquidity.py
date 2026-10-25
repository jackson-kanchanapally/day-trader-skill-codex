"""Causal candle-volume capacity screen; no quote/depth or fill guarantee."""
import datetime as dt
import math
from decimal import Decimal, ROUND_FLOOR
from typing import Optional

from .data import Bar, validate

MODEL = 'lagged-completed-volume-v1'


def normalize(config: Optional[dict]) -> Optional[dict]:
    """An absent model preserves the established unscreened execution policy."""
    if config is None:
        return None
    if not isinstance(config, dict) or set(config) != {'max_participation'}:
        raise ValueError('Liquidity configuration requires exactly max_participation')
    limit = config['max_participation']
    if type(limit) not in (int, float) or not math.isfinite(limit) or not 0 < limit <= 1:
        raise ValueError('max_participation must be finite and in (0,1]')
    return dict(max_participation=float(limit))


def capacity(qty: int, reference: Optional[Bar], at: dt.datetime,
             minutes: int, config: dict) -> dict:
    """Bound a full order by the immediately preceding completed bar's volume.

    The fill bar's final volume never sets its own capacity budget. For a
    timed close this deliberately keeps the same preceding-bar proxy. The
    capacity budget applies to one independent simulated order, not a portfolio.
    """
    settings = normalize(config)
    if settings is None:
        raise ValueError('Capacity assessment requires an explicit liquidity configuration')
    if type(qty) is not int or qty < 1 or type(minutes) is not int or minutes not in (1, 5):
        raise ValueError('Capacity requires positive whole-share quantity and a supported interval')
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('Capacity decision time requires a timezone')
    result = dict(model=MODEL, quantity=qty, decision_at=at.isoformat(),
                  max_participation=settings['max_participation'], reference_time=None,
                  reference_volume=None, maximum_quantity=None, participation=None)
    if reference is None:
        return dict(result, status='UNAVAILABLE', reason='MISSING_PREVIOUS_COMPLETED_BAR')
    validate([reference])
    result.update(reference_time=reference.time.isoformat(), reference_volume=reference.volume)
    if reference.time.date() != at.date() or reference.time+dt.timedelta(minutes=minutes) != at:
        return dict(result, status='UNAVAILABLE', reason='REFERENCE_MUST_BE_PREVIOUS_COMPLETED_BAR')
    units = Decimal(str(reference.volume))*Decimal(str(settings['max_participation']))
    maximum = int(units.to_integral_value(rounding=ROUND_FLOOR))
    passed = qty <= maximum
    return dict(result, maximum_quantity=maximum,
                participation=qty/reference.volume if reference.volume > 0 else None,
                status='PROXY_PASSED' if passed else 'CAPACITY_EXCEEDED',
                reason=None if passed else 'ORDER_EXCEEDS_LAGGED_VOLUME_BUDGET')
