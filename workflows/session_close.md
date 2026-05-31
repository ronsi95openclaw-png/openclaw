# SESSION CLOSE + FIRST DAILY-ROUTINE RUN
## Claude Code Prompt | Ronsi95 AI OS | 2026-05-31 -> next session
## Saved as: workflows/session_close.md

> **HOW TO USE:**
> Two parts. PART A wraps the session that just unblocked auth (calendar +
> push the 3 docs commits). PART B is tomorrow's first real daily-routine run.
> Run PART A now, PART B tomorrow morning (or run both back-to-back if it's
> still today).

---

## CONTEXT — WHERE WE ARE

Auth blocker (stuck since 2026-05-30) is SOLVED. Final state:
- **Auth:** OK — 200 OK via v2/private/get-account-summary
- **Diagnosis:** keys provisioned for v2 surface; v1 was incompatible (401 -> 400/ERR_INTERNAL -> 200)
- **Balance:** STARTING_BALANCE_USD updated 96.00 -> 96.39
- **Code:** trading/exchange.py + executor.py migrated v1 -> v2; commit 70cb112 PUSHED
- **Push:** feature branch + snapshot branch both at 70cb112 on origin
  - Snapshot: feature/telegram-notifications-snapshot-20260531
- **Routine:** memory/DAILY_ROUTINE.md adapted from v2.1; commit f297ab0 LOCAL
- **Handoff:** commits d1c0149, 0dce709 LOCAL

**Bot repo state:**
- origin HEAD: 70cb112 (pushed)
- local HEAD: 0dce709 (3 docs commits ahead of origin: f297ab0, d1c0149, 0dce709)

**Two loose ends:**
1. Phase 4 calendar reminders were printed but not added to phone (manual)
2. 3 trailing docs commits are local-only (pure docs, no urgency, but shouldn't sit a week)

**Open blockers (logged in ACTIVE_TASKS, intentionally deferred):**
- Wire any strategy into executor (Day-14 paper-watch decision gates this)
- Flip TRADING_MODE to LIVE
- Test private/create-order on v2 (ACTIVE_TASKS #1 — gates any LIVE flip)
- Patch sync_to_vault.bat OPENCLAW_ prefix (ACTIVE_TASKS #6)

---

## ABSOLUTE RULES

1. Load contracts first (Ruflo + vault CLAUDE.md)
2. Never read `.env` VALUES
3. Never wire a strategy / flip TRADING_MODE / test create-order this session
4. Never push without explicit "yes push" (the 3 docs commits need confirmation)
5. Never use `git add -A`
6. Never touch the vault outside sync_to_vault.bat
7. Never touch Bucket C files (ai_core/skills/*, .obsidian/graph.json)
8. All risk-parameter / risk.py changes require Category C escalation

---

(Full PART A and PART B runbook body — preserved as session artifact alongside
next_session.md, post_backtest.md, vault_resume.md, post_vault_next.md. The five
runbooks form the chain: backtest -> commit -> vault resume -> auth fix + push
+ routine -> close + first real run.)

---

*Session Close + First Daily Run v1.0 | Ronsi95 AI OS | 2026-05-31*
