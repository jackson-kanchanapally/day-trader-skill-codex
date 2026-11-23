"""Low-correlation blends, added 2026-11-19 after the first research run.

swing_v1 and dip_rsi2_5 daily returns correlated 0.03 over 2011-2026 (see
docs/GOLD_LAB_JOURNAL.md). Post-hoc: only the forward record counts."""


def strategies(lab):
    return [
        lab.Strategy('blend_swing_dip', 'blend', lab.s_blend, {'parts': ('swing_v1', 'dip_rsi2_5'), 'mode': 'sum'},
                     posthoc=True, added='2026-11-19', note='swing_v1 + dip_rsi2_5, capped at 1x'),
        lab.Strategy('blend_three', 'blend', lab.s_blend, {'parts': ('swing_v1', 'donchian_100', 'dip_rsi2_5')},
                     posthoc=True, added='2026-11-19', note='Average of swing_v1, donchian_100, dip_rsi2_5'),
    ]
