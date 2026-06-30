#!/usr/bin/env python3
"""
TraderPost Client — ``vibe-trading/bot/traderpost.py``
======================================================
The ONLY module permitted to make an outbound order POST for the TJR/ICT Lucid 25K
trading bot. Default = DRY_RUN (paper). Implements §9 of the locked ARCHITECTURE.md
contract: build a TraderPost webhook JSON payload from an APPROVED signal and send it.

Hard safety invariants enforced here (never violated):
  * Paper / DRY_RUN by default. A live POST happens ONLY when BOTH
        os.environ["HERMES_BOT_LIVE"] == "1"  AND  config.go_live is True.
    If either is off → build payload, audit a dry_run "logged_only" order, return — no POST.
  * Validate-before-send (independent of the caller / any model output): reject with
    result="rejected" (no POST) if the order is missing a stop or size, size is out of
    the mandate cap, the instrument is off the mandate allowlist, the side is invalid,
    or the kill switch is engaged (re-checked here as defense in depth).
  * Secrets (TRADERPOST_WEBHOOK_URL, TRADERPOST_SECRET) come from os.environ ONLY. They
    are NEVER hardcoded and NEVER written to a log line. Audit records receive only a
    redacted webhook HOST + a boolean — never the URL, query string, secret, or headers.
  * All Lucid limits (max_position_size, instruments_allowed, kill switch path) are read
    at runtime from lucid_mandate.json via MandateView — nothing is hardcoded.

Usage:
    python traderpost.py        # prints a sample DRY_RUN payload + send() result

This module depends only on stdlib + ``requests`` (per the locked dependency map:
traderpost ──> config, audit[types only]). It degrades gracefully if config/audit/mandate
helper modules are not yet present (so it is independently runnable + testable).
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

try:  # requests is only needed for the live POST path.
    import requests
except Exception:  # pragma: no cover - keeps DRY_RUN runnable without requests
    requests = None  # type: ignore[assignment]

# ── Paths (resolved relative to vibe-trading/) ───────────────────────────────────
BOT_DIR = Path(__file__).resolve().parent           # vibe-trading/bot/
VIBE_DIR = BOT_DIR.parent                            # vibe-trading/
MANDATE_FILE = VIBE_DIR / "lucid_mandate.json"
LOG_DIR = BOT_DIR / "logs"

# Secret env var names — the values are NEVER stored in code, only read at call time.
ENV_WEBHOOK_URL = "TRADERPOST_WEBHOOK_URL"
ENV_SECRET = "TRADERPOST_SECRET"
ENV_LIVE = "HERMES_BOT_LIVE"

POST_TIMEOUT_SEC = 10   # mirrors signal_watcher.send_telegram's 10s timeout

# Keys that are SAFE to echo into a logged payload summary / audit record.
# A secret must never appear here (audit._redact enforces this in audit.py too).
_REDACTED_KEYS = {"secret", "token", "api_key", "apikey", "password",
                  "authorization", "auth", "webhook_url", "url"}


# ── Runtime mandate read (no hardcoded limits) ───────────────────────────────────

def _load_mandate_rules(path: Optional[Path] = None) -> dict:
    """
    Read Lucid limits from lucid_mandate.json at runtime. Mirrors the
    ``load_mandate()`` pattern in tjr_backtest.py. Returns the ``rules`` dict.

    A conservative fallback is returned ONLY if the file is briefly missing, so the
    validator stays fail-closed (small cap / full allowlist) rather than crashing.
    """
    p = path or MANDATE_FILE
    try:
        if p.exists():
            with open(p, encoding="utf-8-sig") as f:
                data = json.load(f)
            rules = data.get("rules", {})
            rules.setdefault("kill_switch_file",
                             (data.get("kill_switch", {}) or {}).get("file", "./KILL_SWITCH"))
            rules["_mandate_fallback"] = False
            return rules
    except Exception:
        pass
    # Fallback (defense in depth). The mandate could not be read, so the real Lucid
    # limits are UNKNOWN. We flag this so send() can force DRY_RUN and emit a loud
    # mandate_fallback audit event (ARCHITECTURE §6/§13) — a public send() must NOT
    # rely solely on the runner having forced DRY_RUN. The values below are a crash
    # guard only and never authorize a live POST.
    return {
        "instruments_allowed": ["ES", "MES", "NQ", "MNQ"],
        "max_position_size": 1,
        "kill_switch_file": "./KILL_SWITCH",
        "_mandate_fallback": True,
    }


def _kill_switch_engaged(rules: dict) -> bool:
    """
    Re-check the kill switch here as defense in depth (§9.2). Only the literal
    mandate-declared path (default ``vibe-trading/KILL_SWITCH``) triggers a halt.
    The currently-present ``KILL_SWITCH_DISABLED`` file is NOT a trigger.
    """
    rel = rules.get("kill_switch_file", "./KILL_SWITCH")
    if Path(rel).is_absolute():
        ks = Path(rel)
    else:
        # Strip a leading "./" prefix WITHOUT eating other leading dots.
        # lstrip("./") would mangle e.g. ".killswitch" / "./.hidden"; a prefix
        # slice preserves them.
        stripped = rel[2:] if rel.startswith("./") else rel
        ks = (VIBE_DIR / stripped).resolve()
    return ks.exists()


def _is_finite_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


# Max length for free-text fields forwarded to the broker webhook (§S5).
_MAX_EXTRA_TEXT = 256


def _sanitize_text(value: Any, *, max_len: int = _MAX_EXTRA_TEXT) -> str:
    """
    Bound + strip control characters from an externally-sourced string before it is
    forwarded to the broker webhook (S5 — sanitize external input). Signal fields like
    'reason'/'ts' may originate from a model or file and must not pass through verbatim.
    """
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    # Drop ASCII control chars (incl. CR/LF/NUL) that could corrupt a JSON/log line.
    s = "".join(ch for ch in s if ord(ch) >= 32 and ch != "\x7f")
    if len(s) > max_len:
        s = s[:max_len]
    return s


def _redact_host(url: str) -> Optional[str]:
    """Return ONLY the bare host of a URL (e.g. 'traderpost.io'). Never the path/query/secret."""
    try:
        host = urlparse(url).hostname
        return host or None
    except Exception:
        return None


# ── Payload builder ──────────────────────────────────────────────────────────────

def build_payload(signal: dict, approved_size: int, *, secret: Optional[str] = None) -> dict:
    """
    Build the TraderPost webhook JSON payload from an APPROVED signal.

    TraderPost's webhook contract expects an order intent: a symbol/ticker, an action
    (buy/sell), a quantity, and optional bracket exits (stop loss + take profits).
    We map the locked Signal schema (§2) onto that contract.

    The ``secret`` (if present) is included per TraderPost's contract and is NEVER
    logged — callers must keep the returned dict out of any audit record verbatim
    (use ``payload_summary`` for logging instead).
    """
    side = signal["side"]
    # Public builder guard: never silently emit a SELL for a non-'long' garbage side
    # (typo / None / arbitrary string) when called directly, bypassing send()._validate.
    if side not in ("long", "short"):
        raise ValueError(f"build_payload: invalid side {side!r} (expected 'long' or 'short')")
    action = "buy" if side == "long" else "sell"
    entry = float(signal["entry"])
    stop = float(signal["stop"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])

    payload: dict[str, Any] = {
        "ticker": signal["instrument"],
        "action": action,
        "orderType": "market",
        "quantity": int(approved_size),
        "price": entry,
        "stopLoss": {"type": "stop", "price": stop},
        "takeProfit": {"limitPrice": tp1},
        # TraderPost honors a single TP per leg; carry tp2 as scale-out metadata.
        "extras": {
            "tp1": tp1,
            "tp2": tp2,
            # External free-text — bounded + control-char-stripped before egress (S5).
            "reason": _sanitize_text(signal.get("reason", "")),
            "signal_ts": _sanitize_text(signal.get("ts", "")),
            "source": "tjr_lucid_bot",
        },
        "time": _sanitize_text(signal.get("ts")) or _now_et_iso(),
    }
    if secret:
        # Sent in body per TraderPost's contract. Never logged (see send()).
        payload["secret"] = secret
    return payload


def payload_summary(signal: dict, approved_size: int) -> str:
    """One-line, secret-free human summary for the orders.jsonl record (§8.2)."""
    side = signal.get("side", "?")
    inst = signal.get("instrument", "?")
    stop = signal.get("stop")
    tp1 = signal.get("tp1")
    tp2 = signal.get("tp2")
    return (f"{side} {int(approved_size)} {inst} @mkt "
            f"stop={stop} tp1={tp1} tp2={tp2}")


def _now_et_iso() -> str:
    """ISO-8601 timestamp. Prefers ET; falls back to UTC if zoneinfo is unavailable."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    except Exception:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── TraderPost client ────────────────────────────────────────────────────────────

class TraderPostClient:
    """
    Validate-before-send TraderPost order client. DRY_RUN by default.

    Parameters
    ----------
    config : BotConfig | None
        Operational config (the ``go_live`` half of the live gate). Secrets are NEVER
        read from config — only from os.environ inside ``send()``. If None, ``go_live``
        defaults to False (paper).
    audit : AuditLogger | None
        Audit sink. If None, a minimal built-in JSONL/text logger is used so the module
        is independently runnable. Either way, every order ATTEMPT is logged and no
        secret ever reaches a record.
    mandate_path : Path | None
        Override for lucid_mandate.json (testing). Defaults to vibe-trading/lucid_mandate.json.
    """

    def __init__(self, config: Any = None, audit: Any = None, *,
                 mandate_path: Optional[Path] = None) -> None:
        self.config = config
        self.audit = audit or _FallbackAudit(LOG_DIR)
        self._mandate_path = mandate_path

    # -- gating -------------------------------------------------------------------

    def _go_live_config(self) -> bool:
        """The config half of the live gate; defaults OFF when unset."""
        return bool(getattr(self.config, "go_live", False))

    def _live_requested(self) -> bool:
        """ALL of §9.1 must hold to POST live: env HERMES_BOT_LIVE==1 AND config.go_live."""
        return (os.environ.get(ENV_LIVE) == "1") and self._go_live_config()

    # -- validation (§9.2) --------------------------------------------------------

    def _validate(self, signal: dict, approved_size: int, rules: dict) -> Optional[str]:
        """
        Validate-before-send, independent of caller / model. Returns a reject reason
        string if the order must NOT be sent, else None.
        """
        # Kill switch (defense in depth) — halt everything.
        if _kill_switch_engaged(rules):
            return "kill_switch"

        if not isinstance(signal, dict):
            return "bad_signal_type"

        side = signal.get("side")
        if side not in ("long", "short"):
            return "bad_side"

        instrument = signal.get("instrument")
        allowed = tuple(rules.get("instruments_allowed", ()))
        if instrument not in allowed:
            return "instrument_not_allowed"

        entry = signal.get("entry")
        stop = signal.get("stop")
        if not _is_finite_number(entry):
            return "bad_entry"
        if stop is None or not _is_finite_number(stop):
            return "missing_stop"
        if float(stop) == float(entry):
            return "stop_equals_entry"

        if not isinstance(approved_size, int) or approved_size < 1:
            return "missing_size"
        # Size cap MUST fail closed: if max_position_size is missing / <=0 / non-int,
        # the cap is undeterminable and we have no provable risk_guard-approved ceiling,
        # so we reject rather than let an arbitrary size through (S1/S3 spend limit).
        raw_cap = rules.get("max_position_size", None)
        try:
            cap = int(raw_cap)
        except (TypeError, ValueError):
            cap = 0
        if cap < 1:
            # Distinguish a missing/zero cap (no mandate) from a malformed value.
            return "no_cap" if raw_cap is None else "mandate_invalid"
        if approved_size > cap:
            return "size_over_cap"

        # tp1/tp2 are REQUIRED by the locked §2 Signal schema. They must be PRESENT and
        # finite — not merely "valid if present". Missing/NaN tp keys would otherwise
        # reach build_payload's direct signal['tp1']/['tp2'] access on the live path and
        # raise KeyError BEFORE the network try/except, crashing the runner.
        for k in ("tp1", "tp2"):
            if k not in signal:
                return f"missing_{k}"
            if not _is_finite_number(signal.get(k)):
                return f"missing_{k}"

        return None

    # -- public API (§9) ----------------------------------------------------------

    def send(self, signal: dict, *, approved_size: int) -> dict:
        """
        Validate-before-send, then either POST (live) or log-only (dry_run).

        Returns
        -------
        dict
            { "result": 'logged_only'|'sent'|'rejected'|'error',
              "http_status": int|None,
              "reason": str }
        """
        rules = _load_mandate_rules(self._mandate_path)

        # 1) Validate-before-send (independent of the caller / any model output).
        reject_reason = self._validate(signal, approved_size, rules)
        if reject_reason is not None:
            self._audit_order(signal, approved_size, mode="dry_run",
                              result="rejected", http_status=None,
                              webhook_host=None, reason=reject_reason)
            return {"result": "rejected", "http_status": None, "reason": reject_reason}

        live = self._live_requested()

        # 1b) Mandate fallback (file missing/unreadable) — the real Lucid limits are
        # UNKNOWN. Per ARCHITECTURE §6/§13, force DRY_RUN here (do NOT trust that the
        # runner did) and emit a loud mandate_fallback audit event before continuing.
        # send() is a public entrypoint, so this guard must live here, not only upstream.
        if rules.get("_mandate_fallback"):
            self._audit_event("mandate_fallback",
                              reason="lucid_mandate.json missing/unreadable — forcing DRY_RUN")
            if live:
                live = False  # refuse any live POST when the mandate is not provable

        # 2) DRY_RUN (default): build payload, log it, return WITHOUT POSTing.
        if not live:
            reason = ("DRY_RUN: mandate_fallback — forcing paper"
                      if rules.get("_mandate_fallback")
                      else "DRY_RUN: HERMES_BOT_LIVE!=1 or go_live=false")
            self._audit_order(signal, approved_size, mode="dry_run",
                              result="logged_only", http_status=None,
                              webhook_host=_redact_host(os.environ.get(ENV_WEBHOOK_URL, "")),
                              reason=reason)
            return {"result": "logged_only", "http_status": None, "reason": reason}

        # 3) LIVE path — secrets read from env ONLY, never logged.
        webhook_url = os.environ.get(ENV_WEBHOOK_URL)
        if not webhook_url:
            self._audit_order(signal, approved_size, mode="live",
                              result="error", http_status=None,
                              webhook_host=None, reason="missing_webhook")
            return {"result": "error", "http_status": None, "reason": "missing_webhook"}

        if requests is None:
            self._audit_order(signal, approved_size, mode="live",
                              result="error", http_status=None,
                              webhook_host=_redact_host(webhook_url),
                              reason="requests_unavailable")
            return {"result": "error", "http_status": None, "reason": "requests_unavailable"}

        secret = os.environ.get(ENV_SECRET)
        host = _redact_host(webhook_url)

        try:
            # build_payload can raise (KeyError/ValueError/AssertionError) on a
            # malformed signal that slipped past _validate. Construct it INSIDE the
            # try so any payload error yields result='error' instead of an uncaught
            # exception that crashes the runner mid-live-send.
            payload = build_payload(signal, approved_size, secret=secret)
            resp = requests.post(webhook_url, json=payload, timeout=POST_TIMEOUT_SEC)
            status = resp.status_code
            if 200 <= status < 300:
                self._audit_order(signal, approved_size, mode="live",
                                  result="sent", http_status=status,
                                  webhook_host=host, reason="sent")
                return {"result": "sent", "http_status": status, "reason": "sent"}
            # Non-2xx — log status + redacted host only, never the response secret echo.
            self._audit_order(signal, approved_size, mode="live",
                              result="error", http_status=status,
                              webhook_host=host, reason=f"http_{status}")
            return {"result": "error", "http_status": status, "reason": f"http_{status}"}
        except Exception as exc:
            # Network/timeout error — never crash the runner, never retry-storm.
            # Log only the exception TYPE (no message → no chance of leaking a URL/secret).
            err = type(exc).__name__
            self._audit_order(signal, approved_size, mode="live",
                              result="error", http_status=None,
                              webhook_host=host, reason=f"network_error:{err}")
            return {"result": "error", "http_status": None, "reason": f"network_error:{err}"}

    # -- audit helper -------------------------------------------------------------

    def _audit_event(self, event: str, *, reason: str) -> None:
        """
        Emit a non-order audit event (e.g. ``mandate_fallback``) per ARCHITECTURE §8.3.
        Best-effort: tries the real AuditLogger.log_event, then degrades to the fallback
        sink / stdout. Never raises — a logging failure must not block the safety path.
        """
        try:
            log_event = getattr(self.audit, "log_event", None)
            if callable(log_event):
                log_event(event=event, reason=reason)
                return
        except Exception:
            pass
        try:
            print(json.dumps({"ts": _now_et_iso(), "event": event, "reason": reason},
                             default=str))
        except Exception:
            pass

    def _audit_order(self, signal: dict, approved_size: int, *, mode: str, result: str,
                     http_status: Optional[int], webhook_host: Optional[str],
                     reason: str) -> None:
        """Route to AuditLogger.log_order if available, else the fallback sink."""
        try:
            self.audit.log_order(
                signal={**signal, "size": int(approved_size)},
                mode=mode, result=result, http_status=http_status,
                webhook_host=webhook_host, reason=reason,
            )
        except TypeError:
            # Fallback sink signature.
            self.audit.log_order(  # type: ignore[call-arg]
                signal=signal, approved_size=approved_size, mode=mode, result=result,
                http_status=http_status, webhook_host=webhook_host, reason=reason,
            )


# ── Minimal fallback audit sink (used only when audit.py is not injected) ─────────

class _FallbackAudit:
    """
    Tiny secret-free order logger so traderpost.py is independently runnable/testable.
    The real ``audit.AuditLogger`` (with its full _redact allowlist) is injected by the
    runner in production. This fallback still NEVER writes a URL or secret.
    """

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = Path(log_dir)
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def log_order(self, *, signal: dict, mode: str, result: str,
                  http_status: Optional[int], webhook_host: Optional[str],
                  reason: str, approved_size: Optional[int] = None) -> None:
        size = int(signal.get("size", approved_size or 0) or 0)
        record = {
            "ts": signal.get("ts") or _now_et_iso(),
            "event": "order",
            "mode": mode,
            "instrument": signal.get("instrument"),
            "side": signal.get("side"),
            "size": size,
            "entry": signal.get("entry"),
            "stop": signal.get("stop"),
            "tp1": signal.get("tp1"),
            "tp2": signal.get("tp2"),
            "payload_summary": payload_summary(signal, size),
            "result": result,
            "http_status": http_status,
            "webhook_host": webhook_host,   # redacted host only — never the full URL
            "reason": reason,
        }
        record = _redact_record(record)
        line = json.dumps(record, default=str)
        try:
            with open(self.log_dir / "orders.jsonl", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        # Echo to stdout for smoke tests (no secret can be present in `record`).
        print(line)


def _redact_record(record: dict) -> dict:
    """Drop any key that could carry a secret/URL before serialization (defense in depth)."""
    return {k: v for k, v in record.items() if k.lower() not in _REDACTED_KEYS}


# ── __main__ smoke test: print a sample DRY_RUN payload + send() result ──────────

def _sample_signal() -> dict:
    """A locked-schema (§2) sample signal for the smoke test."""
    return {
        "side": "long",
        "instrument": "ES",
        "entry": 4750.25,
        "stop": 4744.25,         # R = 6.0 points
        "tp1": 4762.25,          # entry + 2R
        "tp2": 4774.25,          # entry + 4R
        "size": 1,
        "reason": "ny_open sweep + bullish FVG in OTE + 1M MSB close",
        "ts": _now_et_iso(),
    }


def main() -> None:
    print("=" * 70)
    print("  TraderPost Client — DRY_RUN smoke test (no live POST)")
    print("=" * 70)

    rules = _load_mandate_rules()
    print(f"Mandate file      : {MANDATE_FILE}")
    print(f"Instruments       : {rules.get('instruments_allowed')}")
    print(f"Max position size : {rules.get('max_position_size')}")
    _ks_rel = str(rules.get("kill_switch_file", "./KILL_SWITCH"))
    _ks_disp = _ks_rel[2:] if _ks_rel.startswith("./") else _ks_rel
    print(f"Kill switch path  : {VIBE_DIR / _ks_disp}")
    print(f"Kill engaged?     : {_kill_switch_engaged(rules)}")
    print(f"HERMES_BOT_LIVE   : {os.environ.get(ENV_LIVE, '<unset>')}")
    print(f"go_live (config)  : False (default) -> DRY_RUN")
    print("-" * 70)

    sig = _sample_signal()
    approved_size = 1

    # Show the would-be payload (secret omitted because none is set in env).
    payload = build_payload(sig, approved_size, secret=os.environ.get(ENV_SECRET))
    safe_payload = _redact_record(dict(payload))  # strip 'secret' if present
    print("Sample DRY_RUN TraderPost payload (secret redacted):")
    print(json.dumps(safe_payload, indent=2, default=str))
    print(f"\nSummary: {payload_summary(sig, approved_size)}")
    print("-" * 70)

    client = TraderPostClient(config=None, audit=None)   # config=None => go_live False
    result = client.send(sig, approved_size=approved_size)
    print(f"\nsend() result -> {result}")

    # Demonstrate validate-before-send rejecting a stop-less order (no POST).
    bad = dict(sig)
    bad["stop"] = None
    bad_result = client.send(bad, approved_size=approved_size)
    print(f"send(no-stop) -> {bad_result}   (expected result='rejected')")

    # Demonstrate size-over-cap rejection.
    cap = int(rules.get("max_position_size", 1) or 1)
    over_result = client.send(sig, approved_size=cap + 5)
    print(f"send(size>cap) -> {over_result}   (expected result='rejected')")

    print("=" * 70)
    print("DRY_RUN complete. No live POST was made.")


if __name__ == "__main__":
    main()
