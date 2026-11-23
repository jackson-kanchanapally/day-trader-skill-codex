"""Challenger strategies for the gold lab, one file per idea: c_<yyyy>_<mm>_<topic>.py.

Each file defines ``strategies(lab)``. ``lab`` is the india.gold_lab module, so a
file needs no imports from this repository. Template:

    def strategies(lab):
        def my_rule(m, n):
            # m is lab.Market: m.c / m.o / m.h / m.l / m.dates lists, m.ema(n), m.vol(n),
            # m.rsi(n), m.prior_max(n), m.prior_min(n), m.dxy_ret63. Return one exposure
            # per session in [0, 1] (None during warm-up); value i may use sessions <= i only.
            return [None if i < n else (1.0 if m.c[i] > m.c[i - n] else 0.0) for i in range(len(m))]
        return [lab.Strategy('my_rule_50', 'my_family', my_rule, {'n': 50}, posthoc=True,
                             added='2026-12-01', note='one-line economic reason')]

tests/test_gold_lab.py checks every strategy here for look-ahead automatically.
"""
