# Hermes — 24/7 personal overseer

Hermes is Ronnie's personal agent that oversees **all** the projects in this
repo from one place: the **ClawBot** crypto trader (root package) and the
**HaulYeah** lead-gen bot (`trash_hauling_bot/`). It has its own Telegram bot
and shares the Flask dashboard.

Hermes is deliberately lightweight: it inspects each project's **on-disk
signals** (log freshness, queue files, recent trades) and never imports their
heavy runtimes (no telegram client, no Playwright, no exchange API). Every read
tolerates missing files, so Hermes can report health even before the other bots
have ever run.

## What it does

- Probes per-bot health (`hermes/health.py`) — running/idle + last-seen,
  ClawBot recent trades & TJR setups, HaulYeah audit freshness, pending
  outreach, and leads.
- Composes a concise plain-text **morning briefing** (`hermes/briefing.py`) with
  a status line per bot plus alerts (e.g. "ClawBot idle >6h",
  "N HaulYeah leads uncontacted", "TJR setup sent").
- Runs an oversight loop (`hermes/overseer.py`) that builds the briefing and
  pushes it to Telegram on an interval.

The same `hermes/health.py` drives the **HaulYeah** and **Hermes** panels on the
shared dashboard (`dashboard/app.py`).

## Run

```bash
# 24/7 oversight loop (sends briefings to Telegram)
python -m hermes.overseer

# single-shot test mode: print one briefing and exit, no Telegram send
python -m hermes.overseer --once
```

## Environment

Set these in the consolidated repo-root `.env` (see `.env.example`):

| Var | Purpose | Default |
| --- | --- | --- |
| `HERMES_BOT_TOKEN` | BotFather token for the Hermes bot (separate from ClawBot / HaulYeah) | — |
| `HERMES_CHAT_ID` | Ronnie's numeric chat id for briefings/alerts | — |
| `HERMES_CHECK_INTERVAL_MINUTES` | How often the oversight loop runs | `30` |

If `HERMES_BOT_TOKEN` / `HERMES_CHAT_ID` are unset, Hermes still runs and logs
the briefing but skips the Telegram send (best-effort, never crashes).

## Hosting

24/7 hosting is via **Railway** (`railway.toml` at the repo root,
`restartPolicyType = "always"`).
