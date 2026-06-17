---
name: loop
description: Inspect and control OpenClaw's scheduler + auto-trade loop (the bot's heartbeat in core/scheduler.py). Use when the user wants to check loop/scheduler status, see scheduled reminders or the daily auto-trade job, run the auto-trade scan now, enable/disable auto-trade, or debug why the loop isn't firing. Not for setting up generic recurring Claude Code tasks — that's the built-in loop skill.
---

# /loop — OpenClaw scheduler & auto-trade loop

OpenClaw's "loop" is the `AsyncIOScheduler` started once in `content/receiver.py`
and managed by `core/scheduler.py`. It drives two things:

1. **Reminders** — one-shot cron jobs persisted to `data/tasks.json`, restored on restart.
2. **Auto-trade** — a daily job (`clawbot_autotrade_daily`) that scans BTC/SOL/XRP/ETH
   with the RSI+MACD strategy and executes HIGH-confidence signals. Config lives in
   `data/autotrade.json` (or `AUTOTRADE_*` env vars for ephemeral cloud deploys).

The scheduler runs inside the live bot process (`python start.py`). When invoked from a
dev session you usually can't touch the running scheduler object directly — instead
inspect state from disk/env and operate the loop through the public functions below.

## How to handle a /loop request

Figure out which of these the user wants, then act.

### Show status
- Read `data/autotrade.json` (falls back to `{enabled: false, scan_time: "08:00", timeframe: "4h"}`).
- Read `data/tasks.json` and list `status == "pending"` reminders with their `time` (UTC).
- Report: auto-trade enabled?, scan time, timeframe, target chat, count of pending reminders.
- If the data dir is empty, check `AUTOTRADE_ENABLED` / `AUTOTRADE_CHAT_ID` / `AUTOTRADE_TIME`
  / `AUTOTRADE_TIMEFRAME` env vars — cloud deploys drive the loop from env.

### Run the scan now (no waiting for the daily cron)
- Public entry point: `core.scheduler.run_autotrade_now()` (async). It calls `_run_autotrade()`,
  which scans, executes HIGH signals via `trading.executor.execute_signals`, and notifies Telegram.
- ⚠️ This places **real trades** unless paper-trading is on. Confirm with the user before running,
  and prefer triggering it through the bot's `/autotrade now` Telegram command rather than invoking
  the executor from a dev shell.

### Enable / disable auto-trade
- Enable:  `core.scheduler.enable_autotrade(chat_id, scan_time="08:00", timeframe="4h")` — persists
  config and registers the cron job (only effective inside the running bot process).
- Disable: `core.scheduler.disable_autotrade()` — sets `enabled: false` and removes the job.
- Via the bot: `/autotrade on [HH:MM] [tf]`, `/autotrade off`, `/autotrade now`, `/autotrade` (status).

### Reminders
- Add:    `add_reminder(chat_id, "HH:MM", text)` — validates time, persists, schedules.
- List:   `get_reminders(chat_id)`.
- Cancel: `cancel_reminder(task_id)`.

### Debug "the loop isn't firing"
Walk this chain:
1. Is the bot process actually running? The scheduler only exists inside `start.py`.
2. `start_scheduler()` must have been called and `_scheduler.running` true.
3. After a restart, `reload_autotrade()` and `_reload_from_disk()` must re-register jobs —
   confirm `data/autotrade.json` says `enabled: true` (or env vars are set).
4. `set_send_fn()` must be injected, or jobs fire silently with no Telegram alert.
5. All cron triggers are **UTC** — a "wrong time" is usually a timezone mistake.
6. Check the process logs for `Failed to schedule task ...` (printed by `_schedule_job`).

## Key references
- `core/scheduler.py` — all loop logic (jobs, persistence, autotrade).
- `content/receiver.py` — `cmd_autotrade` handler and scheduler bootstrap (`set_send_fn`,
  `start_scheduler`, `reload_autotrade`).
- `data/autotrade.json`, `data/tasks.json` — runtime state (git-ignored).

## Guardrails
- Never hardcode secrets — auto-trade reads config from `data/` or `.env`.
- Auto-trade executes real orders; always confirm before running a scan or enabling it.
- Don't delete `data/*.json` to "reset" the loop — archive instead (see CLAUDE.md).
