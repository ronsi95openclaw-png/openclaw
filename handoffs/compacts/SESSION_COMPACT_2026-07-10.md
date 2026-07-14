# Session Compact — 2026-07-10

## What Was Built / Changed
- No code changes. Pure read-only investigation, continuing from 2026-07-09's
  "strategy viability decision" carry-forward item.
- Fetched fresh NQ/ES 1D and 1H data via `fetch_4yr_data.py` (worked around an
  off-by-one in its own `days_back=729` constant hitting Yahoo's 730-day 1H
  boundary, using a direct yfinance call at `days_back=725` — no script edits).

## Decisions Made
- Investigated NQ 1H's eval-FAIL flag (only profitable combo in the original
  `TJR_BACKTEST_SUMMARY.md`). Found the FAIL was real under both stale and
  corrected mandate numbers (drawdown breach), not a stale-mandate artifact.
- Investigated *why* it failed: traced the outlier "day" the summary implied
  and found no such day exists — the consistency violation is a
  running-cumulative-formula artifact (an ordinary win landing right after
  `running` gets reset near zero by a large loss), not a concentrated
  lottery-ticket trade. 12 of 16 trades pay an almost identical ~$635.50.
- Found a real, verifiable bug in `tjr_backtest_4yr.py`: its `stop_points`
  field only feeds TP1/TP2 target math — the actual stop is placed at the
  raw, uncapped sweep level. Live `strategy.py` hard-clamps every stop to
  `max_stop_points=6.0` (2.0-6.0 pt range), instrument-agnostic.
- Verified this bug across all four original combos (ES 1D/1H, NQ 1D/1H):
  proxy losses ran 7x-81x wider than the live cap allows. Worst: a single
  NQ 1D trade with a 1,015.50-pt stop (-$20,314.50) — this alone explains
  NQ 1D's implausible 168.8%-of-account original drawdown. No second bug.
- **Design-level finding, supersedes the per-combo framing entirely:**
  recomputing each combo's P&L/drawdown with corrected (capped) losses flips
  ES 1D and NQ 1D to solidly profitable and brings NQ 1D's drawdown into a
  plausible range — but re-running the 50% consistency check on the
  corrected sequences reveals violations in ALL FOUR combos that were
  completely absent in the original numbers. The wild proxy losses were
  masking a consistency-rule failure common to every combo. This is a
  property of how this TJR/kill-zone design wins (low frequency, near-
  uniform fixed-R payouts), not which instrument/timeframe it trades.
- Exception: ES 1H stayed net negative even after correction — a real,
  uncontaminated negative edge, separate bucket from the other three.

## What Was Learned / Patterns
- A backtest engine's implausible risk numbers (168.8% drawdown on a 25K
  account) are a strong signal to check the engine's own mechanics before
  trusting the strategy conclusion — the number itself was the tell.
- Masking effects are real and non-obvious: an inflated-loss bug didn't just
  make results look worse, it hid a *different, structural* failure mode
  (consistency rule) behind a more dramatic (and wrong) one (raw P&L/DD).
- Substitution recomputation (swap known-bad values, re-run existing
  arithmetic) is a legitimate, fast way to test "is this the same bug or a
  new one" without a full re-simulation — but its limits must be stated
  every time (a real re-run could change which trades stop out elsewhere).

## State Changes
| Pillar | Before | After |
|---|---|---|
| VibeTrader strategy | "NQ 1H is the only viable combo, investigate sizing" | "No combo is viable as specified; the mechanism is design-level (trade frequency/win-uniformity vs. the 50% consistency rule), not instrument-selection. ES 1H is a separate, uncontaminated negative-edge data point." |

## Files Touched
- None (tracked). New data files in `backtest/backtest_4yr/data/` (gitignored,
  confirmed via `git status`), left in place, harmless.
- `handoffs/MASTER_COMPACT.md` updated with this finding + the proxy-bug and
  mini/micro-cap tooling defects as standing carry-forward items.
- `handoffs/compacts/SESSION_COMPACT_2026-06-17.md` removed (folded into
  MASTER_COMPACT's Superseded section — pre-vibe-trading-pivot content,
  merge-threshold housekeeping at 3 compacts).

## Did NOT finish / Carry forward
- **The real open question now:** does a higher-trade-frequency or
  higher-win-variance version of this TJR strategy exist, or is the kill-zone
  setup as specified fundamentally incompatible with Lucid's consistency
  rule regardless of instrument/timeframe? This is a strategy-redesign
  conversation, not a parameter-tuning or instrument-selection one — needs
  Ronnie, not another read-only pass.
- `tjr_backtest_4yr.py`'s stop-cap bug: not fixed (logged as standing tooling
  defect in MASTER_COMPACT).
- Everything else from 2026-07-09's carry-forward list unchanged: tomorrow's
  vault-sync/cron verification, `channel_directory.json` generator hunt,
  ClawBot-Watchdog exit code, memory Phase A build approval, git push
  approval for 5 local commits across 2 repos (unchanged by tonight —
  this investigation made no commits).
