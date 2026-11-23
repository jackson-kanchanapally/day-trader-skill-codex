# Gold lab monthly self-improvement protocol

For any AI: pi, Codex, Claude, Gemini, Aider or a chat model through an API.

You are improving the gold strategy lab in this repository
(`india/gold_lab.py`, documented in `docs/GOLD_LAB.md`). The lab
paper-trades many gold strategies forward and promotes a champion by fixed
rules. Your job is to propose new **challengers**, not to pick winners. The
data decides the winners, later.

## Read first

1. `docs/GOLD_LAB.md`: the architecture and the rules.
2. `docs/GOLD_LAB_JOURNAL.md`: what earlier runs tried and why. Do not
   repeat a rejected idea without a new reason.
3. The brief, `var/gold_lab_brief.md` (included in your prompt when you
   cannot read files). It holds the latest review, made from the real
   ledger in the user's checkout: forward results, replay disagreement,
   blocked challengers and why, and whether the ledger check passed. If the
   ledger check failed, add no strategies: write what you found in the
   journal and stop. You work in an isolated copy that has no ledger, so do
   not look for `var/state`.

## What you may do

The runner (`india/gold_lab_improve.py`) checks your work and rejects
anything outside these limits, whichever AI you are.

- Add **at most three** new strategies in **at most two** new files named
  `india/gold_lab_challengers/c_<yyyy>_<mm>_<topic>.py`. Each file defines
  `strategies(lab)` (see `india/gold_lab_challengers/__init__.py`) and
  imports nothing from this repository. Each strategy needs:
  - an economic reason in one sentence: why gold or the rupee should
    behave this way, for example carry, flows, risk aversion, real rates
    or seasonality of Indian demand;
  - `posthoc=True`, `added='<today, YYYY-MM-DD>'` and a short `note`;
  - only free data that `india.gold` or `india.gold_news` already fetch, or
    one new free source with a documented fallback when it is down;
  - few parameters. Prefer conventional values (20, 50, 100, 200 sessions)
    over tuned ones. Each value in a grid counts as one strategy.
- Append a dated entry to `docs/GOLD_LAB_JOURNAL.md`: your hypothesis, the
  economic reason, and what result would make you drop the idea. Do not
  edit earlier entries.

Bugs you find in the core code, and ideas that need core changes, go in the
journal entry as proposals for the user. Do not edit any other file.

## What you must not do

- Edit any existing file other than appending to the journal. That covers
  `india/gold_lab.py` (PROMOTION, the engine, the costs), existing plugins,
  tests and docs. Propose such changes in the journal for the user to
  decide.
- Edit, delete or rewrite `var/state/gold_lab/decisions.jsonl`,
  `champion.json` or any review file.
- Promote, demote or pick a champion by hand.
- Tune a strategy's parameters on the 2011+ test years until it looks good.
  If you try variants, report every variant you tried in the journal, not
  only the best one.
- Merge, push, commit or touch the Docker stack. The runner tests and
  commits your work on its own branch; the user reviews and merges.

## Steps

1. Read the material above. Summarise in the journal: champion, forward
   leaderboard, what blocked each near-eligible challenger, any replay
   disagreement.
2. Diagnose. Where and when do the current strategies lose (years, high
   volatility, dollar rallies, rate shocks)? Use `--research --json` and
   the review in the brief.
3. Propose up to two challengers that address a diagnosed weakness. Write
   the hypothesis before you run anything.
4. Implement them. If you can run commands, run
   `python3 -m unittest tests.test_gold_lab`: the look-ahead test covers
   every registered strategy automatically.
5. Write the journal entry. The runner then runs the full suite and the
   research, keeps a challenger only if its 2011+ Sharpe reaches the median
   of the existing strategies, and appends every new strategy's result to
   the journal whatever it shows. A bad backtest drops the idea; a good one
   is not proof.
6. End with a short report: what you added and why, and anything that needs
   the user's decision.
