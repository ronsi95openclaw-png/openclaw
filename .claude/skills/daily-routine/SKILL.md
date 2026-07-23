---
name: daily-routine
description: Run OpenClaw's daily operating routine — load session continuity docs, check bot/exchange health, and summarize what changed overnight. Use when the user says "run the daily routine", "morning check", or starts a new session on this repo and wants a status catch-up.
---

This wraps `memory/DAILY_ROUTINE.md` (the canonical, hand-maintained routine) so it can be invoked as a skill instead of pasted by hand each morning — this closes out `memory/ACTIVE_TASKS.md` item #7, which had been waiting on a Claude Code skill for this repo.

Steps:

1. **Load continuity docs** — read `memory/SESSION_HANDOFF.md`, `memory/ACTIVE_TASKS.md`, and the tail of `memory/CHANGES.md`. State out loud: the last commit SHA and what's pending push, any HIGH-priority ACTIVE_TASKS items, and whether their due dates have passed.
2. **Health check** — run `python -m infra.verify_cryptocom_auth` to confirm exchange auth is live; check whether Ollama is reachable (`curl -sf http://localhost:11434/api/tags`) and note that the bot falls back to the Claude API if not; if a scheduler/watchdog process is expected, check it's running rather than assuming.
3. **Pull last 24h of trade activity** — read `data/logs/trades.log` (if present) and summarize trade decisions from the last 24 hours by count and outcome.
4. **Cross-check DECISIONS.md** — if `memory/DECISIONS.md` has a STANDING decision with a "revisit" date that has passed (e.g. the LiquiditySweep paper-watch review dates), surface it explicitly rather than letting it silently lapse.
5. **Summarize** — give a short status: what's healthy, what's blocked, what needs the user's decision today. Don't take any trading-affecting action (mode flips, executor wiring) without explicit confirmation — see `CLAUDE.md`'s "no broad refactors without asking first" rule, which extends to no live-trading changes without asking either.

If `memory/DAILY_ROUTINE.md` and this skill ever diverge, treat `memory/DAILY_ROUTINE.md` as source of truth and update this file to match — it documents the Windows-specific version the user actually runs.
