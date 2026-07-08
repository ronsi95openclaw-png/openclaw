# ARCHITECTURE.md — OpenClaw / ClawBot

Living architecture reference. Complements `CLAUDE.md` (guidance/rules) and
`PROJECT-STATE.md` (a point-in-time HaulYeah/HaulYA'LL audit — not a general
architecture doc, kept separate on purpose). Update this file when a data
flow, security gate, or top-level module changes.

## Entry point

`python start.py` launches the Telegram bot (`content/receiver.py`) and the
Flask dashboard (`dashboard/app.py`) in one process. Requires a git-ignored
`.env` (Telegram, Crypto.com, Anthropic, Ollama config).

## Top-level modules

| Path | Role |
|---|---|
| `content/receiver.py` | Telegram bot: ~65 `CommandHandler` registrations (see below) + a free-text `MessageHandler` that routes to `ask_hybrid`. |
| `core/` | LLM brain (`brain.py`), conversation history (`conversation.py`), APScheduler jobs (`scheduler.py`), market data (`market.py`), knowledge base (`knowledge.py`). |
| `trading/` | Crypto.com connector (`exchange.py`), RSI+MACD strategy (`strategy.py`, `trading_strategy.py`), order execution (`executor.py`), backtesting (`backtest.py`), retry/backoff (`backoff.py`). |
| `agents/` | Background/on-demand workers: news filter, Sheets sync, auto-upgrade, code review, failure memory, CashClaw income pipeline (job scout -> human voice -> applier -> performance tracker -> self review), LifeOS check-ins, social/content publishing. |
| `dashboard/app.py` | Flask dashboard: 24 `@app.route` endpoints (see below) — status/portfolio/holdings pages, JSON APIs, an in-dashboard command console that re-invokes the same Telegram command logic. |
| `skills/` | Claude Code skills. `agent_team_orchestrator.py` (task lifecycle state machine, backed by `data/tasks.json`), `self_improving.py` / `second_brain.py` (memory), the `cashclaw-*` SKILL.md suite (Markdown playbooks for a freelance-income business-ops persona — see below), `cc_nano_banana` (image gen, currently mid-update in the working tree — untouched by this audit). |
| `security/` | `whitelist.py` (chat-id allowlist), `blocklist.py` (substring denylist for `/run`+`/py`), `audit.py` (append-only JSONL log), `rate_limiter.py` (implemented but **not currently wired into `receiver.py` or `app.py` — dead code**, see punch list). |
| `voice/` | Whisper transcription handler for voice messages. |
| `data/` | Runtime state — logs, JSON task/config files, knowledge base, code review reports. Git-ignored. |
| `trash_hauling_bot/`, `vibe-trading/` | Sibling bots sharing this repo (HaulYeah lead-gen, TJR/ICT trading pillar). Out of scope for this audit; left untouched. |

## Data flow: Telegram -> LLM brain -> trading -> dashboard

```
Telegram update
  -> receiver.py: is_authorized(chat_id)  [security/whitelist.py]
  -> (if /run or /py) is_blocked(command) + audit.log_command(...)  [security/blocklist.py, security/audit.py]
  -> cmd_* handler or free-text -> core.brain.ask_hybrid(prompt, history)
       -> classify_complexity() picks a route:
            simple:  Ollama -> OpenRouter -> Claude Haiku
            complex: Claude Haiku -> OpenRouter -> Ollama
       -> response cached 1h in data/response_cache.json; usage tracked in
          data/usage_stats.json
  -> core.conversation persists last 10 turns / 4h TTL per chat
  -> trading commands (/scan, /dca, /autotrade) call trading/strategy.py
     (RSI+MACD) against candles from trading/exchange.py (Crypto.com REST,
     HMAC-signed private endpoints), then trading/executor.py places orders
  -> core/scheduler.py (APScheduler) drives unattended jobs: reminders,
     daily autotrade scan+execute, Hermes graph build, LifeOS check-ins
  -> dashboard/app.py reads the same data/*.json files + calls the same
     trading/core modules to render status without going through Telegram;
     its /api/execute-command endpoint literally re-runs a subset of the
     same command logic (execute_dashboard_command) that receiver.py's
     cmd_* handlers use, so the two surfaces can drift if only one is
     patched (see punch list)
```

## Security gating model

1. **Chat allowlist** (`security/whitelist.py`): every `cmd_*` handler in
   `receiver.py` starts with `is_authorized(chat_id)`; unauthorized chats
   are silently ignored (no leak of bot existence). Empty `ALLOWED_CHAT_ID`
   env var means *deny all*, not allow all — fails closed.
2. **Shell/Python blocklist** (`security/blocklist.py`): `/run` and `/py`
   substring-match a small denylist (`rm -rf`, `shutdown`, fork bombs,
   etc.) before executing. Documented as defense-in-depth, not exhaustive.
3. **Audit log** (`security/audit.py`): every `/run`/`/py` invocation,
   allowed or blocked, is appended as one JSON line to
   `data/logs/audit.log`. Best-effort — write failures are swallowed so a
   full disk never blocks command execution.
4. **Rate limiter** (`security/rate_limiter.py`): implemented (per-chat
   sliding window + burst check) but **not called from anywhere** —
   currently dead code. See punch list.
5. **Dashboard token** (`dashboard/app.py` `_require_dashboard_auth`): if
   `DASHBOARD_TOKEN` is unset, the dashboard trusts all requests
   (localhost-only assumption); if set, every route except `/health`
   requires the token via header, query param, or cookie.
6. **Auto-upgrade whitelist** (`agents/auto_upgrade.py` `_ALLOWED_FILES`):
   the self-modifying agent may only touch a fixed set of files (core
   brain/scheduler/conversation, trading strategy/exchange/executor/
   backtest, a few agents, `receiver.py`, `dashboard/app.py`); `.env`,
   `security/`, `requirements.txt`, `data/` are hard-forbidden regardless
   of LLM suggestion.

## Verified counts (2026-07-08 audit)

- `content/receiver.py`: **~65** `CommandHandler` registrations (not 25 —
  that figure is stale in the separate, non-repo context file at
  `C:\Users\ronsi95openclaw\CLAUDE.md`; it does not appear anywhere inside
  this git repo, so nothing here needed a doc fix beyond flagging it).
- `dashboard/app.py`: **24** `@app.route` endpoints (not 14, same stale
  source as above).
- All 65 registered handler names have a matching `def cmd_*`; no
  orphaned or unregistered handlers found.
- `.venv/Scripts/python.exe -m compileall content core trading agents
  dashboard security skills voice` — all files compile clean.

## The `cashclaw-*` skill suite

`skills/cashclaw/skills/cashclaw-*/SKILL.md` and the flattened top-level
`skills/cashclaw-*/SKILL.md` copies are **byte-identical duplicates** (12
skills, verified by hash). They describe a separate, standalone Node.js
CLI product ("CashClaw" — mission lifecycle, Stripe invoicing, npm package
`cashclaw`) that lives entirely inside `skills/cashclaw/` (its own
`package.json`, CLI, dashboard, Playwright scrapers). This is a *distinct*
system from the in-repo Python `agents/*` CashClaw income pipeline
described in `CASHCLAW_BUILD.md` (Job Scout -> HumanVoice -> Applier ->
Performance Tracker -> Self Review, driven by `/scout`, `/apply_job`,
`/cashclaw` Telegram commands). The two share only a brand name — the
SKILL.md files correctly describe the Node CLI product and do not claim to
be the Python pipeline, so there is no factual error, but the naming
collision is worth flagging for whoever picks this up next (see punch
list).

## Known architectural risk points (not fixed by this audit — proposals only)

See the audit report / commit history on branch
`audit-fable-openclaw-2026-07-08` for the full punch list. Highlights:
`security/rate_limiter.py` is unused; `trading/executor.py` was
intentionally left untouched (live trade execution logic); the dashboard's
`/api/execute-command` duplicates command logic from `receiver.py` instead
of sharing it.
