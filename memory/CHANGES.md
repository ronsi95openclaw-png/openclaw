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
