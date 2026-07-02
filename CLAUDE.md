# CLAUDE.md — OpenClaw (ClawBot)

Guidance for Claude Code in this repo. Keep it short and current.

## Project
Local-LLM crypto trading + assistant bot. Telegram-controlled, Flask web dashboard,
APScheduler loop. Local Ollama brain with Claude API fallback. Remote: `openclaw.git`.

## Pillars (current — see handoffs/MASTER_COMPACT.md for live status)
- **vibe-trading** — live trading pillar (TJR/ICT, Lucid 25K eval). Repo: `vibe-trading/`.
- **HaulYeah** — lead-gen bot, separate venv + `.env.haulyeah`. Repo: `trash_hauling_bot/`.
- **Hermes** — orchestrator/assistant, runs as its own process (`hermes/launch.py`, PID-tracked).
- **OpenAlice / CryptoBot** — retired 2026-07-02, superseded by vibe-trading. Do not surface as live in briefings; historical detail only in old `handoffs/HANDOFF_*.md`.

## Run
- **Entry:** `python start.py` — launches the Telegram bot + Flask dashboard in one process.
- Requires `.env` (git-ignored): Telegram, Crypto.com, Anthropic, Ollama config.

## Layout
- `content/` — Telegram receiver + command handlers
- `core/` — LLM brain, conversation history, scheduler, market data
- `trading/` — exchange (Crypto.com), strategy, executor, backtest
- `agents/` — news, sheets, auto-upgrade, code review, failure memory
- `dashboard/` — Flask dashboard
- `security/` — whitelist auth, audit log, blocklist
- `voice/` — Whisper transcription
- `data/` — runtime logs/reports (git-ignored)
- `trash_hauling_bot/` — separate HaulYeah lead-gen bot (its own venv + `.env.haulyeah`)

## Rules
- Never hardcode secrets — use `.env`.
- **Archive, don't delete** (`_Archive/`); retired clutter lives there and is git-ignored.
- Log trade decisions; the LLM confirms trade signals before execution.
- No broad refactors without asking first.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.
**Status:** Built and active. Hermes platform skill installed.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- hermes-self-review cron (01:00 nightly) runs `graphify update .` automatically.

## Cross-system sync (/sync-state)
*Trigger: "sync state", "sync everything", "check divergence"*

Reconciles this repo, Hermes, and the Obsidian vault. Surfaces divergence — never
silently trust one system's claim over another.

1. **Gather (read-only):** `git log --oneline -20`, `git status`; `handoffs/MASTER_COMPACT.md`
   + newest `handoffs/HANDOFF_*.md`; `memory/ACTIVE_TASKS.md`, `memory/CHANGES.md`;
   Hermes state via `hermes/logs/gateway.out` + `hermes/hermes.pid` (real process, not a
   Telegram topic — confirm it's actually running before trusting its claims).
2. **Verify before trusting:** cross-check anything Hermes or a handoff claims as "done"
   against actual commits/files. Known failure mode: stale handoffs (e.g. `HANDOFF_2026-06-17.md`
   predates MASTER_COMPACT by 2+ weeks) — MASTER_COMPACT.md is the authoritative source when
   they disagree.
3. **Vault sync:** direct write to `Documents\Obsidian Vault\` only via `infra/sync_to_vault.bat`
   (per `skills/ruflo/SKILL.md`) — never edit vault Bucket C files (`.obsidian/graph.json`,
   `ai_core/skills/*`).
4. **Sync back — gated:** git stage only, show `git diff --stat`, STOP — no commit/push without
   explicit "yes push". Never write to Hermes state directly; only through its own process.
5. **Summary:** divergences found, staged files awaiting "yes push", any blocker. If fully in
   sync: one-line confirmation only.
