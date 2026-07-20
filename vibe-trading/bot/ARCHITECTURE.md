# TJR/ICT Lucid 25K Trading Bot — Locked Architecture

**Status:** LOCKED CONTRACT — v1.0 (2026-06-21)
**Scope:** `vibe-trading/bot/` — the live/paper trading runner that turns the TJR/ICT
strategy into mandate-checked, audit-logged, paper-by-default order decisions.
**Authoritative inputs (read at runtime, never copied/hardcoded):**
- Rules: `vibe-trading/lucid_mandate.json`
- Strategy spec: `vibe-trading/strategies/tjr_lucid_strategy.md`
- Reference backtests: `vibe-trading/bot/backtest.py` (the validated engine — reuses this package's own `strategy.py`/`risk_guard.py`; use this one). `vibe-trading/backtest/tjr_backtest.py` is an older, unsynced standalone implementation — do not use for anything that needs to match live behavior. `backtest_4yr/` was a one-off experiment, archived 2026-07-02 to `_Archive/2026-07-02-vibe-trading-cleanup/`.

> This document is the single source of truth for module boundaries and function
> signatures. Every module is implemented independently against THIS contract.
> If code and this doc disagree, the doc wins until the doc is updated.

---

## 0. Hard Safety Invariants (NEVER violate)

These are enforced in **pure code** (not by any model output) and apply to every order path.

1. **Paper / DRY_RUN by default.** A live TraderPost POST may happen **only if BOTH**
   `os.environ["HERMES_BOT_LIVE"] == "1"` **AND** `config.go_live is True`. Both default
   to off/false. If either is missing/false → DRY_RUN: log the would-be payload, POST nothing.
2. **Mandate read at runtime.** All Lucid limits (`max_loss_limit`, `max_position_size`,
   `daily_trade_cap`, `consistency_rule_eval`, `instruments_allowed`, `overnight_holds`,
   `close_eod`) are read from `lucid_mandate.json` on every cycle. **No limit is ever hardcoded.**
3. **Kill switch.** If the file `vibe-trading/KILL_SWITCH` (literal name) exists → **halt + flatten**,
   reject all new orders. The currently-present `KILL_SWITCH_DISABLED` is NOT a trigger
   (only the literal path `vibe-trading/KILL_SWITCH` triggers). Path is read from
   `mandate["kill_switch"]["file"]` resolved relative to `vibe-trading/`.
4. **No secrets in code or logs.** `TRADERPOST_WEBHOOK_URL`, `TRADERPOST_SECRET`, and any token
   come from `os.environ` ONLY. They are never written to a log line, never echoed, never
   included in an audit record. Audit records store a boolean `live_send` + a redacted host only.
5. **risk_guard precedes every order.** No code path may construct or POST an order without
   first receiving a `decision == "approve"` from `risk_guard.check(...)`. On any breach the
   decision is `"reject"` or `"flat"` and **no order is produced**.
6. **Audit EVERY decision.** approve / reject / flat / skip are all logged to
   `vibe-trading/bot/logs/` with timestamp + reason — not just sends.
7. **Circuit breaker.** Halt + flatten on: daily-loss gate hit, consecutive-loss threshold,
   invalid/NaN account state, or kill switch. Once tripped, the runner stays halted for the
   session (no new entries) until restarted.
8. **Validate-before-send.** `traderpost.send()` rejects any order missing a stop or size,
   size > mandate cap, or instrument off the allowlist — independent of where the order came from.

---

## 1. Module Map & Responsibilities

```
vibe-trading/bot/
├── ARCHITECTURE.md         # this file (locked contract)
├── config.py               # BotConfig dataclass + load_config(); env + mandate, NO secrets stored
├── mandate.py              # load_mandate(), MandateView; runtime read of lucid_mandate.json
├── data_loader.py          # bars_by_tf loader — reuses backtest CSV conventions (ET)
├── strategy.py             # generate_signal(...) — PURE function, no I/O, no orders
├── risk_guard.py           # RiskGuard.check(...) — pure-code mandate + circuit breaker
├── traderpost.py           # TraderPostClient.send(...) — DRY_RUN default, validate-before-send
├── account.py              # AccountState builder/validator (realized/unrealized/open pos)
├── audit.py                # AuditLogger — JSONL decision log + rotating text log
├── killswitch.py           # kill_switch_engaged(), flatten coordination
├── runner.py               # orchestration loop: data → strategy → risk_guard → traderpost → audit
└── logs/                   # see §8 logging layout
```

Dependency direction (no cycles):

```
runner ──> data_loader, strategy, risk_guard, traderpost, account, audit, killswitch, config, mandate
risk_guard ──> mandate, audit(types only)
traderpost ──> config, audit(types only)
strategy ──> (stdlib + pandas only; NO project imports, NO I/O)
```

`strategy.py` is intentionally a leaf with **zero** dependencies on the rest of the bot so it
can be unit-tested and backtested in isolation, identical to the reference backtest helpers.

---

## 2. LOCKED Signal Schema

Produced by `strategy.generate_signal(...)`, consumed by `risk_guard.check(...)` and `traderpost.send(...)`.
A Python `dict` (or `TypedDict SignalDict`) with EXACTLY these keys:

```python
Signal = {
    "side":       str | None,   # 'long' | 'short' | None  (None => no setup; not an order)
    "instrument": str,          # 'ES' | 'NQ' | 'MES' | 'MNQ'  (must be in mandate allowlist)
    "entry":      float,        # intended entry price (limit/market ref)
    "stop":       float,        # protective stop price (REQUIRED, non-None, != entry)
    "tp1":        float,        # take-profit 1  == 2R from entry
    "tp2":        float,        # take-profit 2  == 4R from entry
    "size":       int,          # contracts requested by strategy (>=1; risk_guard may clamp/reject)
    "reason":     str,          # human-readable: which TJR rules fired (kill-zone, sweep, FVG/OB, OTE, MSB)
    "ts":         str,          # ISO-8601 timestamp in ET, e.g. '2026-06-21T09:42:00-04:00'
}
```

Rules:
- `generate_signal` returns **`None`** (not a dict) when no setup exists. When it returns a dict,
  `side` is `'long'` or `'short'` and all price fields are present and finite.
- R is defined as `abs(entry - stop)`. `tp1`/`tp2` are computed as `entry ± 2R` / `entry ± 4R`
  (sign by side). This matches `tjr_backtest.py` (`tp1_rr=2.0`, `tp2_rr=4.0`).
- `ts` is **ET, ISO-8601 with offset**. The runner converts to ET before/after; the strategy is
  handed `now_et` already in ET (see §4). Never emit a naive timestamp into the signal.
- The signal carries NO secrets, NO account balances, NO webhook info.

The MNQ-only `signals/incoming/*.json` format used by `signal_watcher.py`
(`{symbol, side, price, take_profit, stop_loss, note, timestamp}`) is a **different, legacy
alert format** and is NOT this schema. If `runner.py` is ever configured to consume external
signal files, `data_loader`/`runner` MUST translate + sanitize them into the locked Signal
schema above before they touch `risk_guard` (see §10 Security — sanitize external input).

---

## 3. Strategy Module — `strategy.py`

Pure, deterministic, side-effect-free. No file I/O, no network, no order placement, no logging.
Reuses the exact detection helpers proven in the reference backtests so live == backtest.

### 3.1 Public function (LOCKED)

```python
def generate_signal(
    bars_by_tf: dict[str, "pandas.DataFrame"],
    now_et: "datetime.datetime",      # tz-aware, ET
    *,
    instrument: str = "ES",
    config: "StrategyConfig | None" = None,
) -> dict | None:
    """
    Implement the TJR/ICT Kill Zone spec. Returns a Signal dict (see §2) or None.
    PURE: must not read files, open sockets, mutate inputs, or place orders.
    """
```

`bars_by_tf` keys are timeframe strings: `"1h"`, `"15m"`, `"5m"`, `"1m"` (lowercase). Each value is a
pandas DataFrame indexed by tz-aware ET `DatetimeIndex` with columns
`['open','high','low','close','volume']` (lowercase). HTF bias uses `"1h"` (fallback `"4h"` if present);
setup uses `"5m"`/`"15m"`; entry trigger uses `"1m"`. Missing a required timeframe → return `None`.

### 3.2 Internal helpers (mirror the backtest; pure)

```python
def in_kill_zone(now_et, zones: list[str]) -> bool
def htf_bias(htf_df) -> str | None          # 'long' (discount, <50% of swing range) | 'short' (premium) | None
def swing_range(df, lookback: int) -> tuple[float, float]   # (swing_low, swing_high)
def detect_liquidity_sweep(df, i, lookback, sweep_bars) -> str | None   # 'bullish_sweep'|'bearish_sweep'
def detect_fvg(df, i) -> tuple[float, float, str] | None    # (low, high, 'bullish'|'bearish') — 3-candle
def detect_order_block(df, i, direction) -> tuple[float, float] | None  # (low, high) of last opposing candle
def in_ote_zone(price, swing_lo, swing_hi, direction) -> bool   # 61.8%–79% fib retrace
def detect_msb_close(df_1m, i, direction) -> bool           # confirmed 1M close beyond last swing
```

### 3.3 Decision order (all must pass, else `None`)

1. **Kill-zone gate** — `in_kill_zone(now_et, config.kill_zones)`. Default `["ny_open"]`
   (08:30–11:00 ET). Zones come from the spec table; kill-zone bounds live in `StrategyConfig`,
   NOT the mandate. (Kill zones from `tjr_backtest.py KILL_ZONES`.)
2. **HTF bias** — discount (price below 50% of swing range) ⇒ look LONG; premium (above 50%) ⇒
   look SHORT. No clear bias ⇒ `None`.
3. **Liquidity sweep** — must take out sell-side (long) / buy-side (short) liquidity then close back.
4. **FVG (3-candle) or Order Block** in the swept zone, direction-matched.
5. **OTE** — the FVG/OB must sit inside the 61.8%–79% fib retracement of the swing.
6. **1M MSB confirmation** — a confirmed 1M candle CLOSE beyond the last 1M swing (long: above swing high,
   short: below swing low). Anticipated/unclosed breaks do NOT qualify.
7. **Stop placement** — beyond the sweep (long: below sweep low − 1 tick; short: above sweep high + 1 tick),
   matching `_open_trade` in the backtests.
8. **Targets** — `tp1 = entry ± 2R`, `tp2 = entry ± 4R` where `R = abs(entry - stop)`.

`size` defaults to `config.default_contracts` (1). `risk_guard` owns clamping to the mandate cap —
the strategy never reads the mandate.

### 3.4 `StrategyConfig` (dataclass, strategy-local; NOT mandate)

```python
@dataclass(frozen=True)
class StrategyConfig:
    kill_zones: tuple[str, ...] = ("ny_open",)
    lookback: int = 20
    sweep_bars: int = 3
    msb_bars: int = 5
    ote_low: float = 0.618
    ote_high: float = 0.79
    tp1_rr: float = 2.0
    tp2_rr: float = 4.0
    default_contracts: int = 1
```

---

## 4. Account State — `account.py`

`risk_guard.check` is fed an `account_state` dict built here from realized/unrealized P&L and open
positions (paper ledger in DRY_RUN; broker echo if live). LOCKED shape:

```python
AccountState = {
    "account_size":        float,   # from mandate (read-through), e.g. 25000.0
    "equity":              float,   # account_size + realized session P&L
    "realized_pnl_today":  float,   # signed $ realized this session
    "unrealized_pnl":      float,   # signed $ on open position (0.0 if flat)
    "total_eval_profit":   float,   # equity - account_size (running, all sessions)
    "open_position": {              # None if flat
        "instrument": str, "side": str, "size": int, "entry": float, "stop": float
    } | None,
    "trade_count_today":   int,     # entries opened today
    "consecutive_losses":  int,     # for circuit breaker
    "session_date":        str,     # ISO date (ET)
    "now_et":              str,     # ISO-8601 ET timestamp of evaluation
}
```

```python
def build_account_state(ledger, mandate_view, now_et) -> dict
def validate_account_state(state: dict) -> tuple[bool, str]
    # False + reason if any required field missing, non-finite (NaN/inf), or negative where impossible.
    # A False here => circuit breaker (halt+flatten) in risk_guard.
```

---

## 5. Risk Guard — `risk_guard.py` (LOCKED API)

Pure code. Enforces the mandate **independently of any model output**. Every order path calls this first.

```python
class RiskGuard:
    def __init__(self, mandate_view: "MandateView", *,
                 daily_gate_pct: float = 0.80,
                 consecutive_loss_limit: int = 3,
                 eod_flatten_et: "datetime.time" = time(15, 55)):
        ...

    def check(self, signal: dict, account_state: dict) -> dict:
        """
        Returns:
          { "decision": 'approve'|'reject'|'flat', "size": int, "reason": str }
        - 'approve' => order may proceed at the returned (possibly clamped) size.
        - 'reject'  => rule breach on THIS signal; no order.
        - 'flat'    => stand down / flatten posture (kill switch, circuit breaker, EOD); no new order.
        Never raises on bad input — invalid account_state => decision 'flat' (fail-closed).
        """
```

### 5.1 Checks (evaluated in order; first failure decides) — all numbers from mandate

1. **Kill switch present** ⇒ `flat` ("kill_switch").
2. **account_state invalid/NaN** ⇒ `flat` ("invalid_account_state") — circuit breaker.
3. **EOD flatten** — `now_et.time() >= eod_flatten_et` (15:55 ET) ⇒ `flat` ("eod_flatten").
   No overnight holds (`mandate.overnight_holds == false`).
4. **Instrument allowlist** — `signal.instrument not in mandate.instruments_allowed` ⇒ `reject`.
5. **Side sanity** — `side not in ('long','short')` or missing/non-finite `stop`/`entry` ⇒ `reject`.
6. **Daily trade cap** — `trade_count_today >= mandate.daily_trade_cap` ⇒ `reject` ("daily_trade_cap").
7. **Hard max-loss limit** — if `(realized_pnl_today + unrealized_pnl) <= -mandate.max_loss_limit`
   ⇒ `flat` ("max_loss_limit") — circuit breaker.
8. **Soft daily-loss gate** — if `realized_pnl_today <= -(max_loss_limit * daily_gate_pct)`
   (default 80% ⇒ -$1,200 of $1,500) ⇒ `flat` ("daily_loss_gate") — circuit breaker.
9. **Consecutive losses** — `consecutive_losses >= consecutive_loss_limit` ⇒ `flat` ("consecutive_losses").
10. **Consistency 50%** — if `total_eval_profit > 0` and
    `realized_pnl_today >= total_eval_profit * mandate.consistency_rule_eval`
    ⇒ `reject` ("consistency_cap").
11. **Position-size clamp** — `size = max(1, min(signal.size, mandate.max_position_size))`. If a clamp
    occurred, note it in `reason`; never silently exceed the cap.

If all pass ⇒ `{ "decision": "approve", "size": <clamped>, "reason": "ok" }`.

> `daily_gate_pct`, `consecutive_loss_limit`, `eod_flatten_et` are operational tunables on the
> RiskGuard (defaults match the strategy spec's $1,200 gate and 15:55 EOD). The mandate-derived
> hard numbers (`max_loss_limit`, `max_position_size`, `daily_trade_cap`, `consistency_rule_eval`,
> `instruments_allowed`) are ALWAYS read from `lucid_mandate.json` via `MandateView`.

---

## 6. Mandate Loader — `mandate.py`

Reuses the `load_mandate()` pattern from both backtests (read `lucid_mandate.json`, return its `rules`).

```python
def load_mandate(path: "Path | None" = None) -> dict      # raw JSON; default path = vibe-trading/lucid_mandate.json

@dataclass(frozen=True)
class MandateView:
    account_size: float
    max_loss_limit: float
    consistency_rule_eval: float
    overnight_holds: bool
    close_eod: bool
    instruments_allowed: tuple[str, ...]
    max_position_size: int
    daily_trade_cap: int
    kill_switch_file: str            # mandate["kill_switch"]["file"], resolved vs vibe-trading/
    auto_flatten_on_kill: bool
    mode: str                        # 'paper' (mandate's own mode flag)

    @classmethod
    def from_file(cls, path=None) -> "MandateView": ...
```

`MandateView` is **re-read each runner cycle** (or on file mtime change) so edits to the mandate take
effect without restart. No default values are baked in as the live source of truth; the file is
authoritative. (A fallback default is permitted ONLY to avoid a crash if the file is briefly missing,
and when fallback is used the runner logs a loud `mandate_fallback` audit event and stays in DRY_RUN.)

---

## 7. Data Loader — `data_loader.py`

Reuses the CSV conventions from the reference backtests so live bars == backtest bars.

### 7.1 LOCKED historical CSV format (NinjaTrader 5M, matches `tjr_backtest.py`)

```
Date,Time,Open,High,Low,Close,Volume
20240101,083000,4750.25,4752.00,4748.50,4751.00,12500
```

- Header optional (row whose first cell upper-cases to `DATE` is skipped — same as backtest).
- `Date` = `%Y%m%d`, `Time` = `%H%M%S` (zero-padded to 6). Combined parse: `"%Y%m%d%H%M%S"`.
- **Timestamps are ET.** The loader localizes naive parsed datetimes to `America/New_York`.
- Malformed rows are skipped (same tolerant behavior as `parse_csv`).
- The 4-yr yfinance format (`Datetime,Open,High,Low,Close,Volume` with `-04:00` offset) is also
  accepted via a second parser (mirrors `parse_yfinance_csv`); offset is honored/converted to ET.

### 7.2 API

```python
def load_bars_csv(path: "Path") -> "pandas.DataFrame"
    # -> DataFrame indexed by tz-aware ET DatetimeIndex, cols ['open','high','low','close','volume'] (lowercase)

def resample(bars_1m: "pandas.DataFrame", tf: str) -> "pandas.DataFrame"
    # tf in {'1m','5m','15m','1h','4h'}; OHLCV aggregation: open=first, high=max, low=min, close=last, volume=sum

def build_bars_by_tf(source, timeframes=('1h','15m','5m','1m')) -> dict[str, "pandas.DataFrame"]
    # source = a 1m (or finest) DataFrame or a CSV path; returns the dict strategy.generate_signal consumes
```

For live operation the loader may instead be fed a rolling bar buffer from a market-data feed; it MUST
yield the identical `bars_by_tf` dict shape (tz-aware ET index, lowercase OHLCV columns). The runner is
agnostic to source as long as this contract holds.

---

## 8. Logging Layout — `vibe-trading/bot/logs/`

`audit.py` owns all logging. Three sinks, all under `bot/logs/`:

```
bot/logs/
├── decisions.jsonl          # APPEND-ONLY JSONL — ONE record per decision (approve/reject/flat/skip)
├── orders.jsonl             # APPEND-ONLY JSONL — one record per order ATTEMPT (DRY_RUN or live)
├── runner.log               # rotating human text log (INFO+), like signal_watcher.log style
└── sessions/
    └── YYYY-MM-DD.jsonl      # per-session copy of that day's decision+order records (for review)
```

### 8.1 Decision record (`decisions.jsonl`) — LOCKED

```json
{
  "ts": "2026-06-21T09:42:03-04:00",
  "event": "decision",
  "decision": "reject",
  "stage": "risk_guard",
  "side": "long",
  "instrument": "ES",
  "entry": 4750.25, "stop": 4744.25, "tp1": 4762.25, "tp2": 4774.25,
  "requested_size": 2, "approved_size": 0,
  "reason": "daily_trade_cap",
  "account": { "realized_pnl_today": -120.0, "trade_count_today": 10,
               "consecutive_losses": 1, "total_eval_profit": 380.0 },
  "live_send": false,
  "dry_run": true
}
```

Every call to `risk_guard.check` produces exactly one decision record. `skip` (no signal at all —
`generate_signal` returned `None`) is logged at most once per cycle with `stage:"strategy"` and a
short `reason` (e.g. `"outside_kill_zone"`, `"no_setup"`) — used sparingly to avoid log spam.

### 8.2 Order record (`orders.jsonl`) — LOCKED

```json
{
  "ts": "2026-06-21T09:42:03-04:00",
  "event": "order",
  "mode": "dry_run",                 // 'dry_run' | 'live'
  "instrument": "ES", "side": "long", "size": 1,
  "entry": 4750.25, "stop": 4744.25, "tp1": 4762.25, "tp2": 4774.25,
  "payload_summary": "long 1 ES @mkt stop=4744.25 tp1=4762.25 tp2=4774.25",
  "result": "logged_only",           // 'logged_only' (dry) | 'sent' | 'rejected' | 'error'
  "http_status": null,               // int when live; null in dry_run
  "webhook_host": "traderpost.io",   // redacted host only — NEVER the full URL or secret
  "reason": "DRY_RUN: HERMES_BOT_LIVE!=1 or go_live=false"
}
```

**Never** log `TRADERPOST_WEBHOOK_URL`, `TRADERPOST_SECRET`, query strings, or auth headers. Only the
bare host (e.g. `traderpost.io`) and a boolean. `audit.py` runs a redaction pass on every record.

### 8.3 Audit API

```python
class AuditLogger:
    def __init__(self, log_dir: "Path"): ...
    def log_decision(self, *, signal: dict | None, result: dict, account_state: dict,
                     stage: str, dry_run: bool, live_send: bool) -> None
    def log_order(self, *, signal: dict, mode: str, result: str,
                  http_status: int | None, webhook_host: str | None, reason: str) -> None
    def log_event(self, event: str, reason: str, **fields) -> None   # halt/flatten/mandate_fallback/etc.
```

All writes are atomic appends; records carry an ISO-8601 ET `ts`. No secret ever reaches a record
(enforced by a `_redact()` allowlist on keys before serialization).

---

## 9. TraderPost Client — `traderpost.py`

The ONLY module permitted to make an outbound order POST. Default = DRY_RUN.

```python
class TraderPostClient:
    def __init__(self, config: "BotConfig", audit: "AuditLogger"): ...

    def send(self, signal: dict, *, approved_size: int) -> dict:
        """
        Validate-before-send, then either POST (live) or log-only (dry_run).
        Returns { "result": 'logged_only'|'sent'|'rejected'|'error',
                  "http_status": int|None, "reason": str }.
        """
```

### 9.1 Gating (ALL must hold to POST live)

```python
live = (os.environ.get("HERMES_BOT_LIVE") == "1") and bool(config.go_live)
```

If `live` is False ⇒ build the payload, `audit.log_order(mode="dry_run", result="logged_only", ...)`,
return without POSTing.

### 9.2 Validate-before-send (independent of caller / model)

Reject (no POST, `result="rejected"`) if ANY of:
- `signal["stop"]` missing / non-finite / equal to entry,
- `approved_size` < 1 or `approved_size > mandate.max_position_size`,
- `signal["instrument"]` not in `mandate.instruments_allowed`,
- `signal["side"]` not in `('long','short')`,
- kill switch present (re-checked here as defense in depth).

### 9.3 Secrets & payload

- `webhook_url = os.environ["TRADERPOST_WEBHOOK_URL"]`, `secret = os.environ.get("TRADERPOST_SECRET")`.
  If `live` is requested but `webhook_url` is unset ⇒ `result="error"`, reason `"missing_webhook"`,
  no POST, stays safe.
- Secret is sent in the POST body/header per TraderPost's contract — and is NEVER logged. Audit gets
  only `webhook_host` (parsed host) + `result`.
- Timeout on POST (e.g. 10s, like `signal_watcher.send_telegram`); network errors ⇒ `result="error"`,
  logged, runner continues (no crash, no retry storm).

---

## 10. Runner — `runner.py` (orchestration)

```python
def run_cycle(ctx: "BotContext") -> dict:
    """
    One evaluation cycle. Returns a summary dict (for tests / dashboards).
    Order of operations is FIXED:
      1. killswitch.engaged()?  -> flatten + audit 'halt'; return.
      2. mandate_view = MandateView.from_file()           # runtime re-read
      3. bars_by_tf = data_loader.build_bars_by_tf(...)   # ET bars
      4. account_state = account.build_account_state(...); ok,why = validate_account_state(...)
         if not ok: risk_guard returns 'flat' -> audit -> flatten -> return  (circuit breaker)
      5. signal = strategy.generate_signal(bars_by_tf, now_et, instrument=ctx.instrument)
         if signal is None: audit skip ('no_setup'/'outside_kill_zone'); return.
      6. result = risk_guard.check(signal, account_state)   # MANDATORY gate
         audit.log_decision(...)  # ALWAYS, regardless of decision
      7. if result.decision == 'approve':
             tp = traderpost.send(signal, approved_size=result.size)  # DRY_RUN by default
             audit.log_order(...)
         else: no order (reject/flat already audited).
    """

def main() -> None:
    # build BotContext, then loop run_cycle on a poll interval (like signal_watcher's loop),
    # or run once for --once. Honors kill switch each loop. Never POSTs unless §9.1 holds.
```

`flatten()` lives in `killswitch.py` / coordinated by runner: on halt it issues exit orders for any
open position **through the same `traderpost.send` validate-before-send path** (or, in DRY_RUN, logs
the would-be flatten), then refuses new entries for the session.

---

## 11. Config — `config.py`

```python
@dataclass(frozen=True)
class BotConfig:
    go_live: bool = False                 # config half of the live gate (default OFF)
    instrument: str = "ES"
    poll_interval_sec: int = 10
    strategy: "StrategyConfig" = StrategyConfig()
    daily_gate_pct: float = 0.80
    consecutive_loss_limit: int = 3
    eod_flatten_et: "datetime.time" = time(15, 55)
    log_dir: "Path" = Path(__file__).resolve().parent / "logs"

def load_config(path: "Path | None" = None) -> "BotConfig":
    """Load operational config (NOT secrets, NOT mandate limits). go_live defaults False."""
```

- `config.go_live` is the config half of the live gate; `HERMES_BOT_LIVE` is the env half. **Both** off
  by default. Secrets are NEVER stored in `BotConfig` — they are read from `os.environ` at call time
  inside `traderpost.py` only.
- Mandate limits are NOT duplicated here; `risk_guard`/`mandate` read them from the JSON at runtime.

---

## 12. Security Invariants (from llm-trading-agent-security skill)

| # | Invariant | Where enforced |
|---|-----------|----------------|
| S1 | Spend/loss limits enforced in pure code, read from `lucid_mandate.json`, independent of model output | `risk_guard.py`, `mandate.py` |
| S2 | Circuit breaker: halt+flatten on daily-loss gate, consecutive losses, invalid/NaN account, kill switch | `risk_guard.py` checks 2,7,8,9 + `runner.run_cycle` step 1/4 |
| S3 | Validate-before-send: reject order w/o stop or size, size>cap, instrument off allowlist; DRY_RUN logs payload + returns | `traderpost.send` §9.2/§9.1 |
| S4 | Keys/webhooks from `os.environ` ONLY (`TRADERPOST_WEBHOOK_URL`, `TRADERPOST_SECRET`); never in code/logs | `traderpost.py` + `audit._redact()` |
| S5 | Sanitize external/file-sourced input before it can drive an order | `data_loader`/`runner` translate+validate any external signal into the §2 schema before `risk_guard` |
| S6 | Audit every decision with timestamp + reason | `audit.py`, `runner` step 6 (ALWAYS) |
| S7 | Paper/DRY_RUN default; live only when `HERMES_BOT_LIVE==1` AND `config.go_live` | `traderpost.send` §9.1 |
| S8 | Kill switch halts + flattens | `killswitch.py`, `runner` step 1, `risk_guard` check 1, `traderpost` §9.2 |
| S9 | Model output (e.g. `vibe_agent` recommendations) can NEVER bypass `risk_guard` or place an order; it is, at most, advisory context fed into the strategy/runner and is subject to all gates | architecture-wide; `strategy` stays pure, no model call in the order path |

> Note: `agent/vibe_agent.py` is analysis-only and intentionally outside the order path. If its output
> is ever consumed by the runner, it is treated as untrusted external input (S5) and must pass through
> `strategy`/`risk_guard`/`traderpost` gates like any other signal. It must never short-circuit them.

---

## 13. Independent-Implementation Checklist

Each module can be built and tested alone against this contract:

- [ ] `mandate.py` — `MandateView.from_file()` round-trips `lucid_mandate.json`; fallback only on missing file (logs `mandate_fallback`, forces DRY_RUN).
- [ ] `data_loader.py` — parses the two CSV formats; emits tz-aware ET, lowercase OHLCV; `resample` aggregations correct.
- [ ] `strategy.py` — `generate_signal` pure; returns `None` outside kill zone / no setup; emits §2 schema with `tp1=2R`, `tp2=4R`, stop beyond sweep, ts in ET ISO-8601.
- [ ] `account.py` — `validate_account_state` flags NaN/inf/missing → triggers circuit breaker.
- [ ] `risk_guard.py` — `check` returns one of approve/reject/flat for each of the §5.1 cases using mandate numbers; clamps size; never raises.
- [ ] `traderpost.py` — DRY_RUN by default; validate-before-send rejects bad orders; live POST only under §9.1; no secret in any log.
- [ ] `audit.py` — three sinks under `bot/logs/`; redaction allowlist; ISO-8601 ET ts; one decision record per `check`.
- [ ] `killswitch.py` — only literal `vibe-trading/KILL_SWITCH` triggers (NOT `KILL_SWITCH_DISABLED`); coordinates flatten.
- [ ] `runner.py` — fixed step order; audits every decision; never POSTs unless §9.1; halts on kill switch / circuit breaker.

---

## 14. Reuse Notes (do not reinvent)

- **CSV parsing / Bar fields / kill-zone tables / FVG / sweep / MSB detection** — port directly from
  `backtest/tjr_backtest.py` and `backtest/backtest_4yr/tjr_backtest_4yr.py`. Keep semantics identical
  so the live strategy and the backtest agree bar-for-bar.
- **Mandate loading** — same `load_mandate()` pattern (read JSON `rules`), but the bot re-reads at runtime.
- **Kill-switch check** — same pattern as `agent/vibe_agent.py` (`KILL_SWITCH.exists()`), pointed at the
  mandate-declared path, and extended to actually flatten + halt (the bridge only refused; the bot must flatten).
- **Logging style / Telegram-free** — model `runner.log` on `signals/signal_watcher.py`'s stdout+file
  logging; the bot does NOT depend on Telegram for the order path (alerts are an optional side-channel).
- **EOD/flatten times** — 15:55 ET EOD flatten matches the strategy spec and `EOD_CLOSE_TIME` in the backtest.

---

**END OF LOCKED CONTRACT v1.0**
