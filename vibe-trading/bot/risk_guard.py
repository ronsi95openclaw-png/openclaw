#!/usr/bin/env python3
"""
risk_guard.py — Independent Risk Layer for the TJR/ICT Lucid 25K Bot
====================================================================
LOCKED CONTRACT: vibe-trading/bot/ARCHITECTURE.md §5 (RiskGuard).

This module enforces the Lucid 25K mandate in PURE CODE, completely independent
of any model output. Every order path MUST call ``RiskGuard.check(...)`` BEFORE
an order can be constructed or sent. On any rule breach the decision is
``'reject'`` (this signal is bad) or ``'flat'`` (stand down / flatten posture),
and NO order is produced.

Hard safety invariants honored here (ARCHITECTURE.md §0 / §12):
  - All Lucid limits are READ AT RUNTIME from ``lucid_mandate.json`` via a
    ``MandateView`` — no limit number is ever hardcoded. (S1)
  - Circuit breaker: halt + flatten on the daily-loss gate, hard max-loss,
    consecutive losses, invalid/NaN account state, or kill switch. (S2)
  - Kill switch: presence of the mandate-declared ``KILL_SWITCH`` file => 'flat'.
    The currently-present ``KILL_SWITCH_DISABLED`` is NOT a trigger. (S8)
  - Audit EVERY decision (approve/reject/flat) with timestamp + reason. (S6)
  - ``check`` NEVER raises on bad input; invalid account_state => 'flat'
    (fail-closed).

Decision schema returned by ``check`` (LOCKED):
    { "decision": 'approve'|'reject'|'flat', "size": int, "reason": str }

Numbers come from the mandate; operational tunables (``daily_gate_pct``,
``consecutive_loss_limit``, ``eod_flatten_et``) default to the strategy spec's
80% / 3-losses / 15:55-ET values.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Optional

# ── Paths (resolve relative to vibe-trading/) ────────────────────────────────
# risk_guard.py lives in vibe-trading/bot/ ; vibe-trading/ is its parent.parent.
BOT_DIR = Path(__file__).resolve().parent          # vibe-trading/bot/
VIBE_DIR = BOT_DIR.parent                           # vibe-trading/
MANDATE_FILE = VIBE_DIR / "lucid_mandate.json"
LOG_DIR = BOT_DIR / "logs"
DECISIONS_LOG = LOG_DIR / "decisions.jsonl"


# ── Mandate Loader (mirrors the backtest load_mandate() pattern) ─────────────

def load_mandate(path: "Optional[Path]" = None) -> dict:
    """Read ``lucid_mandate.json`` and return the raw JSON dict.

    Re-read at runtime so edits to the mandate take effect without a restart.
    A fallback default is returned ONLY if the file is missing/unreadable, so a
    transiently-absent file does not crash the risk layer (the caller stays in
    DRY_RUN and should log a ``mandate_fallback`` event per ARCHITECTURE.md §6).

    Args:
        path: optional override path; defaults to ``vibe-trading/lucid_mandate.json``.

    Returns:
        The parsed mandate dict (with a ``rules`` key).
    """
    p = Path(path) if path is not None else MANDATE_FILE
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    # Fallback defaults (match the known Lucid 25K eval rules) — used only to
    # avoid a crash if the file is briefly missing. The file is authoritative.
    return {
        "rules": {
            "account_size": 25000,
            "max_loss_limit": 1000,
            "profit_target": 1250,
            "consistency_rule_eval": 0.50,
            "overnight_holds": False,
            "close_eod": True,
            "instruments_allowed": ["ES", "MES", "NQ", "MNQ"],
            "max_position_size": 2,
            "daily_trade_cap": 10,
            "flatten_trigger_et": "16:30",
        },
        "kill_switch": {"file": "./KILL_SWITCH", "auto_flatten_on_kill": True},
        "mode": "paper",
        "_fallback": True,
    }


@dataclass(frozen=True)
class MandateView:
    """Typed, read-only projection of the Lucid mandate (ARCHITECTURE.md §6).

    Built from ``lucid_mandate.json`` at runtime. NO limit is baked in as a live
    source of truth; this view simply reflects the file. Re-build each cycle (or
    on mtime change) so edits to the mandate take effect without a restart.
    """

    account_size: float
    max_loss_limit: float
    consistency_rule_eval: float
    overnight_holds: bool
    close_eod: bool
    instruments_allowed: tuple
    max_position_size: int
    daily_trade_cap: int
    kill_switch_file: str            # resolved absolute path vs vibe-trading/
    auto_flatten_on_kill: bool
    mode: str                        # mandate's own mode flag ('paper')
    is_fallback: bool = False        # True if loaded from the crash-avoidance default
    profit_target: "Optional[float]" = None       # informational; not a risk gate
    flatten_trigger_et: "Optional[str]" = None    # "HH:MM"; internal safety-buffer flatten

    @classmethod
    def from_dict(cls, mandate: dict) -> "MandateView":
        """Build a MandateView from an already-parsed mandate dict."""
        rules = mandate.get("rules", {})
        ks = mandate.get("kill_switch", {}) or {}
        raw_ks_file = ks.get("file", "./KILL_SWITCH")
        # Resolve the kill-switch path relative to vibe-trading/ (ARCHITECTURE.md §0.3).
        ks_path = Path(raw_ks_file)
        if not ks_path.is_absolute():
            ks_path = (VIBE_DIR / raw_ks_file).resolve()
        profit_target = rules.get("profit_target")
        return cls(
            account_size=float(rules.get("account_size", 25000)),
            max_loss_limit=float(rules.get("max_loss_limit", 1000)),
            consistency_rule_eval=float(rules.get("consistency_rule_eval", 0.50)),
            overnight_holds=bool(rules.get("overnight_holds", False)),
            close_eod=bool(rules.get("close_eod", True)),
            instruments_allowed=tuple(rules.get("instruments_allowed",
                                                ["ES", "MES", "NQ", "MNQ"])),
            max_position_size=int(rules.get("max_position_size", 2)),
            daily_trade_cap=int(rules.get("daily_trade_cap", 10)),
            kill_switch_file=str(ks_path),
            auto_flatten_on_kill=bool(ks.get("auto_flatten_on_kill", True)),
            mode=str(mandate.get("mode", "paper")),
            is_fallback=bool(mandate.get("_fallback", False)),
            profit_target=float(profit_target) if profit_target is not None else None,
            flatten_trigger_et=rules.get("flatten_trigger_et"),
        )

    @classmethod
    def from_file(cls, path: "Optional[Path]" = None) -> "MandateView":
        """Load + project the mandate file (re-read at runtime)."""
        return cls.from_dict(load_mandate(path))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_finite_number(x) -> bool:
    """True iff x is an int/float that is finite (not NaN, not +/-inf)."""
    if isinstance(x, bool):  # bool is an int subclass; treat as non-numeric here
        return False
    if not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _parse_et_time(now_et) -> "Optional[time]":
    """Extract a ``datetime.time`` from an account_state ``now_et`` value.

    Accepts a ``datetime``, a ``time``, or an ISO-8601 string. Returns None if it
    cannot be parsed. A None result is treated as a CIRCUIT BREAKER (fail-closed):
    ``_validate_account_state`` requires ``now_et`` to be present AND parseable, so
    an unparseable clock yields ``'flat'`` (invalid_account_state) rather than
    silently disabling the EOD/overnight gate. The EOD check itself also re-parses
    and refuses to approve when the time is unparseable.
    """
    if now_et is None:
        return None
    if isinstance(now_et, datetime):
        return now_et.timetz() if now_et.tzinfo else now_et.time()
    if isinstance(now_et, time):
        return now_et
    if isinstance(now_et, str):
        s = now_et.strip()
        try:
            return datetime.fromisoformat(s).time()
        except ValueError:
            # Try a bare time string like "15:55" or "15:55:00".
            for fmt in ("%H:%M:%S", "%H:%M"):
                try:
                    return datetime.strptime(s, fmt).time()
                except ValueError:
                    continue
    return None


def _now_et_iso() -> str:
    """Best-effort ISO-8601 ET timestamp for audit records.

    Uses the IANA ``America/New_York`` zone when available; otherwise falls back
    to a UTC timestamp tagged so it's never silently misread as ET.
    """
    try:
        from zoneinfo import ZoneInfo  # py>=3.9
        return datetime.now(ZoneInfo("America/New_York")).isoformat()
    except Exception:  # pragma: no cover - zoneinfo/tzdata missing
        return datetime.now(timezone.utc).isoformat() + " (UTC-fallback)"


# ── Risk Guard (LOCKED API — ARCHITECTURE.md §5) ─────────────────────────────

class RiskGuard:
    """Pure-code mandate enforcement + circuit breaker.

    ``check(signal, account_state)`` is the single gate every order path must
    pass. It reads the live mandate via ``MandateView`` and returns exactly one
    of ``approve`` / ``reject`` / ``flat`` with a clamped size and a reason
    string. It never raises on bad input (fail-closed to ``flat``) and audits
    every call.
    """

    def __init__(
        self,
        mandate_view: "MandateView",
        *,
        daily_gate_pct: float = 0.80,
        consecutive_loss_limit: int = 3,
        eod_flatten_et: "time" = time(15, 55),
        audit_log: "Optional[Path]" = None,
    ) -> None:
        """Construct the guard.

        Args:
            mandate_view: live projection of ``lucid_mandate.json``. Hard numbers
                (max_loss_limit, max_position_size, daily_trade_cap,
                consistency_rule_eval, instruments_allowed) ALWAYS come from here.
            daily_gate_pct: soft daily-loss gate as a fraction of ``max_loss_limit``
                (default 0.80 => -$800 of $1,000). Operational tunable.
            consecutive_loss_limit: consecutive-loss circuit-breaker threshold.
            eod_flatten_et: end-of-day flatten time in ET (default 15:55).
            audit_log: override path for the decisions JSONL (defaults to
                ``bot/logs/decisions.jsonl``).
        """
        self.mandate = mandate_view
        self.daily_gate_pct = float(daily_gate_pct)
        self.consecutive_loss_limit = int(consecutive_loss_limit)
        self.eod_flatten_et = eod_flatten_et
        self._audit_log = Path(audit_log) if audit_log is not None else DECISIONS_LOG
        # Count of decisions whose audit record failed to write (S6 detectability).
        self.audit_failures = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def check(self, signal: dict, account_state: dict) -> dict:
        """Enforce the mandate against one signal + account snapshot.

        Returns a dict ``{"decision", "size", "reason"}`` where decision is:
          - ``'approve'`` — order may proceed at the returned (possibly clamped) size.
          - ``'reject'``  — rule breach on THIS signal; no order this cycle.
          - ``'flat'``    — stand down / flatten posture (kill switch, circuit
                            breaker, EOD); no new order.

        NEVER raises: any unexpected error or invalid ``account_state`` produces
        a ``'flat'`` decision (fail-closed). Every call is audit-logged.
        """
        try:
            result = self._evaluate(signal, account_state)
        except Exception as exc:  # pragma: no cover - defensive fail-closed
            result = {
                "decision": "flat",
                "size": 0,
                "reason": f"internal_error:{type(exc).__name__}",
            }
        # Audit EVERY decision (ARCHITECTURE.md §0.6 / S6). Logging must never crash
        # the order gate, but a FAILED audit must not be fully silent either: a
        # missing decision record is an S6 breach and has to be detectable. We
        # increment a failure counter and emit to stderr instead of swallowing.
        try:
            self._audit(signal, account_state, result)
        except Exception as exc:  # logging must not crash the gate, but surface it
            self.audit_failures += 1
            try:
                import sys
                print(
                    f"[risk_guard] AUDIT FAILURE (#{self.audit_failures}): "
                    f"decision record NOT written ({type(exc).__name__}: {exc}) "
                    f"— S6 audit guarantee unverifiable for this decision.",
                    file=sys.stderr,
                )
            except Exception:  # pragma: no cover - stderr itself unavailable
                pass
        return result

    # ── Evaluation (checks in LOCKED order — first failure decides) ───────────

    def _evaluate(self, signal: dict, account_state: dict) -> dict:
        m = self.mandate

        # Coerce to dicts defensively so attribute access below cannot raise.
        signal = signal if isinstance(signal, dict) else {}
        acct = account_state if isinstance(account_state, dict) else None

        # 1. Kill switch present => flat (circuit breaker / S8).
        if self._kill_switch_present():
            return self._flat("kill_switch")

        # 2. account_state invalid / NaN => flat (circuit breaker / S2).
        ok, why = self._validate_account_state(acct)
        if not ok:
            return self._flat(f"invalid_account_state:{why}")

        # 3. EOD flatten — no overnight holds. now_et.time() >= 15:55 ET => flat.
        #    FAIL-CLOSED: an unparseable now_et must NOT disable the EOD gate.
        #    (_validate_account_state already requires now_et to be parseable, so
        #    this branch is defence-in-depth: if for any reason the clock is bad
        #    here, we stand down instead of approving an entry at an unknown hour.)
        now_t = _parse_et_time(acct.get("now_et"))
        if now_t is None:
            return self._flat("invalid_account_state:invalid_now_et")
        if self._time_ge(now_t, self.eod_flatten_et):
            return self._flat("eod_flatten")
        # Belt-and-suspenders: mandate forbids overnight and requires EOD close.
        # The runner owns the actual flatten: a 'flat' decision at/after the EOD
        # cutoff MUST be consumed by runner.run_cycle (ARCHITECTURE.md §10 step 4/7)
        # as "flatten any open position", not merely "skip a new entry" — otherwise
        # an open position could be held past 16:00 ET (overnight_holds=false).

        # 4. Instrument allowlist (from mandate) => reject if off-list.
        instrument = signal.get("instrument")
        if instrument not in m.instruments_allowed:
            return self._reject(
                f"instrument_not_allowed:{instrument!r} "
                f"(allowed={list(m.instruments_allowed)})"
            )

        # 5. Side sanity + finite entry/stop => reject.
        side = signal.get("side")
        if side not in ("long", "short"):
            return self._reject(f"bad_side:{side!r}")
        entry = signal.get("entry")
        stop = signal.get("stop")
        if not _is_finite_number(entry) or not _is_finite_number(stop):
            return self._reject("non_finite_entry_or_stop")
        if float(entry) == float(stop):
            return self._reject("stop_equals_entry")

        # 6. Daily trade cap (from mandate) => reject.
        trade_count = int(acct.get("trade_count_today", 0))
        if trade_count >= m.daily_trade_cap:
            return self._reject(f"daily_trade_cap ({m.daily_trade_cap})")

        realized = float(acct.get("realized_pnl_today", 0.0))
        unrealized = float(acct.get("unrealized_pnl", 0.0))

        # 7. Hard max-loss limit (from mandate) => flat (circuit breaker).
        #    Combined realized + unrealized at-or-below -max_loss_limit halts.
        if (realized + unrealized) <= -m.max_loss_limit:
            return self._flat(f"max_loss_limit (-${m.max_loss_limit:.0f})")

        # 8. Soft daily-loss gate (80% of max_loss by default) => flat.
        #    Uses the SAME combined (realized + unrealized) exposure as the hard
        #    gate above so it is strictly tighter, never looser. The strategy spec
        #    (strategies/tjr_lucid_strategy.md §148) defines the gate on "account
        #    P&L on the day", i.e. INCLUDING open positions. Using realized-only
        #    here would let a losing open position + a small realized loss stack a
        #    new entry up to the hard wall, defeating the early-warning purpose.
        gate = m.max_loss_limit * self.daily_gate_pct
        if (realized + unrealized) <= -gate:
            return self._flat(f"daily_loss_gate (-${gate:.0f})")

        # 9. Consecutive losses => flat (circuit breaker).
        consec = int(acct.get("consecutive_losses", 0))
        if consec >= self.consecutive_loss_limit:
            return self._flat(f"consecutive_losses ({consec}>={self.consecutive_loss_limit})")

        # 10. Consistency 50% cap (from mandate) => reject.
        #     If running eval profit is positive and today's realized profit is
        #     already >= 50% of it, stop adding for the day.
        total_eval = float(acct.get("total_eval_profit", 0.0))
        if total_eval > 0 and realized >= total_eval * m.consistency_rule_eval:
            return self._reject(
                f"consistency_cap ({m.consistency_rule_eval:.0%} of ${total_eval:.0f})"
            )

        # 11. Position-size clamp — only ever clamp DOWN to the mandate cap.
        #     A non-positive requested size is MALFORMED input (ARCHITECTURE.md §2:
        #     size>=1 by contract), so it is REJECTED — never floored up to 1. A
        #     bad/0/-N size must not be coerced into a live 1-contract order.
        requested = signal.get("size", 1)
        try:
            requested_i = int(requested)
        except (TypeError, ValueError):
            return self._reject(f"non_positive_size:{requested!r}")
        if requested_i < 1:
            return self._reject(f"non_positive_size:{requested_i}")
        clamped = min(requested_i, m.max_position_size)  # clamp DOWN only
        if clamped != requested_i:
            reason = (f"ok (size_clamped {requested_i}->{clamped} "
                      f"cap={m.max_position_size})")
        else:
            reason = "ok"

        return {"decision": "approve", "size": clamped, "reason": reason}

    # ── Sub-checks ────────────────────────────────────────────────────────────

    def _kill_switch_present(self) -> bool:
        """True iff the mandate-declared literal KILL_SWITCH file exists.

        Only the literal path resolves here; ``KILL_SWITCH_DISABLED`` (the switch
        being OFF) does NOT match because the mandate file points at the exact
        ``KILL_SWITCH`` name (ARCHITECTURE.md §0.3).
        """
        try:
            return Path(self.mandate.kill_switch_file).exists()
        except OSError:  # pragma: no cover - defensive
            return False

    def _validate_account_state(self, state) -> "tuple[bool, str]":
        """Validate the account snapshot. False => circuit breaker ('flat').

        Flags: missing dict, missing required numeric field, non-finite (NaN/inf)
        value, or impossible negatives (negative trade/loss counters).
        """
        if not isinstance(state, dict):
            return False, "not_a_dict"

        required_numeric = (
            "realized_pnl_today",
            "unrealized_pnl",
            "total_eval_profit",
            "trade_count_today",
            "consecutive_losses",
        )
        for key in required_numeric:
            if key not in state:
                return False, f"missing:{key}"
            if not _is_finite_number(state[key]):
                return False, f"non_finite:{key}"

        if int(state["trade_count_today"]) < 0:
            return False, "negative:trade_count_today"
        if int(state["consecutive_losses"]) < 0:
            return False, "negative:consecutive_losses"

        # now_et is a REQUIRED field (LOCKED AccountState schema, ARCHITECTURE.md
        # §4). It must be present AND parseable via _parse_et_time; otherwise the
        # EOD/overnight gate would be silently bypassed. A missing/garbage clock is
        # therefore an invalid account state => circuit breaker ('flat'), NOT a
        # skipped EOD check. This closes the overnight/EOD fail-open hole.
        if "now_et" not in state:
            return False, "missing:now_et"
        if _parse_et_time(state["now_et"]) is None:
            return False, "invalid_now_et"

        # account_size / equity are optional here but, if present, must be finite.
        for key in ("account_size", "equity"):
            if key in state and not _is_finite_number(state[key]):
                return False, f"non_finite:{key}"

        return True, ""

    @staticmethod
    def _time_ge(a: "time", b: "time") -> bool:
        """Compare two times ignoring tzinfo (both are ET by contract)."""
        an = a.replace(tzinfo=None) if a.tzinfo else a
        bn = b.replace(tzinfo=None) if b.tzinfo else b
        return an >= bn

    # ── Result builders ───────────────────────────────────────────────────────

    @staticmethod
    def _flat(reason: str) -> dict:
        return {"decision": "flat", "size": 0, "reason": reason}

    @staticmethod
    def _reject(reason: str) -> dict:
        return {"decision": "reject", "size": 0, "reason": reason}

    # ── Audit (ARCHITECTURE.md §8.1 decision record) ─────────────────────────

    def _audit(self, signal: dict, account_state: dict, result: dict) -> None:
        """Append one decision record to ``bot/logs/decisions.jsonl``.

        Carries an ISO-8601 ET timestamp + reason. NO secrets are present in a
        signal/account by contract; we still only copy a known allowlist of keys.
        """
        sig = signal if isinstance(signal, dict) else {}
        acct = account_state if isinstance(account_state, dict) else {}

        record = {
            "ts": _now_et_iso(),
            "event": "decision",
            "decision": result.get("decision"),
            "stage": "risk_guard",
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
                "unrealized_pnl": acct.get("unrealized_pnl"),
                "trade_count_today": acct.get("trade_count_today"),
                "consecutive_losses": acct.get("consecutive_losses"),
                "total_eval_profit": acct.get("total_eval_profit"),
            },
            "mandate_fallback": self.mandate.is_fallback,
            # NOTE: send mode (live_send / dry_run) is intentionally OMITTED here.
            # risk_guard is the gate, not the sender — it cannot know the live/dry
            # state, which is owned by traderpost. The traderpost order record is
            # the single source of truth for send mode (ARCHITECTURE.md §9). Logging
            # a hardcoded dry_run=True here would corrupt the S6 audit trail if the
            # runner ever sends live after an 'approve'.
        }

        self._audit_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self._audit_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")


# ── Smoke Test (__main__) ─────────────────────────────────────────────────────

def _base_account(**overrides) -> dict:
    """A healthy baseline account_state for the smoke test."""
    state = {
        "account_size": 25000.0,
        "equity": 25000.0,
        "realized_pnl_today": 0.0,
        "unrealized_pnl": 0.0,
        "total_eval_profit": 0.0,
        "open_position": None,
        "trade_count_today": 0,
        "consecutive_losses": 0,
        "session_date": "2026-06-21",
        "now_et": "2026-06-21T09:42:00-04:00",
    }
    state.update(overrides)
    return state


def _base_signal(**overrides) -> dict:
    """A valid long ES signal (entry/stop/tp1/tp2 with R=6pts) for the smoke test."""
    sig = {
        "side": "long",
        "instrument": "ES",
        "entry": 4750.25,
        "stop": 4744.25,    # R = 6.0
        "tp1": 4762.25,     # entry + 2R
        "tp2": 4774.25,     # entry + 4R
        "size": 1,
        "reason": "ny_open sweep+FVG+OTE+MSB",
        "ts": "2026-06-21T09:42:00-04:00",
    }
    sig.update(overrides)
    return sig


def _smoke() -> int:
    """Run a self-contained smoke test against the live mandate. Returns 0 on pass."""
    mv = MandateView.from_file()
    print(f"Mandate loaded (fallback={mv.is_fallback}): "
          f"max_loss=${mv.max_loss_limit:.0f} cap={mv.max_position_size} "
          f"daily_cap={mv.daily_trade_cap} consistency={mv.consistency_rule_eval} "
          f"instruments={list(mv.instruments_allowed)}")
    print(f"Kill-switch file watched: {mv.kill_switch_file} "
          f"(present={Path(mv.kill_switch_file).exists()})")

    # Use a temp audit log so the smoke test never pollutes the real decisions log.
    tmp_log = LOG_DIR / "decisions.smoke.jsonl"
    rg = RiskGuard(mv, audit_log=tmp_log)

    cases = [
        # name, signal, account, expected_decision, [expected_size]
        ("approve_clean", _base_signal(), _base_account(), "approve", 1),
        ("clamp_size_over_cap", _base_signal(size=9), _base_account(), "approve",
         mv.max_position_size),
        ("reject_bad_instrument", _base_signal(instrument="BTC"), _base_account(),
         "reject", None),
        ("reject_bad_side", _base_signal(side="sideways"), _base_account(),
         "reject", None),
        ("reject_stop_equals_entry", _base_signal(stop=4750.25), _base_account(),
         "reject", None),
        ("reject_nan_stop", _base_signal(stop=float("nan")), _base_account(),
         "reject", None),
        ("reject_daily_trade_cap",
         _base_signal(),
         _base_account(trade_count_today=mv.daily_trade_cap),
         "reject", None),
        ("flat_hard_max_loss",
         _base_signal(),
         _base_account(realized_pnl_today=-(mv.max_loss_limit + 1)),
         "flat", None),
        ("flat_soft_daily_gate",
         _base_signal(),
         _base_account(realized_pnl_today=-(mv.max_loss_limit * 0.80)),
         "flat", None),
        # Bug #1 regression: soft gate must use COMBINED realized+unrealized.
        # realized=-100, unrealized=(soft gate - 100) => total == soft gate (80%
        # of max_loss_limit). Must be 'flat', NOT 'approve' (open risk already at
        # the stop). Derived from mv.max_loss_limit so it holds regardless of the
        # mandate's actual dollar figure.
        ("flat_soft_daily_gate_combined",
         _base_signal(),
         _base_account(realized_pnl_today=-100.0,
                       unrealized_pnl=-(mv.max_loss_limit * 0.80 - 100.0)),
         "flat", None),
        ("flat_consecutive_losses",
         _base_signal(),
         _base_account(consecutive_losses=3),
         "flat", None),
        ("reject_consistency_cap",
         _base_signal(),
         _base_account(total_eval_profit=800.0, realized_pnl_today=400.0),
         "reject", None),
        ("flat_eod",
         _base_signal(),
         _base_account(now_et="2026-06-21T15:55:00-04:00"),
         "flat", None),
        ("flat_invalid_account_nan",
         _base_signal(),
         _base_account(realized_pnl_today=float("nan")),
         "flat", None),
        ("flat_invalid_account_missing",
         _base_signal(),
         {"now_et": "2026-06-21T09:42:00-04:00"},  # missing required fields
         "flat", None),
        # Bug #4 regression: non-positive requested size is malformed -> reject,
        # never floored up to a live 1-contract order.
        ("reject_zero_size",
         _base_signal(size=0), _base_account(), "reject", None),
        ("reject_negative_size",
         _base_signal(size=-5), _base_account(), "reject", None),
        # Bug #2/#3 regression: a missing or garbage now_et must NOT fail-open and
        # approve at an unknown hour — it is an invalid account state => 'flat'.
        ("flat_now_et_none",
         _base_signal(), _base_account(now_et=None), "flat", None),
        ("flat_now_et_garbage",
         _base_signal(), _base_account(now_et="not-a-time"), "flat", None),
        ("flat_now_et_missing",
         _base_signal(),
         {k: v for k, v in _base_account().items() if k != "now_et"},
         "flat", None),
    ]

    passed = 0
    failed = 0
    for name, sig, acct, expect, *exp_size in cases:
        res = rg.check(sig, acct)
        ok = res["decision"] == expect
        if exp_size and exp_size[0] is not None:
            ok = ok and res["size"] == exp_size[0]
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] {name:<28} -> {res['decision']:<7} "
              f"size={res['size']} reason={res['reason']}")

    # Order safety invariant: only 'approve' may yield a non-zero size.
    for name, sig, acct, expect, *_ in cases:
        res = rg.check(sig, acct)
        if res["decision"] != "approve" and res["size"] != 0:
            print(f"  [FAIL] order-safety: {name} non-approve with size={res['size']}")
            failed += 1

    # ── Contract: EOD 'flat' must be consumed as FLATTEN, not 'skip cycle' ──────
    # ARCHITECTURE.md §10 (runner step 4/7) + §0/§7 mandate: at/after 15:55 ET the
    # runner MUST flatten any OPEN position via traderpost.send, not merely refuse a
    # new entry — risk_guard alone cannot exit the market. This models the runner's
    # consumption of a 'flat' decision and asserts the flatten path fires when a
    # position is open. (Couples risk_guard's 'flat' to the runner's flatten duty.)
    def _runner_consumes(decision: str, has_open_position: bool) -> str:
        """Mirror runner.run_cycle: 'flat'/'reject' produce no NEW entry, but a
        'flat' with an open position MUST flatten it (overnight_holds=false)."""
        if decision == "approve":
            return "enter"
        if decision == "flat" and has_open_position:
            return "flatten_open_position"
        return "no_new_entry"

    eod_acct = _base_account(
        now_et="2026-06-21T15:55:00-04:00",
        open_position={"instrument": "ES", "side": "long", "size": 1},
    )
    eod_res = rg.check(_base_signal(), eod_acct)
    action = _runner_consumes(eod_res["decision"], eod_acct.get("open_position") is not None)
    if not (eod_res["decision"] == "flat" and action == "flatten_open_position"):
        print(f"  [FAIL] eod_flatten_contract: decision={eod_res['decision']} "
              f"action={action} (expected flat -> flatten_open_position)")
        failed += 1
    else:
        passed += 1
        print(f"  [PASS] {'eod_flatten_contract':<28} -> flat consumed as "
              f"'{action}' (runner must flatten, not skip)")

    print(f"\nSmoke test: {passed} passed, {failed} failed.")
    print(f"Decision audit written to: {tmp_log}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(_smoke())
