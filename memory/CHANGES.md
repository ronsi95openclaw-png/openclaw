# CHANGES

Append-only change log. One entry per change. Categories:
- **A** — Routine / safe / auto-approved (read-only checks, log fetches, doc updates)
- **B** — Requires Ronnie's approval before applying
- **C** — Escalation / incident / something broke

Entry format:
```
## [YYYY-MM-DD HH:MM] — <Category> — <Short title>
**Trigger:** what made this necessary
**Action:** what was done
**Result:** what changed / measured outcome
**Files touched:** list
**Git tag:** optional ref
**Approved by:** Ronnie | Auto
**Status:** APPLIED | REVERTED | DEFERRED
---
```

---

## [2026-05-30 18:30] — A — Bootstrap memory/ directory
**Trigger:** next_session workflow assumed memory/ existed at the Claude-openclaw root; it did not
**Action:** Created memory/ with scaffolding for CHANGES.md, DECISIONS.md, SESSION_HANDOFF.md, ACTIVE_TASKS.md
**Result:** Workflow's log targets now exist
**Files touched:** memory/ (new dir + 4 files)
**Approved by:** Ronnie ("go for it")
**Status:** APPLIED
---

## [2026-05-31 06:55] — C — HANDS-OFF on vault; STEP 7 vault commit + push aborted
**Trigger:** Mid-step UserPromptSubmit notice: "Obsidian vault reorganization in progress — another Claude Code session is actively reorganizing the vault. Do not write/edit/rm/commit anything under Documents\Obsidian Vault\ for the rest of this work."
**Action:**
- Aborted in-flight `rm` of three un-prefixed duplicates in vault and the planned re-sync
- Reverted un-committed change to `infra/sync_to_vault.bat` (had patched OpenClaw section to use `OPENCLAW_` prefix matching prior vault commit 89b8ee2's disambiguation pattern). Reason for revert: the other session may choose a different convention; don't pre-commit to one.
- Did NOT touch the vault's git state; did NOT delete the duplicates I synced earlier this session
**Result:**
- Bot code: clean (sync_to_vault.bat back to f27a4aa version; this-session work in 3 commits stays)
- Vault working tree: still has the duplicates from the earlier (pre-notice) `sync_to_vault.bat` run — `20 - OpenClaw/Memory/{ACTIVE_TASKS,DECISIONS,SESSION_HANDOFF}.md` (untracked) alongside the prior commit's `OPENCLAW_*.md`. The other reorg session will see them and decide.
- STEP 7B vault commit + push: NOT done.
**Files touched:** infra/sync_to_vault.bat (reverted to HEAD)
**Approved by:** Ronnie (hands-off directive)
**Status:** APPLIED (revert + halt); vault sync DEFERRED until all-clear
**Follow-up:** After the all-clear, decide naming convention with the reorg session's owner, then re-patch sync_to_vault.bat and re-sync.
---

## [2026-05-31 06:35] — A — LiquiditySweep paper-watch infra built + scheduled
**Trigger:** post_backtest STEP 4 — operationalize the 14-day paper-watch decision
**Action:**
- Wrote `infra/paper_watch_liquiditysweep.py` (corrected against real codebase: uses `LiquiditySweepStrategy()` class, v1 API endpoint, lowercase `1d` timeframe, `dataclasses.asdict()` for Signal serialization, `datetime.now(timezone.utc)` instead of deprecated `utcnow()`, self-bootstraps sys.path so it works from any cwd)
- Wrote `infra/paper_watch_run.bat` wrapper (sets cwd to repo root, prefers trash_hauling_bot venv python, falls back to system python)
- Wrote `memory/strategy/paper-watch-liquiditysweep.md` (tracking note, day-7 + day-14 dates, success criteria, disable instructions)
- Installed Windows scheduled task `ClawBot-LiquiditySweep-Watch`: DAILY @ 09:00 local, runs paper_watch_run.bat
- Smoke-tested: 4 entries written to `data/paper_watch/liquidity_sweep.jsonl`; caught a live MEDIUM-confidence BUY on XRP_USDT
**Result:** Daily signal observation begins. No executor changes. No trading impact.
**Files touched:** infra/paper_watch_liquiditysweep.py (new), infra/paper_watch_run.bat (new), memory/strategy/paper-watch-liquiditysweep.md (new), memory/ACTIVE_TASKS.md (M), memory/SESSION_HANDOFF.md (M), data/paper_watch/liquidity_sweep.jsonl (new, gitignored)
**Cadence chosen:** DAILY (not 4hr as runbook proposed) — 1d candles only refresh once per day, so 4hr would produce 4× duplicate signals
**Approved by:** Ronnie ("Script + daily Windows scheduled task")
**Status:** APPLIED
---

## [2026-05-31 00:15] — A — Two atomic local commits made (NOT pushed)
**Trigger:** post_backtest workflow STEP 2
**Action:**
- Commit `83f6160` — fix(gitignore): broaden .env to .env*
- Commit `f27a4aa` — feat(backtest): 5-strategy comparison + regime test + memory scaffold (9 files, +1175 lines, includes workflows/post_backtest.md tracked as a session artifact)
- Identity: `clawbot@openclaw.local / ClawBot` (repo-local, not global git config)
- Branch: `feature/telegram-notifications`
- Push: NOT performed (awaits explicit "yes push")
**Result:** Backtest session work is locally durable. .env-family files confirmed gitignored. No runtime state staged.
**Files touched:** (see commits)
**Approved by:** Ronnie (workflow STEP 2 authorization)
**Status:** APPLIED — LOCAL ONLY
---

## [2026-05-31 00:10] — C — Pre-commit sanity check caught .gitignore gap
**Trigger:** post_backtest workflow STEP 1A — `git check-ignore` showed `.env.new`, `.env.backup-20260530-131338`, `.env.old-20260530-134625` were NOT ignored. Only literal `.env` was. Any future `git add .` would leak credentials.
**Action:** Patched `.gitignore` line 1: `.env` → `.env*` (glob covers all variants).
**Result:** All four .env-family files now report IGNORED. `.venv/` still ignored independently (line 7). Committed atomically before the backtest feature commit.
**Files touched:** .gitignore
**Approved by:** Auto (Category C — security-critical pre-commit catch; safe pattern, no functional risk)
**Status:** APPLIED
---

## [2026-05-30 21:30] — A — Extended sync_to_vault.bat to cover Claude-openclaw memory/
**Trigger:** Bat file only synced trash_hauling_bot/memory; new Claude-openclaw root memory/ wouldn't reach the vault
**Action:** Added two `call :sync` lines — root memory/ → `20 - OpenClaw/Memory/`, and memory/strategy/ → `20 - OpenClaw/Memory/Strategy/`
**Result:** sync_to_vault.bat now mirrors all three memory locations
**Files touched:** infra/sync_to_vault.bat
**Approved by:** Auto (Category A — convention-matching extension; mkdir already handled by existing :sync helper)
**Status:** APPLIED
---

## [2026-05-30 21:25] — A — Strategy comparison run + decision documented
**Trigger:** Phase 4-6 of next_session workflow
**Action:**
- Built `infra/run_strategy_comparison.py` (5 strategies × 4 symbols + 4-quarter BTC regime test)
- Ran against existing `*_1d_1y.json` data; saved snapshot to `data/backtest/comparison_20260530-2119.json`
- Wrote decision note `memory/strategy/backtest-2026-05-30.md`
- Logged decision to `memory/DECISIONS.md`
**Result:**
- Phase 5D escalation triggered — no strategy hit 3/4 quarters
- LiquiditySweep selected as paper-watch candidate (best PnL, strongest per-symbol signal, but 1/4 regime score)
- Executor NOT touched. DEMO mode preserved.
**Files touched:** infra/run_strategy_comparison.py (new), memory/strategy/backtest-2026-05-30.md (new), memory/DECISIONS.md, data/backtest/comparison_20260530-2119.json (new)
**Approved by:** Ronnie (escalation option: "stay DEMO + paper-watch LiquiditySweep")
**Status:** APPLIED
---

## [2026-05-30 18:30] — A — Fixed Unicode crash in verify_cryptocom_auth
**Trigger:** Verifier crashed with UnicodeEncodeError on Windows cp1252 console before printing 401 diagnostic
**Action:** Added `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` at module load (guarded by hasattr for older Pythons)
**Result:** Verifier now prints the full diagnostic; still returns 401 because keys are stale
**Files touched:** infra/verify_cryptocom_auth.py
**Approved by:** Auto (Category A — bug fix on a tool we're about to depend on)
**Status:** APPLIED
---
