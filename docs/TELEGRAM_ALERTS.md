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
| `alerts` | `india.picks --serve`: sends the morning alert at `alert_time` (08:45 IST) on NSE trading days, retried up to 3 times; settles paper trades every 10 minutes; sends the 15:40 scorecard |

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

A second LaunchAgent runs `--settle` every 10 minutes. It does nothing
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

Each active lesson takes `learn_penalty` (10) points off new setups with
that trait. The total is capped at `learn_max_penalty` (20). The alert shows
each deduction and its evidence (`📚 −10: …`). Lessons only subtract points
and never add them. The dashboard's **Lessons learned** panel lists every
trait's record.

Patterns in losing trades, such as stops hit before T1 or failed breakouts,
appear as suggestions. They are not applied automatically, because they
change the trade rules and need a backtest first. Set `"learning": false` to
record without adjusting scores.

Small samples mislead. A few trades a day means weeks before any trait
reaches 8 trades, and a lesson is a ranking adjustment, not proof.

Simulated fills are optimistic in one way and pessimistic in another. Real
fills can be worse than a candle's close. The stop-first rule, however,
under-counts some winners.

## Limits

- Signals come from delayed end-of-day data. Opening gaps, news and results
  can invalidate a setup before 09:15. The alert flags unusual volume and
  large moves, but it does not read news or the results calendar.
- The built-in NSE holiday calendar covers 2026. Before January 2027, add the
  2027 holidays to a calendar file and set `"calendar"` in the config.
  Otherwise the job reports that the calendar does not cover the date.
- Scores are a rule-based ranking, not a probability of profit. Nothing has
  been backtested as a portfolio strategy. Paper-trade the alerts before
  risking money.
