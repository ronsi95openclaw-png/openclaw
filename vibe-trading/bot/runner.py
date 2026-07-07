#!/usr/bin/env python3
"""
TJR/ICT Lucid 25K Trading Bot — Orchestrator (``runner.py``)
============================================================
Implements §10 of ``bot/ARCHITECTURE.md`` (LOCKED CONTRACT v1.0).

The runner wires the locked pipeline together, one cycle at a time::

    killswitch -> mandate(reload) -> data_loader -> account -> strategy
               -> risk_guard (MANDATORY gate) -> traderpost (DRY_RUN default) -> audit

HARD SAFETY INVARIANTS enforced here (never violated):
  * Paper / DRY_RUN by DEFAULT. A live TraderPost POST may happen ONLY if
    ``os.environ["HERMES_BOT_LIVE"] == "1"`` AND ``config.go_live is True``.
    Both default to off/false. The runner NEVER POSTs by itself — every send
    flows through ``traderpost.send()`` which re-gates §9.1.
  * Mandate limits are read from ``lucid_mandate.json`` at runtime, every cycle.
    NO limit is ever hardcoded in this file.
  * Kill switch: presence of ``vibe-trading/KILL_SWITCH`` => halt + flatten +
    reject all new orders for the session. ``KILL_SWITCH_DISABLED`` is NOT a
    trigger (only the literal mandate-declared path triggers).
  * No secrets in code or logs. Webhook URL / secret live in ``os.environ`` and
    are touched ONLY inside ``traderpost.py``; the runner never reads them.
  * ``risk_guard.check`` precedes EVERY order. On any breach the decision is
    ``reject``/``flat`` and no order is produced.
  * EVERY decision (approve/reject/flat/skip) is audited to ``bot/logs/``.
  * Circuit breaker: once tripped (kill switch, invalid/NaN account, daily-loss
    gate, consecutive losses, hard max-loss) the runner stays HALTED for the
    session (no new entries) until restarted.

This module owns NO mandate numbers, NO secrets, NO order POST. It only
orchestrates. Sibling modules (``config``, ``mandate``, ``data_loader``,
``strategy``, ``risk_guard``, ``traderpost``, ``account``, ``audit``,
``killswitch``) are imported when present; lightweight, contract-conformant
fallback shims are used so the runner is runnable (and smoke-testable) before
every sibling lands. Fallbacks NEVER relax a safety invariant — they fail
closed (DRY_RUN, reject/flat).

CLI::

    python runner.py            # poll loop (DRY_RUN by default)
    python runner.py --once     # exactly one cycle, then exit
    python runner.py --once --csv path/to/ES_5M.csv --instrument ES
    python runner.py --selftest # internal smoke test (no external deps/data)

Author: ClawBot vibe-trading bot
"""
from __future__ import annotations

import argparse
import importlib
import math
import os
import signal
import sys
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

# ── Telegram alert (Hermes bot → user on approved signal) ───────────────────

def _send_telegram_alert(signal: dict, approved_size: int) -> None:
    """Fire-and-forget Telegram alert when a signal is approved.

    Env vars (set in Claude-openclaw .env):
      HERMES_TELEGRAM_BOT_TOKEN  — Hermes bot token
      HERMES_TELEGRAM_CHAT_ID    — Hermes HQ group ID (-1004424433192)
      HERMES_TELEGRAM_THREAD_ID  — VibeTrade floor thread (2); optional
    Never raises — a notification failure must not affect the trade path.
    """
    token = os.environ.get("HERMES_TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("HERMES_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        import urllib.request, json as _json
        side = signal.get("side", "?").upper()
        inst = signal.get("instrument", "?")
        entry = signal.get("entry", "?")
        stop = signal.get("stop", "?")
        tp1 = signal.get("tp1", "?")
        tp2 = signal.get("tp2", "?")
        reason = signal.get("reason", "")
        ts = signal.get("ts", "")
        text = (
            f"\U0001f7e2 *TJR A+ SETUP — {inst} {side}*\n"
            f"Size: {approved_size} contract(s)\n"
            f"Entry: `{entry}` | Stop: `{stop}`\n"
            f"TP1: `{tp1}` | TP2: `{tp2}`\n"
            f"Reason: {reason}\n"
            f"Time: {ts}\n"
            f"_PAPER MODE — no live order sent_"
        )
        body: dict = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        thread_id = os.environ.get("HERMES_TELEGRAM_THREAD_ID")
        if thread_id:
            try:
                body["message_thread_id"] = int(thread_id)
            except (ValueError, TypeError):
                pass
        payload = _json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass   # never let a notification failure touch the trade path


# ── Paths & constants (paths only — never limits, never secrets) ──────────────

BOT_DIR = Path(__file__).resolve().parent                 # vibe-trading/bot/
VIBE_DIR = BOT_DIR.parent                                  # vibe-trading/
LOG_DIR = BOT_DIR / "logs"
MANDATE_FILE = VIBE_DIR / "lucid_mandate.json"
ET = ZoneInfo("America/New_York")

# Locked §2 Signal schema keys — the ONLY fields permitted to survive
# _sanitize_signal into risk_guard / traderpost / the audit logs. Any extra
# field on an external/file-sourced signal (S5) is dropped (allowlist).
_SIGNAL_KEYS = (
    "side", "instrument", "entry", "stop", "tp1", "tp2", "size", "reason", "ts",
)

# Dollar value of ONE full point of price movement, per contract, by instrument.
# These mirror the LOCKED values used by the backtest engines (bot/backtest.py +
# backtest/tjr_backtest.py) so the paper fill simulation prices closes the SAME
# way the validated backtest does — they are NOT mandate limits and NOT secrets.
# Only used by the DRY_RUN paper fill/close simulation below to turn a price
# delta (points) into a realized $ P&L so the risk_guard circuit breakers
# (max-loss / daily-loss / consecutive-loss) can actually be exercised in paper
# mode. An unknown instrument falls back to 1.0 (points == dollars) — never a
# wider, riskier value.
_POINT_VALUE = {
    "ES": 50.0, "MES": 5.0, "NQ": 20.0, "MNQ": 2.0,
}

# Make the bot package importable whether run as a script or a module.
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


def now_et() -> datetime:
    """Current wall-clock time as a tz-aware ET datetime."""
    return datetime.now(tz=ET)


def _iso_et(dt: Optional[datetime] = None) -> str:
    """ISO-8601 timestamp in ET (with offset)."""
    return (dt or now_et()).astimezone(ET).isoformat(timespec="seconds")


def _is_finite_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


# ──────────────────────────────────────────────────────────────────────────────
#  Sibling-module loading (real module if present, else a fail-closed fallback)
# ──────────────────────────────────────────────────────────────────────────────
#
#  The runner depends on these per the contract:
#     config, mandate, data_loader, strategy, risk_guard, traderpost,
#     account, audit, killswitch
#
#  Each is imported best-effort. If a sibling is not yet implemented, a minimal
#  shim is substituted that conforms EXACTLY to the locked API and ALWAYS fails
#  closed (DRY_RUN / reject / flat). This lets runner.py be developed, run, and
#  smoke-tested independently, and be a no-op drop-in once siblings land.


def _try_import(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


# ── audit fallback (JSONL decisions/orders + rotating text log; redacted) ─────

class _FallbackAuditLogger:
    """Minimal contract-conformant AuditLogger (§8.3) used until audit.py lands.

    Three sinks under ``bot/logs/``: ``decisions.jsonl``, ``orders.jsonl``,
    ``runner.log`` (+ a per-session copy). Runs a redaction allowlist so no
    secret can ever reach a record.
    """

    # keys that may NEVER appear in a record, even if upstream tries
    _SECRET_KEYS = {
        "webhook", "webhook_url", "traderpost_webhook_url", "url",
        "secret", "traderpost_secret", "token", "api_key", "apikey",
        "authorization", "auth", "password", "headers", "query",
    }

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.sessions_dir = self.log_dir / "sessions"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.decisions_path = self.log_dir / "decisions.jsonl"
        self.orders_path = self.log_dir / "orders.jsonl"
        self.runner_log = self.log_dir / "runner.log"

    # -- redaction -------------------------------------------------------------
    def _redact(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if str(k).lower() in self._SECRET_KEYS:
                    out[k] = "<redacted>"
                else:
                    out[k] = self._redact(v)
            return out
        if isinstance(obj, (list, tuple)):
            return [self._redact(v) for v in obj]
        return obj

    def _session_path(self) -> Path:
        return self.sessions_dir / f"{now_et().date().isoformat()}.jsonl"

    def _append_jsonl(self, path: Path, record: dict) -> None:
        import json
        line = json.dumps(self._redact(record), default=str, separators=(",", ":"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # mirror into the per-session file for review
        try:
            with open(self._session_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _text(self, level: str, msg: str) -> None:
        line = f"{_iso_et()} [{level}] {msg}"
        print(line, flush=True)
        try:
            with open(self.runner_log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    # -- contract API (§8.3) ---------------------------------------------------
    def log_decision(self, *, signal: Optional[dict], result: dict,
                     account_state: dict, stage: str, dry_run: bool,
                     live_send: bool) -> None:
        sig = signal or {}
        acct = account_state or {}
        record = {
            "ts": _iso_et(),
            "event": "decision",
            "decision": result.get("decision"),
            "stage": stage,
            "side": sig.get("side"),
            "instrument": sig.get("instrument"),
            "entry": sig.get("entry"),
            "stop": sig.get("stop"),
            "tp1": sig.get("tp1"),
            "tp2": sig.get("tp2"),
            "requested_size": sig.get("size"),
            "approved_size": result.get("size", 0),
            "reason": result.get("reason"),
            "account": {
                "realized_pnl_today": acct.get("realized_pnl_today"),
                "trade_count_today": acct.get("trade_count_today"),
                "consecutive_losses": acct.get("consecutive_losses"),
                "total_eval_profit": acct.get("total_eval_profit"),
            },
            "live_send": bool(live_send),
            "dry_run": bool(dry_run),
        }
        self._append_jsonl(self.decisions_path, record)
        self._text("DECISION", f"{stage}:{result.get('decision')} "
                               f"reason={result.get('reason')} "
                               f"size={result.get('size', 0)}")

    def log_order(self, *, signal: dict, mode: str, result: str,
                  http_status: Optional[int], webhook_host: Optional[str],
                  reason: str) -> None:
        sig = signal or {}
        size = sig.get("size")
        record = {
            "ts": _iso_et(),
            "event": "order",
            "mode": mode,
            "instrument": sig.get("instrument"),
            "side": sig.get("side"),
            "size": size,
            "entry": sig.get("entry"),
            "stop": sig.get("stop"),
            "tp1": sig.get("tp1"),
            "tp2": sig.get("tp2"),
            "payload_summary": (
                f"{sig.get('side')} {size} {sig.get('instrument')} @mkt "
                f"stop={sig.get('stop')} tp1={sig.get('tp1')} tp2={sig.get('tp2')}"
            ),
            "result": result,
            "http_status": http_status,
            "webhook_host": webhook_host,   # bare host only, never the URL/secret
            "reason": reason,
        }
        self._append_jsonl(self.orders_path, record)
        self._text("ORDER", f"{mode}:{result} {record['payload_summary']} reason={reason}")

    def log_event(self, event: str, reason: str, **fields) -> None:
        record = {"ts": _iso_et(), "event": event, "reason": reason}
        record.update(fields)
        self._append_jsonl(self.decisions_path, record)
        self._text("EVENT", f"{event} reason={reason}")


# ── config fallback (§11) — go_live defaults False; NO secrets stored ─────────

@dataclass(frozen=True)
class _FallbackStrategyConfig:
    kill_zones: tuple = ("ny_open",)
    lookback: int = 20
    sweep_bars: int = 2
    msb_bars: int = 5
    ote_low: float = 0.618
    ote_high: float = 0.79
    tp1_rr: float = 2.0
    tp2_rr: float = 4.0
    default_contracts: int = 1


@dataclass(frozen=True)
class _FallbackBotConfig:
    go_live: bool = False                 # config half of the live gate (OFF)
    instrument: str = "ES"
    poll_interval_sec: int = 10
    strategy: Any = field(default_factory=_FallbackStrategyConfig)
    daily_gate_pct: float = 0.80
    consecutive_loss_limit: int = 3
    eod_flatten_et: time = time(15, 55)
    session_open_et: time = time(8, 30)   # trading-window floor (ny_open start)
    log_dir: Path = LOG_DIR


def _fallback_load_config(path: Optional[Path] = None) -> Any:
    return _FallbackBotConfig()


# ── mandate fallback (§6) — runtime read of lucid_mandate.json; fail-closed ───

@dataclass(frozen=True)
class _FallbackMandateView:
    account_size: float
    max_loss_limit: float
    consistency_rule_eval: float
    overnight_holds: bool
    close_eod: bool
    instruments_allowed: tuple
    max_position_size: int
    daily_trade_cap: int
    kill_switch_file: str
    auto_flatten_on_kill: bool
    mode: str
    is_fallback: bool = False   # True => file missing => loud audit + force DRY_RUN

    @classmethod
    def from_file(cls, path: Optional[Path] = None) -> "_FallbackMandateView":
        import json
        p = Path(path) if path else MANDATE_FILE
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                rules = raw.get("rules", {})
                ks = raw.get("kill_switch", {}) or {}
                return cls(
                    account_size=float(rules["account_size"]),
                    max_loss_limit=float(rules["max_loss_limit"]),
                    consistency_rule_eval=float(rules["consistency_rule_eval"]),
                    overnight_holds=bool(rules.get("overnight_holds", False)),
                    close_eod=bool(rules.get("close_eod", True)),
                    instruments_allowed=tuple(rules["instruments_allowed"]),
                    max_position_size=int(rules["max_position_size"]),
                    daily_trade_cap=int(rules["daily_trade_cap"]),
                    kill_switch_file=str(ks.get("file", "./KILL_SWITCH")),
                    auto_flatten_on_kill=bool(ks.get("auto_flatten_on_kill", True)),
                    mode=str(raw.get("mode", "paper")),
                    is_fallback=False,
                )
            except Exception:
                pass
        # File missing/unreadable: fail closed (paper, conservative caps) and
        # flag fallback so the runner logs `mandate_fallback` and stays DRY_RUN.
        return cls(
            account_size=25000.0, max_loss_limit=1500.0,
            consistency_rule_eval=0.50, overnight_holds=False, close_eod=True,
            instruments_allowed=("ES", "MES", "NQ", "MNQ"),
            max_position_size=1, daily_trade_cap=0,   # cap 0 => no new entries
            kill_switch_file="./KILL_SWITCH", auto_flatten_on_kill=True,
            mode="paper", is_fallback=True,
        )


# ── account fallback (§4) — paper ledger -> AccountState; validator ───────────

class _PaperLedger:
    """Tiny in-memory paper ledger used in DRY_RUN until account.py lands.

    Tracks realized session P&L, the (single) open position, entry count, and a
    consecutive-loss counter. Persists nothing; resets per process start.

    PAPER FILL/CLOSE SIMULATION (the fix): the runner now drives
    ``_paper_manage_position(...)`` each cycle, which settles the open position
    against the latest bar (stop / TP1 / TP2) and calls ``record_close(...)`` with
    a realized P&L. That makes ``realized_pnl_today`` and ``consecutive_losses``
    actually MOVE in DRY_RUN, so risk_guard's daily-loss (check 8), max-loss
    (check 7) and consecutive-loss (check 9) breakers ARE exercised in paper mode.

    SAFETY NOTE: this is a MINIMAL OHLC-based approximation, not a true fill feed
    — it cannot see intrabar order (it assumes a stop fills before a target when a
    single bar straddles both, biasing toward losses / fail-safe), models no
    slippage and no commission, and only inspects the latest bar. It is good
    enough to EXERCISE the breakers, but a real ``account.py`` driven by broker
    fills must replace it before any paper P&L number is reported as truth (the
    runner still audits ``paper_ledger_not_risk_validating`` at boot to say so).
    """

    # The fallback ledger now exercises the breakers via the paper close sim, but
    # it is still an OHLC approximation rather than a true broker fill feed, so it
    # does not claim to be the authoritative risk-validating account source.
    is_risk_validating: bool = False

    def __init__(self):
        self.session_date: Optional[date] = None
        self.realized_pnl_today: float = 0.0
        # PRIOR cumulative eval profit (all sessions strictly before today).
        # Today's realized P&L is tracked separately in realized_pnl_today and
        # is folded in here only at session roll-over. The consistency check
        # must compare today's profit against this prior baseline, NOT against a
        # denominator that already includes today (see account builder).
        self.prior_eval_profit: float = 0.0
        self.trade_count_today: int = 0
        self.consecutive_losses: int = 0
        self.open_position: Optional[dict] = None

    def roll_session(self, today: date) -> None:
        if self.session_date != today:
            # carry today's realized result into the prior-eval baseline so the
            # consistency denominator excludes the NEW day's realized P&L.
            if self.session_date is not None:
                self.prior_eval_profit += self.realized_pnl_today
            self.session_date = today
            self.realized_pnl_today = 0.0
            self.trade_count_today = 0
            # consecutive_losses and prior_eval_profit carry across sessions

    def record_entry(self, instrument: str, side: str, size: int,
                     entry: float, stop: float,
                     tp1: Optional[float] = None,
                     tp2: Optional[float] = None) -> None:
        self.trade_count_today += 1
        pos = {
            "instrument": instrument, "side": side, "size": int(size),
            "entry": float(entry), "stop": float(stop),
        }
        # Carry the protective targets so the paper fill/close simulation can
        # detect TP1/TP2 hits. They are stored separately from the LOCKED §4
        # open_position schema fields above (which build_account_state echoes);
        # the simulator reads them off the same dict.
        if _is_finite_number(tp1):
            pos["tp1"] = float(tp1)
        if _is_finite_number(tp2):
            pos["tp2"] = float(tp2)
        # tp1_filled tracks the partial: after TP1 the runner books half and the
        # remainder runs to TP2 / a breakeven-ish stop (stop stays at original).
        pos["tp1_filled"] = False
        self.open_position = pos

    def record_close(self, realized_pnl: float) -> None:
        """Apply a realized P&L from a (simulated/real) fill close.

        Updates session realized P&L and the consecutive-loss counter so the
        risk_guard circuit breakers (checks 7/8/9) can actually trip. Only a
        real fill feed or backtest harness should call this; the live DRY_RUN
        path does not, by design (no real fill price)."""
        pnl = float(realized_pnl)
        if not math.isfinite(pnl):
            return
        self.realized_pnl_today += pnl
        if pnl < 0:
            self.consecutive_losses += 1
        elif pnl > 0:
            self.consecutive_losses = 0
        self.open_position = None

    def flatten(self) -> None:
        self.open_position = None


def _point_value(instrument: Any) -> float:
    """Dollars per point per contract for ``instrument`` (paper sim only).

    Unknown instruments fall back to 1.0 (points == dollars), the most
    conservative choice — it never inflates a simulated loss/gain beyond the raw
    point delta, so a breaker cannot be tripped *harder* by an unrecognized
    symbol than by a known one."""
    return float(_POINT_VALUE.get(str(instrument), 1.0))


def _paper_pnl(side: str, entry: float, exit_price: float, size: int,
               instrument: Any) -> float:
    """Realized $ P&L for closing ``size`` contracts of ``side`` from ``entry``
    to ``exit_price`` (no commission modeled — a MINIMAL sim). Long profits when
    price rises; short profits when price falls."""
    pts = (exit_price - entry) if side == "long" else (entry - exit_price)
    return pts * _point_value(instrument) * int(size)


def _latest_bar(bars_by_tf: Optional[dict]) -> Optional[dict]:
    """Return the most-recent bar as a plain ``{high, low, close}`` dict from the
    finest available timeframe in ``bars_by_tf``. None if no usable bar.

    Reads the last row of the finest (1m -> 5m -> 15m -> 1h) OHLCV frame. This is
    deliberately tiny: the paper close sim only needs the latest bar's high/low to
    decide whether a stop/target was touched."""
    if not bars_by_tf:
        return None
    for tf in ("1m", "5m", "15m", "1h", "4h"):
        df = bars_by_tf.get(tf)
        if df is None:
            continue
        try:
            if getattr(df, "empty", True):
                continue
            row = df.iloc[-1]
            hi, lo, cl = float(row["high"]), float(row["low"]), float(row["close"])
        except Exception:
            continue
        if _is_finite_number(hi) and _is_finite_number(lo) and _is_finite_number(cl):
            return {"high": hi, "low": lo, "close": cl}
    return None


def _paper_manage_position(ledger: _PaperLedger, bars_by_tf: Optional[dict]) -> Optional[dict]:
    """MINIMAL paper fill/close simulation (DRY_RUN only).

    Looks at the open paper position and the latest bar; if the bar's range
    touched the stop (SL) or a take-profit (TP1/TP2), book the realized P&L via
    ``ledger.record_close(...)`` so ``realized_pnl_today`` and
    ``consecutive_losses`` MOVE — which is exactly what lets risk_guard's
    daily-loss / max-loss / consecutive-loss circuit breakers actually trip in
    paper mode (the whole point of this fix).

    Conservative tie-break: if a single bar straddles BOTH the stop and a target
    (we cannot know intrabar ordering from OHLC alone), assume the STOP filled
    first (worst case). This biases the sim toward losses, so it can only make the
    breakers MORE likely to fire, never less — fail-safe for a risk simulation.

    Partial model: TP1 books HALF the size (floor, min 1) and leaves the runner
    flat-to-TP2 on the remainder with the original stop. TP2 or SL books the rest.

    Returns a small event dict (for audit) or None if nothing filled this bar."""
    pos = ledger.open_position
    if not pos:
        return None
    bar = _latest_bar(bars_by_tf)
    if bar is None:
        return None

    side = pos.get("side")
    entry = float(pos.get("entry"))
    stop = float(pos.get("stop"))
    size = int(pos.get("size", 1))
    instrument = pos.get("instrument")
    hi, lo = bar["high"], bar["low"]

    def _touched(level: Any, is_stop: bool) -> bool:
        if not _is_finite_number(level):
            return False
        lvl = float(level)
        if side == "long":
            return lo <= lvl if is_stop else hi >= lvl
        # short
        return hi >= lvl if is_stop else lo <= lvl

    stop_hit = _touched(stop, is_stop=True)
    tp1 = pos.get("tp1")
    tp2 = pos.get("tp2")

    # --- worst-case: stop first if the bar straddles stop and any target -------
    if stop_hit:
        remaining = size
        pnl = _paper_pnl(side, entry, stop, remaining, instrument)
        ledger.record_close(pnl)   # books pnl, updates consecutive_losses, clears pos
        return {"exit": "stop", "price": stop, "size": remaining, "realized": pnl}

    # --- TP2 closes the whole remainder ---------------------------------------
    if _touched(tp2, is_stop=False):
        remaining = size
        pnl = _paper_pnl(side, entry, float(tp2), remaining, instrument)
        ledger.record_close(pnl)
        return {"exit": "tp2", "price": float(tp2), "size": remaining, "realized": pnl}

    # --- TP1 books a partial (half), leaves the remainder running -------------
    if (not pos.get("tp1_filled")) and _touched(tp1, is_stop=False):
        half = max(1, size // 2)
        if half >= size:
            # size 1: TP1 closes the whole position (no remainder to run to TP2)
            pnl = _paper_pnl(side, entry, float(tp1), size, instrument)
            ledger.record_close(pnl)
            return {"exit": "tp1_full", "price": float(tp1), "size": size, "realized": pnl}
        pnl = _paper_pnl(side, entry, float(tp1), half, instrument)
        # Book the partial against session P&L WITHOUT clearing the position or
        # touching consecutive_losses (a winning partial must not reset the loss
        # streak prematurely, and the remainder is still open). Mutate the ledger
        # fields directly for the partial; record_close() owns the FINAL close.
        if math.isfinite(pnl):
            ledger.realized_pnl_today += pnl
        pos["size"] = size - half
        pos["tp1_filled"] = True
        ledger.open_position = pos
        return {"exit": "tp1_partial", "price": float(tp1), "size": half, "realized": pnl}

    return None


def _fallback_build_account_state(ledger: _PaperLedger, mandate_view: Any,
                                  now: datetime) -> dict:
    realized = float(ledger.realized_pnl_today)
    unreal = 0.0   # paper: no live mark; flatten-on-exit model
    account_size = float(getattr(mandate_view, "account_size", 25000.0))
    equity = account_size + realized
    # total_eval_profit is the PRIOR cumulative eval profit (all sessions before
    # today). It deliberately EXCLUDES today's realized P&L so the consistency
    # check (realized_today vs total_eval * 0.50) compares today's day-profit
    # against a prior baseline. Including today would make the gate fire for any
    # realized>0 on a fresh process (realized >= realized*0.5 is always true).
    prior_eval = float(getattr(ledger, "prior_eval_profit", 0.0))
    return {
        "account_size": account_size,
        "equity": equity,
        "realized_pnl_today": realized,
        "unrealized_pnl": unreal,
        "total_eval_profit": prior_eval,
        "open_position": ledger.open_position,
        "trade_count_today": int(ledger.trade_count_today),
        "consecutive_losses": int(ledger.consecutive_losses),
        "session_date": now.date().isoformat(),
        "now_et": _iso_et(now),
    }


def _fallback_validate_account_state(state: dict) -> tuple:
    required_numeric = (
        "account_size", "equity", "realized_pnl_today", "unrealized_pnl",
        "total_eval_profit",
    )
    required_int = ("trade_count_today", "consecutive_losses")
    if not isinstance(state, dict):
        return False, "account_state_not_dict"
    for k in required_numeric:
        if k not in state:
            return False, f"missing:{k}"
        if not _is_finite_number(state[k]):
            return False, f"non_finite:{k}"
    for k in required_int:
        if k not in state or not isinstance(state[k], int) or state[k] < 0:
            return False, f"bad_int:{k}"
    if state["account_size"] <= 0:
        return False, "account_size_non_positive"
    return True, "ok"


# ── risk_guard fallback (§5) — pure-code mandate gate; never raises ───────────

class _FallbackRiskGuard:
    """Contract-conformant RiskGuard (§5) used until risk_guard.py lands.

    Enforces the mandate INDEPENDENTLY of any model output, in order, first
    failure decides. Returns ``{"decision","size","reason"}``. Never raises —
    invalid account_state => ``flat`` (fail-closed).
    """

    def __init__(self, mandate_view: Any, *, daily_gate_pct: float = 0.80,
                 consecutive_loss_limit: int = 3,
                 eod_flatten_et: time = time(15, 55),
                 session_open_et: time = time(8, 30),
                 kill_switch_check: Optional[Callable[[], bool]] = None):
        self.m = mandate_view
        self.daily_gate_pct = float(daily_gate_pct)
        self.consecutive_loss_limit = int(consecutive_loss_limit)
        self.eod_flatten_et = eod_flatten_et
        # Lower-bound trading-window guard (defense-in-depth). The PRIMARY
        # kill-zone gate lives in strategy.py (contract S3.3 step 1, 08:30–11:00
        # ET ny_open); this is a coarser floor so that, once a real strategy
        # lands, an out-of-window signal (e.g. 03:00 ET overnight) is still
        # rejected by the guard and cannot drive an order. It rejects NEW entries
        # before session_open_et; the EOD flatten still owns the upper bound.
        self.session_open_et = session_open_et
        self._kill = kill_switch_check or (lambda: False)

    def check(self, signal: dict, account_state: dict) -> dict:
        m = self.m
        # 1. kill switch
        try:
            if self._kill():
                return {"decision": "flat", "size": 0, "reason": "kill_switch"}
        except Exception:
            return {"decision": "flat", "size": 0, "reason": "kill_switch_check_error"}

        # 2. account state invalid/NaN => circuit breaker
        ok, why = _fallback_validate_account_state(account_state)
        if not ok:
            return {"decision": "flat", "size": 0, "reason": "invalid_account_state"}

        # 3. EOD flatten (no overnight holds). Consult mandate.overnight_holds
        #    EXPLICITLY: the no-overnight guarantee must not rest solely on the
        #    15:55 flatten. With overnight_holds == False (the mandate value),
        #    reaching the EOD flatten time forces flat. If a mandate ever set
        #    overnight_holds == True, the EOD flatten would NOT auto-flat here.
        now = self._parse_now(account_state)
        overnight_ok = bool(getattr(m, "overnight_holds", False))
        if now is not None:
            now_t = now.timetz().replace(tzinfo=None)
            # 3a. EOD upper bound — flat at/after EOD unless overnight is allowed.
            if not overnight_ok and now_t >= self.eod_flatten_et:
                return {"decision": "flat", "size": 0, "reason": "eod_flatten"}
            # 3b. Lower-bound trading-window floor (defense-in-depth). A NEW entry
            #     arriving before session open (e.g. 03:00 ET overnight) is
            #     rejected on time grounds even if a real strategy emitted it.
            #     Primary kill-zone enforcement still lives in strategy.py.
            if now_t < self.session_open_et:
                return {"decision": "reject", "size": 0, "reason": "outside_trading_window"}

        if not isinstance(signal, dict):
            return {"decision": "reject", "size": 0, "reason": "no_signal"}

        # 4. instrument allowlist
        if signal.get("instrument") not in m.instruments_allowed:
            return {"decision": "reject", "size": 0, "reason": "instrument_not_allowed"}

        # 5. side / price sanity
        if signal.get("side") not in ("long", "short"):
            return {"decision": "reject", "size": 0, "reason": "bad_side"}
        if not _is_finite_number(signal.get("entry")) or not _is_finite_number(signal.get("stop")):
            return {"decision": "reject", "size": 0, "reason": "bad_price"}
        if float(signal["stop"]) == float(signal["entry"]):
            return {"decision": "reject", "size": 0, "reason": "stop_equals_entry"}

        realized = float(account_state["realized_pnl_today"])
        unreal = float(account_state["unrealized_pnl"])
        total_eval = float(account_state["total_eval_profit"])

        # 6. daily trade cap
        if int(account_state["trade_count_today"]) >= m.daily_trade_cap:
            return {"decision": "reject", "size": 0, "reason": "daily_trade_cap"}

        # 7. hard max-loss limit => circuit breaker
        if (realized + unreal) <= -m.max_loss_limit:
            return {"decision": "flat", "size": 0, "reason": "max_loss_limit"}

        # 8. soft daily-loss gate => circuit breaker
        if realized <= -(m.max_loss_limit * self.daily_gate_pct):
            return {"decision": "flat", "size": 0, "reason": "daily_loss_gate"}

        # 9. consecutive losses => circuit breaker
        if int(account_state["consecutive_losses"]) >= self.consecutive_loss_limit:
            return {"decision": "flat", "size": 0, "reason": "consecutive_losses"}

        # 10. consistency 50%
        if total_eval > 0 and realized >= total_eval * m.consistency_rule_eval:
            return {"decision": "reject", "size": 0, "reason": "consistency_cap"}

        # 11. position-size clamp
        req = signal.get("size", 1)
        try:
            req = int(req)
        except Exception:
            req = 1
        clamped = max(1, min(req, m.max_position_size))
        reason = "ok" if clamped == req else f"size_clamped_{req}->{clamped}"
        return {"decision": "approve", "size": clamped, "reason": reason}

    @staticmethod
    def _parse_now(account_state: dict) -> Optional[datetime]:
        ts = account_state.get("now_et")
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(ts)
            return dt.astimezone(ET) if dt.tzinfo else dt.replace(tzinfo=ET)
        except Exception:
            return None


# ── traderpost fallback (§9) — DRY_RUN default; validate-before-send ──────────

class _FallbackTraderPostClient:
    """Contract-conformant TraderPostClient (§9) used until traderpost.py lands.

    DRY_RUN by default. A live POST requires BOTH ``HERMES_BOT_LIVE == "1"`` AND
    ``config.go_live is True`` (§9.1). Validate-before-send (§9.2) is applied
    independent of the caller. Secrets come from ``os.environ`` only and never
    reach a log line.
    """

    def __init__(self, config: Any, audit: Any, mandate_view: Any,
                 kill_switch_check: Optional[Callable[[], bool]] = None):
        self.config = config
        self.audit = audit
        self.mandate_view = mandate_view
        self._kill = kill_switch_check or (lambda: False)

    def _live_requested(self) -> bool:
        return (os.environ.get("HERMES_BOT_LIVE") == "1") and bool(getattr(self.config, "go_live", False))

    def send(self, signal: dict, *, approved_size: int) -> dict:
        m = self.mandate_view
        # validate-before-send (defense in depth; independent of caller/model)
        if self._kill():
            res = {"result": "rejected", "http_status": None, "reason": "kill_switch"}
            self.audit.log_order(signal=signal, mode="dry_run", result="rejected",
                                 http_status=None, webhook_host=None, reason="kill_switch")
            return res
        # A reduce-only MARKET CLOSE (flatten) carries no protective stop by
        # design — skip stop-geometry validation for it. Every NEW entry still
        # requires a finite stop != entry (validate-before-send §9.2).
        is_flatten = bool(signal.get("reduce_only")) or signal.get("order_type") == "market"
        if not is_flatten:
            stop = signal.get("stop")
            if not _is_finite_number(stop) or float(stop) == float(signal.get("entry", stop)):
                return self._reject(signal, "missing_or_invalid_stop")
        if not isinstance(approved_size, int) or approved_size < 1:
            return self._reject(signal, "invalid_size")
        if approved_size > m.max_position_size:
            return self._reject(signal, "size_over_cap")
        if signal.get("instrument") not in m.instruments_allowed:
            return self._reject(signal, "instrument_not_allowed")
        if signal.get("side") not in ("long", "short"):
            return self._reject(signal, "bad_side")

        live = self._live_requested()
        if not live:
            reason = "DRY_RUN: HERMES_BOT_LIVE!=1 or go_live=false"
            self.audit.log_order(signal={**signal, "size": approved_size},
                                 mode="dry_run", result="logged_only",
                                 http_status=None,
                                 webhook_host=self._host_only(),
                                 reason=reason)
            return {"result": "logged_only", "http_status": None, "reason": reason}

        # live path — the fallback intentionally does NOT POST. Real traderpost.py
        # owns the network call. Until it lands, refuse to send and stay safe.
        self.audit.log_order(signal={**signal, "size": approved_size},
                             mode="dry_run", result="logged_only",
                             http_status=None, webhook_host=self._host_only(),
                             reason="live_gate_open_but_traderpost_module_absent")
        return {"result": "logged_only", "http_status": None,
                "reason": "traderpost_module_absent_no_live_send"}

    def _reject(self, signal: dict, reason: str) -> dict:
        self.audit.log_order(signal=signal, mode="dry_run", result="rejected",
                             http_status=None, webhook_host=None, reason=reason)
        return {"result": "rejected", "http_status": None, "reason": reason}

    @staticmethod
    def _host_only() -> Optional[str]:
        url = os.environ.get("TRADERPOST_WEBHOOK_URL")
        if not url:
            return None
        try:
            from urllib.parse import urlparse
            return urlparse(url).hostname   # bare host only — never the full URL
        except Exception:
            return None


# ── killswitch fallback (§ runner step1 / S8) ─────────────────────────────────

def _fallback_kill_switch_engaged(mandate_view: Any) -> bool:
    """Only the literal mandate-declared ``KILL_SWITCH`` path triggers.

    ``KILL_SWITCH_DISABLED`` is explicitly NOT a trigger.
    """
    rel = getattr(mandate_view, "kill_switch_file", "./KILL_SWITCH")
    # removeprefix strips the literal leading "./" once; lstrip("./") is a CHAR
    # SET strip that would mangle other relative forms (e.g. ".KILL", "../x").
    p = (VIBE_DIR / rel.removeprefix("./")).resolve() if not Path(rel).is_absolute() else Path(rel)
    return p.exists()


# ── data_loader fallback (§7) — emits bars_by_tf or None ──────────────────────

def _fallback_build_bars_by_tf(source: Any,
                               timeframes=("1h", "15m", "5m", "1m")) -> Optional[dict]:
    """Build ``bars_by_tf`` from a 5M/1M CSV path (NinjaTrader/yfinance format).

    Returns None when no usable source is available (live feed not wired yet),
    which the runner treats as "no bars -> skip" rather than an error.
    """
    if source is None:
        return None
    try:
        import pandas as pd
    except Exception:
        return None
    path = Path(source)
    if not path.exists():
        return None
    df = _parse_csv_to_df(path)
    if df is None or df.empty:
        return None
    out: dict = {}
    rule_map = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    for tf in timeframes:
        rule = rule_map.get(tf)
        if rule is None:
            continue
        res = df.resample(rule).agg(agg).dropna(how="any")
        out[tf] = res
    return out


def _parse_csv_to_df(path: Path):
    """Parse NinjaTrader (Date,Time,...) or yfinance (Datetime,...) CSV to a
    tz-aware ET-indexed OHLCV DataFrame with lowercase columns (§7.1)."""
    import csv as _csv
    import pandas as pd
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = _csv.reader(f)
        for row in reader:
            if not row:
                continue
            first = row[0].strip().upper()
            if first in ("DATE", "DATETIME", ""):
                continue
            try:
                if len(row) >= 7 and row[1].strip().replace(":", "").isdigit() \
                        and len(row[0].strip()) == 8 and row[0].strip().isdigit():
                    # NinjaTrader: Date,Time,O,H,L,C,V (naive ET)
                    dt = datetime.strptime(
                        f"{row[0].strip()}{row[1].strip().zfill(6)}", "%Y%m%d%H%M%S")
                    dt = dt.replace(tzinfo=ET)
                    o, h, l, c = row[2], row[3], row[4], row[5]
                    v = row[6] if len(row) > 6 else 0
                else:
                    # yfinance: Datetime,O,H,L,C,V (offset-aware -> ET)
                    dt = pd.Timestamp(row[0]).to_pydatetime()
                    dt = dt.astimezone(ET) if dt.tzinfo else dt.replace(tzinfo=ET)
                    o, h, l, c = row[1], row[2], row[3], row[4]
                    v = row[5] if len(row) > 5 else 0
                rows.append((dt, float(o), float(h), float(l), float(c), float(v)))
            except (ValueError, IndexError, TypeError):
                continue
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close", "volume"])
    df = df.sort_values("dt").set_index("dt")
    df.index = pd.DatetimeIndex(df.index)
    return df


# ── strategy fallback (§3) — pure; returns None when not wired ────────────────

def _fallback_generate_signal(bars_by_tf: dict, now: datetime, *,
                              instrument: str = "ES", config: Any = None) -> Optional[dict]:
    """Fail-closed strategy stub. The real ``strategy.generate_signal`` (the
    pure TJR/ICT implementation) replaces this. Until then: emit NO setups so
    the runner never produces an order from a stub."""
    return None


# ──────────────────────────────────────────────────────────────────────────────
#  Resolve siblings: real module attribute if present, else the fallback above.
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_siblings() -> dict:
    mods = {
        "config": _try_import("config"),
        "mandate": _try_import("mandate"),
        "data_loader": _try_import("data_loader"),
        "strategy": _try_import("strategy"),
        "risk_guard": _try_import("risk_guard"),
        "traderpost": _try_import("traderpost"),
        "account": _try_import("account"),
        "audit": _try_import("audit"),
        "killswitch": _try_import("killswitch"),
        "paper_ledger": _try_import("paper_ledger"),
    }

    def pick(mod_name: str, attr: str, fallback):
        mod = mods.get(mod_name)
        return getattr(mod, attr, fallback) if mod else fallback

    return {
        "load_config": pick("config", "load_config", _fallback_load_config),
        "MandateView": pick("mandate", "MandateView", _FallbackMandateView),
        "build_bars_by_tf": pick("data_loader", "build_bars_by_tf", _fallback_build_bars_by_tf),
        "generate_signal": pick("strategy", "generate_signal", _fallback_generate_signal),
        "RiskGuard": pick("risk_guard", "RiskGuard", _FallbackRiskGuard),
        "TraderPostClient": pick("traderpost", "TraderPostClient", _FallbackTraderPostClient),
        "build_account_state": pick("account", "build_account_state", _fallback_build_account_state),
        "validate_account_state": pick("account", "validate_account_state", _fallback_validate_account_state),
        "AuditLogger": pick("audit", "AuditLogger", _FallbackAuditLogger),
        "kill_switch_engaged": pick("killswitch", "kill_switch_engaged", None),
        "paper_ledger_open_position": pick("paper_ledger", "open_position", None),
        "_real_modules": {k: (v is not None) for k, v in mods.items()},
    }


# ──────────────────────────────────────────────────────────────────────────────
#  BotContext + runner
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class BotContext:
    """Everything ``run_cycle`` needs. Built once in ``main``/``build_context``."""
    config: Any
    audit: Any
    ledger: _PaperLedger
    siblings: dict
    instrument: str = "ES"
    csv_source: Optional[str] = None          # CSV path for offline/forward-test bars
    halted: bool = False                       # circuit-breaker latch (session-sticky)
    halt_reason: str = ""

    # mandate is re-read each cycle, but cache the view for convenience
    def mandate_view(self):
        MandateView = self.siblings["MandateView"]
        return MandateView.from_file(MANDATE_FILE)

    def kill_engaged(self, mandate_view: Any) -> bool:
        fn = self.siblings.get("kill_switch_engaged")
        if callable(fn):
            try:
                # real killswitch.kill_switch_engaged() may take no args or a view
                try:
                    return bool(fn(mandate_view))
                except TypeError:
                    return bool(fn())
            except Exception:
                return True   # fail closed: if kill check errors, assume engaged
        return _fallback_kill_switch_engaged(mandate_view)


def build_context(*, instrument: Optional[str] = None,
                  csv_source: Optional[str] = None,
                  config_path: Optional[Path] = None) -> BotContext:
    """Assemble a BotContext with config, audit logger, paper ledger, siblings."""
    siblings = _resolve_siblings()
    config = siblings["load_config"](config_path)
    audit = siblings["AuditLogger"](getattr(config, "log_dir", LOG_DIR))
    inst = instrument or getattr(config, "instrument", "ES")
    ledger = _PaperLedger()

    # one-time startup banner noting which siblings are real vs fallback
    real = siblings["_real_modules"]
    fallbacks = sorted(k for k, present in real.items() if not present)
    audit.log_event(
        "runner_start",
        reason="boot",
        instrument=inst,
        dry_run_default=True,
        go_live=bool(getattr(config, "go_live", False)),
        hermes_bot_live=os.environ.get("HERMES_BOT_LIVE", "<unset>"),
        fallback_modules=fallbacks,
    )
    # Flag that the risk-bearing account source is the fallback paper ledger. It
    # NOW exercises the breakers via the OHLC paper fill/close simulation
    # (realized P&L + consecutive losses move on stop/TP fills), but it is a
    # minimal approximation (no intrabar order, slippage, or commission), NOT a
    # broker fill feed — a real account.py MUST replace it before any paper P&L
    # number is reported as truth. (The 'account' module being a fallback is the
    # signal here.)
    if not real.get("account") and not getattr(ledger, "is_risk_validating", False):
        audit.log_event(
            "paper_ledger_not_risk_validating",
            reason=("fallback _PaperLedger exercises risk_guard breakers via an OHLC "
                    "paper fill/close simulation (realized P&L + consecutive losses do "
                    "move on stop/TP fills), but it is a minimal approximation, NOT a "
                    "broker fill feed — replace with account.py before trusting paper P&L."),
        )
    return BotContext(config=config, audit=audit, ledger=ledger,
                      siblings=siblings, instrument=inst, csv_source=csv_source)


def _flatten(ctx: BotContext, mandate_view: Any, reason: str,
             mark_price: Optional[float] = None) -> None:
    """Flatten any open position through the validate-before-send path (or, in
    DRY_RUN, log the would-be flatten), then latch the session halt.

    For the paper ledger this is a forced MARKET CLOSE (EOD / kill / circuit
    breaker), so it books realized P&L at ``mark_price`` (the latest bar close)
    via ``record_close(...)`` — that way an EOD flatten of a losing/winning open
    position updates ``realized_pnl_today`` / ``consecutive_losses`` instead of
    silently vanishing. If no mark is known we fall back to the entry price (P&L
    == 0): a forced flatten must never crash and must never invent a P&L."""
    pos = ctx.ledger.open_position
    if pos:
        # Construct a REDUCE-ONLY MARKET-CLOSE exit, NOT a fresh stop-protected
        # entry in the opposite direction. A flatten must carry NO protective
        # stop: a stop on the original (entry) level is geometrically inverted
        # relative to the reversed exit side (long-flatten -> short exit, whose
        # valid stop would sit ABOVE entry, not at/below it). A real
        # traderpost.py that validates stop geometry could reject or mis-place
        # such a flatten. ``order_type="market"`` + ``reduce_only=True`` + no
        # ``stop`` tells the broker to close the position at market with no new
        # protective bracket. tp1/tp2 are likewise omitted.
        exit_side = "short" if pos["side"] == "long" else "long"
        exit_signal = {
            "side": exit_side,
            "instrument": pos["instrument"],
            "entry": pos["entry"],
            "stop": None,            # market close — no protective stop
            "tp1": None, "tp2": None,
            "size": pos["size"],
            "order_type": "market",
            "reduce_only": True,
            "reason": f"flatten:{reason}",
            "ts": _iso_et(),
        }
        try:
            client = _build_traderpost(ctx, mandate_view)
            client.send(exit_signal, approved_size=pos["size"])
        except Exception as e:  # never crash on flatten
            ctx.audit.log_event("flatten_error", reason=str(e))
        # Book the forced-close realized P&L on the paper ledger (mark at the
        # latest bar close, else entry => P&L 0). record_close() updates
        # realized_pnl_today + consecutive_losses and clears the position. For a
        # real account.py (broker-backed), record_close may be absent — fall back
        # to flatten() so we never crash and never double-count a broker fill.
        if isinstance(ctx.ledger, _PaperLedger):
            exit_px = mark_price if _is_finite_number(mark_price) else pos["entry"]
            realized = _paper_pnl(pos["side"], float(pos["entry"]), float(exit_px),
                                  int(pos["size"]), pos["instrument"])
            ctx.ledger.record_close(realized)
            ctx.audit.log_event("flatten_close", reason=f"flatten:{reason}",
                                exit_price=float(exit_px), realized=realized,
                                realized_pnl_today=ctx.ledger.realized_pnl_today,
                                consecutive_losses=ctx.ledger.consecutive_losses)
        else:
            ctx.ledger.flatten()
    ctx.audit.log_event("flatten", reason=reason, had_position=bool(pos))


def _client_takes_mandate(ctx: BotContext) -> bool:
    """The fallback TraderPostClient takes a mandate_view; a real one may not.
    Detect by signature to stay compatible with either."""
    import inspect
    Client = ctx.siblings["TraderPostClient"]
    try:
        params = inspect.signature(Client.__init__).parameters
        return "mandate_view" in params
    except (TypeError, ValueError):
        return False


def _halt(ctx: BotContext, mandate_view: Any, reason: str,
          mark_price: Optional[float] = None) -> None:
    """Trip the session circuit breaker: flatten + latch halted."""
    if not ctx.halted:
        ctx.audit.log_event("halt", reason=reason)
    ctx.halted = True
    ctx.halt_reason = reason
    _flatten(ctx, mandate_view, reason=reason, mark_price=mark_price)


def run_cycle(ctx: BotContext) -> dict:
    """One evaluation cycle (§10). Returns a summary dict (tests/dashboards).

    FIXED order of operations:
      1. kill switch? -> flatten + audit 'halt'; return.
      2. mandate_view = MandateView.from_file()      (runtime re-read)
      3. bars_by_tf = data_loader.build_bars_by_tf(...)
      4. account_state = account.build_account_state(...); validate
         invalid -> 'flat' -> audit -> flatten -> return  (circuit breaker)
      5. signal = strategy.generate_signal(...); None -> audit skip; return.
      6. result = risk_guard.check(signal, account_state)  (MANDATORY gate)
         audit.log_decision(...)  ALWAYS, regardless of decision.
      7. approve -> traderpost.send(...) (DRY_RUN default) + log_order;
         else no order (reject/flat already audited).
    """
    sib = ctx.siblings
    now = now_et()
    summary: dict = {"ts": _iso_et(now), "instrument": ctx.instrument,
                     "halted": ctx.halted, "decision": None, "order": None}

    # 2. mandate (runtime re-read) — read FIRST so kill-switch path is known
    mandate_view = ctx.mandate_view()
    if getattr(mandate_view, "is_fallback", False):
        ctx.audit.log_event("mandate_fallback", reason="lucid_mandate.json missing/unreadable; DRY_RUN forced")

    # If we're already halted for the session, do nothing but re-affirm flat.
    if ctx.halted:
        summary["decision"] = "flat"
        summary["reason"] = f"session_halted:{ctx.halt_reason}"
        return summary

    # 1. kill switch
    if ctx.kill_engaged(mandate_view):
        _halt(ctx, mandate_view, reason="kill_switch")
        summary.update(decision="flat", reason="kill_switch", halted=True)
        return summary

    # 3. bars
    build_bars = sib["build_bars_by_tf"]
    try:
        bars_by_tf = build_bars(ctx.csv_source) if ctx.csv_source is not None else build_bars(None)
    except Exception as e:
        ctx.audit.log_event("data_error", reason=str(e))
        bars_by_tf = None
    if not bars_by_tf:
        result = {"decision": "skip", "size": 0, "reason": "no_bars"}
        ctx.audit.log_decision(signal=None, result=result, account_state={},
                               stage="data_loader", dry_run=True, live_send=False)
        summary.update(decision="skip", reason="no_bars")
        return summary

    # 4. account state + validation (circuit breaker on invalid/NaN)
    ctx.ledger.roll_session(now.date())

    # 4a. PAPER FILL/CLOSE SIMULATION (DRY_RUN only). Before snapshotting the
    #     account, settle any open paper position against the latest bar: a touched
    #     stop/TP1/TP2 books realized P&L into the ledger so realized_pnl_today and
    #     consecutive_losses MOVE — which is what makes risk_guard's daily-loss /
    #     max-loss / consecutive-loss breakers actually trip in paper mode. Guarded
    #     to the fallback _PaperLedger (a real account.py gets fills from the broker,
    #     so we must NOT double-count there). Never raises (best-effort sim).
    if isinstance(ctx.ledger, _PaperLedger):
        try:
            fill = _paper_manage_position(ctx.ledger, bars_by_tf)
        except Exception as e:
            ctx.audit.log_event("paper_fill_error", reason=str(e))
            fill = None
        if fill:
            ctx.audit.log_event(
                "paper_fill", reason=f"paper_close:{fill.get('exit')}",
                exit=fill.get("exit"), price=fill.get("price"),
                size=fill.get("size"), realized=fill.get("realized"),
                realized_pnl_today=ctx.ledger.realized_pnl_today,
                consecutive_losses=ctx.ledger.consecutive_losses,
            )

    account_state = sib["build_account_state"](ctx.ledger, mandate_view, now)
    ok, why = sib["validate_account_state"](account_state)
    if not ok:
        result = {"decision": "flat", "size": 0, "reason": "invalid_account_state"}
        ctx.audit.log_decision(signal=None, result=result, account_state=account_state,
                               stage="account", dry_run=True, live_send=False)
        _halt(ctx, mandate_view, reason=f"invalid_account_state:{why}")
        summary.update(decision="flat", reason="invalid_account_state", halted=True)
        return summary

    # 5. strategy (pure; None => no setup)
    try:
        signal = sib["generate_signal"](bars_by_tf, now, instrument=ctx.instrument,
                                        config=getattr(ctx.config, "strategy", None))
    except Exception as e:
        ctx.audit.log_event("strategy_error", reason=str(e))
        signal = None
    if signal is None:
        result = {"decision": "skip", "size": 0, "reason": "no_setup"}
        ctx.audit.log_decision(signal=None, result=result, account_state=account_state,
                               stage="strategy", dry_run=True, live_send=False)
        summary.update(decision="skip", reason="no_setup")
        return summary

    # sanitize external/file-sourced signal into the locked schema (S5) before gate
    signal = _sanitize_signal(signal, default_instrument=ctx.instrument)

    # 6. risk_guard — MANDATORY gate; build a fresh RiskGuard each cycle so it
    #    reads the freshly re-loaded mandate_view.
    guard = _build_risk_guard(ctx, mandate_view)
    result = guard.check(signal, account_state)
    live_requested = (os.environ.get("HERMES_BOT_LIVE") == "1") and bool(getattr(ctx.config, "go_live", False))
    ctx.audit.log_decision(signal=signal, result=result, account_state=account_state,
                           stage="risk_guard", dry_run=not live_requested,
                           live_send=bool(live_requested and result.get("decision") == "approve"))
    summary["decision"] = result.get("decision")
    summary["reason"] = result.get("reason")
    summary["approved_size"] = result.get("size", 0)

    decision = result.get("decision")
    if decision == "flat":
        # 'flat' from risk_guard means stand-down (kill/circuit/EOD) -> latch halt.
        # Mark any open paper position at the latest bar close so an EOD/circuit
        # flatten books realized P&L (don't invent a price if no bar is available).
        _bar = _latest_bar(bars_by_tf)
        mark = _bar["close"] if _bar else None
        _halt(ctx, mandate_view, reason=f"risk_guard_flat:{result.get('reason')}",
              mark_price=mark)
        summary["halted"] = True
        return summary
    if decision != "approve":
        # 'reject' — single signal breach; no order, already audited.
        return summary

    # 7. approved -> route order through traderpost (DRY_RUN by default)
    client = _build_traderpost(ctx, mandate_view)
    tp = client.send(signal, approved_size=result["size"])
    summary["order"] = tp

    # update the paper ledger so daily cap / position tracking advance
    if tp.get("result") in ("logged_only", "sent"):
        ctx.ledger.record_entry(
            instrument=signal["instrument"], side=signal["side"],
            size=result["size"], entry=signal["entry"], stop=signal["stop"],
            tp1=signal.get("tp1"), tp2=signal.get("tp2"),
        )
        # persist to disk too (ctx.ledger is in-memory, discarded when this
        # --once process exits) so trade_journal.jsonl actually accumulates
        # fills across cron invocations instead of staying permanently empty.
        open_position = ctx.siblings.get("paper_ledger_open_position")
        if tp.get("result") == "logged_only" and open_position is not None:
            try:
                open_position({**signal, "size": result["size"]})
            except Exception as e:
                ctx.audit.log_event("paper_ledger_open_error", reason=str(e))
        _send_telegram_alert(signal, approved_size=result["size"])
    return summary


def _build_risk_guard(ctx: BotContext, mandate_view: Any):
    RiskGuard = ctx.siblings["RiskGuard"]
    kwargs = dict(
        daily_gate_pct=getattr(ctx.config, "daily_gate_pct", 0.80),
        consecutive_loss_limit=getattr(ctx.config, "consecutive_loss_limit", 3),
        eod_flatten_et=getattr(ctx.config, "eod_flatten_et", time(15, 55)),
        session_open_et=getattr(ctx.config, "session_open_et", time(8, 30)),
    )
    # the fallback guard accepts a kill_switch_check; a real one may not
    import inspect
    try:
        params = inspect.signature(RiskGuard.__init__).parameters
        if "kill_switch_check" in params:
            kwargs["kill_switch_check"] = lambda: ctx.kill_engaged(mandate_view)
        # drop kwargs the real guard doesn't accept
        kwargs = {k: v for k, v in kwargs.items() if k in params}
    except (TypeError, ValueError):
        pass
    return RiskGuard(mandate_view, **kwargs)


def _build_traderpost(ctx: BotContext, mandate_view: Any):
    # mandate_view / kill_switch_check are keyword-only on the real TraderPostClient
    # (traderpost.py:241-244) — pass them by keyword, never positionally, or
    # construction raises TypeError the moment a signal is ever actually approved.
    Client = ctx.siblings["TraderPostClient"]
    kwargs: dict = {}
    if _client_takes_mandate(ctx):
        kwargs["mandate_view"] = mandate_view
    if _client_takes_kill(Client):
        kwargs["kill_switch_check"] = lambda: ctx.kill_engaged(mandate_view)
    return Client(ctx.config, ctx.audit, **kwargs)


def _client_takes_kill(Client) -> bool:
    import inspect
    try:
        return "kill_switch_check" in inspect.signature(Client.__init__).parameters
    except (TypeError, ValueError):
        return False


def _sanitize_signal(signal: dict, *, default_instrument: str = "ES") -> dict:
    """Coerce any externally-produced signal into the locked §2 schema (S5).

    Ensures the keys/types ``risk_guard`` and ``traderpost`` rely on exist and
    are well-formed BEFORE the signal can drive an order. Does NOT invent prices
    or relax checks — missing/invalid fields stay missing so the gate rejects.
    """
    if not isinstance(signal, dict):
        return {"side": None}
    # ALLOWLIST: copy ONLY the locked §2 schema keys. An external/file-sourced
    # signal (S5) may carry arbitrary extra fields (e.g. a 'note' smuggling a
    # token, or a nested 'payload'); those must NOT reach risk_guard or the
    # audit logs, where the _redact allowlist would not catch a secret hidden in
    # a non-standard key. Anything outside _SIGNAL_KEYS is dropped here.
    out = {k: signal[k] for k in _SIGNAL_KEYS if k in signal}
    out.setdefault("instrument", default_instrument)
    out.setdefault("size", 1)
    try:
        out["size"] = int(out["size"])
    except (TypeError, ValueError):
        out["size"] = 1
    out.setdefault("reason", "external_signal")
    out.setdefault("ts", _iso_et())
    # derive tp1/tp2 only if absent and entry/stop are valid (tp1=2R, tp2=4R)
    if _is_finite_number(out.get("entry")) and _is_finite_number(out.get("stop")) \
            and out.get("side") in ("long", "short"):
        entry, stop = float(out["entry"]), float(out["stop"])
        r = abs(entry - stop)
        sign = 1.0 if out["side"] == "long" else -1.0
        out.setdefault("tp1", entry + sign * 2.0 * r)
        out.setdefault("tp2", entry + sign * 4.0 * r)
    return out


# ──────────────────────────────────────────────────────────────────────────────
#  CLI / loop
# ──────────────────────────────────────────────────────────────────────────────

_STOP = {"flag": False}


def _install_signal_handlers(ctx: BotContext) -> None:
    def _handler(signum, frame):
        _STOP["flag"] = True
        ctx.audit.log_event("shutdown", reason=f"signal_{signum}")
    for s in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if s is not None:
            try:
                signal.signal(s, _handler)
            except (ValueError, OSError):
                pass   # not in main thread / unsupported platform


def run_loop(ctx: BotContext) -> None:
    """Poll ``run_cycle`` forever (until kill switch / circuit breaker / signal)."""
    interval = max(1, int(getattr(ctx.config, "poll_interval_sec", 10)))
    _install_signal_handlers(ctx)
    ctx.audit.log_event("loop_start", reason=f"interval={interval}s")
    while not _STOP["flag"]:
        try:
            summary = run_cycle(ctx)
        except Exception as e:   # never let one cycle kill the loop
            ctx.audit.log_event("cycle_error", reason=str(e))
            summary = {"decision": "error", "reason": str(e)}
        if ctx.halted:
            ctx.audit.log_event("loop_halted", reason=ctx.halt_reason)
            break
        # sleep in 1s slices so signals are responsive
        slept = 0
        while slept < interval and not _STOP["flag"]:
            _time.sleep(1)
            slept += 1
    ctx.audit.log_event("loop_stop", reason="halted" if ctx.halted else "signal_or_eof")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="TJR/ICT Lucid 25K trading bot runner (paper/DRY_RUN by default)")
    parser.add_argument("--once", action="store_true",
                        help="Run exactly one cycle, then exit.")
    parser.add_argument("--instrument", default=None,
                        help="Instrument symbol (ES/NQ/MES/MNQ). Default: config.")
    parser.add_argument("--csv", default=None,
                        help="CSV bar source for offline/forward-test mode.")
    parser.add_argument("--selftest", action="store_true",
                        help="Run the internal smoke test (no external data) and exit.")
    parser.add_argument("--config", default=None,
                        help="Path to a JSON config file (e.g. bot/live_config.json). "
                             "Must contain {\"go_live\": true} to arm the config half of the live gate. "
                             "The env half (HERMES_BOT_LIVE=1) must also be set. Both are required.")
    args = parser.parse_args(argv)

    if args.selftest:
        return 0 if _selftest() else 1

    config_path = Path(args.config) if args.config else None
    ctx = build_context(instrument=args.instrument, csv_source=args.csv,
                        config_path=config_path)
    if args.once:
        summary = run_cycle(ctx)
        ctx.audit.log_event("once_done", reason=str(summary.get("decision")),
                            summary_decision=summary.get("decision"),
                            summary_reason=summary.get("reason"))
        return 0
    run_loop(ctx)
    return 0


# ──────────────────────────────────────────────────────────────────────────────
#  Smoke test (self-contained; no network, no real bars, paper-only)
# ──────────────────────────────────────────────────────────────────────────────

def _selftest() -> bool:
    """Exercise the fail-closed paths and the approve path with a synthetic
    signal, asserting DRY_RUN safety and risk_guard gating. Returns True on pass.
    """
    import tempfile
    ok = True

    def check(cond: bool, label: str):
        nonlocal ok
        status = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"  [{status}] {label}")

    with tempfile.TemporaryDirectory() as td:
        log_dir = Path(td) / "logs"
        audit = _FallbackAuditLogger(log_dir)
        config = _FallbackBotConfig()
        mandate_view = _FallbackMandateView.from_file(MANDATE_FILE)

        # mandate read from the real lucid_mandate.json (not hardcoded here)
        check(mandate_view.max_position_size in (1, 2), "mandate loaded (max_position_size)")
        check(mandate_view.account_size == 25000.0, "mandate account_size=25000")

        # build a paper context manually (avoid touching real logs/ dir)
        ledger = _PaperLedger()
        ledger.roll_session(now_et().date())
        ctx = BotContext(config=config, audit=audit, ledger=ledger,
                         siblings=_resolve_siblings(), instrument="ES")
        # force fallbacks for a hermetic test regardless of sibling presence
        ctx.siblings.update({
            "MandateView": _FallbackMandateView,
            "RiskGuard": _FallbackRiskGuard,
            "TraderPostClient": _FallbackTraderPostClient,
            "build_account_state": _fallback_build_account_state,
            "validate_account_state": _fallback_validate_account_state,
            "generate_signal": _fallback_generate_signal,
            "build_bars_by_tf": _fallback_build_bars_by_tf,
            "kill_switch_engaged": None,
        })
        ctx.audit = audit

        # account state validity
        state = _fallback_build_account_state(ledger, mandate_view, now_et())
        good, _ = _fallback_validate_account_state(state)
        check(good, "valid account_state passes validator")
        bad = dict(state); bad["equity"] = float("nan")
        bad_ok, _ = _fallback_validate_account_state(bad)
        check(not bad_ok, "NaN account_state rejected (circuit breaker)")

        # risk_guard: synthetic approvable signal during NY-open kill zone
        sig = {
            "side": "long", "instrument": "ES",
            "entry": 4750.25, "stop": 4744.25,
            "tp1": 4762.25, "tp2": 4774.25,
            "size": 5, "reason": "selftest", "ts": _iso_et(),
        }
        # use a fixed pre-EOD ET timestamp so the EOD gate doesn't fire
        state_pre_eod = dict(state)
        state_pre_eod["now_et"] = datetime(2026, 6, 21, 9, 42, tzinfo=ET).isoformat()
        guard = _FallbackRiskGuard(mandate_view, kill_switch_check=lambda: False)
        res = guard.check(sig, state_pre_eod)
        check(res["decision"] == "approve", "valid signal approved")
        check(res["size"] <= mandate_view.max_position_size, "size clamped to mandate cap")

        # instrument off allowlist -> reject
        bad_inst = dict(sig); bad_inst["instrument"] = "BTC"
        check(guard.check(bad_inst, state_pre_eod)["decision"] == "reject",
              "off-allowlist instrument rejected")

        # missing stop -> reject
        no_stop = dict(sig); no_stop["stop"] = float("nan")
        check(guard.check(no_stop, state_pre_eod)["decision"] == "reject",
              "missing/NaN stop rejected")

        # kill switch engaged -> flat
        guard_kill = _FallbackRiskGuard(mandate_view, kill_switch_check=lambda: True)
        check(guard_kill.check(sig, state_pre_eod)["decision"] == "flat",
              "kill switch -> flat")

        # EOD gate -> flat
        state_eod = dict(state); state_eod["now_et"] = datetime(2026, 6, 21, 15, 56, tzinfo=ET).isoformat()
        check(guard.check(sig, state_eod)["decision"] == "flat", "post-15:55 ET -> flat")

        # lower-bound trading-window floor -> reject (defense-in-depth)
        state_overnight = dict(state); state_overnight["now_et"] = datetime(2026, 6, 21, 3, 0, tzinfo=ET).isoformat()
        check(guard.check(sig, state_overnight)["decision"] == "reject",
              "03:00 ET (pre-session) -> reject (outside_trading_window)")

        # consistency denominator excludes today's realized: first-day profit not blocked
        led_fresh = _PaperLedger(); led_fresh.roll_session(now_et().date())
        led_fresh.record_close(900.0)   # realized today > 0, no prior eval profit
        st_fresh = _fallback_build_account_state(led_fresh, mandate_view, now_et())
        st_fresh["now_et"] = datetime(2026, 6, 21, 9, 42, tzinfo=ET).isoformat()
        check(st_fresh["total_eval_profit"] == 0.0, "fresh-process total_eval_profit excludes today")
        check(guard.check(sig, st_fresh)["decision"] in ("approve",),
              "first-day profit not blocked by consistency cap")

        # paper ledger fill simulation drives consecutive-loss breaker
        led_loss = _PaperLedger(); led_loss.roll_session(now_et().date())
        for _ in range(3):
            led_loss.record_close(-100.0)
        st_loss = _fallback_build_account_state(led_loss, mandate_view, now_et())
        st_loss["now_et"] = datetime(2026, 6, 21, 9, 42, tzinfo=ET).isoformat()
        check(guard.check(sig, st_loss)["decision"] == "flat",
              "3 consecutive losses -> flat (breaker trips from fills)")

        # _sanitize_signal allowlists §2 keys: extra/secret field dropped
        dirty = {"side": "long", "instrument": "ES", "entry": 4750.25, "stop": 4744.25,
                 "size": 1, "note": "BEARER_TOKEN_LEAK", "payload": {"k": "v"}}
        clean = _sanitize_signal(dirty, default_instrument="ES")
        check("note" not in clean and "payload" not in clean,
              "_sanitize_signal drops non-schema keys (no token smuggling)")
        check(set(clean).issubset(set(_SIGNAL_KEYS)),
              "_sanitize_signal output restricted to §2 schema keys")

        # flatten exit is a reduce-only market close with NO protective stop
        led_pos = _PaperLedger(); led_pos.roll_session(now_et().date())
        led_pos.record_entry("ES", "long", 1, 4750.25, 4744.25)
        ctx_flat = BotContext(config=config, audit=audit, ledger=led_pos,
                              siblings=_resolve_siblings(), instrument="ES")
        ctx_flat.siblings["TraderPostClient"] = _FallbackTraderPostClient
        ctx_flat.siblings["kill_switch_engaged"] = None
        _flatten(ctx_flat, mandate_view, reason="selftest_flatten")
        check(led_pos.open_position is None, "flatten clears open position (market close accepted)")

        # traderpost: DRY_RUN by default (no env, no go_live) -> logged_only, never POST
        client = _FallbackTraderPostClient(config, audit, mandate_view,
                                           kill_switch_check=lambda: False)
        approved = dict(sig); approved["size"] = res["size"]
        tp = client.send(approved, approved_size=res["size"])
        check(tp["result"] == "logged_only", "DRY_RUN traderpost logs only (no POST)")
        check(tp["http_status"] is None, "DRY_RUN http_status is None")

        # traderpost: even with go_live but HERMES_BOT_LIVE unset -> still DRY_RUN
        config_live = _FallbackBotConfig(go_live=True)
        os.environ.pop("HERMES_BOT_LIVE", None)
        client_live = _FallbackTraderPostClient(config_live, audit, mandate_view)
        tp2 = client_live.send(approved, approved_size=res["size"])
        check(tp2["result"] in ("logged_only",), "go_live but env off -> still DRY_RUN")

        # traderpost validate-before-send: oversize rejected
        over = dict(sig)
        tp3 = client.send(over, approved_size=mandate_view.max_position_size + 5)
        check(tp3["result"] == "rejected", "oversize order rejected before send")

        # run_cycle end-to-end with no bars -> skip (audited), not crash
        summary = run_cycle(ctx)
        check(summary["decision"] in ("skip", "flat"), "run_cycle no-bars -> skip/flat (no crash)")

        # logs exist + contain NO secret
        os.environ["TRADERPOST_WEBHOOK_URL"] = "https://traderpost.io/webhook/SECRETTOKEN123"
        os.environ["TRADERPOST_SECRET"] = "SUPERSECRET"
        client2 = _FallbackTraderPostClient(config, audit, mandate_view)
        client2.send(approved, approved_size=res["size"])
        orders_text = (log_dir / "orders.jsonl").read_text(encoding="utf-8")
        check("SECRETTOKEN123" not in orders_text and "SUPERSECRET" not in orders_text,
              "no secret leaks into order log")
        check("traderpost.io" in orders_text, "redacted host present in order log")
        os.environ.pop("TRADERPOST_WEBHOOK_URL", None)
        os.environ.pop("TRADERPOST_SECRET", None)

        decisions_exist = (log_dir / "decisions.jsonl").exists()
        check(decisions_exist, "decisions.jsonl written")

    print("\nSMOKE TEST:", "ALL PASS" if ok else "FAILURES")
    return ok


if __name__ == "__main__":
    raise SystemExit(main())
