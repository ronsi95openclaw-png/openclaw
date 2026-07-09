# Vibe-Trading Bot — Architecture Audit (2026-07-08)

Read-only audit of `vibe-trading/` (TJR/ICT Lucid-25K bot, paper mode via Tradovate demo
through TraderPost). No logic files were modified. Strategy/risk/mandate files are locked
from edits by policy (see "Locked files" at the bottom).

---

## 1. Pipeline (as actually wired, verified in code)

```
Hermes cron (vibe_paper_scan.py, %LOCALAPPDATA%\hermes\scripts\, ~every 15 min Mon-Fri)
  │ 1. fetch_es_data.py            yfinance ES=F 5m bars -> backtest/data/ES_5M_live.csv
  │ 2. paper_ledger.py resolve     settle open DISK paper positions vs latest bar
  │                                -> bot/logs/trade_journal.jsonl (closed trades)
  │ 3. runner.py --csv ... --once  one full evaluation cycle:
  │
  └─▶ runner.run_cycle:
        killswitch check ─▶ mandate re-read ─▶ data_loader.build_bars_by_tf (ET, lowercase OHLCV)
        ─▶ in-memory paper fill sim ─▶ account_state build+validate
        ─▶ strategy.generate_signal (pure; kill-zone/HTF-bias/sweep/FVG-OB/OTE/1m-MSB)
        ─▶ _sanitize_signal (locked 9-key schema allowlist)
        ─▶ risk_guard.RiskGuard.check (mandate numbers, fail-closed to flat)
        ─▶ traderpost.TraderPostClient.send (validate-before-send; DRY_RUN default)
        ─▶ audit JSONL (decisions.jsonl / orders.jsonl / runner.log / sessions/)
        ─▶ on approve: paper_ledger.open_position() persists position to disk
                       + Telegram alert to Hermes HQ VibeTrade floor
```

Module reality vs docs: `bot/ARCHITECTURE.md` / `bot/README.md` list `mandate.py`,
`account.py`, `audit.py`, `killswitch.py` as modules. **None exist.** runner.py ships
contract-conformant fail-closed fallbacks for all four, and those fallbacks ARE the
production path (audit logging, kill-switch check, account state, mandate view). A real
`MandateView` exists inside `risk_guard.py`, but runner only looks for it in a `mandate`
module, so the fallback view is used. This is safe (fallbacks fail closed) but the docs
overstate what exists, and the boot audit event `paper_ledger_not_risk_validating` fires
every run by design.

Two parallel paper ledgers exist:
- **In-memory `_PaperLedger`** (runner.py) — per-process; drives risk_guard breakers
  within a run. Discarded at exit (irrelevant under `--once` cron usage).
- **Disk ledger** (`paper_ledger.py`) — `bot/logs/paper_positions.json` +
  `trade_journal.jsonl`; bridges `--once` runs; resolved by the Hermes cron step 1b.
  As of this audit, neither file exists in `bot/logs/` — no paper entry has been
  approved+persisted yet.

## 2. lucid_mandate.json (source of truth, re-read every cycle)

$25,000 account · EOD-drawdown type · max_loss_limit $1,500 · daily_loss_limit null
(soft 80% gate = -$1,200 comes from BotConfig.daily_gate_pct) · consistency 50% (eval) ·
no overnight holds, close EOD · instruments ES/MES/NQ/MNQ · max_position_size 2 ·
daily_trade_cap 10 · payout_min_profitable_days 5 · mode "paper".
No limit is hardcoded on the order path (informational scripts do hardcode — see findings).

## 3. Kill switch

Trigger = existence of the literal file `vibe-trading/KILL_SWITCH` (path from
`mandate["kill_switch"]["file"]`, resolved vs `vibe-trading/`). The present
`KILL_SWITCH_DISABLED` marker is **not** a trigger. Checked at three layers: runner
step 1, risk_guard check 1, traderpost validate-before-send. `auto_flatten_on_kill: true`
→ runner `_halt()` → `_flatten()` sends a reduce-only market-close through
traderpost — **but see finding B1: the real traderpost.py rejects that flatten order.**

## 4. Live gate status

Live POST requires `HERMES_BOT_LIVE=1` (env) AND `config.go_live=true` (config file) —
both verified OFF. A **third** gate was added 2026-07-08: `eval_gate.passed_evaluation()`
(≥25 closed paper trades, ≥5 profitable days, consistency + max-loss clean, no
risk-guard-bypass evidence in audit logs) via `config.is_live_enabled()` — **but nothing
on the order path calls it (finding B2)**.

## 5. Go-live checklist status (from bot/README.md, assessed against code/logs)

| Item | Status |
|---|---|
| Paper proven (multi-session, decisions/orders behaving) | ❌ Outstanding — decisions.jsonl accumulating, but `trade_journal.jsonl` is empty/absent → eval_gate cannot pass (0 of 25 trades) |
| Mandate authoritative & current | ✅ File matches Lucid 25K rules; read at runtime |
| Secrets in os.environ only | ✅ No webhook/secret literals found in `bot/` (note: `bot/.env` exists — verify it holds no TraderPost secret before live) |
| No secret in any log | ✅ Redaction in both real traderpost fallback audit + runner fallback audit; selftest asserts it |
| Kill switch verified (create/remove KILL_SWITCH end-to-end) | ❌ Not evidenced in logs; **blocked by B1 (flatten rejected by real traderpost)** |
| risk_guard precedes every order | ✅ In code (runner step 6; traderpost re-validates) |
| Validate-before-send confirmed | ⚠️ Works for entries; rejects flattens (B1) |
| Circuit breakers tested (daily gate / max loss / consec / NaN / EOD) | ⚠️ Exercised by in-memory paper sim + selftest; not yet observed live-paper |
| External input sanitized | ✅ `_sanitize_signal` allowlist + traderpost `_sanitize_text` |
| Audit confirmed (every decision) | ✅ decisions.jsonl / sessions/ populated |
| security-review skill run on risk_guard/traderpost/runner | ❌ No evidence found |

**Verdict: NOT ready for live.** Blockers: B1, B2, empty trade journal (eval gate), kill-switch end-to-end test.

## 6. Findings (flagged, NOT fixed — logic lockout policy)

### High
- **B1 — Live/paper flatten orders are rejected by the real TraderPost client.**
  `runner.py:1115-1126` builds the kill-switch/EOD/circuit-breaker flatten as a
  reduce-only market close with `stop=None, tp1=None, tp2=None`. Only the *fallback*
  client (runner.py:831) understands `reduce_only`/`order_type=="market"`. The real
  `traderpost.py:_validate` (lines ~286-289 `missing_stop`, ~311-315 `missing_tp1/tp2`)
  has no flatten exemption → every flatten is `result="rejected"`. Paper impact: cosmetic
  (ledger closes locally). Live impact: **auto_flatten_on_kill would never reach the
  broker; a position could be held through a kill-switch or past EOD.** Must be fixed in
  `traderpost.py` (locked — needs explicit approval) before any go-live.
- **B2 — The new third live gate (eval_gate) is not enforced on the order path.**
  `config.is_live_enabled()` (config.py:185-215) wires in `eval_gate.passed_evaluation`,
  but no caller exists outside config.py's own smoke test. The actual POST gate is
  `traderpost.TraderPostClient._live_requested()` (traderpost.py:255-257) and
  runner.py:1280 — both still the two-boolean check. Flipping the two booleans today
  would arm live sends with zero paper track record, exactly what eval_gate was built to
  prevent. (traderpost.py is locked; flagged only.)

### Medium
- **B3 — `eval_gate.py` can raise despite its "never raises" contract.**
  `_check_max_loss` (eval_gate.py:173-177) sorts by `_parse_ts(...) or
  datetime.min.replace(tzinfo=None)`. Journal `ts` values are tz-aware ISO strings
  (trade_journal.py:53) while the fallback is naive → mixed aware/naive comparison in
  `sorted()` raises `TypeError` (same class of issue in
  `_check_kill_switch_risk_breach`'s `halt_ts - breach_ts`, lines 265-276, if sources
  mix). NinjaTrader-format bar timestamps ("20260707 100000") from
  `paper_ledger.resolve_from_csv` also parse to None. `passed_evaluation` only catches
  `OSError`/JSON errors around loading — check exceptions escape. Contained today only
  because `config.is_live_enabled` wraps it in a broad try/except (fail-closed).
- **B4 — Zombie paper positions at size 1 in `paper_ledger.py`.**
  `resolve_bar` TP1 branch (paper_ledger.py:161-182): `half = max(1, size_rem // 2)` — a
  1-contract position (the default) books TP1 with `size_remaining = 0` and the position
  stays "open" with 0 contracts; a later TP2/SL logs a $0, 0-contract journal row. The
  runner's in-memory sim handles this case (`half >= size` → full close,
  runner.py:610-614); the disk ledger does not. Inflates trade count / distorts journal
  rows the eval gate will read.
- **B5 — TP1→TP2 sequencing differs between the two paper sims.** Disk ledger requires
  `tp1_filled` before TP2 can fill (paper_ledger.py:112-122); the runner in-memory sim
  closes the full position at TP2 even if TP1 never filled (runner.py:601-605). Same
  signal, different paper P&L depending on which sim books it.
- **B6 — 1-minute timeframe is synthetic.** The live feed is 5-minute bars
  (fetch_es_data.py → ES_5M_live.csv), but `data_loader._build_from_csv` labels the raw
  frame `"1m"` (data_loader.py:91-96). The strategy's 1m MSB confirmation therefore runs
  on 5m bars. Consistent with the backtests, but the ARCHITECTURE §3 "1-minute MSB"
  wording overstates granularity. (When `build_bars_by_tf(None)` live-fetch is used it
  pulls true 1m bars — so CSV-cron and live-fetch paths confirm MSB on different
  granularities.)

### Low / informational
- **B7 — Dead constructor params.** `TraderPostClient.__init__` accepts `mandate_view` and
  `kill_switch_check` (traderpost.py:241-244) and ignores both; runner detects and passes
  them (runner.py:1349-1359) for nothing. Harmless (client re-reads mandate + re-checks
  kill switch itself) but misleading.
- **B8 — Docs list four modules that don't exist** (mandate/account/audit/killswitch —
  see §1). README/ARCHITECTURE say "the doc wins," so either land the modules or amend
  the doc (locked-contract change — user call).
- **B9 — Hardcoded limits + wrong flatten time in informational scripts.**
  `trade_morning_brief.py:22-26` hardcodes 1500/10 and says flatten "4 PM ET";
  `vibe_paper_scan.py:115` (Hermes side) repeats it. Actual EOD flatten is 15:55 ET.
  Informational only — no order-path impact.
- **B10 — Stale docstring.** `paper_ledger.py:5-6` says "Each vibe_paper_scan.py run
  calls auto_resolve()" — the scan actually shells out to `paper_ledger.py resolve`
  (CLI), and the file lives in Hermes scripts, not this repo.
- **B11 — `signal_watcher.py` legacy schema** (`symbol/price/take_profit/stop_loss`) is
  alert-only and never feeds the runner — correct per contract §2, just be aware it is a
  different schema than the locked Signal dict.
- **B12 — `data_loader.py` NinjaTrader branch** `tz_localize(ET)` (line 83) will raise on
  DST-ambiguous timestamps (falls back to `None` via the broad except → treated as
  "no bars"); fail-closed, so acceptable.

## 7. Locked / off-limits files (not edited, and why)

| File | Why locked |
|---|---|
| `bot/strategy.py` | Signal generation — explicit auto-improve lockout after past incident |
| `bot/risk_guard.py` | Risk limits / circuit breakers — same lockout |
| `lucid_mandate.json` | Source of truth for all trading limits |
| `KILL_SWITCH_DISABLED` | Kill-switch marker — untouchable per task rules |
| `bot/traderpost.py` | Order routing / validate-before-send (trade path) |
| `bot/runner.py` | Orchestrates the order path (sizing, flatten, gating) |
| `bot/config.py`, `bot/eval_gate.py` | Live-gate logic (paper/live arming) |
| `bot/paper_ledger.py`, `bot/trade_journal.py` | Feed eval_gate's go-live decision |
| `bot/data_loader.py`, `bot/backtest.py` | Bar pipeline / validated engine — affects signals |
| `bot/logs/*` | Live-generated logs — never edit/clean |
| `bot/ARCHITECTURE.md`, `bot/README.md` | Declared LOCKED CONTRACT ("doc wins") |

No existing file was modified. This document is the only artifact added, on branch
`audit-fable-vibetrading-wt-2026-07-08` in the isolated worktree
`C:\Users\ronsi95openclaw\Claude-openclaw-vibetrading-audit`.

---

## Addendum — 2026-07-09

**A crash bug this audit missed, found and fixed the same day it was written
(commit `75a42dd`, a few hours after this document was published):**

`config.py` defined its own local `StrategyConfig` dataclass instead of importing
the one in `strategy.py`. It had drifted out of sync — missing `ob_bars`,
`stop_ticks`, `max_stop_points`, `max_minutes_in_kz` entirely, and stale
`kill_zones`/`sweep_bars` defaults. Since the `max_minutes_in_kz` field was added
to the real `StrategyConfig` (commit `ce13678`, predating this audit), **every
15-minute cron evaluation had been crashing inside `generate_signal()`** with
`'StrategyConfig' object has no attribute 'max_minutes_in_kz'`, fail-closed to
`skip`/`no_setup` every single time. In other words: for an unknown window before
the fix, the strategy was **never actually capable of evaluating a real setup at
all** — every "no_setup" decision in that window was a swallowed crash, not the
strategy legitimately finding nothing.

This is a materially different (and arguably more serious) issue than anything in
§6 above (B1–B12), and this audit did not catch it — §1's description of the
pipeline as functioning end-to-end was itself inaccurate for that window. Fixed by
making `strategy.py` the single source of truth for `StrategyConfig`; verified via
`runner.py --once` reaching `skip`/`no_setup` with no `strategy_error`, and
`decisions.jsonl` confirms clean decisions since the fix landed.

**Also corrected 2026-07-09** (full-ecosystem fix pass, separate from this crash
fix): `lucid_mandate.json`'s `max_loss_limit` was wrong ($1,500 instead of the
real $1,000), `profit_target` ($1,250) was missing entirely, the account name was
corrected to `LucidFlex_25K`, and the five conflicting internal flatten times
scattered across this codebase and the Hermes-side scripts/skills/cron prompts
(15:45/15:55/16:00/16:30/16:45 ET) were reconciled to two authoritative values:
internal flatten trigger 16:30 ET, Lucid's own hard EOD cutoff 16:45 ET. B1
(kill-switch/EOD flatten rejected by `traderpost.py`) and B2 (`eval_gate` not
wired into the live-send path) from §6 above were also fixed in this same pass —
see `bot/traderpost.py` and `bot/runner.py` for the current implementation; §5's
"Verdict: NOT ready for live" still holds, now solely because the trade journal
has 0 of the 25 trades `eval_gate` requires, not because of B1/B2.
