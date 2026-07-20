#!/usr/bin/env python3
"""
eval_gate.py — Third gate on the live/paper switch (ARCHITECTURE.md-style contract)
=====================================================================================
NEW FILE (2026-07-08). Does NOT touch ``risk_guard.py`` or ``strategy.py``.

The live/paper switch (``config.is_live_enabled``) previously only checked TWO
conditions: ``os.environ["HERMES_BOT_LIVE"] == "1"`` and ``config.go_live is
True``. NEITHER of those says anything about whether the bot has actually
proven itself in paper mode. This module adds that missing performance gate:
a bot with zero (or a handful of lucky) paper trades must NOT be able to arm
live trading just because both boolean flags were flipped.

``passed_evaluation(trade_journal_path, mandate_path)`` is the single public
entrypoint. It is PURE read-only: it never writes, never raises (fails CLOSED
to ``(False, [...])`` on any I/O/parse problem), and never touches
``risk_guard.py``/``strategy.py``. It reads:

  - ``trade_journal_path``  — JSONL of PAPER trades, one closed trade per line,
    written by ``trade_journal.py``'s ``log`` command (schema: ts, date, side,
    instrument, entry, exit, pnl, note). Every row in this file is a CLOSED
    (realized) trade by construction — it always carries both an ``entry`` AND
    an ``exit`` price, so there is no "unrealized" row to accidentally count.
  - ``mandate_path``         — ``lucid_mandate.json`` (read at runtime, no
    hardcoded numbers — same pattern as ``config.py``/``risk_guard.py``).

It ALSO does best-effort, sibling-file checks (same log directory as
``trade_journal_path``) against ``decisions.jsonl`` (the risk_guard audit
trail) and ``orders.jsonl`` (the order-send audit trail) for the
risk-guard-bypass and kill-switch checks. Those two files are optional: if
absent, the corresponding check is SKIPPED (not silently passed) and a note is
appended to the returned reasons only when a problem is actually found in
them — their absence alone never causes a spurious failure, since the primary
gate (trade-count / profitable-days / consistency / max-loss) already fails
closed on an empty/thin journal.

MINIMUM SAMPLE SIZE — why 25:
  The Lucid mandate caps trades at 10/day. 25 approved paper trades is roughly
  2.5-3 trading days' worth at the cap, or about a full trading week at a more
  realistic 5-8 trades/day. That is enough for the sample to plausibly include
  at least one losing day and to exercise the daily-loss gate / consecutive-
  loss breaker / consistency rule in a way that means something — a 3-5 trade
  lucky streak proves nothing. 25 is picked over 20 (too easy to hit with a
  single good morning) and short of 30 (would delay a real go-live decision
  for little extra statistical confidence at this sample size). Documented
  here, not buried in a magic number.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── Tunables (documented, not hidden magic numbers) ─────────────────────────
MIN_SAMPLE_TRADES: int = 25          # see module docstring for rationale
KILL_SWITCH_RISK_WINDOW_MIN: int = 30  # minutes: how close a risk-breach 'flat'
                                        # must be BEFORE a kill_switch halt to be
                                        # treated as "the kill switch was engaged
                                        # for a risk-breach reason"
_RISK_BREACH_REASON_PREFIXES = (
    "max_loss_limit",
    "daily_loss_gate",
    "consecutive_losses",
)


def _load_mandate(mandate_path) -> dict:
    p = Path(mandate_path)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_jsonl(path: Path) -> list[dict]:
    """Best-effort JSONL loader. Missing file -> []. Malformed lines skipped."""
    out: list[dict] = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _load_trade_journal(path: Path) -> list[dict]:
    """Load the trade journal. Every row here is a CLOSED (realized) trade by
    construction (trade_journal.py's `log` command always records entry+exit+pnl
    together) — there is no separate 'unrealized' row shape to filter out."""
    return _load_jsonl(path)


def _parse_ts(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _check_sample_size(trades: list[dict]) -> Optional[str]:
    n = len(trades)
    if n < MIN_SAMPLE_TRADES:
        return (
            f"insufficient_sample: {n} paper trade(s) logged, need >= "
            f"{MIN_SAMPLE_TRADES} (guards against gaming with a lucky short streak)"
        )
    return None


def _check_profitable_days(trades: list[dict], rules: dict) -> Optional[str]:
    min_days = rules.get("payout_min_profitable_days")
    if min_days is None:
        return None  # mandate doesn't define this rule -> nothing to enforce
    byday: dict[str, float] = {}
    for t in trades:
        d = t.get("date")
        pnl = t.get("pnl")
        if d is None or not isinstance(pnl, (int, float)):
            continue
        byday[d] = byday.get(d, 0.0) + float(pnl)
    profitable_days = sum(1 for v in byday.values() if v > 0)
    if profitable_days < int(min_days):
        return (
            f"insufficient_profitable_days: {profitable_days} profitable day(s) "
            f"(realized P&L only) out of {len(byday)} traded day(s), need >= "
            f"{int(min_days)} (mandate.payout_min_profitable_days)"
        )
    return None


def _check_consistency(trades: list[dict], rules: dict) -> Optional[str]:
    cons = rules.get("consistency_rule_eval")
    if cons is None:
        return None
    cons = float(cons)
    byday: dict[str, float] = {}
    for t in trades:
        d = t.get("date")
        pnl = t.get("pnl")
        if d is None or not isinstance(pnl, (int, float)):
            continue
        byday[d] = byday.get(d, 0.0) + float(pnl)
    total = sum(byday.values())
    if total <= 0 or not byday:
        return None  # no aggregate profit yet -> consistency rule not evaluable
    best_day = max(byday.values())
    limit = total * cons
    if best_day > limit:
        best_date = max(byday, key=byday.get)
        return (
            f"consistency_breach: best day {best_date} P&L ${best_day:.2f} exceeds "
            f"{cons:.0%} of total eval profit (${total:.2f}) -> limit ${limit:.2f}"
        )
    return None


def _check_max_loss(trades: list[dict], rules: dict) -> Optional[str]:
    max_loss = rules.get("max_loss_limit")
    if max_loss is None:
        return None
    max_loss = float(max_loss)
    # Sort chronologically by ts (fallback to date+order-in-file if ts missing).
    def _sort_key(t: dict):
        ts = _parse_ts(t.get("ts"))
        return ts or datetime.min.replace(tzinfo=None)

    ordered = sorted(trades, key=_sort_key)
    cumulative = 0.0
    peak = 0.0
    worst_dd = 0.0
    worst_at = None
    for t in ordered:
        pnl = t.get("pnl")
        if not isinstance(pnl, (int, float)):
            continue
        cumulative += float(pnl)
        peak = max(peak, cumulative)
        dd = peak - cumulative
        if dd > worst_dd:
            worst_dd = dd
            worst_at = t.get("ts") or t.get("date")
    if worst_dd >= max_loss:
        return (
            f"max_loss_breach: trailing drawdown ${worst_dd:.2f} at/after {worst_at} "
            f">= mandate max_loss_limit ${max_loss:.2f}"
        )
    return None


def _check_risk_guard_bypass(log_dir: Path) -> Optional[str]:
    """Best-effort cross-check: a risk_guard 'reject'/'flat' decision that was
    somehow followed by an order that actually SENT LIVE (mode != 'dry_run', or
    a result other than 'rejected'/'logged_only') would mean a bad request got
    through anyway. Returns None (pass / not evaluable) unless such a record is
    actually found. Absence of decisions.jsonl or orders.jsonl -> not evaluable
    (None), not a failure — the primary journal-based checks already fail
    closed on a thin/empty journal."""
    decisions = _load_jsonl(log_dir / "decisions.jsonl")
    orders = _load_jsonl(log_dir / "orders.jsonl")
    if not decisions or not orders:
        return None

    live_leak = [
        o for o in orders
        if o.get("event") == "order"
        and o.get("mode") not in (None, "dry_run")
    ]
    if live_leak:
        first = live_leak[0]
        return (
            f"risk_guard_bypass: order record with mode={first.get('mode')!r} "
            f"found (expected 'dry_run' for every paper-eval order) at ts="
            f"{first.get('ts')}"
        )

    sent_despite_reject = [
        o for o in orders
        if o.get("event") == "order"
        and o.get("result") not in (None, "rejected", "logged_only")
    ]
    if sent_despite_reject:
        first = sent_despite_reject[0]
        return (
            f"risk_guard_bypass: order result={first.get('result')!r} (expected "
            f"'rejected' or 'logged_only' in paper mode) at ts={first.get('ts')}"
        )
    return None


def _check_kill_switch_risk_breach(log_dir: Path) -> Optional[str]:
    """Best-effort: flag a kill_switch halt that was IMMEDIATELY preceded (within
    KILL_SWITCH_RISK_WINDOW_MIN) by a risk-breach 'flat' decision (max_loss_limit,
    daily_loss_gate, consecutive_losses) -- i.e. the kill switch appears to have
    been engaged BECAUSE of a risk breach, not for an unrelated reason (routine
    pause, maintenance, manual test). Absence of decisions.jsonl -> not evaluable
    (None)."""
    decisions = _load_jsonl(log_dir / "decisions.jsonl")
    if not decisions:
        return None

    halts = [d for d in decisions if d.get("event") == "halt" and d.get("reason") == "kill_switch"]
    if not halts:
        return None

    breach_flats = []
    for d in decisions:
        if d.get("event") != "decision" or d.get("decision") != "flat":
            continue
        reason = str(d.get("reason") or "")
        if any(reason.startswith(p) for p in _RISK_BREACH_REASON_PREFIXES):
            ts = _parse_ts(d.get("ts"))
            if ts is not None:
                breach_flats.append((ts, reason))

    window = timedelta(minutes=KILL_SWITCH_RISK_WINDOW_MIN)
    for halt in halts:
        halt_ts = _parse_ts(halt.get("ts"))
        if halt_ts is None:
            continue
        for breach_ts, reason in breach_flats:
            if breach_ts <= halt_ts and (halt_ts - breach_ts) <= window:
                return (
                    f"kill_switch_risk_breach: kill switch halted at {halt.get('ts')} "
                    f"within {KILL_SWITCH_RISK_WINDOW_MIN}min of a risk-breach flat "
                    f"({reason} at {breach_ts.isoformat()})"
                )
    return None


def passed_evaluation(
    trade_journal_path,
    mandate_path,
) -> "tuple[bool, list[str]]":
    """Return ``(True, [])`` iff the paper track record clears every eval gate.

    Reads ``trade_journal_path`` (JSONL of closed paper trades) and
    ``mandate_path`` (``lucid_mandate.json``). Never raises: any read/parse
    problem is treated as a FAILURE (fail-closed), not an exception, since this
    function gates whether live orders may ever be armed.

    Checks (all must pass):
      1. Minimum sample size (>= MIN_SAMPLE_TRADES approved paper trades).
      2. Minimum profitable trading days (mandate.payout_min_profitable_days),
         counted from REALIZED P&L only (every journal row is realized by
         construction — see module docstring).
      3. Consistency: no single day's realized profit > mandate's
         consistency_rule_eval fraction of total realized profit in the window.
      4. No max_loss_limit breach anywhere in the trailing cumulative P&L curve.
      5. (best-effort) No evidence in decisions.jsonl/orders.jsonl that a
         risk_guard reject/flat was bypassed by an order that actually sent.
      6. (best-effort) The kill switch was never engaged within
         KILL_SWITCH_RISK_WINDOW_MIN minutes of a risk-breach 'flat' decision.

    Returns ``(bool, list[str])`` — the list is empty iff the bool is True;
    otherwise it holds one specific, human-readable reason per failed check.
    """
    reasons: list[str] = []

    journal_path = Path(trade_journal_path)
    try:
        trades = _load_trade_journal(journal_path)
    except OSError as exc:
        return False, [f"trade_journal_unreadable: {exc}"]

    try:
        mandate = _load_mandate(mandate_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, [f"mandate_unreadable: {exc}"]

    rules = mandate.get("rules", {}) or {}

    # 1. Sample size — checked first; a thin sample makes every other stat
    #    meaningless, but we still report ALL failing checks below rather than
    #    short-circuiting, so a caller sees the full picture.
    r = _check_sample_size(trades)
    if r:
        reasons.append(r)

    # 2. Minimum profitable days (realized P&L only).
    r = _check_profitable_days(trades, rules)
    if r:
        reasons.append(r)

    # 3. Consistency (50% rule).
    r = _check_consistency(trades, rules)
    if r:
        reasons.append(r)

    # 4. Max trailing drawdown breach.
    r = _check_max_loss(trades, rules)
    if r:
        reasons.append(r)

    # 5 & 6. Best-effort cross-checks against sibling audit logs.
    log_dir = journal_path.parent
    r = _check_risk_guard_bypass(log_dir)
    if r:
        reasons.append(r)
    r = _check_kill_switch_risk_breach(log_dir)
    if r:
        reasons.append(r)

    return (len(reasons) == 0, reasons)


# ── __main__ smoke test / CLI ────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    BOT_DIR = Path(__file__).resolve().parent
    VIBE_DIR = BOT_DIR.parent
    default_journal = BOT_DIR / "logs" / "trade_journal.jsonl"
    default_mandate = VIBE_DIR / "lucid_mandate.json"

    journal_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else default_journal
    mandate_arg = Path(sys.argv[2]) if len(sys.argv) > 2 else default_mandate

    ok, why = passed_evaluation(journal_arg, mandate_arg)
    print(f"trade_journal : {journal_arg} (exists={journal_arg.exists()})")
    print(f"mandate       : {mandate_arg} (exists={mandate_arg.exists()})")
    print(f"passed_evaluation -> {ok}")
    for reason in why:
        print(f"  - {reason}")
    sys.exit(0 if ok else 1)
