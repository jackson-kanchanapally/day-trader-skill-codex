# Gold strategy lab

`python3 -m india.gold_lab` runs many gold strategies side by side. It
measures them honestly over 20 years, paper-trades all of them forward from
today in a log that cannot be edited afterwards, and replaces the
**champion** (the strategy whose trades are sent to Telegram) only when
rules fixed in advance say a challenger is better. A monthly
self-improvement run can add new challengers, but it cannot change the rules
or the record. Research and paper trading only: it places no orders.

The [morning gold alert](GOLD_ALERTS.md) answers "is there a trade
today?" The lab answers "which way of trading gold actually works, and is
it still working?"

## Why a lab instead of a better indicator

The morning alert's backtest found that its entry signals did no better than
random long entries with the same exits. Most of its profit came from gold's
long rise in rupees. Adding indicators to one system and keeping what looks
good is how backtests get overfitted. The lab instead:

1. tests many simple, well-known strategy types in the same engine;
2. picks parameters only from past data (walk-forward);
3. shows how much of the best result luck alone would produce;
4. records every strategy's daily decision before its outcome exists;
5. promotes a challenger only after 40 forward sessions and statistical
   evidence over the 2011–2026 test years.

## The pieces

| Layer | What it does |
| --- | --- |
| Market | Daily ₹/gram proxy (COMEX gold × USD/INR × basis, from `india.gold`), the dollar index and the US 10-year yield, all from Yahoo |
| Strategies | Each one is a pure function: price history → target exposure (0 to 1) per session. The same function drives the backtest, the walk-forward test and the live ledger. Challengers added later live in `india/gold_lab_challengers/`. A test checks every registered strategy for look-ahead bias |
| Engine | Whole GOLDPETAL lots, sized by volatility, traded at the next session's open, with MCX costs, a monthly contract roll and the futures carry |
| Evaluation | Full-history metrics, a yearly walk-forward, and block-bootstrap comparisons of Sharpe ratios |
| Ledger | `var/state/gold_lab/decisions.jsonl`: one line per COMEX session with every strategy's decision, hash-chained so any edit is detected |
| Review | Forward results replayed from the ledger, the promotion check, an implementation-drift check, and a report in `var/state/gold_lab/reviews/` |

## The strategies

| Family | Configs | Rule (long or flat) |
| --- | --- | --- |
| benchmark | `buy_hold`, `buy_hold_vt` | Always long, at 1× notional or volatility-targeted |
| swing | `swing_v1` | The morning alert's rules. Approximate here: the lab trades at the next open, while the alert fills intraday |
| tsmom | `tsmom_63/126/252` | Long while the close is above the close 3, 6 or 12 months ago |
| tsmom_blend | `tsmom_blend` | The share of 1/3/6/12-month lookbacks with positive momentum |
| ma_trend | `ma_10_50`, `ma_20_100`, `ma_50_200` | Fast EMA above slow EMA |
| donchian | `donchian_20/55/100` | In on a close above the prior N-session high, out on a close below the prior N/2-session low |
| dip_buy | `dip_rsi2_5/10/20` | Above EMA200 and RSI(2) below 5/10/20; out on a close above EMA5 or after 10 sessions |
| macro_tsmom | `macro_tsmom_0/2` | `tsmom_blend` at half size while the dollar index is up more than 0% or 2% over 63 sessions |
| ensemble | `ensemble_trend` | The average of `tsmom_blend`, EMA 50/200 and Donchian 55 |
| blend (post-hoc) | `blend_swing_dip`, `blend_three` | Low-correlation combinations, added after the first research run |

**Post-hoc** means designed after seeing the results. Its backtest is
in-sample by construction, so only its forward record is clean evidence.
Every strategy added later is post-hoc in the same way.

## How the engine trades

- **Signal**: computed at each completed COMEX close (by 03:30 IST).
- **Order**: at the next session's open, which corresponds roughly to
  MCX's 09:00 IST open.
- **Size**: `exposure × min(max_leverage, vol_target_pct ÷ 40-session
  volatility) × equity ÷ price`, rounded down to whole lots. The position
  changes only when the target moves by more than `rebalance_band` (25%) or
  the signal turns on or off. This avoids paying ₹20 brokerage a day for
  one-lot adjustments.
- **Costs**: the morning alert's MCX cost model per order. A held position
  pays a sell and a buy at each month change, because GOLDPETAL expires
  monthly.
- **Carry**: MCX futures trade above spot by roughly the Indian interest
  rate. A futures holder therefore earns about spot minus `carry_pct`
  (6.5%) a year. `cash_rate_pct` credits interest on the account's equity.
  Set it to your liquid fund's yield if your idle cash and margin
  collateral earn interest. CAGR is the account's total return; Sharpe
  always uses returns above `cash_rate_pct`.

### Carry is the biggest number in the lab

For 2011–2026, holding 1× long the whole time:

| | CAGR |
| --- | --- |
| Gold in rupees (the proxy itself) | 11.3% |
| Futures, costs and rolls, no carry | 10.9% |
| Futures with 6.5% carry, idle cash | **3.3%** |
| Futures with 6.5% carry, cash earning 6.5% | 10.7% |

Two practical consequences. To hold gold for the long run, an ETF or a
sovereign gold bond is far cheaper than rolling futures with idle cash.
And if you trade futures, keep the cash you don't need for margin in a
liquid fund, or pledge it if your broker allows.

## Results on 19 Nov 2026

```sh
python3 -m india.gold_lab --config config/picks.json --research
```

Test years 2011 to 18 Nov 2026 (15.8 years), capital ₹1,00,000, volatility
target 15%, no leverage, costs, rolls and 6.5% carry included. Cash earns
nothing in the first table.

| Strategy | CAGR | Sharpe | Max DD | Exposure | P(Sharpe > buy & hold) |
| --- | --- | --- | --- | --- | --- |
| blend_swing_dip* | 3.1% | 0.46 | 14.5% | 35% | 0.77 |
| blend_three* | 2.0% | 0.40 | 15.9% | 60% | 0.72 |
| donchian_100 | 3.5% | 0.37 | 36.9% | 55% | 0.66 |
| swing_v1 | 2.0% | 0.34 | 11.6% | 31% | 0.56 |
| dip_rsi2_5 | 1.1% | 0.33 | 7.4% | 4% | 0.57 |
| buy_hold | 4.0% | 0.29 | 53.0% | 100% | – |
| ensemble_trend | 2.3% | 0.26 | 45.5% | 93% | 0.38 |
| tsmom_blend | 1.2% | 0.16 | 49.0% | 93% | 0.12 |

\* post-hoc

With cash earning 6.5% (`cash_rate_pct: 6.5`), the top of the table is:

| Strategy | CAGR | Sharpe | Max DD | P(Sharpe > buy & hold) |
| --- | --- | --- | --- | --- |
| blend_swing_dip* | 10.2% | 0.47 | 6.4% | 0.78 |
| blend_three* | 9.3% | 0.46 | 4.3% | 0.82 |
| donchian_100 | 11.2% | 0.42 | 17.1% | 0.76 |
| swing_v1 | 9.1% | 0.37 | 7.0% | 0.61 |
| buy_hold | 11.1% | 0.29 | 37.1% | – |

What this says:

- **No strategy is proven better than buy and hold.** 21 configurations
  were tried, so the best of them would show a Sharpe of about 0.62 from
  luck alone (the *haircut*). Every Sharpe in the table is below 0.5, and
  a Sharpe's standard error over 16 years is about 0.26.
- **The timing strategies earn about the same as holding, with much smaller
  drawdowns.** With cash earning interest, `swing_v1` and the blends kept
  most of buy and hold's return while their worst drawdowns were 4–7%
  instead of 37%. That is the honest case for trading gold rather than
  holding it.
- **Momentum lookbacks on their own (`tsmom_*`) were the weakest family.**
  The dollar gate made them worse, in line with the regime-filter tests in
  [GOLD_ALERTS.md](GOLD_ALERTS.md).
- **The walk-forward meta-selector** chose each year's best family from the
  previous five years. It made a Sharpe of 0.45 out of sample, no better
  than the best single strategies. Its picks include the post-hoc blends,
  so even that figure is partly in-sample.

## Forward paper trading

```sh
python3 -m india.gold_lab --config config/picks.json --dry-run   # what today's run would log and send
python3 -m india.gold_lab --config config/picks.json             # log today's decisions (once per COMEX session)
python3 -m india.gold_lab --config config/picks.json --status    # forward results so far
python3 -m india.gold_lab --config config/picks.json --verify    # check the hash chain
```

With `gold.enabled`, `india.picks --serve` (the Docker `alerts` service)
runs the lab once a day, Monday to Friday, right after the morning gold
alert. It retries up to 3 times, 20 minutes apart. Each run:

1. computes every strategy's exposure at the last completed COMEX close;
2. appends one hash-chained line to `decisions.jsonl` (at most one per
   session; a repeat is refused);
3. replays every strategy's forward paper account **from the logged
   decisions only**, starting with `capital` on its first logged day;
4. sends a Telegram message only when the champion's lot count changes,
   for example `🟢 BUY 2 GOLDPETAL at the MCX open`;
5. on `review_weekday` (Monday), runs the full review and sends the weekly
   scoreboard.

`/lab` in the bot chat shows the champion, the ledger check and every
strategy's forward return and current lots.

Forward P&L is never stored; it is recomputed from the log and the price
data on every run. Changing a strategy's code therefore cannot change its
past forward record: the record follows what was logged, not what the code
would do now.

**Implementation drift.** The review replays each strategy on today's data
and compares it with what was logged on each past day. A disagreement
means the strategy looked ahead, Yahoo revised its history, or the code
changed without a version bump. More than 5% disagreement blocks promotion.

## Champion and promotion

The initial champion is `swing_v1`, the rules the morning alert already
sends, so the lab and the alert start in agreement. It is stored in
`var/state/gold_lab/champion.json` with its history.

A challenger replaces the champion at a review only if **all** of these hold
(`PROMOTION` in `india/gold_lab.py`):

| Rule | Value |
| --- | --- |
| Forward paper sessions | ≥ 40 (about two months) |
| Test-year Sharpe above the champion's | ≥ +0.10 |
| Block-bootstrap P(challenger's Sharpe > champion's), test years | ≥ 0.80 |
| Replay disagreement with its logged decisions | ≤ 5% |
| Forward return versus the champion's | not more than 1 s.d. of the difference behind |

If several qualify, the largest Sharpe edge wins. The haircut is reported
but is not a rule. No gold strategy can clear a 0.6 haircut in 16 years
of data, so requiring it would freeze the lab forever. The rules guard
against bugs, look-ahead and big mistakes; they cannot create an edge the
data does not contain. Forty forward sessions cannot show skill either;
they show that the strategy behaves live as it did in the backtest.

`--review` runs the check on demand and writes
`reviews/YYYY-MM-DD.md` and `.json`. With `--dry-run` it reports without
promoting.

## Self-improvement

The lab improves on two levels:

- **Automatically, every week**: the review re-ranks every strategy with
  the latest data and promotes a challenger when the fixed rules say so.
- **Monthly, with any server-hosted AI**: `scripts/gold_lab_improve.sh`
  (`python3 -m india.gold_lab_improve`) gives an AI the protocol in
  [automation/gold-lab-improve.md](../automation/gold-lab-improve.md), the
  latest review and the journal ([GOLD_LAB_JOURNAL.md](GOLD_LAB_JOURNAL.md)).
  The AI may only add new challenger files under
  `india/gold_lab_challengers/` and append to the journal.

```sh
scripts/gold_lab_improve.sh --agent pi                                  # pi coding agent, its default model
scripts/gold_lab_improve.sh --agent pi --model openai/gpt-6-sol         # pi with a chosen provider/model
scripts/gold_lab_improve.sh --agent codex                               # OpenAI Codex CLI
scripts/gold_lab_improve.sh --agent claude                              # Claude Code
DEEPSEEK_API_KEY=... scripts/gold_lab_improve.sh --agent api --provider deepseek   # any OpenAI-compatible cloud API
scripts/gold_lab_improve.sh --agent cmd --agent-cmd "my-ai --prompt-file {prompt_file}"
```

Agent CLIs (`pi`, `codex`, `claude`, `gemini`, `aider`, or your own command
with `--agent cmd`) edit files themselves. With `--agent api`, a chat model
behind an OpenAI-compatible cloud API (`deepseek`, `openai`, `openrouter`,
`groq`, `anthropic`, or `custom` with `--base-url`) returns the files as
JSON. The runner writes them and sends any errors back, up to 3 rounds.

**Only server-hosted models.** Local models are refused before anything
runs: Ollama, LM Studio, localhost and LAN endpoints, and model names like
`ollama/...`. On 19 Nov 2026 a 14B local model used about 12 GB and
restarted the 16 GB laptop this runs on. A run with a cloud model costs this
machine about a minute of tests.

**The runner enforces the rules, whatever the AI does:**

1. The AI works in an isolated git worktree on a new branch,
   `gold-lab/improve-YYYY-MM`. Your checkout, `var/state` and the containers
   are not touched.
2. Diff policy: only new `india/gold_lab_challengers/c_*.py` files and
   appends to the journal are allowed. Any other change fails the run.
3. At most 3 new strategies in 2 files. Each must be marked post-hoc and
   dated today, and every plugin must load.
4. The look-ahead test and the whole test suite must pass.
5. Research gate: a new strategy's 2011+ Sharpe must reach the median of the
   existing strategies. A file with any failing strategy is dropped, but its
   result is still written to the journal.
6. The runner writes its own verdict to the journal and commits on the
   branch. It never merges or pushes. To adopt the new challengers:
   `git merge gold-lab/improve-YYYY-MM && docker compose up -d --build`.
   Their forward record starts on the next daily run.

A plugin file defines `strategies(lab)` and returns `lab.Strategy` objects
(see `india/gold_lab_challengers/__init__.py`). It cannot change PROMOTION,
the engine, the cost model or the ledger. The two blends added on 19 Nov live
in `c_2026_11_blends.py` as the reference example.

That is the guard against "recursive self-improvement" becoming recursive
overfitting. Ideas can come from the backtest, but promotion needs evidence
that arrived after the idea.

## Settings

Optional `config/gold_lab.json` (copy `config/gold_lab.example.json`). This
is a separate file, so `config/picks.json` and the running containers'
config validation are not affected.

| Setting | Default | Meaning |
| --- | --- | --- |
| `capital` | `gold.capital` | ₹ per paper account |
| `vol_target_pct`, `vol_lookback` | 15, 40 | Yearly volatility each position is scaled to, and its window |
| `max_leverage` | 1.0 | Notional cap as a multiple of equity |
| `rebalance_band` | 0.25 | No-trade band for lot changes |
| `carry_pct` | 6.5 | Yearly futures carry on notional |
| `cash_rate_pct` | 0 | Yearly interest on equity; set your liquid fund's yield if cash earns it |
| `roll_monthly` | true | Charge a round trip each month while a position is held |
| `train_years`, `first_test_year` | 5, 2011 | Walk-forward window and the first test year |
| `bootstrap_samples` | 400 | Bootstrap resamples |
| `telegram` | true | Champion changes and the weekly scoreboard |
| `review_weekday` | 0 | Weekday of the automatic review (0 = Monday) |
| `initial_champion` | `swing_v1` | Champion until the first promotion |

## Limits

- Proxy prices, not MCX quotes: the basis, MCX hours and contract rolls
  differ from the proxy. Over 2006–2026 the import duty changed several
  times, which moves MCX against the proxy.
- Next-open fills. Stop-based strategies such as `swing_v1` are
  approximated; their exact R-based backtest is in
  [GOLD_ALERTS.md](GOLD_ALERTS.md).
- Daily bars only. Signals arrive after the COMEX close, about 5.5 hours
  before MCX opens; overnight moves are taken as they come.
- Costs and carry are estimates. Lot rounding at ₹1 lakh is coarse:
  about 7 lots at ₹13,000/g.
- Yahoo's endpoints are unofficial. A revised history shows up as replay
  disagreement in the review.
- Past results do not guarantee future ones. This is research and paper
  trading, not investment advice.
