# CLAUDE.md — OpenClaw (ClawBot)

Guidance for Claude Code in this repo. Keep it short and current.

## Project
Local-LLM crypto trading + assistant bot. Telegram-controlled, Flask web dashboard,
APScheduler loop. Local Ollama brain with Claude API fallback. Remote: `openclaw.git`.

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

## Skills (`.claude/skills/`)
HaulYeah skills the hermes bot can invoke (each wraps real code in `trash_hauling_bot/`):
- `haulyeah-lead-digest` — compact, length-bounded new-leads alert (fixes the truncating cron).
- `haulyeah-outreach` — draft a DFW outreach message + container pitch (draft only, never sends).
- `haulyeah-meta-ads` — generate FB/IG ad copy + carousel; live ads are owner-gated.
- `haulyeah-marketplace-scan` — scan FB Marketplace for hauling jobs (needs one-time human FB login).

## Hermes runtime (`.claude/settings.json`, `.mcp.json`)
The hermes Telegram bot (`Ronsi95.hermes.bot`) is a self-hosted Claude Code agent rooted at
this repo. Two pinned files keep it from the "Response remained truncated after 3 continuation
attempts" failure that hit *every* message (not just the lead cron):
- `.claude/settings.json` — `CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192` so a turn finishes instead of
  stopping at `max_tokens` and exhausting the harness's continuation retries; `MAX_THINKING_TOKENS=2048`
  so extended thinking can't eat the whole output budget (why even "Hi" was truncating); and
  `enableAllProjectMcpServers=false` so project MCP servers don't auto-load.
- DEPLOY: the bot only picks this up after the host pulls the branch/merge **and restarts** the
  process. A `max_tokens`/thinking env set on the host launch command overrides this file.
- `.mcp.json` — pinned to `{}` (no project MCP servers). The HaulYeah skills call local Python,
  not MCP, so the bot needs none day-to-day.
- NOTE: this only controls *project*-level config. Connectors attached at the **account** level
  (Meta Ads, Higgsfield, crypto, Supabase, Gmail, Drive, GitHub) must be trimmed on the host —
  the repo can't detach them. Fewer connectors = leaner context = less truncation risk.

## Rules
- Never hardcode secrets — use `.env`.
- **Archive, don't delete** (`_Archive/`); retired clutter lives there and is git-ignored.
- Log trade decisions; the LLM confirms trade signals before execution.
- No broad refactors without asking first.
