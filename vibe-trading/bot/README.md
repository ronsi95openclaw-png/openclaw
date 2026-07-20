# TJR/ICT Lucid 25K Trading Bot

Live/paper trading runner that turns the **TJR ICT Kill Zone** strategy into
mandate-checked, audit-logged, **paper-by-default** order decisions for the
Lucid 25K futures eval.

- **Status:** v1.0 — paper / DRY_RUN by default. **Live POSTs are OFF until two locks are flipped (see [Go-Live Checklist](#go-live-checklist)).**
- **Locked design:** [`ARCHITECTURE.md`](ARCHITECTURE.md) is the single source of truth for module boundaries and function signatures. If code and that doc disagree, the doc wins.
- **Source of truth for all trading limits:** [`../lucid_mandate.json`](../lucid_mandate.json) — read at runtime, **never** hardcoded.

> If you read nothing else, read [Safety Model](#safety-model) and the [Go-Live Checklist](#go-live-checklist).

---

## What the bot does

Every cycle the bot:

1. **Loads ET bars** (`data_loader.py`) using the exact same CSV/timeframe conventions as the backtest, so **live == backtest** bar-for-bar.
2. **Re-reads the mandate** (`mandate.py`) from `lucid_mandate.json` — every limit (max loss, position cap, daily trade cap, consistency rule, instrument allowlist, no-overnight, EOD close) is read fresh from the file.
3. **Generates a signal** (`strategy.py`) — a *pure* function implementing the TJR/ICT spec: NY-Open kill-zone gate, HTF discount/premium bias, liquidity sweep, FVG/Order-Block in the OTE (61.8–79% fib) zone, 1-minute MSB confirmation close, stop beyond the sweep, `TP1 = 2R`, `TP2 = 4R`. Returns a [Signal dict](#the-signal-contract) or `None`.
4. **Gates the signal through `risk_guard.py`** — pure code that enforces the mandate independently of any model output. Returns `approve` / `reject` / `flat`.
5. **Only on `approve`**, hands the (possibly size-clamped) signal to `traderpost.py`, which **validates-before-send** and then **logs the payload (DRY_RUN) or POSTs (live, only if both locks are on)**.
6. **Audits every decision** (`audit.py`) — approve / reject / flat / skip — with timestamp + reason.

The strategy never reads the mandate, never touches the network, and never places an order. Risk and execution are separate, pure-code layers.

---

## Module map & data flow

```
vibe-trading/bot/
├── ARCHITECTURE.md     # LOCKED contract (read this first)
├── README.md           # this file
├── config.py           # BotConfig + load_config() — env + ops config, NO secrets, NO mandate numbers
├── mandate.py          # load_mandate(), MandateView — runtime read of lucid_mandate.json
├── data_loader.py      # load_bars_csv / resample / build_bars_by_tf — ET bars, lowercase OHLCV
├── strategy.py         # generate_signal(...) — PURE, no I/O, no orders
├── risk_guard.py       # RiskGuard.check(...) — pure-code mandate + circuit breaker
├── account.py          # build_account_state / validate_account_state
├── traderpost.py       # TraderPostClient.send(...) — DRY_RUN default, validate-before-send
├── audit.py            # AuditLogger — JSONL decision/order log + rotating text log
├── killswitch.py       # kill_switch_engaged(), flatten coordination
├── runner.py           # orchestration loop
└── logs/               # decisions.jsonl, orders.jsonl, runner.log, sessions/
```

**The order path (the only way an order can be born):**

```
   data_loader ──▶ strategy ──▶ risk_guard ──▶ traderpost ──▶ (DRY_RUN log │ live POST)
   (ET bars)      (Signal│None)  (approve/      (validate-       │
                                  reject/flat)   before-send)     └─▶ audit (EVERY decision)

         ▲                          ▲                  ▲
    mandate.py (runtime)      mandate.py (runtime)   mandate.py + os.environ secrets
    killswitch.py checked at runner step 1, risk_guard check 1, AND traderpost (defense in depth)
```

Dependency direction (no cycles): `runner` depends on everything; `risk_guard` depends only on `mandate`; `strategy` is a **leaf** (stdlib + pandas only, zero project imports) so it can be unit-tested and backtested in isolation.

**Hard rule:** no code path may construct or POST an order without first receiving `decision == "approve"` from `risk_guard.check(...)`. Model/agent output (e.g. `vibe_agent`) is, at most, advisory context — it can **never** bypass `risk_guard` or place an order.

---

## The Signal contract

`strategy.generate_signal(...)` returns `None` (no setup) **or** a dict with exactly these keys, consumed unchanged by `risk_guard` and `traderpost`:

```python
{
  "side":       "long" | "short" | None,   # None => no order
  "instrument": "ES" | "NQ" | "MES" | "MNQ",
  "entry":      float,
  "stop":       float,    # REQUIRED, finite, != entry
  "tp1":        float,    # entry ± 2R
  "tp2":        float,    # entry ± 4R   (R = abs(entry - stop))
  "size":       int,      # >= 1; risk_guard may clamp to the mandate cap
  "reason":     str,      # which TJR rules fired
  "ts":         str,      # ISO-8601 ET, e.g. "2026-06-21T09:42:00-04:00"
}
```

The MNQ-only `signals/incoming/*.json` alert format is a **different, legacy schema** — it must be translated + sanitized into the above before it can touch `risk_guard`.

---

## Data: getting it & running the backtest

### CSV format (matches `backtest/tjr_backtest.py`)

NinjaTrader 5-minute export, header optional (a row whose first cell is `Date` is skipped). **Timestamps are ET.**

```
Date,Time,Open,High,Low,Close,Volume
20240101,083000,4750.25,4752.00,4748.50,4751.00,12500
```

- `Date` = `%Y%m%d`, `Time` = `%H%M%S` (zero-padded to 6).
- Malformed rows are skipped. The 4-yr yfinance format (`Datetime,Open,High,Low,Close,Volume` with a `-04:00` offset) is also accepted.

To produce it: NinjaTrader → **Tools → Historical Data Manager** → download ES front month, 5-Minute bars → right-click → Export → CSV → save to `vibe-trading/backtest/data/ES_5M.csv`.

### Run the backtest

```bash
cd C:\Users\ronsi95openclaw\Claude-openclaw

# Preferred: the validated engine, reuses THIS package's strategy.py/risk_guard.py
python vibe-trading/bot/trade_ops.py backtest vibe-trading/backtest/data/ES_5M.csv

# Older, standalone, unsynced with live strategy logic — avoid unless you need it specifically
python vibe-trading/backtest/tjr_backtest.py --csv vibe-trading/backtest/data/ES_5M.csv --daily
```

Results print to console and are saved to `vibe-trading/backtest/results/` (standalone engine) or `vibe-trading/bot/logs/backtests/` (preferred engine). The backtest reads the **same** `lucid_mandate.json` the bot does.

(A one-off 4-year backtest experiment, `backtest_4yr/`, was archived 2026-07-02 to `_Archive/2026-07-02-vibe-trading-cleanup/` — it was never wired to the live strategy or any cron job.)

---

## Paper run (DRY_RUN — the default, and where you should live)

DRY_RUN is the default. With **neither** `HERMES_BOT_LIVE` nor `go_live` set, the bot does everything except the outbound POST: it computes the order, logs the would-be payload, and POSTs nothing.

```bash
cd C:\Users\ronsi95openclaw\Claude-openclaw\vibe-trading\bot

# One evaluation cycle against a CSV bar source, then exit
python runner.py --once --csv ../backtest/data/ES_5M.csv

# Continuous poll loop (still DRY_RUN — no env, no config flip)
python runner.py --csv ../backtest/data/ES_5M.csv
```

Watch the results land in `bot/logs/`:

- `decisions.jsonl` — one record per `risk_guard.check` (approve/reject/flat) + sparse `skip` records when there's no setup.
- `orders.jsonl` — one record per order attempt; in DRY_RUN every record is `"mode": "dry_run"`, `"result": "logged_only"`.
- `runner.log` — human-readable INFO log.
- `sessions/YYYY-MM-DD.jsonl` — that day's decision+order copy for review.

A DRY_RUN order record looks like:

```json
{
  "event": "order", "mode": "dry_run", "result": "logged_only",
  "instrument": "ES", "side": "long", "size": 1,
  "payload_summary": "long 1 ES @mkt stop=4744.25 tp1=4762.25 tp2=4774.25",
  "http_status": null, "webhook_host": "traderpost.io",
  "reason": "DRY_RUN: HERMES_BOT_LIVE!=1 or go_live=false"
}
```

**Run paper until you trust it.** The mandate ships with `"mode": "paper"`.

---

## Go-Live Checklist

> ⚠️ **Going live arms real-money order submission. Do NOT flip these locks until every box below is checked.** Live is gated behind **TWO independent locks plus a hard 1-contract operating cap** for the first live session.

A live TraderPost POST happens **only if ALL of the following hold simultaneously**:

```python
live = (os.environ.get("HERMES_BOT_LIVE") == "1") and bool(config.go_live)
```

### Security pre-deploy checklist (all must be true)

- [ ] **Paper proven.** Backtest passes and a multi-session paper run shows `decisions.jsonl` / `orders.jsonl` behaving correctly (sweeps, FVG/OB, OTE, MSB, EOD flatten, daily gate all firing as expected).
- [ ] **Mandate is authoritative & current.** `lucid_mandate.json` reflects the real account: `max_position_size`, `max_loss_limit (1500)`, `daily_trade_cap (10)`, `consistency_rule_eval (0.50)`, `instruments_allowed`, `overnight_holds=false`, `close_eod=true`. **No limit is hardcoded anywhere in `bot/`.**
- [ ] **Secrets are in `os.environ` only.** `TRADERPOST_WEBHOOK_URL` and `TRADERPOST_SECRET` are exported in the environment — **not** in any file, not in `BotConfig`, not committed. Grep the repo to confirm no secret/webhook literal exists.
- [ ] **No secret in any log.** Confirm `orders.jsonl` shows only `webhook_host` (e.g. `traderpost.io`) + a boolean — never a full URL, query string, header, or token. `audit._redact()` is in place.
- [ ] **Kill switch verified.** Creating `vibe-trading/KILL_SWITCH` halts + flattens and rejects new orders; removing it restores normal operation. (The existing `KILL_SWITCH_DISABLED` is **not** a trigger — only the literal path `vibe-trading/KILL_SWITCH`, read from `mandate["kill_switch"]["file"]`.)
- [ ] **risk_guard precedes every order.** Verified that no path POSTs without `decision == "approve"`. Reject/flat produce no order.
- [ ] **Validate-before-send confirmed.** `traderpost.send` rejects any order with a missing/non-finite stop, `size < 1` or `size > mandate.max_position_size`, an off-allowlist instrument, or a wrong side — independent of where the order came from.
- [ ] **Circuit breakers tested.** Daily-loss gate (80% ⇒ −$1,200 of $1,500), hard max-loss (−$1,500), consecutive-loss limit, invalid/NaN account state, and EOD flatten (15:55 ET) each halt + flatten and stay halted for the session.
- [ ] **External input sanitized.** Any file-sourced/agent signal is translated into the [Signal contract](#the-signal-contract) and passes through `strategy`/`risk_guard`/`traderpost` — it can never short-circuit the gates.
- [ ] **Audit confirmed.** Every decision (approve/reject/flat/skip) is written with an ISO-8601 ET timestamp + reason.
- [ ] **`security-review` skill run** against `risk_guard.py`, `traderpost.py`, `runner.py` (apply `llm-trading-agent-security` invariants S1–S9).

### Flipping the locks (only after every box above is checked)

```bash
# Lock 1 — environment half (per session, never committed)
export HERMES_BOT_LIVE=1
export TRADERPOST_WEBHOOK_URL="https://webhooks.traderpost.io/..."   # NEVER commit / log this
export TRADERPOST_SECRET="..."                                       # NEVER commit / log this

# Lock 2 — config half: set go_live=true in your operational config (default is False)

# Hard cap — first live session runs ONE contract.
#   Keep mandate max_position_size at the eval cap AND start the strategy/operator at 1 contract.
python runner.py --csv <live-or-feed-source>
```

If `HERMES_BOT_LIVE != "1"` **or** `config.go_live` is false **or** `TRADERPOST_WEBHOOK_URL` is unset, the bot stays in DRY_RUN (or returns `result="error"` / `"missing_webhook"` and POSTs nothing). **Default is always off.**

**Roll back to paper instantly:** `unset HERMES_BOT_LIVE` (or set `go_live=false`), or `touch vibe-trading/KILL_SWITCH` to halt + flatten immediately.

---

## Safety model

Enforced in **pure code** (never by model output), on every order path:

| # | Invariant | Where |
|---|-----------|-------|
| S1 | Loss/size limits read from `lucid_mandate.json` at runtime, independent of any model | `risk_guard.py`, `mandate.py` |
| S2 | Circuit breaker: halt + flatten on daily-loss gate, consecutive losses, invalid/NaN account, kill switch — stays halted for the session | `risk_guard.py`, `runner.run_cycle` |
| S3 | Validate-before-send: reject order w/o stop or size, size > cap, instrument off allowlist; DRY_RUN logs payload, POSTs nothing | `traderpost.send` |
| S4 | `TRADERPOST_WEBHOOK_URL` / `TRADERPOST_SECRET` from `os.environ` ONLY; never in code or logs | `traderpost.py` + `audit._redact()` |
| S5 | Sanitize external/file-sourced input into the Signal schema before it can drive an order | `data_loader` / `runner` |
| S6 | Audit **every** decision (approve/reject/flat/skip) with timestamp + reason | `audit.py`, `runner` |
| S7 | Paper/DRY_RUN default; live only when `HERMES_BOT_LIVE==1` **AND** `config.go_live` | `traderpost.send` |
| S8 | Kill switch (`vibe-trading/KILL_SWITCH`) halts + flattens | `killswitch.py`, `runner`, `risk_guard`, `traderpost` |
| S9 | Model/agent output can never bypass `risk_guard` or place an order — advisory only | architecture-wide; `strategy` stays pure |

**risk_guard checks (mandate numbers, evaluated in order, first failure decides):** kill switch → invalid account → EOD flatten (15:55 ET) → instrument allowlist → side/stop sanity → daily trade cap → hard max-loss limit → soft daily-loss gate (80%) → consecutive losses → consistency 50% cap → position-size clamp. `risk_guard` never raises on bad input — it fails **closed** to `flat`.

---

## Reference

- **Locked contract:** [`ARCHITECTURE.md`](ARCHITECTURE.md)
- **Mandate (source of truth):** [`../lucid_mandate.json`](../lucid_mandate.json)
- **Strategy spec:** [`../strategies/tjr_lucid_strategy.md`](../strategies/tjr_lucid_strategy.md)
- **Backtest engine (preferred, validated):** [`backtest.py`](backtest.py)
- **Reference backtest (older, unsynced — avoid):** [`../backtest/tjr_backtest.py`](../backtest/tjr_backtest.py)
- **Security invariants:** `llm-trading-agent-security` skill (S1–S9)
