# MCX GOLD PETAL swing alerts

`python3 -m india.gold` sends one Telegram message each weekday morning about
MCX GOLD PETAL (GOLDPETAL, 1 gram per lot). The message contains a swing setup
with an entry order, a stop, a target, a trailing exit and a lot size, or it
says to stay flat and which levels to watch. Every setup it sends is
paper-traded, and later messages report trades that are pending, open or
closed. It uses free data only, is standard-library Python and places no
orders.

## Where the prices come from

MCX has no free price API, and Yahoo carries no MCX contracts. MCX gold is
priced from international gold and the rupee, so the alert builds a
rupees-per-gram **proxy** from two free Yahoo series:

```
proxy (₹/g) = COMEX gold GC=F ($/oz) × USD/INR ÷ 31.1035 × basis
```

`basis` covers import duty and the domestic premium or discount. Each
morning the alert chooses it in this order:

1. Your own calibration, if it is at most `calibration_max_days` (7) old.
2. MCX futures prices quoted in the last 24 hours of Indian headlines, for
   example "MCX gold futures fall nearly 2% to Rs 1.31 lakh". Each quote is
   divided by the proxy at the time the headline was published, and the
   median is used. Only "to/at Rs X" quotes from futures or MCX headlines
   count; retail rates and past highs do not. Lakh quotes are rounded, so
   expect this to be within about 1%.
3. An older calibration.
4. The default, 1.15. On 18 Nov 2026, three MCX quotes in the news implied
   1.148–1.16. The earlier default, 1.06, assumed 6% import duty and put
   levels about 9% too low.

To pin it exactly, read the GOLDPETAL price in your broker app and send
`/calibrate 12450` to the bot, or run:

```sh
python3 -m india.gold --config config/picks.json --calibrate 12450
# Docker: docker compose exec alerts python -m india.gold --config config/picks.json --calibrate 12450
```

This saves `basis = 12450 ÷ (live COMEX × live USD/INR ÷ 31.1035)` to
`var/state/gold/calibration.json`. The alert names the basis it used and its
age, and it warns after `calibration_max_days` (7). Recalibrate weekly, and
after a budget changes import duty. Every level also comes with its distance
from the proxy close in %. If GOLDPETAL trades away from the proxy, shift
each level by the same percentage.

The dollar index (DX-Y.NYB) and the US 10-year yield (^TNX), also from
Yahoo, give macro context.

## What a message contains

Each alert is one short card. The verdict comes first, then the price, the
backdrop, then either the plan or the levels to watch:

```
🟡 GOLDPETAL · Wed 18 Nov · ⛔ NO TRADE, stay flat
₹13,076/g now (-2.8% vs close ₹13,452)

🧭 Backdrop for buyers: BEARISH (-2)
▼ Trend down, weekly and daily · RSI 33 · ADX 31
▼ Dollar +1.7% · US 10Y 5.10% (-0.01), 20 d
▼ News -46: gold falling, Fed hawkish, strong dollar
▬ Funds neutral (33% of 3 y)
▲ Fear high: VIX 25 (+31% in 5 d)

👀 Watch: bounce to ₹14,026 (EMA20) · close below ₹13,388 (20-day low)
Shorts would fit, but they are off: they lost in every backtest.

📰 Gold drops below $3,800 as Fed hike bets deepen bullion selloff · Investing.com
📰 Gold, silver ETFs plunge up to 3% as bullion prices hit multi-month lows · Moneycontrol.com

Basis 1.152 (2 MCX quotes in news) · rule-based, not advice · /news /price /trades
```

On a setup day, the plan replaces the watch line:

```
🟡 GOLDPETAL · Wed 05 Aug · 🟢 BUY setup (breakout) · conviction Medium · score 61
...backdrop...
▶ Entry: SL-buy above ₹15,255, limit ₹15,437 · valid 3 sessions
✖ Stop ₹14,349 (-5.2%)
🎯 Target ₹16,161 (+6.8%, 1R) → stop to entry, then trail ₹725 behind the best price
📦 1 lot · max loss ₹958 · margin ≈ ₹1,526 · exit by session 40
Why: closed beyond the 20-day high, weekly trend flat, above EMA200, ...
⚠️ High volatility (ATR 2.4% of price); the stop is wide
⚠️ Weekly trend is flat; consider half size
```

Percentages are measured from the last close, so levels can be shifted to
the live MCX price. ▲ helps a gold buyer and ▼ hurts. Open paper trades
(`📒`) and the running record appear when there are any. The line under the
price is the delayed live proxy when the quote is available; otherwise it is
the last close. When overnight COMEX trading is already past a new setup's
trigger or no-chase level, that is the first warning. At most three
warnings and two headlines are shown. `/news` gives the full news brief,
with six headlines, the driver tally, positioning detail and MCX quotes.

## News, positioning and risk mood

`india/gold_news.py` adds a block to every alert, using free sources only:

| Source | What it gives |
| --- | --- |
| Google News RSS, 6 searches | Global gold, Fed/dollar/yields and geopolitics; India: MCX gold, gold futures, rupee, RBI and import duty |
| Economic Times commodities RSS | Indian commodity news |
| CFTC Commitments of Traders | COMEX gold managed-money net position, weekly (official US government API) |
| Yahoo | VIX and the S&P 500 |

```
📰 News flow: negative for gold (-38) · 298 headlines in 48 h, 123 scored
Drivers (▲ supports gold, ▼ weighs): ▼ gold price down 56 · ▲ geopolitical risk 20 · ▼ Fed/rates hawkish 17 · ...
🔴 Gold, silver prices tumble as stronger dollar, rate-hike fears weigh (Business Standard, 44 min ago)
🔴 Gold slides to over one-year low on firm dollar, US rate outlook (Reuters, 2 h ago)
🔴 Gold hovers near over one-year low as firm dollar, Fed-hike bets weigh (🇮🇳 Economic Times, 28 h ago)
🇮🇳 MCX in the news: ₹13,100/g (Moneycontrol.com, 2 h ago) → basis ≈ 1.154
📊 Positioning (CFTC, 10 Nov): managed money net long 112,341 (+8.7k w/w), 33% of 3 y → neutral
😨 Risk mood: VIX 22.9 (+15% in 5 d) · S&P 500 below EMA20 → risk-off
🧭 Backdrop for gold longs: bearish (-2) · trend ▼ · macro ▼ · news ▼ · positioning ▬ · risk mood ▲
```

**How news is scored.** Each headline is matched against a fixed list of
rules (`CUES`). A rule is a pattern, a direction for gold and a driver tag.
Examples: gold price up or down (the verb must follow "gold" within two
words); dollar up or down; Fed hawkish or dovish; yields up or down;
geopolitical risk against easing tensions; central-bank buying; ETF flows;
profit-taking; analyst forecasts cut or raised. Indian rules cover the rupee
(a weaker rupee lifts MCX gold), import-duty changes, festival and wedding
demand, and weak demand. A headline's direction is the sign of its matches.
The score runs from −100 to +100 over 48 hours:

- Gold headlines weigh 1, macro headlines 0.5.
- Weights halve every 24 hours.
- A pseudo-weight of 4 pulls thin samples toward 0, so one headline alone
  is never more than "mixed".

At +25 or more the flow is *supportive*, at −25 or less *negative*. The
message shows the driver tally and up to six headlines, links included. Major
sources and headlines with more drivers come first, with three global, two
Indian and one macro. Rate-list and clickbait headlines match nothing and
are not scored. The rules miss sarcasm, negation ("skeptical of a big rate
hike" still reads hawkish) and anything not in the list.

**Positioning.** The managed-money net position is ranked against the last
156 weekly reports: 85th percentile or higher is *crowded long*, 15th or
lower *washed out*. The report is dated Tuesday and published on Friday.

**Risk mood.** *Risk-off* means VIX ≥ 22 or up 20% in 5 sessions; *calm*
means VIX ≤ 15.

**Backdrop and conviction.** Five votes for a long, each +1, 0 or −1:

- trend: weekly plus daily
- macro: dollar and US 10-year yield over 20 sessions
- news flow
- positioning: crowded long −1, washed out +1
- risk mood: risk-off +1

A total of +2 or more is *bullish*, −2 or less *bearish*. A setup is rated
**High** conviction with a score of 70+ and a backdrop of +2 or more for its
side, **Low** with −2 or less, and **Medium** otherwise. A setup against the
news flow gets a flag and a High risk level. With `"news": "skip"` it is
reported but not added. A crowded-long position flags long setups. On a
bearish day with no setup, the message says a short would fit, but shorts
stay off because they lost in every backtest.

**What is and is not measured.** Free news has no history, so the news score
cannot be backtested. It is context, and the default `"news": "flag"` never
blocks a trade. Each paper trade records its backdrop and news score at alert
time. Over time the record shows whether trading with or against the news did
better. Positioning has history, and it was tested (see the backtest
section): it did not hold up as a filter.

## Setups

The alert uses completed daily bars only. A COMEX day ends at 17:00 New York
time, well before the 08:30 IST alert. Two setups qualify:

| Setup | Rules (buy; sells are mirrored) |
| --- | --- |
| Trend pullback | Close > EMA50, EMA20 > EMA50, EMA50 rising; weekly trend up (close above a rising 10-week EMA); a low in the last 3 sessions within 0.25 ATR of EMA20; the signal bar closes above EMA20 and above its open, in the upper half of its range; RSI 42–70; at most 1.2 ATR above EMA20 |
| 20-day breakout | Close above the prior 20-session high; ADX ≥ 20; close in the top 40% of the bar; above EMA50; weekly trend not down; RSI ≤ 78; at most 3 ATR above EMA20 |

The score (out of 100) adds weekly alignment (20), daily structure (20),
momentum: MACD and RSI (15), ADX and directional index (15), setup quality
(15), and macro (15): a falling dollar index and falling US yields support
longs. A setup needs `min_score` (60). Only one gold position is open at a
time; a new signal while a paper trade is pending or open is reported but not
added.

## Trade plan

- **Entry**: SL order 0.1 ATR beyond the signal bar's high (buy) or low
  (sell). Its limit is the no-chase level, 0.5 ATR further. The order is
  valid for 3 sessions. Re-place it each morning, because commodity orders
  are day orders.
- **Stop**: beyond the 5-session swing extreme plus 0.25 ATR, kept between
  1 and 2.5 ATR from the entry.
- **T1**: 1R. Book half (with 2+ lots) and move the stop to the entry.
- **Trail**: then trail the stop on the rest by 2 × the signal-day ATR
  behind the best price since the entry, updated at each close. Set
  `"trail_atr": 0` to use a fixed T2 at `target2_r` (3R) instead.
- **Time stop**: exit at the close of session 40.
- **Size**: whole lots, so the loss at the stop plus estimated costs stays
  within `risk_per_trade`. Notional is capped at
  `capital × max_leverage` (1.0 means no leverage). Margin is an estimate
  (`margin_pct`, 10%); check your broker's calculator.
- **Costs** (estimated, `costs` overrides): ₹20 brokerage per order, CTT 0.01%
  on the sell side, MCX transaction charge 0.0021%, SEBI fee, 0.002% stamp
  duty on the buy side, 18% GST on fees, and 1 tick of slippage per side.
- **Flags**: FOMC decision days (built in for 2026) and your own
  `event_dates` in the entry window and the first two weeks of the trade;
  high ATR; stretched RSI; a flat weekly trend; dollar or yields moving
  against the trade.

GOLDPETAL contracts expire monthly. Trade a contract month with at least
three weeks to expiry, and check the expiry date on MCX.

## Paper trading

Each sent setup goes to `var/state/gold/trades.json`. Every morning, each
unfinished trade is replayed on the proxy's daily bars, using the basis it
was planned with:

| Event | Rule |
| --- | --- |
| Entry | A session that opens beyond the entry fills at the open; otherwise a touch fills at the entry |
| Gap skip | The first session after the signal opens beyond the no-chase level |
| Expired | Not triggered within 3 sessions |
| Stop | At the stop, or at the open if it gapped through. If a bar touches both the stop and a target, the stop is assumed first |
| T1 / trail / time | As in the plan |

The message reports new triggers and T1s, open trades marked to the latest
close in ₹ and R, closed trades with net P&L and R after costs, and the
running record. These are proxy results, not your broker's fills.

## Live level alerts

With `live_watch` on (default), `--serve` checks pending and open gold paper
trades every `watch_minutes` (15) during `mcx_hours` (09:00–23:30 IST,
Monday–Friday). It uses Yahoo's delayed 15-minute COMEX bars × USD/INR × the
trade's basis, and sends one message per event:

| Message | When |
| --- | --- |
| ⏳ Near the trigger | A pending trade comes within 0.25 ATR of its trigger: make sure the SL order is placed |
| 🔔 Trigger crossed | The proxy trades through the trigger |
| 🎯 T1 reached | Book half (or, with 1 lot, keep it) and move the stop to the entry |
| 🛑 Stop reached | The proxy trades through the current stop (initial, breakeven or trailing) |

Bars from before the signal session's close are ignored. Bars missed while
the machine slept are caught up on the next check, and their message names
the bar's time. These messages are heads-ups for managing your broker order.
The morning alert's daily replay is still the official paper record: it
leaves out the trigger and T1 the watcher already sent, and it confirms a
stop with its net P&L. Yahoo's 15-minute COMEX data has occasional gaps;
those minutes cannot be checked. `--watch --dry-run` runs one check on a
temporary copy of the state and prints the messages.

## Telegram commands

With `commands` on (default), `--serve` reads the bot's new messages on each
30-second tick and answers these, from the alert chat only:

| Command | Reply |
| --- | --- |
| `/gold` | Today's swing view, built now with the live line and the news block. A preview: nothing is recorded |
| `/news` | The news flow, MCX quotes in the news, positioning, risk mood and the backdrop vote |
| `/price` | The delayed live proxy, its inputs, and the distance to your pending trigger or open trade's stop and T1, with open P&L |
| `/trades` | The last ten gold paper trades and the running record |
| `/calibrate 12450` | Sets the basis from the GOLDPETAL price in your broker app (₹ signs and commas are fine) |
| `/help` | The list |

Messages from other chats are ignored. Commands older than 10 minutes, for
example from while the machine was asleep, are skipped rather than replayed.
In a group chat, any member can send commands. The bot must not have a
webhook set, because Telegram's `getUpdates` and webhooks exclude each other.

## Backtest: what the rules have earned

```sh
python3 -m india.gold --config config/picks.json --backtest                   # 2006 to today, ~2 s
python3 -m india.gold --config config/picks.json --backtest --since 2015-01-01
```

The backtest runs the live functions over daily history since 2000 (warm-up),
one position at a time, with costs. It writes the report to
`var/state/gold/backtest-latest.json`.

Results on 17 Nov 2026, default settings, 2006-01-01 to 2026-11-16:

| | Trades | Win | Avg R (± s.e.) | Total | Profit factor | Max DD |
| --- | --- | --- | --- | --- | --- | --- |
| Default rules (long-only, T1 1R + 2 ATR trail, 40 sessions) | 106 | 61% | +0.20 ± 0.12 | +21.7R | 1.52 | 5.6R |

How the defaults were chosen, and what that means:

- **First design**: both sides, T1 1.5R, T2 3R, 10-session time stop. It
  averaged **−0.07R** over 207 trades. Most trades hit the time stop, and
  shorts averaged −0.29R.
- **Exit variants (2006–2018 only)**: five variants were compared. All were
  within noise of zero; long-only, T1 1R with a 2 ATR trail was +0.01R ± 0.14
  over 58 trades. Shorts lost −0.25R to −0.38R in every variant, so
  `allow_short` is off by default. In rupees, gold has a long upward drift.
- **Held-out check (2019–2026, run once)**: the chosen rules made
  **+0.46R ± 0.19** over 49 trades (profit factor 2.5). Random long entries
  with the same exits made **+0.25R ± 0.14**. On 2006–2018 they made about
  −0.03R.
- **Regime filters (2006–2018, 18 Nov 2026)**: five gates with an economic
  reason were tested on the signals and on random long entries: 12-month
  momentum > 0, 6-month momentum > 0, close above EMA200, dollar index not
  rising, and ATR ≤ 2%. None improved on no filter by more than its
  standard error (signals: −0.04R to +0.07R; no filter +0.01R). The two
  momentum gates and the dollar gate made random entries worse. None was
  adopted, and the 2019–2026 period was not used to test them.
- **Positioning filter (18 Nov 2026)**: CFTC weekly reports from June 2006
  were used as of their Friday release. On 2006–2018, skipping longs when
  managed money was at or above the 85th percentile of 3 years lifted the
  signals from +0.08R (47 trades) to +0.28R (31), and random entries from
  −0.08R to −0.03R. That is about one standard error. On 2019–2026, checked
  once, it lowered the signals from +0.45R to +0.37R and left random
  entries flat (+0.14R → +0.15R). It was not adopted as a filter; crowded
  positioning is a flag only.
- **Score**: it does not rank outcomes. Signals scoring 80+ did no better
  than 60–79.

In short, gold's 2019–2026 bull market and the plan's structure (stay long,
cut at 1R, book at 1R, trail the rest) produced most of the result. The
entry signals have not shown skill beyond that. Treat the alerts as a
disciplined, risk-defined way to trade with gold's trend, not as a proven
predictive edge. Paper-trade them, watch the running record, and rerun the
backtest after changing any rule.

## Turn it on

Add a `gold` object to `config/picks.json` (copy it from
`config/picks.example.json`) and set `"enabled": true`. The containers read
`config/` live but run the code they were built with, and older code rejects
the `gold` key. Rebuild both containers first, or the stock alert and the
dashboard fail until you do. Then:

```sh
python3 -m india.gold --config config/picks.json --dry-run        # print today's message
python3 -m india.gold --config config/picks.json --calibrate 12450 # or send /calibrate 12450 to the bot
docker compose up -d --build                                       # rebuild BEFORE adding "gold" to picks.json
```

`india.picks --serve` (the Docker `alerts` service) runs the gold alert
Monday–Friday from `alert_time` (08:30 IST) until 23:00, once per day. It
retries a failed run up to 3 times, 20 minutes apart, and a failure is sent
to the chat. A run that finds no new COMEX session since the last alert (a US
holiday, for example) sends nothing. Without Docker, schedule
`python3 -m india.gold --config config/picks.json` at 08:30 on weekdays with
cron or a LaunchAgent.

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | false | Run inside `--serve` |
| `alert_time` | 08:30 | IST, before MCX opens at 09:00 |
| `capital`, `max_leverage` | 100000, 1.0 | Notional cap |
| `risk_per_trade` | 1000 | ₹ loss at the stop including costs |
| `min_score` | 60 | Minimum setup score |
| `allow_short` | false | Allow sell setups |
| `contract`, `lot_grams`, `tick` | GOLDPETAL, 1, 1.0 | Contract name, grams per lot, ₹ tick per gram |
| `margin_pct` | 10 | Estimated margin, % of notional |
| `basis`, `calibration_max_days` | 1.15, 7 | Fallback basis; a calibration older than this is used only after MCX quotes in the news |
| `entry_valid_sessions`, `max_hold_sessions` | 3, 40 | Order validity, time stop |
| `target1_r`, `trail_atr`, `target2_r` | 1.0, 2.0, 3.0 | T1; trail after T1 (0 = fixed T2 at `target2_r`) |
| `event_dates` | [] | Extra events: `[{"date": "2026-12-10", "name": "US CPI"}]` |
| `costs` | see above | Cost overrides |
| `live_watch`, `watch_minutes`, `mcx_hours` | true, 15, ["09:00", "23:30"] | Live level alerts and their cadence and hours |
| `commands` | true | Answer Telegram commands |
| `news` | flag | `flag`: news, positioning and risk mood as context and flags; `skip`: also hold back setups against the news flow; `off`: none of it |

With ₹1,000 risk per trade and gold's daily ATR of about ₹300/g, swing stops
are ₹300–800 per lot, so most plans are 1–3 lots.

## Limits

- News scoring is a rule list, not a reading of the article. Google News
  search results change, and some regional sites appear. Feeds can be down
  and are then reported as unavailable. Nothing is retried.

- The levels come from a proxy, not from MCX quotes. MCX trades 09:00–23:30
  IST (23:55 during US daylight saving time), and its daily bar does not line
  up exactly with COMEX's. The basis drifts with duty, the domestic premium
  and time to expiry.
- GC=F is Yahoo's front-month COMEX series. It is not adjusted for
  rollovers, so roll days can show small jumps.
- Yahoo's endpoints are unofficial and can change, lag or rate-limit without
  notice.
- The FOMC dates are built in for 2026 only. Add later meetings, CPI and US
  payrolls under `event_dates`. Unscheduled news is not covered.
- Live alerts follow delayed COMEX data, at most every `watch_minutes`, and
  only while the machine is awake. Always keep the SL order and the stop
  with your broker.
- This is rule-based research, not investment advice. Futures are leveraged;
  verify prices, margin and expiry with your broker.
