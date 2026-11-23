# Daily Telegram trade alerts

`python3 -m india.picks` scans a liquid NSE universe once per trading day and
sends up to three trade setups to a Telegram bot. Each setup includes an entry
plan, an exit plan and risk details. It is standard-library Python and places
no orders.

## What each alert contains

```
🟢 1. RADICO · BUY (intraday) · score 72/100
Close ₹4,638.20 (+4.5%) · Vol 1.4× avg · RSI 60 · MACD above signal · ATR 3.2%
Why: price > EMA20 > EMA50, above EMA200, histogram expanding, closed at a 20-day high

Entry plan   buy above ₹4,642.90 on a 5-min close after 09:30 · don't chase
             above ₹4,679.50 · cancel if not triggered by 11:30 · skip on a gap
Exit plan    stop ₹4,569.70 · T1 ₹4,716.10 (1R, book 50%, stop to entry)
             T2 ₹4,789.30 (2R) · square off by 15:10
Risk         qty 12 · max loss ≈ ₹992 incl. costs · reward ≈ ₹1,201
             support/resistance · risk level · warnings
```

The header shows the NIFTY 50 trend and how many stocks were scanned and how
many qualified. The footer gives the day's loss limit. If fewer than three
stocks qualify, the alert says so. It does not pad the list with weaker
setups. On a day with no qualifying setups, it says to stay flat.

## How stocks are chosen

The scan uses completed daily candles (Yahoo, about one year). The universe
defaults to the NIFTY 200 list from NSE's archive. A stock must pass every
filter:

| Filter | Default |
| --- | --- |
| Price | ≥ ₹50 |
| 20-session average traded value | ≥ ₹25 crore |
| Last session volume vs 20-session average | ≥ 1.2× |
| ATR(14) as % of price | 1–5% |
| BUY alignment | close > EMA20 > EMA50, MACD above signal, RSI 50–72 |
| SELL alignment | close < EMA20 < EMA50, MACD below signal, RSI 28–50 |
| Not extended | close within 2 × ATR of EMA20 |
| Fresh data | last candle is the latest completed NSE session |

Stocks that pass are scored out of 100:

| Component | Points |
| --- | --- |
| Trend: EMA stack, EMA200 side, rising/falling EMA20 | 20 |
| MACD: crossover within 3 sessions, histogram expanding, side of zero | 20 |
| RSI sweet spot (55–68 for buys, 32–45 for sells) | 15 |
| Volume: 1×→3× average, plus a candle in the trade's direction | 25 |
| Structure: close at or near the 20-day high (buys) / low (sells) | 10 |
| Liquidity: ₹25 cr → ₹500 cr average traded value | 10 |
| Against the NIFTY 50 trend | −10 |

Picks need `min_score` (60). At most one per sector by default.

## Trade plan rules

Plans are intraday.

- **Entry**: 0.1% beyond the previous session's high (buy) or low (sell).
  Enter only after a 5-minute candle closes beyond that level after 09:30.
- **No-chase**: entry + 0.25 × ATR. Skip if the stock gaps past it.
  Cancel if not triggered by 11:30.
- **Stop**: 0.5 × ATR(14), kept between 0.6% and 2.5% of the entry price.
- **Targets**: T1 = 1R (book half, move the stop to entry), T2 = 2R.
- **Square-off**: 15:10 IST.
- **Size**: whole shares chosen so the loss at the stop, plus estimated
  brokerage, STT, exchange fees, GST, stamp duty and 5 bps slippage, stays
  within `risk_per_trade`. Exposure is capped at `capital`; leverage is not
  assumed.
- **Prices**: rounded to a tick-safe increment (0.05 / 0.10 / 0.50 / 1 / 5 by
  price band). Verify the actual tick with your broker.

Short-sale setups are intraday-only (MIS). The cash segment cannot carry a
short overnight. Set `"allow_short": false` for buy-only alerts.

## Results, board meetings and ex-dates

A setup built on yesterday's candle is meaningless if the company announces
results before today's open: the stock can gap far past a 0.5×ATR stop. Before
choosing picks, the alert reads two public NSE lists for the window from the
signal session through the trade day, weekends included
(`india/events.py`):

| Event | Effect with `"events": "skip"` (default) |
| --- | --- |
| Board meeting for results, fund raising, buyback, split, bonus, merger, scheme, rights, delisting and similar | Stock dropped. The alert lists it: `📅 Skipped for results/events: ABB (Board meeting 13 Nov: Financial Results)` |
| Split, bonus, rights, demerger or consolidation ex-date on the trade day | Stock dropped (the price scale changes overnight) |
| Other board meeting (e.g. "Other business matters"), dividend or other ex-date on the trade day | Kept, with a `📅` line on the pick |

`"events": "flag"` keeps every stock and marks blocked ones with a `📅` warning
and a High risk level; `"off"` skips the check. When the check ran, the alert
header says `📅 Results, board meetings and ex-dates checked`. If NSE is
unreachable, the last fetched calendar is used when it covers the window;
otherwise the alert says the calendar was unavailable and you should check
results yourself. It never guesses an event.

## Setup

1. **Create the bot.** In Telegram, message **@BotFather**, send `/newbot` and
   copy the token. Then open your new bot and press **Start**.
2. **Find your chat ID.** Open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy
   `result[0].message.chat.id`. For a group, add the bot to the group and
   post a message first. Group IDs start with `-`.
3. **Store the secrets** outside git. `var/` is ignored:

   ```sh
   mkdir -p var
   printf 'TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=%s\n' 'YOUR_TOKEN' 'YOUR_CHAT_ID' > var/telegram.env
   chmod 600 var/telegram.env
   ```

   Exported `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` variables take
   precedence over the file.
4. **Configure**: `cp config/picks.example.json config/picks.json` and edit
   capital and risk.
5. **Test**:

   ```sh
   python3 -m india.picks --config config/picks.json --test-message   # one-line ping
   python3 -m india.picks --config config/picks.json --dry-run        # print today's alert
   ```

## Run it always-on with Docker (recommended)

`docker-compose.yml` runs two containers with `restart: unless-stopped`:

| Service | What it does |
| --- | --- |
| `dashboard` | The dashboard at http://127.0.0.1:8765, published on host loopback only |
| `alerts` | `india.picks --serve`: sends the morning alert at `alert_time` (08:45 IST) on NSE trading days, retried up to 3 times; settles paper trades every `settle_minutes` (10), sending in-session updates; sends the 15:40 scorecard; optionally records delayed stock/index snapshots in the same loop; with `gold.enabled`, sends the [MCX GOLD PETAL swing alert](GOLD_ALERTS.md) at 08:30 IST Mon–Fri |

```sh
docker compose up -d --build     # start, or rebuild after code changes
docker compose ps                # both should be "healthy"
docker compose logs -f alerts    # scheduler activity
docker compose down              # stop
```

`./var` (alert records, results, `telegram.env`), `./trader` and `./config`
(read-only) are mounted into the containers. The Telegram token is never
copied into the image. The host CLI and the containers share the same state
files. Turn on **Docker Desktop → Settings → General → Start Docker Desktop
when you sign in**, so the stack comes back after a reboot.

Use either Docker or the LaunchAgents below, not both. The sent and
settled markers prevent duplicate messages, but two schedulers would
duplicate the data fetching.

## Schedule it without Docker (macOS)

```sh
python3 -m india.picks --config config/picks.json --print-launchd 08:45 \
  > ~/Library/LaunchAgents/com.daytrader.picks.plist
launchctl load ~/Library/LaunchAgents/com.daytrader.picks.plist
```

This runs Monday–Friday at 08:45 local time; the Mac must use IST. The job
checks the NSE holiday calendar itself and exits quietly on holidays. If the
Mac was asleep at 08:45, launchd runs the job on wake. A wake before 11:30
still sends the alert; a later wake during the session is skipped as stale. A
run after the close sends the plan for the next session. Each session gets one
alert; `--force` resends. Logs go to `var/picks.log`, and each run's full
JSON, including every score and reject reason, goes to
`var/state/picks/picks-<date>.json`.

On Linux, use cron: `45 8 * * 1-5 cd /path/to/checkout && python3 -m india.picks --config config/picks.json`
(with `CRON_TZ=Asia/Kolkata` if the host is not on IST).

If a run fails, for example because data is unavailable, the bot sends a short
failure notice instead of staying silent.

## Paper trading every alert

Every alerted stock is traded on paper with the alert's own rules. Trades
are replayed on Yahoo's delayed 5-minute candles:

| Event | Rule |
| --- | --- |
| Entry | First candle ending between 09:30 and 11:30 that closes beyond the entry level. The fill is that candle's close. |
| Skip | The first candle opens past the no-chase level (gap), or the trigger candle closes past it (chase). |
| Not triggered | No qualifying close by 11:30. |
| Stop | The stop price, or the candle's open if it gapped through the stop. If one candle touches both the stop and a target, the stop is assumed first. |
| Target 1 | Book half at T1 and move the stop to the fill price (breakeven). |
| Target 2 | Exit the rest at T2. |
| Square-off | Anything still open exits at the open of the 15:10 candle. |

Net P&L subtracts the same estimated charges and slippage used for sizing.
R is net P&L divided by the alert's planned maximum loss.

```sh
python3 -m india.picks --config config/picks.json --settle                  # today, so far
python3 -m india.picks --config config/picks.json --settle --date 2026-11-13 # a past session (within ~60 days)
```

A second LaunchAgent runs `--settle` every `settle_minutes` (10). It does nothing
outside NSE sessions or before 09:20. During the session it saves
provisional results, with open trades marked to the latest candle. After
15:40 IST the session is final, and the bot sends one scorecard with each
trade's entry, exits, net P&L and the running total. Settle failures are
logged to `var/picks-settle.log`, not sent to Telegram.
If the data feed is briefly unreachable, each trade keeps its last good
result (marked stale), and the scorecard waits for fresh data until 18:00 IST.

```sh
python3 -m india.picks --config config/picks.json --print-launchd-settle \
  > ~/Library/LaunchAgents/com.daytrader.picks.settle.plist
launchctl load ~/Library/LaunchAgents/com.daytrader.picks.settle.plist
```

### In-session updates

With `"live_updates": true` (default), each settle also sends a short
Telegram message when a paper trade reaches a milestone:

```
⏱ Paper trade update · 09:41 IST
🟢 RADICO BUY triggered · paper fill 12 @ ₹4,643.10 (5-min close 09:35)
    Stop ₹4,569.70 · T1 ₹4,716.10 · T2 ₹4,789.30
```

Milestones are the trigger, T1 (half booked, stop to entry), the final exit
(stop, breakeven, T2 or square-off, with net P&L and R), a gap or chase skip,
and "not triggered by 11:30". Each is sent once per trade
(`var/state/picks/updates-<date>.json`). A trigger, T1 or exit found more
than 30 minutes after its candle (for example after the Mac woke from sleep),
or any milestone on the final settle, is recorded but not sent, because the
scorecard covers it. A gap skip or "not triggered" notice has no candle time,
so it is sent by the first in-session settle that sees it, however late. A
failed send is retried on the next settle. Updates follow delayed candles and
the settle cadence, so they arrive up to `settle_minutes` plus the feed delay
after the event. Set `"settle_minutes": 5` for faster updates. They report the
paper trade, not your broker's fills.

Results are saved as `var/state/picks/results-<date>.json`. Open the
dashboard's **Alert trades** page (`python3 -m dashboard`, then
http://127.0.0.1:8765). It shows net P&L, win rate, average R, a cumulative
curve, the next session's planned alerts, and each session's trades with
entry and exit times. This record is separate from the Trading journal's
paper-account ledger. Only alerts that were actually sent are recorded and
traded; `--dry-run` saves nothing.

## Why each trade won or lost, and lessons

After each settle, `india/review.py` explains every paper trade with
rule-based tags measured on the same 5-minute candles:

| Tag | Meaning |
| --- | --- |
| `FAILED_BREAKOUT` | Stopped within 30 minutes without moving 0.5R in favour |
| `REVERSED_NEAR_T1` | Reached +0.7R or more, then reversed to the stop |
| `STOP_TOO_TIGHT` | After the stop, price reached T1 the same day |
| `NO_FOLLOW_THROUGH` / `DRIFT_WIN` | Never reached T1; closed at square-off at a loss / a profit |
| `FOLLOW_THROUGH`, `PARTIAL_*` | Reached T2, or booked half at T1 |
| `GAP_AGAINST` | Opened at least 0.25 ATR against the signal close |
| `MARKET_AGAINST` / `MARKET_TAILWIND` | NIFTY moved 0.3% or more against / with the trade |
| `LATE_ENTRY`, `CHASED_ENTRY` | Triggered after 10:45, or filled 0.4R or more past the entry |
| `NO_BREAKOUT`, `GAP_PAST_NO_CHASE` | Did not trigger, or skipped on a gap |

The scorecard and the dashboard show a one-line *Why* for each trade, with
the best and worst excursion in R.

Each alert also records its setup **traits** at alert time: buy or short,
RSI stretched, a 3% or larger prior-day move, a volume spike of 2.5× or more,
ATR of 3% or more, more than 1.2 ATR from EMA20, against the NIFTY trend, a
neutral market, no fresh MACD crossover, or a base score below 65. Every
morning, closed trades from final sessions are grouped by trait. A trait
becomes an **active lesson** when all of these hold:

- At least `learn_min_trades` (8) closed trades have it.
- They average −0.25R or worse.
- They are at least 0.25R worse than enough trades without it.
- That gap is more than twice its standard error. A worse-looking trait
  inside the noise shows as *Within noise* on the dashboard, not as a lesson.

Each active lesson takes `learn_penalty` (10) points off new setups with
that trait. The total is capped at `learn_max_penalty` (20). The alert shows
each deduction and its evidence (`📚 −10: …`). Lessons only subtract points
and never add them. The dashboard's **Lessons learned** panel lists every
trait's record.

Patterns in losing trades, such as stops hit before T1 or failed breakouts,
appear as suggestions. They are not applied automatically, because they
change the trade rules and need a backtest first. Set `"learning": false` to
record without adjusting scores.

Small samples mislead. One paper trade's R typically varies by about 0.8R,
so the average of 8 trades is uncertain by about ±0.28R: the standard-error
condition usually needs dozens of trades before a 0.25R gap counts. A lesson
is a ranking adjustment, not proof.

Simulated fills are optimistic in one way and pessimistic in another. Real
fills can be worse than a candle's close. The stop-first rule, however,
under-counts some winners.

## Backtest the scoring

```sh
python3 -m india.picks_backtest --config config/picks.json            # ~30 s first run, cached per day
python3 -m india.picks_backtest --config config/picks.json --since 2025-01-01
```

For every past session (about four years of Yahoo daily candles), the
backtest scores the universe exactly as the alert does, with the same
features, filters, plan and top-N selection and learning off, using only
candles up to that session. It then trades each candidate on the next
session:

- **Daily approximation**, every session. Only the next day's open, high, low
  and close are known, so each trade is walked along open→high→low→close and
  open→low→high→close with the plan's gap skip, trigger, stop, T1/breakeven
  and T2, exiting at the close. The worse and better paths bracket the result.
  The 5-minute trigger close, the 11:30 deadline and the 15:10 square-off
  cannot be checked, so it counts more triggers than the live rules do.
- **Intraday check**, about the last 60 days. Yahoo still has 5-minute
  candles for these, so the recent alert picks are replayed with the exact
  paper-trade rules (`india.picks.simulate`) and compared with the daily
  bracket.

The report shows the alert's result (both paths, by side and by year),
average R by score bucket for every candidate, each trait's gap with its
standard error, and how many trades a lesson needs to clear the noise. The
full report goes to `var/state/picks/backtest-latest.json`. Caveats are
printed with it: today's index list (survivorship bias), prices not adjusted
for dividends, estimated costs, and no events filter in history.

## Intraday recorder

The optional recorder collects intraday evidence for later playbooks. It
creates no trade signals and sends no Telegram messages, and it does not
change the morning alert or its measured record. It uses two free sources and
standard-library Python only: no broker API, no paid feed.

| Source | What is recorded | Calls |
| --- | --- | --- |
| NSE official MCP, `cm_get_live_indices` | Sampled five-minute candles for the configured indices: NIFTY 50, NIFTY BANK, India VIX and 14 sector indices by default | One every `poll_seconds` (30) |
| Yahoo spark endpoint | Five-minute closes for the stock universe | Once per completed window: one call per 20 symbols, one second apart (10 calls for the NIFTY 200) |

Stocks do not come from NSE because its live stock call returns at most 500
of about 2,000 rows, with only a symbol-prefix filter. On 17 November 2026 it
covered 19 to 22 of the 200 NIFTY 200 stocks per call, and no stock window
came out complete. That snapshot is still available as `"feeds": ["indices",
"equity"]`, off by default.

### Enable it

Copy the `recorder` object from `config/picks.example.json` into
`config/picks.json`, set `"enabled": true` and restart the `alerts` service:

```sh
docker compose up -d --build alerts
```

The recorder runs inside the existing `--serve` loop; there is no second
scheduler. It is off in the example config until a full session has been
checked. Configs without a `recorder` object keep it off.

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | false | Turn collection on |
| `state_dir` | `var/state/recorder` | Where the evidence and status are kept |
| `feeds` | `["indices"]` | NSE feeds to poll; add `"equity"` for the partial NSE stock snapshot |
| `poll_seconds` | 30 | Seconds between NSE calls (30–300). With two feeds they alternate |
| `indices` | NIFTY 50, NIFTY BANK, INDIA VIX and the sector indices | Exact NSE index names to keep |
| `stock_source` | `yahoo` | `yahoo` records stock closes; `off` records none |
| `symbols` | empty | Stock universe as `.NS` names; empty uses the alert's universe |
| `max_age_seconds` | 180 | Oldest NSE sample accepted, and the longest wait before a window is frozen |
| `timeout_seconds` | 10 | Timeout per request |
| `max_backoff_seconds` | 900 | Cap on the backoff after failures |
| `snapshot_limit`, `symbol_filter` | 500, empty | Only for the `equity` feed: row cap and one symbol prefix |

Each source backs off on its own, so a failure on one never pauses another.
An NSE feed waits 60, 120, 240, 480 and then at most 900 seconds after
consecutive failures. A failed Yahoo call ends that refresh at once, and the
next refresh waits 5, 10 and then at most 15 minutes. Nothing is retried
immediately, calls never run in parallel, and the timing survives a restart.
Polling follows the sourced NSE calendar, so nothing is requested on holidays
or outside the session. The recorder shares one loop with the morning alert
and the paper settles: while a long job runs (a morning scan retried after
09:15, for example), no index samples are taken. Missed polls are not caught
up in a burst; those minutes stay missing.

### Keep the machine awake

A sleeping computer records nothing. The recorder, the morning alert, the
paper-trade updates and the Docker containers all stop while a Mac sleeps,
and Docker Desktop does not keep it awake. On 17 November 2026 a live check
ended when the Mac went to sleep at 11:50 IST.

Index samples missed during sleep are gone; those windows are saved as
incomplete. Stock closes are different: each Yahoo response holds the whole
session, so the next refresh records the missed windows, with a `known_at`
that shows they arrived late.

During NSE hours (09:15–15:30 IST), use one of:

- `caffeinate -i -t 23400 &` in a terminal before the open. It holds the Mac
  awake for six and a half hours, then lets it sleep again.
- On mains power: **System Settings → Battery → Options → Prevent automatic
  sleeping on power adapter when the display is off**. Closing the lid still
  sleeps the Mac unless an external display is attached.
- An always-on Linux machine running `docker compose up -d`, or
  `python3 -m india.picks --config config/picks.json --serve`.

### Check a source without Telegram

```sh
python3 -m india.picks --config config/picks.example.json --record-once --dry-run
python3 -m india.picks --config config/picks.example.json --record-once --record-feed stocks --dry-run
```

The first makes one NSE index call. The second makes one Yahoo refresh (10
calls for the NIFTY 200, about 12 seconds). Each prints coverage, missing and
rejected instruments and any error. A dry run uses an in-memory database and
a temporary universe cache, reads no Telegram secrets and saves nothing.
Omit `--dry-run` to keep the observation. `--record-feed equity` checks the
NSE stock snapshot.

### What is stored

`var/state/recorder/snapshots.sqlite` holds the normalised index samples,
every failed call, the scheduler state, the frozen index candles and the stock
closes. `status.json` shows the latest result per source. Nothing here is part
of the source release.

**Index candles** are labelled `SAMPLED_DELAYED_SNAPSHOT_5M`,
`exchange_candle: false`, `paper: true`, `execution_enabled: false`.

- NSE index times have minute precision and trail the clock by one to two
  minutes. Several samples inside one source minute are normal; the latest
  one received is the minute's last price.
- A window is frozen when a sample from after its end arrives, so the close is
  the last sample of the final minute. If none arrives, it is frozen
  `max_age_seconds` after the window ends. `known_at` records when. A frozen
  candle is never rewritten.
- `complete` is the strict flag: all five source minutes were sampled.
- `usable` is the flag for a close-based check: the final minute was sampled,
  at most one minute is missing and the prices are consistent.
  `missing_minutes` always lists what was not seen.
- Open and close are the first and last sampled prices. A new day high or low
  is added to a window only when both samples around it fall inside that
  window. Index volume is always null.
- A missed, stale or rejected sample never fills a minute, and nothing is
  interpolated.

**Stock closes** are labelled `YAHOO_5M_CLOSE`, `close_only: true`,
`exchange_candle: false`.

- Only windows inside the NSE session that ended at least 20 seconds before
  the response count. The window in progress and Yahoo's trailing live tick
  are ignored.
- A window with no close from Yahoo stays missing.
- The first close recorded for a window is kept. If a later response shows a
  different value, the refresh counts it in `revised_closes` and leaves the
  stored one alone.
- There is no open, high, low or volume. `fetch_yahoo_5m` in `india/picks.py`
  returns full candles for one symbol when a playbook needs them.

```python
from india.recorder import load_candles, load_stock_closes
day, cutoff = '2026-11-17', '2026-11-17T12:00:00+05:30'
index_rows = load_candles('var/state/recorder', day, as_of=cutoff, usable_only=True)
stock_rows = load_stock_closes('var/state/recorder', day, as_of=cutoff)
```

`as_of` returns only what was known at that time, for causal replays.

With the `equity` feed on, NSE stock candles also carry volume as the change
in cumulative shares between samples (`volume_exchange_aligned: false`) and
session VWAP as cumulative value over cumulative shares. A missing baseline
or a counter reset leaves volume null.

### Live check

On 17 November 2026, 12:30–13:08 IST, both sources ran in memory at the
`--serve` cadence, with no Telegram messages. Six full windows, 12:35 to
13:00, were checked:

| Source | Expected | Result |
| --- | --- | --- |
| NSE indices (17) | 102 index windows | 68 usable, 34 strictly complete. 70 calls succeeded and one returned HTTP 502. Four windows had at most one missing minute; the first two each missed two. Candles were frozen 22–110 seconds after their window ended. |
| Yahoo stocks (NIFTY 200) | 1,200 closes | 1,198 recorded, 1,195 within 90 seconds of the window end. 90 calls, no errors. 51 closes differed on a later refresh; the first value was kept. |

The index shortfall comes from the source. NSE's index time has minute
precision, trails the clock by 20–140 seconds and advances irregularly: in an
earlier check, two polls a minute apart both showed 12:03 and the next showed
12:05, so 12:04 could not be sampled at all. Faster polling cannot recover
such a minute. A full-session run has not been checked.

### Recorder limits

- Yahoo's endpoint is unofficial and undocumented. It can change, rate-limit
  or lag without notice; the delay seen in one check is not a guarantee.
- NSE describes its MCP as informational and not for real-time trading. These
  samples are for local paper research.
- A usable index candle is a sample of delayed prices, not the exchange's
  open, high, low and close.
- Intraday history starts when the recorder starts. Yahoo keeps about 60 days
  of five-minute candles; nothing here gives years of intraday data.
- `india/index_fno.py` reads the index candles when the index F&O switch is
  on (see [Index options signals](#index-options-signals)).

## Alert switches

The dashboard's **Alert trades** page starts with **Telegram alert streams**,
one switch per stream. Each switch is saved in
`var/state/alert-switches.json` (beside the picks `state_dir`). The dashboard
writes this file and the `--serve` scheduler reads it on every 30-second tick,
so nothing needs a restart. Both Docker services mount `./var`.

| Switch | Default | When off |
| --- | --- | --- |
| Gold futures signals | On | The gold morning alert, live level updates, failure notices and gold lab messages are not sent. No new gold paper trade is opened. The gold lab still logs its forward decisions, live watch still advances so old crossings are not replayed later, and `/gold`, `/price` and the other commands still answer. |
| Index options signals | Off | `india.index_fno` does not run and its recorder is stopped. |

The gold switch only works when `gold.enabled` is true in
`config/picks.json`; the dashboard says so when it is not. The daily stock
picks are not switched here.

## Index options signals

`india/index_fno.py` runs a paper-only options desk for NIFTY and BANKNIFTY
while the **Index options signals** switch is on.

**09:05 desk brief.** For each index it lists yesterday's high, low and close,
the central pivot range (CPR; narrow means a trend day is likely, wide means a
range day), R1/S1, the daily trend (EMA20/EMA50) and ATR. It also gives the
India VIX regime and the structure that regime calls for, and the expiry and
lot in use. With a live chain it adds PCR, max pain and the call and put OI
walls. Daily data comes from NSE's historical MCP.

**Entries.** Per index, once per session:

1. The 09:15–09:30 opening range from the recorder's five-minute index
   candles (all three must be usable).
2. A five-minute close beyond the range by a buffer (0.05% of price or 10% of
   the range, whichever is more) before 13:30. No trade when the range is under
   0.15% or over 1.2%, VIX is above 25, the day is listed in `event_dates`, or
   the breakout is seen more than 10 minutes late.
3. A grade from 0 to 100 that weighs:
   - daily-trend alignment (±20)
   - CPR width (±10)
   - whether price is beyond the CPR (±5)
   - room to the next level (PDH/PDL, R1/S1, R2/S2, CPR, OI wall); a level
     closer than 1R **blocks** the trade
   - breakout candle strength (+10)
   - opening-range quality (+5)
   - cheap VIX (+5)
   - PCR agreement (±10)

   Grades below `min_score` (60) are not traded. With `send_skips`, one line
   says why.
4. The option structure. The expiry is the first one at least `min_dte` (2)
   sessions away, which avoids expiry-day theta and gamma. Candidates are
   priced, sized and checked for reward to T2 per rupee risked, after charges
   (`min_option_rr`, 1.5):
   - VIX at or below `buy_iv_max` (16): buy outright, ATM, or one strike in
     the money when expiry is three sessions or less away. A debit spread is
     the fallback when an outright lot does not fit the budget.
   - VIX above 16: compare a one-strike-ITM buy (less extrinsic value to
     bleed) with a debit spread at least four strikes wide (the sold leg
     offsets IV), and pick the better reward/risk.

   Naked option selling is never suggested. Lots are sized so the stop costs
   at most `risk_per_trade` including charges; when this is null the picks
   `risk_per_trade` is used. A structure that does not fit is skipped with the
   rupee figure.

**Alert.** Each alert gives:

- the contract (for example `NIFTY 01 DEC 24100 CE`), grade and expiry
- an entry premium with a do-not-chase price
- a stop as both a premium and a spot level
- T1 (1R: book half, stop to cost) and T2 (2R, pulled in to a level between
  1.2R and 2R)
- a theta time stop (60 minutes without T1) and the square-off at 15:15
- rupee risk, reward/risk, and the reasons for the grade

Updates follow for T1, T2, stop, breakeven, time stop and square-off, with the
exit premium and the P&L in rupees after estimated charges.

**Premiums: free, no broker account.** NSE publishes no free official
option-chain service, and its Terms of Use restrict automated collection from
the website, so nothing here scrapes it. Premiums are Black-Scholes estimates
from NSE's official India VIX. NIFTY uses VIX as it is; BANKNIFTY uses VIX ×
its 20-day realised volatility ÷ NIFTY's, held to 0.8–2.0, from the same free
daily history. The brief shows the IV used. Alerts lead with the index level
for every stop and target and show premiums as "≈". Each alert also gives the
option's delta (for example "≈₹0.54 per NIFTY point"): if your broker shows a
different premium, keep trading the index levels, and skip if the premium is
above the stated cap. Exits are judged on the index; their premiums are
estimates. PCR, max pain and OI walls need a live chain, so they are left out
on the free source.

Expiries follow `contracts.<index>.expiry_weekday` and `monthly`: NIFTY weekly
on Tuesday and BANKNIFTY on the last Tuesday of the month, each moved to the
previous session when that day is a holiday. Lot sizes default to 65 (NIFTY)
and 30 (BANKNIFTY). **Check these against NSE's current circulars**, because
NSE revises them.

Optional: if you already have an Upstox account, `"chain_source": "upstox"`
with `UPSTOX_ANALYTICS_TOKEN=...` in `var/telegram.env` uses its live chain
(bid/ask, IV, delta, OI, PCR, max pain) and its real expiries and lot sizes.
It is never used unless you choose it.

When the switch is on and `recorder.enabled` is false, the scheduler starts an
indices-only recorder and stops it when the switch goes off. Turn the switch on
before 09:05: a recorder started after 09:15 misses the opening range. To try
it without Telegram:

```sh
python3 -m india.index_fno --config config/picks.json --dry-run
python3 -m india.index_fno --config config/picks.json --dry-run --as-of 2026-11-23T09:06:00+05:30
```

**Daily risk.** At most `max_open_trades` (1) options trade is open at a time
across NIFTY and BANKNIFTY. Realised losses plus the risk of open trades stay
within `max_daily_loss`; when that is null, the cap is 2 × the options
`risk_per_trade`. A new trade is sized to the room left under the cap, and when
there is none it is skipped with one line giving the reason.

**Offline mornings.** For example, the laptop has no internet, or is asleep,
until 10:15:

- **Brief:** it waits for the daily data and goes out late (marked
  `LATE 10:16`, with the opening ranges) at any time before the entry
  deadline. After 13:30 it is skipped.
- **Missing candles:** the scheduler fills the recorder's missing candles from
  Yahoo's free five-minute chart, the whole gap and not just the opening range.
  A breakout that happened while offline is then recognised as stale and is not
  chased; a breakout after the reconnect is traded as usual. The recorder's own
  candles are always preferred, Yahoo is asked at most every five minutes per
  index, and only for windows that closed more than three minutes earlier.
- **Messages:** they go into an outbox in the day's state file and are sent in
  order. A failed send stays queued for the next tick instead of being lost,
  and a message delivered two or more minutes late starts with "⌛ Delayed N
  min". Undelivered messages expire:
  - a new entry after `fresh_minutes` (10), because acting late is worse than
    missing it; its follow-ups are dropped too, and one note lists the entries
    that expired
  - a skip note after 30 minutes
  - the brief at the entry deadline
  - trade updates at 16:00
- **Daily jobs:** the morning stock picks, the gold alert and the gold lab only
  use one of their three daily attempts when Telegram is reachable. A late
  connection still gets them, within their usual windows (picks until 11:30,
  gold until 23:00).

**Replay backtest.** `python3 -m india.index_fno_backtest --config
config/picks.json` drives the live engine through the last ~60 sessions, five
minutes at a time. It uses Yahoo's free five-minute NIFTY, BANKNIFTY and India
VIX candles and NSE daily levels from before each session, and writes
`var/state/index_fno/backtest-latest.json`. It runs two passes: the live rules,
and every breakout with the grade threshold, blockers and risk guard off,
broken down by grade, blocker, index, side and structure.

The first run (26 Aug–20 Nov 2026, 59 sessions) found **no edge**:

- Every breakout taken: 106 trades, +0.02R per trade (±0.05 standard error)
  on spot and +0.04R on modelled option P&L.
- The 60-minute time stop closed 92 of the 106. Price reached 1R within an
  hour of only 6% of breakouts, and within the day of 31%.
- Grade A did +0.08R (±0.10) against −0.01R for grade C, which is within the
  noise.
- The "level within 1R" blocker stopped 86 of the 106 without improving
  results.
- The live rules sent one trade, because the blocker and the ₹1,000 budget
  stopped the rest.

Limits:

- Spot levels come from NSE's delayed snapshot (one to three minutes behind),
  sampled into candles that are not exchange candles. Exits are judged on spot,
  and their premiums are live chain quotes or model estimates.
- These rules have **no backtest**: there is no history of option prices here.
  Treat the alerts as a paper experiment until the recorded results show an
  edge after costs.
- NSE describes its MCP data as informational, not for real-time trading
  ([NOTICE.md](../NOTICE.md)).

## Limits

- Signals come from delayed end-of-day data. Opening gaps and news can
  invalidate a setup before 09:15. The results/events check covers scheduled
  board meetings and ex-dates, not unscheduled news.
- The built-in NSE holiday calendar covers 2026. Before January 2027, add the
  2027 holidays to a calendar file and set `"calendar"` in the config.
  Otherwise the job reports that the calendar does not cover the date.
- Scores are a rule-based ranking, not a probability of profit. The first
  backtest (13 Nov 2026, NIFTY 200, Sep 2022–Nov 2026, default settings)
  found **no edge after costs**. The alert averaged −0.30R per trade on the
  worse daily path and −0.04R on the better one, and −0.17R over 26
  recent trades replayed on 5-minute candles. Score barely predicted outcome
  (correlation +0.04). Costs alone take about 0.13R per trade. Rerun the
  backtest after changing any rule, and paper-trade before risking money.
