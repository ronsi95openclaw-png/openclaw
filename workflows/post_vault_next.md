# POST-VAULT-RESUME — NEXT STEPS (KEY ALREADY REFRESHED)
## Claude Code Prompt | Ronsi95 AI OS | 2026-05-31
## v1.1 — supersedes v1.0; saved as workflows/post_vault_next.md

> **HOW TO USE:**
> Ronnie has ALREADY refreshed the Crypto.com API keys in the browser and
> pasted them into .env. This prompt skips the "wait for refresh" step and
> goes straight to verifying + handling the result.
>
> 4 phases, ~21 minutes total.

---

## CONTEXT

Vault resume completed earlier. Final state:
- **Vault:** `5d1d8a7` pushed to origin/main, 13/13 folders, contract files present
- **Bot repo:** 5 local commits on `feature/telegram-notifications`, NOT pushed:
  ```
  2d2124e  docs(memory): vault all-clear — STEP 7B resumed
  dc03f9c  docs(memory): log vault hands-off + STEP 7 deferred
  4444841  feat(paper-watch): LiquiditySweep daily signal logger
  f27a4aa  feat(backtest): 5-strategy comparison + regime test
  83f6160  fix(gitignore): broaden .env to .env*
  ```
- **Live infra:** `ClawBot-LiquiditySweep-Watch` daily 09:00, smoke-test caught XRP BUY/MEDIUM

**NEW STATUS:** Ronnie refreshed Crypto.com API keys in browser and saved to `.env`.
Phase ordering rebuilt: auth FIRST (unblock everything), then push, then routine.

Three things in sequence:
1. **Phase 1** — Verify Crypto.com auth (FIRST PRIORITY)
2. **Phase 2** — Bot repo push decision
3. **Phase 3** — Build DAILY_ROUTINE.md adapted to real paths
4. **Phase 4** — Calendar reminders + final report

---

## ABSOLUTE RULES

1. Load Ruflo + vault contract first
2. Never read `.env` VALUES — only check presence/length
3. Never modify `trading/risk.py` or risk parameters in this session
4. Never flip `TRADING_MODE` to LIVE
5. Never push without explicit "yes push"
6. DAILY_ROUTINE.md must be ADAPTED to real paths, never pasted verbatim
7. If Crypto.com verifier returns 401 → retry once after 60s, then escalate
8. All commits this session are LOCAL only (push only on explicit gate)

---

(Full runbook body — preserved as session artifact. Phases:
- STEP 0: contracts + handoff
- PHASE 1 1A-1D: pre-flight key check, verifier, balance update, log
- PHASE 2 2A-2D: commit safety scan, optional snapshot, push gate, execute
- PHASE 3 3A-3F: path inventory, adaptation table, write adapted routine, commit
- PHASE 4 4A-4D: calendar reminders, SESSION_HANDOFF update, final report, final commit)

---

*Post-Vault-Resume Workflow (Key-Refreshed Edition) v1.1 | Ronsi95 AI OS | 2026-05-31*
*Built by Claude Opus 4 (planning) for Claude Code (execution)*
