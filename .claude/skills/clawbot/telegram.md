# ClawBot — Telegram & Railway Reference

---

## Telegram relay architecture

Railway IPs are not on Telegram's allowlist. Direct `sendMessage` calls → 403.

```
User → Telegram → Railway /telegram/webhook → _dispatch(update)
                                             → writes reply to Supabase telegram_outbox
                                             ↓
Local machine TelegramRelayDaemon (runtime/telegram_relay.py)
  polls telegram_outbox every 3s
  sends via api.telegram.org (local IP whitelisted)
  marks sent_at = NOW()
                                             ↓
User receives reply
```

---

## Key environment variables

| Variable | Where | Value | Effect |
|----------|-------|-------|--------|
| `TELEGRAM_OUTBOX_MODE` | `railway.toml` | `supabase` | Routes sendMessage to Supabase outbox |
| `RAILWAY_PUBLIC_URL` | Railway auto-set | `https://cryptobot-production-18e1.up.railway.app` | Skips getUpdates polling, uses webhook |
| `TELEGRAM_BOT_TOKEN` | Railway env / .env | `8647354078:AAEb…` | Bot auth |
| `TELEGRAM_CHAT_ID` | Railway env / .env | `6082698835` | Ronnie's chat |

---

## railway.toml (excerpt)

```toml
[build]
builder = "DOCKERFILE"

[deploy]
startCommand = "python main.py"
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5

[variables]
TELEGRAM_OUTBOX_MODE = "supabase"
```

---

## Supabase telegram_outbox table

### Schema

```sql
CREATE TABLE telegram_outbox (
    id         uuid      PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id    text      NOT NULL,
    text       text      NOT NULL,
    parse_mode text      NOT NULL DEFAULT 'HTML',
    created_at timestamptz NOT NULL DEFAULT NOW(),
    sent_at    timestamptz,
    error      text
);

CREATE INDEX idx_telegram_outbox_unsent
    ON telegram_outbox (created_at)
    WHERE sent_at IS NULL;
```

### RLS policies (applied via migration `enable_rls_telegram_outbox`)

```sql
ALTER TABLE telegram_outbox ENABLE ROW LEVEL SECURITY;

-- Railway can insert replies
CREATE POLICY relay_insert ON telegram_outbox
    FOR INSERT TO anon WITH CHECK (true);

-- Relay daemon can read unsent
CREATE POLICY relay_select ON telegram_outbox
    FOR SELECT TO anon USING (sent_at IS NULL);

-- Relay daemon marks sent
CREATE POLICY relay_update ON telegram_outbox
    FOR UPDATE TO anon USING (true) WITH CHECK (true);
```

### Relay flush query (runtime/telegram_relay.py)

```python
# Poll unsent messages, ordered oldest first, max 20 per flush
rows = (
    sb.table("telegram_outbox")
    .select("id, chat_id, text, parse_mode, created_at")
    .is_("sent_at", "null")
    .order("created_at")
    .limit(20)
    .execute()
    .data
)

# Mark sent
sb.table("telegram_outbox")
  .update({"sent_at": datetime.utcnow().isoformat()})
  .eq("id", row["id"])
  .execute()

# Mark failed
sb.table("telegram_outbox")
  .update({"error": str(exc)})
  .eq("id", row["id"])
  .execute()
```

---

## Webhook setup (Railway auto-registers on boot)

`dashboard/api/server.py` startup event:

```python
@app.on_event("startup")
async def _register_telegram_webhook():
    pub_url = os.getenv("RAILWAY_PUBLIC_URL", "")
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not pub_url or not token:
        return
    webhook_url = f"{pub_url.rstrip('/')}/telegram/webhook"
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={"url": webhook_url, "drop_pending_updates": True},
        timeout=10,
    )
    logger.info("Webhook registered: %s → %s", webhook_url, resp.json())
```

**Local mode** (no `RAILWAY_PUBLIC_URL`): runs `deleteWebhook` first, then starts polling.

### Webhook endpoint

```python
@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        update = await request.json()
        cmd_bot = _get_cmd_bot()
        if cmd_bot:
            # Return 200 immediately — dispatch in background
            asyncio.create_task(asyncio.to_thread(cmd_bot._dispatch, update))
    except Exception as exc:
        logger.warning("Webhook dispatch error: %s", exc)
    return {"ok": True}
```

CRITICAL: Must return `{"ok": True}` within 5s or Telegram retries → duplicate messages. Use `asyncio.create_task` for dispatch.

---

## Railway deploy commands

```bash
# Check current status
RAILWAY_CALLER="skill:clawbot" railway status --json

# View live logs (last 200 lines)
RAILWAY_CALLER="skill:clawbot" railway logs --lines 200

# Redeploy (push current branch)
git push -u origin claude/blofin-trading-bot-dashboard-TUJBC

# Set/update env var
RAILWAY_CALLER="skill:clawbot" railway variable set KEY=value --service cryptobot

# List all variables
RAILWAY_CALLER="skill:clawbot" railway variable list --service cryptobot --json
```

---

## Polling mode (local machine only)

`runtime/telegram_bot.py` — `TelegramCommandBot._poll_once()`:

1. `getUpdates?timeout=25&offset={offset}` (long poll)
2. On 409: auto-calls `deleteWebhook`, waits 2s, retries
3. Dispatches each update to `_dispatch(update)`
4. Updates `offset = update_id + 1` to avoid re-processing

```python
# Polling skipped on Railway (RAILWAY_PUBLIC_URL set):
def start(self) -> None:
    if os.getenv("RAILWAY_PUBLIC_URL"):
        logger.info("Railway webhook mode — skipping getUpdates polling")
        return
    # start polling thread...
```

---

## Outbound alert flow (runtime/telegram_alerts.py)

All alerts call `_send(text)` which calls `_api("sendMessage", {...})` which:
- If `TELEGRAM_OUTBOX_MODE=supabase` → writes to `telegram_outbox`
- Otherwise → calls `api.telegram.org` directly

Alert functions:
- `alert_trade_open(symbol, side, entry, sl, tp, size, strategy, balance, regime)`
- `alert_trade_close(symbol, side, entry, exit_price, pnl, outcome, balance, strategy)`
- `alert_milestone_hit(milestone_num, balance, goal)`
- `alert_emergency_halt(reason, balance)`
- `alert_bot_started(gate_status)` — shows live gate progress bars
- `alert_execution_resumed(balance)` — fired by /resume command
- `alert_capital_recovered(old_state, balance)` — fired by orchestrator on SAFE transition

---

## Telegram relay daemon (runtime/telegram_relay.py)

```bash
# Start relay standalone (use when Railway is deployed, you're running locally)
python runtime/telegram_relay.py

# The relay runs automatically via main.py when RAILWAY_PUBLIC_URL is not set
```

Relay config:
- `_POLL_INTERVAL = 3` seconds between Supabase polls
- `_MAX_AGE_SEC = 120` — discards messages older than 2 minutes
- Marks sent: `sent_at = NOW()`
- Marks failed: `error = str(exc)`

---

## Common Telegram issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Commands silently ignored (local) | Webhook still registered from Railway deploy | Auto-fixed: 409 → deleteWebhook in _poll_once |
| Commands silently ignored (Railway) | Both webhook and polling running | Fixed: skip polling when RAILWAY_PUBLIC_URL set |
| Bot replies nothing | TELEGRAM_OUTBOX_MODE=supabase but relay not running | Start relay: `python runtime/telegram_relay.py` |
| Duplicate responses | Webhook handler took > 5s to return | Fixed: asyncio.create_task dispatch |
| 403 on sendMessage from Railway | IP not on Telegram allowlist | Fixed: use Supabase outbox architecture |
| /livecheck WR shows wrong % | Bar showed progress toward goal, not actual WR | Fixed: _wr_bar() helper in live_mode_gate.py |

---

## HaulYall bot (separate — ~/haulyall/bot.py)

Token: `8831940231:AAGUCwYSiUZsQT8xIYYj7ffRPUDUBem73po`
Never mix with OpenClaw bot. Keep running in separate terminal.
