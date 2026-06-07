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
- `trash_hauling_bot/` — separate HaulYeah lead-gen bot (its own venv; reads the consolidated root `.env` via `load_dotenv(../.env)` with namespaced `TRASH_BOT_*`/`FB_*` keys — no separate `.env.haulyeah`)

## Rules
- Never hardcode secrets — use `.env`.
- **Archive, don't delete** (`_Archive/`); retired clutter lives there and is git-ignored.
- Log trade decisions; the LLM confirms trade signals before execution.
- No broad refactors without asking first.
