# Gold lab journal

One dated entry per research or self-improvement run, newest last. Record
every variant tried, not only the ones kept. See [GOLD_LAB.md](GOLD_LAB.md)
and [automation/gold-lab-improve.md](../automation/gold-lab-improve.md).

## 2026-11-19: lab built, first research run

**Setup.** 19 configurations in 9 families (benchmark, swing, tsmom,
tsmom_blend, ma_trend, donchian, dip_buy, macro_tsmom, ensemble). The
parameter grids use conventional values chosen before any run. Capital
₹1,00,000, 15% volatility target, 1× maximum leverage, 6.5% carry, monthly
rolls and MCX costs. Test years 2011 to 2026-11-18.

**Methodology fixes during the run.**
- The first bootstrap compared mean returns, which penalised strategies
  that are often in cash. It now compares Sharpe ratios.
- The first promotion rule subtracted the multiple-testing haircut (+0.61).
  No strategy could ever clear it, so the lab would never promote. The
  haircut is now reported but is not a rule.
- With `cash_rate_pct` above 0, Sharpe counted interest as skill. Sharpe now
  always uses returns above the cash rate.
- The initial champion was set to `ensemble_trend` before the first run.
  It was changed to `swing_v1` after the run. The stated reason is
  continuity with the live morning alert and its smaller drawdown (11.6%
  against 45.5%). This choice was made after seeing results.

**Results (test years, cash earning nothing).** Best Sharpe:
donchian_100 0.37, swing_v1 0.34, dip_rsi2_5 0.33; buy and hold 0.29. No
configuration's P(Sharpe > buy and hold) exceeded 0.66. Momentum lookbacks
alone were weakest (0.14–0.26), and the dollar gate made them worse
(macro_tsmom_0: 0.02).

**Carry.** 1× buy and hold: 11.3% CAGR for the proxy, 10.9% after costs
and rolls, 3.3% after 6.5% carry with idle cash, and 10.7% with cash earning
6.5%. Carry is the largest single factor in futures results.

**Post-hoc additions.** Daily-return correlation of swing_v1 and dip_rsi2_5
was 0.03 (swing_v1 with donchian_100: 0.59). Variants tried, test-year
Sharpe: swing+donchian_100 summed 0.42; swing+donchian_100+dip summed 0.39;
the three averaged 0.42; swing+dip summed 0.44. Two were registered:
`blend_swing_dip` (0.46 in the registry run) and `blend_three` (0.40). Both
are flagged post-hoc; only their forward records count.

**Open questions for later runs.**
- Real yields (FRED DFII10) as a regime input: the classic gold driver, and
  its relationship broke in 2022.
- Indian demand seasonality (wedding season, Akshaya Tritiya, Diwali)
  against MCX returns.
- Whether a 1.5–2× leverage cap on the low-drawdown blends is acceptable to
  the user. This is a risk-appetite decision, not a research one.
