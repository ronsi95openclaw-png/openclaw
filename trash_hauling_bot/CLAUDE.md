# CLAUDE.md

Guidance for Claude Code working in this repo. Keep it loaded; keep it current and short.

## Project
- **Name:** HaulYeah — Trash Hauling Lead Gen & Scheduling Bot
- **What it is:** Scrapes Facebook Marketplace for junk-removal / hauling leads, scores + de-dupes them, drafts outreach (Claude, optional), and syncs booked jobs to Google Sheets + Calendar. Telegram-controlled; runs on an APScheduler loop. Deliberately isolated from the ClawBot crypto bot.
- **Stack:** Python 3.x, python-telegram-bot, APScheduler, Playwright (FB scraping), Google Sheets/Calendar API (service account), Anthropic (optional outreach), python-dotenv. Has its own `.venv`.
- **Owner:** Ronnie

## Commands  ← highest-value section, fill this in precisely
- Run: from `trash_hauling_bot/` — `.venv\Scripts\Activate.ps1; python main.py`  (loads consolidated `..\.env` at the repo root)
- First-time FB login (once, before the first scrape): `python -m agents.scraper --login`
- Test: `pip install -r requirements-dev.txt` then `pytest`
- Lint / typecheck: none configured (no ruff/black/mypy in deps)
- Build / deploy: `docker compose up -d`  (Dockerfile + docker-compose.yml). A Linux systemd unit, `trash_hauling_bot.service`, also exists.
- Keyless smoke test: set `DRY_RUN=true` in the root `..\.env`, then `python main.py` — bot starts fully, all data in memory.

## Layout
- `main.py` — orchestrator: wires the three agents, starts the Telegram bot + scheduler
- `config.py` — env-backed `Config` dataclass; `validate()` checks required keys on startup
- `agents/` — `scraper.py` (FB Marketplace via Playwright), `outreach.py` (lead messaging), `calendar_sync.py`
- `integrations/` — `telegram_bot.py` (commands + job alerts), `gcal.py`, `sheets.py`
- `utils/` — `sanitize.py`, `dedup.py`, `retry.py`, `audit.py`
- `tests/` — pytest suite (sanitize, scoring, dedup, audit)
- `data/` — runtime only, gitignored: `bot.log`, `audit.log`, `fb_profile/` (FB session cookies), `pending_outreach.json`

## Conventions
- Python, type-hinted; config via a dataclass. Small, single-responsibility modules. No placeholder/TODO stubs in committed code.
- Secrets are in the consolidated `..\.env` at the repo root (gitignored). Never hardcode. Variable names are namespaced (`TRASH_BOT_*`, `FB_*`, etc.) so HaulYeah can't cross-load the crypto bot's tokens. HaulYeah still uses a separate BotFather token (`TRASH_BOT_TOKEN`, not `TELEGRAM_BOT_TOKEN`).
- Commit messages: imperative mood, concise (`feat:` / `fix:` prefixes, matching existing history).

## How to work here
- For non-trivial work (multi-file, new feature, refactor): briefly state the approach, the files you'll touch, and the order — then execute. Skip the ceremony for small, obvious fixes.
- Make incremental, reviewable changes. No broad refactors unless asked.
- Before changing a shared module, check what depends on it; preserve backward compatibility when reasonable.
- Match the existing architecture. If it needs to change, flag it and explain — don't silently rework it.

## Memory & handoff
Living project notes live in `memory/` (create on first use). After meaningful work, update:
- `ACTIVE_TASKS.md` — in progress / next up
- `DECISIONS.md` — architecture choices and the reasoning
- `SESSION_HANDOFF.md` — current state, open problems, next priorities
Never leave an undocumented half-finished change.

## Never
- Commit secrets. The root `..\.env` and `data/google_credentials.json` are git-ignored — confirm before any commit.
- Touch: the root `..\.env`, `data/google_credentials.json`, `data/fb_profile/` (live Facebook login cookies), or any `.env.haulyeah.old-*` legacy backups.
- Rename or refactor broadly without confirming first.

## Response style
Compact, structured, actionable. Minimal filler.
