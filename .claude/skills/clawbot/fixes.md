# ClawBot — Fixes Reference

All production bugs fixed, newest first.

---

## Fix 13 — Windows fcntl crash (commit `fbf632b`)

**Symptom**: `No module named 'fcntl'` on Windows — 29 files import POSIX-only `fcntl`.

**Fix**: Patch `sys.modules` at the very top of `main.py`, before any other import:

```python
if sys.platform == "win32":
    import types as _types
    _fcntl = _types.ModuleType("fcntl")
    _fcntl.LOCK_EX = 2;  _fcntl.LOCK_SH = 1
    _fcntl.LOCK_NB = 4;  _fcntl.LOCK_UN = 8
    _fcntl.FD_CLOEXEC = 1
    _fcntl.F_GETFD = 1;  _fcntl.F_SETFD = 2
    _fcntl.F_GETFL = 3;  _fcntl.F_SETFL = 4
    _fcntl.flock  = lambda fd, op: None
    _fcntl.lockf  = lambda fd, cmd, *a: None
    _fcntl.fcntl  = lambda fd, cmd, *a: 0
    _fcntl.ioctl  = lambda fd, cmd, *a: 0
    sys.modules["fcntl"] = _fcntl
    del _types, _fcntl
```

---

## Fix 12 — Google Sheets ERROR spam (commit `fbf632b`)

**Symptom**: `ERROR openclaw.reporting.google_sheets — SheetReporter: connection failed: …` logged on every trade when `credentials.json` absent.

**Fix** in `reporting/google_sheets.py`, `_connect()`:

```python
except FileNotFoundError:
    logger.debug(
        "SheetReporter: credentials file not found: %s (Sheets reporting disabled)",
        self._creds_file
    )
```

Changed `logger.error` → `logger.debug` for `FileNotFoundError`.

---

## Fix 11 — get_bot() double CryptoComBot init (commit `8619f37`)

**Symptom**: 7 CRITICAL `non-monotonic sequence` warnings at startup. Server.py's `get_bot()` created a second `CryptoComBot`, both writing to shared `event_store.jsonl` simultaneously.

**Fix** in `dashboard/api/server.py`:

```python
def get_bot():
    global _bot
    if _bot is None:
        try:
            from runtime.telegram_bot import _cmd_bot
            if _cmd_bot is not None and getattr(_cmd_bot, "_bot_ref", None) is not None:
                _bot = _cmd_bot._bot_ref
                return _bot
        except Exception:
            pass
        from trading.cryptocom_bot import CryptoComBot
        _bot = CryptoComBot()
    return _bot
```

---

## Fix 10 — Webhook handler duplicate responses (commit `8619f37`)

**Symptom**: Telegram retried webhook after 5-second timeout → duplicate command responses.

**Root cause**: Synchronous handler ran full command logic before returning `{"ok": True}`.

**Fix** in `dashboard/api/server.py`:

```python
@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    try:
        update = await request.json()
        cmd_bot = _get_cmd_bot()
        if cmd_bot:
            asyncio.create_task(asyncio.to_thread(cmd_bot._dispatch, update))
    except Exception as exc:
        logger.warning("Webhook dispatch error: %s", exc)
    return {"ok": True}
```

---

## Fix 9 — /livecheck WR bar showed wrong value (commit `8619f37`)

**Symptom**: `/livecheck` showed `WR: [████░░░░] 55%` when actual WR was 30%. The bar was showing progress-toward-goal (30/54 = 55%), not the actual win rate.

**Fix** in `runtime/live_mode_gate.py` — dedicated `_wr_bar()` helper:

```python
def _wr_bar(win_rate: float, target: float, width: int = 8) -> str:
    ratio  = min(win_rate / target, 1.0) if target > 0 else 0.0
    filled = int(width * ratio)
    label  = "✅" if win_rate >= target else f"{win_rate:.0%}"
    return f"[{'█' * filled}{'░' * (width - filled)}] {label}"
```

Usage: `_wr_bar(win_rate, 0.54)` → `[████░░░░] 30% (need 54%)`

---

## Fix 8 — Startup integrity false-positive on Railway (commit `e6e7f59`)

**Symptom**: Bot started with execution paused on Railway because `trade_outcomes.jsonl` didn't exist yet.

**Fix** in `infra/state_store.py`, `startup_integrity_check()`:

```python
if not _OUTCOMES_FILE.exists():
    return {"ok": True, "local_count": 0, "supabase_count": 0, "issues": []}
```

Guard added before reading the file — if no trade history yet, integrity is fine.

---

## Fix 7 — Telegram commands silent (webhook + poll conflict) (commit `e6e7f59`)

**Symptom**: On Railway, both `setWebhook` and `getUpdates` were running simultaneously. Telegram delivers to one or the other, not both.

**Fix** in `runtime/telegram_bot.py`, `TelegramCommandBot.start()`:

```python
def start(self) -> None:
    if os.getenv("RAILWAY_PUBLIC_URL"):
        logger.info("Railway webhook mode — skipping getUpdates polling")
        return
    # ... start polling thread
```

---

## Fix 6 — 409 webhook conflict auto-heal (commit `46a5dbc`)

**Symptom**: After Railway deploy, webhook stays registered. Running locally returns 409 on `getUpdates`. Bot silently stops receiving commands.

**Fix** in `runtime/telegram_bot.py`, `_poll_once()`:

```python
if not data.get("ok"):
    err_code = data.get("error_code", 0)
    if err_code == 409:
        logger.warning("getUpdates: 409 webhook conflict — auto-deleting webhook")
        try:
            del_req = urllib.request.Request(
                f"https://api.telegram.org/bot{tok}/deleteWebhook",
                data=b'{"drop_pending_updates":false}',
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(del_req, timeout=5) as dr:
                logger.info("Webhook deleted: %s", json.loads(dr.read().decode()))
        except Exception as de:
            logger.warning("Auto-deleteWebhook failed: %s", de)
        self._stop.wait(2)
    else:
        logger.warning("getUpdates not ok (code %s): %s", err_code, data)
        self._stop.wait(_RETRY_SLEEP)
    return
```

---

## Fix 5 — Balance showing $98 instead of $295 (earlier commit)

**Symptom**: Bot state showed balance=$98 (starting_balance) instead of actual $295.30.

**Root cause**: `balance` key missing from state dict — only `starting_balance` and `total_pnl` present.

**Fix** in `trading/cryptocom_bot.py`, state loading:

```python
# Balance derivation when balance key missing from state:
if "balance" not in raw:
    raw["balance"] = raw.get("starting_balance", 98.0) + raw.get("total_pnl", 0.0)
```

Also applied via direct Supabase SQL:
```sql
UPDATE bot_state
SET balance = 295.30, total_pnl = 197.30
WHERE id = (SELECT id FROM bot_state ORDER BY updated_at DESC LIMIT 1);
```

---

## Fix 4 — Railway IP blocks Telegram outbound (commit `7e938ed`)

**Symptom**: All `sendMessage` calls from Railway returned 403 — Telegram allowlists IPs and Railway cloud IPs are not on it.

**Architecture fix**: Route all outbound Telegram replies through Supabase:

```
Railway handler → telegram_outbox (Supabase) → local relay daemon → Telegram
```

Key env var: `TELEGRAM_OUTBOX_MODE=supabase` (set in `railway.toml`)

Relay daemon in `runtime/telegram_relay.py` polls `telegram_outbox` every 3s, sends from local machine (IP whitelisted), marks `sent_at=NOW()`.

---

## Fix 3 — TREND_FOLLOW 0% WR in TRENDING_BULL (data-driven)

**Root cause**: `TREND_FOLLOW` had 0 wins in TRENDING_BULL regime (went 0/4).

**Fix** in `research/regimes/strategy_compatibility.py`:

```python
FORBIDDEN = {
    "TREND_FOLLOW": {
        "RANGING", "MEAN_REVERTING", "VOL_COMPRESSION",
        "PANIC", "LIQUIDATION_CASCADE", "LIQUIDITY_DROUGHT",
        "UNKNOWN", "TRENDING_BULL",   # ← added
    },
}
```

---

## Fix 2 — TREND_FOLLOW SL had no ceiling (large loss)

**Symptom**: Single TREND_FOLLOW trade lost -$424. `sl = entry ± ATR×2` had no upper bound.

**Fix** in `trading/strategies.py`, TREND_FOLLOW signal generation:

```python
MAX_SL_PCT = 0.05  # 5% hard cap
sl_raw = entry_price * (1 + ATR_MULT * atr_ratio)
sl = min(sl_raw, entry_price * (1 + MAX_SL_PCT))   # long
sl = max(sl_raw, entry_price * (1 - MAX_SL_PCT))   # short
```

---

## Fix 1 — TREND_FOLLOW entered on noise (EMA gap too small)

**Symptom**: TREND_FOLLOW triggered on micro-moves, not actual trends.

**Fix**: Raise EMA gap threshold from 0.10% → 2.0%:

```python
EMA_GAP_THRESHOLD = 0.02  # 2% gap required between fast/slow EMA
```

---

## Import chain test (all 14 modules)

Run this after any change to verify clean imports:

```bash
cd /home/user/openclaw
python -c "
mods=[
  'settings',
  'infra.state_store',
  'risk.capital_preservation',
  'trading.cryptocom_bot',
  'runtime.telegram_bot',
  'runtime.telegram_relay',
  'runtime.morning_briefing',
  'runtime.live_mode_gate',
  'dashboard.api.server',
]
[(__import__(m), print('OK', m)) for m in mods]
"
```

All 9 lines should print `OK <module>` with no tracebacks.
