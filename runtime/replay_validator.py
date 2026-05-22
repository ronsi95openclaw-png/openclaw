"""Deterministic Replay Validator for the OpenClaw AI decision journal.

Reads the append-only replay_journal.jsonl and validates:
  - JSON integrity of every line
  - Temporal ordering of events
  - Signal -> intent verdict pairing (within 5 seconds, by trace_id)
  - Capital state machine validity (only legal transitions allowed)
  - Duplicate event detection (same event_type + trace_id within 1 second)
  - Open intents (approved but never actioned)
  - Deterministic SHA-256 checksum of all event content

This module is STANDALONE: it imports nothing from the OpenClaw codebase so
it can be run independently for audit / CI purposes.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("openclaw.runtime.replay_validator")

# ── Capital state machine ─────────────────────────────────────────────────────

# Legal transitions in the capital state machine.
# Key = from_state, Value = set of legal to_states.
# Rules:
#   - Can only escalate: SAFE -> DEFENSIVE -> CRITICAL -> EMERGENCY_HALT
#   - Only auto-recovery path: DEFENSIVE -> SAFE
#   - All self-transitions (state unchanged) are legal (idempotent records)
_LEGAL_TRANSITIONS: Dict[str, Set[str]] = {
    "SAFE":           {"SAFE", "DEFENSIVE", "CRITICAL", "EMERGENCY_HALT"},
    "DEFENSIVE":      {"DEFENSIVE", "SAFE", "CRITICAL", "EMERGENCY_HALT"},
    "CRITICAL":       {"CRITICAL", "EMERGENCY_HALT"},
    "EMERGENCY_HALT": {"EMERGENCY_HALT"},
}

_ALL_CAPITAL_STATES = frozenset({"SAFE", "DEFENSIVE", "CRITICAL", "EMERGENCY_HALT"})

# Event type constants — mirrors ReplayJournal registered event types.
ET_SCAN_START        = "scan_start"
ET_REGIME_CLASSIFIED = "regime_classified"
ET_SIGNAL_GENERATED  = "signal_generated"
ET_INTENT_SUBMITTED  = "intent_submitted"
ET_INTENT_APPROVED   = "intent_approved"
ET_INTENT_REJECTED   = "intent_rejected"
ET_CAPITAL_STATE     = "capital_state_change"
ET_POSITION_OPENED   = "position_opened"
ET_POSITION_CLOSED   = "position_closed"
ET_RISK_OVERRIDE     = "risk_override"
ET_KILL_SWITCH       = "kill_switch"
ET_BRAIN_INFERENCE   = "brain_inference"

# All event types that resolve / consume a pending signal intent.
_INTENT_RESOLUTION_EVENTS = {ET_INTENT_APPROVED, ET_INTENT_REJECTED}

# All event types that close an open intent (position lifecycle).
_INTENT_CLOSE_EVENTS = {ET_POSITION_OPENED, ET_POSITION_CLOSED}


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class ReplayIssue:
    """A single validation finding."""
    severity:    str   # "INFO" | "WARNING" | "ERROR"
    event_index: int   # 0-based index in the sorted event list (-1 = global)
    event_type:  str   # event_type field value, or "" for global issues
    description: str


@dataclass
class ReplayValidationReport:
    """Full result of a replay validation run."""
    passed:             bool
    total_events:       int
    valid_events:       int
    invalid_events:     int
    issues:             List[ReplayIssue]
    capital_state_final: str           # last known capital state, or "UNKNOWN"
    open_intents:       int            # signals approved but never positioned/closed
    duplicate_count:    int
    checksum:           str            # SHA-256 hex of sorted event content


# ── ReplayValidator ───────────────────────────────────────────────────────────

class ReplayValidator:
    """Validates the OpenClaw replay journal for correctness and consistency.

    Usage:
        report = ReplayValidator.validate_file("data/replay_journal.jsonl")
        if not report.passed:
            for issue in report.issues:
                if issue.severity == "ERROR":
                    print(issue.description)
    """

    # Maximum time gap (seconds) between signal_generated and its corresponding
    # intent_approved / intent_rejected with the same trace_id.
    SIGNAL_INTENT_WINDOW_SEC = 5

    # Two events are considered duplicates if they share the same event_type and
    # trace_id and are within this many seconds of each other.
    DUPLICATE_WINDOW_SEC = 1

    # ── Class-level entry point ───────────────────────────────────────────────

    @classmethod
    def validate_file(cls, journal_path: str = "data/replay_journal.jsonl") -> "ReplayValidationReport":
        """Validate a replay journal file, handling file-not-found gracefully.

        Args:
            journal_path: Path to the JSONL journal file.

        Returns:
            ReplayValidationReport with passed=False and a single ERROR issue
            if the file cannot be opened.
        """
        path = Path(journal_path)
        if not path.exists():
            issue = ReplayIssue(
                severity="ERROR",
                event_index=-1,
                event_type="",
                description=f"Journal file not found: {path}",
            )
            return ReplayValidationReport(
                passed=False,
                total_events=0,
                valid_events=0,
                invalid_events=0,
                issues=[issue],
                capital_state_final="UNKNOWN",
                open_intents=0,
                duplicate_count=0,
                checksum="",
            )

        return cls().validate(str(path))

    # ── Instance-level validate ───────────────────────────────────────────────

    def validate(self, journal_path: str) -> ReplayValidationReport:
        """Run all checks on a replay journal file.

        Args:
            journal_path: Absolute or relative path to the JSONL journal.

        Returns:
            Fully populated ReplayValidationReport.
        """
        issues: List[ReplayIssue] = []

        # ── Step 1: Parse every line ──────────────────────────────────────────
        raw_events, parse_issues, invalid_count = self._parse_lines(journal_path)
        issues.extend(parse_issues)

        total_events = len(raw_events) + invalid_count
        valid_events = len(raw_events)

        if not raw_events:
            # Nothing valid to analyse further.
            checksum = self._compute_checksum([])
            has_errors = any(i.severity == "ERROR" for i in issues)
            return ReplayValidationReport(
                passed=not has_errors,
                total_events=total_events,
                valid_events=valid_events,
                invalid_events=invalid_count,
                issues=issues,
                capital_state_final="UNKNOWN",
                open_intents=0,
                duplicate_count=0,
                checksum=checksum,
            )

        # ── Step 2: Sort events by ts ─────────────────────────────────────────
        sorted_events, sort_issues = self._sort_and_check_order(raw_events)
        issues.extend(sort_issues)

        # ── Step 3: Signal -> intent verdict pairing ──────────────────────────
        issues.extend(self._check_signal_intent_pairing(sorted_events))

        # ── Step 4: Capital state machine validity ────────────────────────────
        capital_state_final, state_issues = self._check_capital_state_machine(sorted_events)
        issues.extend(state_issues)

        # ── Step 5: Duplicate event detection ─────────────────────────────────
        dup_issues, duplicate_count = self._check_duplicates(sorted_events)
        issues.extend(dup_issues)

        # ── Step 6: Time-backwards events (already detected in step 2, counted separately)
        #           No additional work needed here.

        # ── Step 7: Open intents (approved but never positioned/closed) ───────
        open_intents = self._count_open_intents(sorted_events)

        # ── Step 8: Checksum ──────────────────────────────────────────────────
        checksum = self._compute_checksum(sorted_events)

        # ── Step 9: Determine overall pass/fail ───────────────────────────────
        has_errors = any(i.severity == "ERROR" for i in issues)
        passed = not has_errors

        return ReplayValidationReport(
            passed=passed,
            total_events=total_events,
            valid_events=valid_events,
            invalid_events=invalid_count,
            issues=issues,
            capital_state_final=capital_state_final,
            open_intents=open_intents,
            duplicate_count=duplicate_count,
            checksum=checksum,
        )

    # ── Step implementations ──────────────────────────────────────────────────

    def _parse_lines(
        self, journal_path: str
    ) -> Tuple[List[Dict[str, Any]], List[ReplayIssue], int]:
        """Read and JSON-decode every line in the journal.

        Returns:
            (valid_events, issues, invalid_count)
        """
        valid_events: List[Dict[str, Any]] = []
        issues: List[ReplayIssue] = []
        invalid_count = 0
        line_number = 0

        try:
            with open(journal_path, "r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line_number += 1
                    line = raw_line.strip()
                    if not line:
                        continue  # skip blank lines silently

                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError as exc:
                        invalid_count += 1
                        issues.append(ReplayIssue(
                            severity="ERROR",
                            event_index=line_number - 1,
                            event_type="",
                            description=(
                                f"Line {line_number}: JSONDecodeError — {exc}; "
                                f"raw content: {line[:120]!r}"
                            ),
                        ))
                        continue

                    if not isinstance(entry, dict):
                        invalid_count += 1
                        issues.append(ReplayIssue(
                            severity="ERROR",
                            event_index=line_number - 1,
                            event_type="",
                            description=f"Line {line_number}: parsed value is not a JSON object",
                        ))
                        continue

                    # Attach the source line number for tracing.
                    entry["_line"] = line_number
                    valid_events.append(entry)

        except OSError as exc:
            issues.append(ReplayIssue(
                severity="ERROR",
                event_index=-1,
                event_type="",
                description=f"Could not open journal file: {exc}",
            ))

        return valid_events, issues, invalid_count

    def _sort_and_check_order(
        self, events: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[ReplayIssue]]:
        """Sort events by ts (ascending) and flag any time-backwards occurrences.

        Timestamps are normalised to UTC-aware datetimes. Events without a
        parseable ts are placed at the end and flagged as WARNING.

        Returns:
            (sorted_events, issues)
        """
        issues: List[ReplayIssue] = []
        parsed: List[Tuple[Optional[datetime], Dict[str, Any]]] = []

        for idx, evt in enumerate(events):
            ts_str = evt.get("ts")
            dt = self._parse_ts(ts_str)
            if dt is None:
                issues.append(ReplayIssue(
                    severity="WARNING",
                    event_index=idx,
                    event_type=str(evt.get("event_type", "")),
                    description=(
                        f"Event at source line {evt.get('_line', '?')} has unparseable "
                        f"timestamp: {ts_str!r} — placed at end of sorted order"
                    ),
                ))
            parsed.append((dt, evt))

        # Sort: events with a valid ts first (ascending), then ts=None at the end.
        parsed.sort(key=lambda x: (x[0] is None, x[0] or datetime.min.replace(tzinfo=timezone.utc)))
        sorted_events = [evt for _, evt in parsed]
        sorted_dts = [dt for dt, _ in parsed]

        # Detect time-backwards events in the ORIGINAL (pre-sort) order.
        # We compare against the original sequence to catch when the bot itself
        # wrote events out-of-order, not just ordering artefacts.
        prev_dt: Optional[datetime] = None
        for idx, evt in enumerate(events):
            ts_str = evt.get("ts")
            dt = self._parse_ts(ts_str)
            if dt is None:
                prev_dt = None
                continue
            if prev_dt is not None and dt < prev_dt:
                issues.append(ReplayIssue(
                    severity="WARNING",
                    event_index=idx,
                    event_type=str(evt.get("event_type", "")),
                    description=(
                        f"Time-backwards event at source line {evt.get('_line', '?')}: "
                        f"ts={ts_str!r} is before previous ts={prev_dt.isoformat()!r}"
                    ),
                ))
            prev_dt = dt

        return sorted_events, issues

    def _check_signal_intent_pairing(
        self, events: List[Dict[str, Any]]
    ) -> List[ReplayIssue]:
        """For each signal_generated, verify a matching intent verdict exists.

        Matching criteria:
          - Same trace_id
          - event_type is intent_approved or intent_rejected
          - Verdict ts is within SIGNAL_INTENT_WINDOW_SEC after the signal ts

        Unmatched signals are flagged as WARNING (the verdict may arrive in a
        later journal rotation, but missing verdicts indicate pipeline gaps).
        """
        issues: List[ReplayIssue] = []

        # Build an index: trace_id -> list of (sorted_index, event) for verdict events.
        verdict_index: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
        for idx, evt in enumerate(events):
            if evt.get("event_type") in _INTENT_RESOLUTION_EVENTS:
                tid = evt.get("trace_id")
                if tid:
                    verdict_index.setdefault(tid, []).append((idx, evt))

        for idx, evt in enumerate(events):
            if evt.get("event_type") != ET_SIGNAL_GENERATED:
                continue

            trace_id = evt.get("trace_id")
            signal_ts = self._parse_ts(evt.get("ts"))

            if not trace_id:
                issues.append(ReplayIssue(
                    severity="WARNING",
                    event_index=idx,
                    event_type=ET_SIGNAL_GENERATED,
                    description=(
                        f"signal_generated at source line {evt.get('_line', '?')} "
                        f"has no trace_id — cannot match to intent verdict"
                    ),
                ))
                continue

            matching_verdicts = verdict_index.get(trace_id, [])

            if not matching_verdicts:
                issues.append(ReplayIssue(
                    severity="WARNING",
                    event_index=idx,
                    event_type=ET_SIGNAL_GENERATED,
                    description=(
                        f"signal_generated (trace_id={trace_id!r}) at source line "
                        f"{evt.get('_line', '?')} has no matching intent_approved or "
                        f"intent_rejected in the journal"
                    ),
                ))
                continue

            # Check that at least one verdict falls within the time window.
            if signal_ts is None:
                # Can't check timing; skip window check but note we found a verdict.
                continue

            window_ok = False
            for _, verdict_evt in matching_verdicts:
                verdict_ts = self._parse_ts(verdict_evt.get("ts"))
                if verdict_ts is None:
                    continue
                delta = (verdict_ts - signal_ts).total_seconds()
                if 0 <= delta <= self.SIGNAL_INTENT_WINDOW_SEC:
                    window_ok = True
                    break
                # Also accept verdicts that arrived slightly before (clock skew tolerance: 0.5s)
                if -0.5 <= delta < 0:
                    window_ok = True
                    break

            if not window_ok:
                # Find the closest verdict for a useful error message.
                closest_delta = min(
                    abs((self._parse_ts(v.get("ts")) - signal_ts).total_seconds())
                    for _, v in matching_verdicts
                    if self._parse_ts(v.get("ts")) is not None
                ) if any(self._parse_ts(v.get("ts")) for _, v in matching_verdicts) else float("inf")

                issues.append(ReplayIssue(
                    severity="WARNING",
                    event_index=idx,
                    event_type=ET_SIGNAL_GENERATED,
                    description=(
                        f"signal_generated (trace_id={trace_id!r}) at source line "
                        f"{evt.get('_line', '?')} has no matching verdict within "
                        f"{self.SIGNAL_INTENT_WINDOW_SEC}s "
                        f"(closest verdict delta={closest_delta:.3f}s)"
                    ),
                ))

        return issues

    def _check_capital_state_machine(
        self, events: List[Dict[str, Any]]
    ) -> Tuple[str, List[ReplayIssue]]:
        """Replay capital_state_change events and validate each transition.

        Returns:
            (final_state_str, issues)
            final_state_str is "UNKNOWN" if no capital_state_change events exist.
        """
        issues: List[ReplayIssue] = []
        current_state: Optional[str] = None
        final_state = "UNKNOWN"

        for idx, evt in enumerate(events):
            if evt.get("event_type") != ET_CAPITAL_STATE:
                continue

            payload = evt.get("payload", {})
            old_state = str(payload.get("old_state", "")).upper()
            new_state = str(payload.get("new_state", "")).upper()

            # Validate that both states are known.
            if old_state not in _ALL_CAPITAL_STATES:
                issues.append(ReplayIssue(
                    severity="ERROR",
                    event_index=idx,
                    event_type=ET_CAPITAL_STATE,
                    description=(
                        f"capital_state_change at source line {evt.get('_line', '?')} "
                        f"has unknown old_state={old_state!r}"
                    ),
                ))
                continue

            if new_state not in _ALL_CAPITAL_STATES:
                issues.append(ReplayIssue(
                    severity="ERROR",
                    event_index=idx,
                    event_type=ET_CAPITAL_STATE,
                    description=(
                        f"capital_state_change at source line {evt.get('_line', '?')} "
                        f"has unknown new_state={new_state!r}"
                    ),
                ))
                continue

            # Validate continuity: old_state must match the state we tracked.
            if current_state is not None and old_state != current_state:
                issues.append(ReplayIssue(
                    severity="ERROR",
                    event_index=idx,
                    event_type=ET_CAPITAL_STATE,
                    description=(
                        f"capital_state_change at source line {evt.get('_line', '?')}: "
                        f"old_state={old_state!r} does not match expected "
                        f"current_state={current_state!r} — possible missing event or "
                        f"journal corruption"
                    ),
                ))

            # Validate that the transition is legal.
            legal_targets = _LEGAL_TRANSITIONS.get(old_state, set())
            if new_state not in legal_targets:
                issues.append(ReplayIssue(
                    severity="ERROR",
                    event_index=idx,
                    event_type=ET_CAPITAL_STATE,
                    description=(
                        f"capital_state_change at source line {evt.get('_line', '?')}: "
                        f"illegal transition {old_state!r} -> {new_state!r}. "
                        f"Legal targets from {old_state!r}: {sorted(legal_targets)}"
                    ),
                ))

            current_state = new_state
            final_state = new_state

        return final_state, issues

    def _check_duplicates(
        self, events: List[Dict[str, Any]]
    ) -> Tuple[List[ReplayIssue], int]:
        """Detect duplicate events: same event_type + trace_id within 1 second.

        Events with trace_id=None are excluded from duplicate detection
        (they are anonymous global events and cannot be deduplicated by trace_id).

        Returns:
            (issues, duplicate_count)
        """
        issues: List[ReplayIssue] = []
        duplicate_count = 0

        # key=(event_type, trace_id) -> list of (sorted_index, ts_datetime)
        seen: Dict[Tuple[str, str], List[Tuple[int, datetime]]] = {}

        for idx, evt in enumerate(events):
            event_type = str(evt.get("event_type", ""))
            trace_id = evt.get("trace_id")

            if trace_id is None:
                continue  # cannot dedup anonymous events

            trace_id = str(trace_id)
            ts = self._parse_ts(evt.get("ts"))
            if ts is None:
                continue  # can't compare timing

            key = (event_type, trace_id)
            prior = seen.get(key, [])

            for prior_idx, prior_ts in prior:
                delta = abs((ts - prior_ts).total_seconds())
                if delta <= self.DUPLICATE_WINDOW_SEC:
                    duplicate_count += 1
                    issues.append(ReplayIssue(
                        severity="WARNING",
                        event_index=idx,
                        event_type=event_type,
                        description=(
                            f"Duplicate event detected: event_type={event_type!r}, "
                            f"trace_id={trace_id!r} at source line "
                            f"{evt.get('_line', '?')} appears again within "
                            f"{delta:.3f}s of event at sorted index {prior_idx} "
                            f"(threshold: {self.DUPLICATE_WINDOW_SEC}s)"
                        ),
                    ))
                    break  # report once per duplicate pair

            seen.setdefault(key, []).append((idx, ts))

        return issues, duplicate_count

    def _count_open_intents(self, events: List[Dict[str, Any]]) -> int:
        """Count intents that were approved but never resolved by a position event.

        An intent is "open" if:
          - There is an intent_approved event with a given trace_id
          - There is NO position_opened or position_closed event with the same
            trace_id anywhere in the journal

        Note: an approved intent may have been rejected at execution time (e.g.
        exchange error) — those count as open intents in the journal view.
        """
        approved_traces: Set[str] = set()
        resolved_traces: Set[str] = set()

        for evt in events:
            event_type = evt.get("event_type")
            trace_id = evt.get("trace_id")
            if not trace_id:
                continue

            if event_type == ET_INTENT_APPROVED:
                approved_traces.add(str(trace_id))
            elif event_type in _INTENT_CLOSE_EVENTS:
                resolved_traces.add(str(trace_id))

        open_intents = approved_traces - resolved_traces
        return len(open_intents)

    def _compute_checksum(self, events: List[Dict[str, Any]]) -> str:
        """Compute a deterministic SHA-256 checksum of all event content.

        Events are sorted by ts (already sorted by this point) before hashing
        so the checksum is stable regardless of the order events appear in the
        file. Internal bookkeeping keys (prefixed with '_') are stripped before
        hashing to keep the checksum independent of validator internals.

        Returns the hex-encoded SHA-256 digest, or "" if events is empty.
        """
        if not events:
            return ""

        digest = hashlib.sha256()

        # Sort by ts string to guarantee stable ordering across runs.
        sorted_for_hash = sorted(
            events,
            key=lambda e: (e.get("ts") or "", e.get("event_type") or ""),
        )

        for evt in sorted_for_hash:
            # Strip internal validator keys before hashing.
            clean = {k: v for k, v in evt.items() if not k.startswith("_")}
            # Produce a stable, sorted-key JSON string.
            serialised = json.dumps(clean, sort_keys=True, default=str)
            digest.update(serialised.encode("utf-8"))

        return digest.hexdigest()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_ts(ts_str: Any) -> Optional[datetime]:
        """Parse an ISO 8601 timestamp string to a UTC-aware datetime.

        Returns None if the string cannot be parsed.
        """
        if not ts_str or not isinstance(ts_str, str):
            return None

        # Python 3.7+ fromisoformat doesn't handle 'Z' suffix.
        normalised = ts_str.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalised)
        except (ValueError, OverflowError):
            return None

        # Ensure UTC-aware.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)

        return dt
