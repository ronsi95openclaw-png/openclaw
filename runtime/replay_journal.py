"""Append-only AI decision journal for deterministic replay.

Every AI-influenced decision is recorded here: signal generation,
regime classification, intent approval/rejection, and risk scaling.
The journal can be replayed to reproduce any past decision sequence.

Format: one JSON object per line (JSONL).
All writes are atomic (write to temp then rename).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.runtime.replay_journal")


class ReplayJournal:
    """Append-only JSONL journal of all runtime AI decisions.

    Thread-safe. Each entry has:
      - event_type: what kind of decision was recorded
      - trace_id:   propagated from the originating scan/request
      - ts:         ISO8601 timestamp
      - payload:    event-specific data
    """

    # Registered event types
    EVENT_SCAN_START        = "scan_start"
    EVENT_REGIME_CLASSIFIED = "regime_classified"
    EVENT_SIGNAL_GENERATED  = "signal_generated"
    EVENT_INTENT_SUBMITTED  = "intent_submitted"
    EVENT_INTENT_APPROVED   = "intent_approved"
    EVENT_INTENT_REJECTED   = "intent_rejected"
    EVENT_CAPITAL_STATE     = "capital_state_change"
    EVENT_POSITION_OPENED   = "position_opened"
    EVENT_POSITION_CLOSED   = "position_closed"
    EVENT_RISK_OVERRIDE     = "risk_override"
    EVENT_KILL_SWITCH       = "kill_switch"
    EVENT_BRAIN_INFERENCE   = "brain_inference"   # AI model call

    def __init__(self, path: str = "data/replay_journal.jsonl",
                 max_size_mb: float = 100.0):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._max_bytes = int(max_size_mb * 1024 * 1024)

    # ── Write API ─────────────────────────────────────────────────────────────

    def record(self, event_type: str, trace_id: Optional[str],
               payload: Dict[str, Any]) -> None:
        entry = {
            "event_type": event_type,
            "trace_id":   trace_id,
            "ts":         datetime.now(timezone.utc).isoformat(),
            "payload":    payload,
        }
        self._append(entry)

    def record_regime(self, trace_id: Optional[str], symbol: str,
                      label: str, adx: float, atr_ratio: float,
                      confidence: float = 1.0) -> None:
        self.record(self.EVENT_REGIME_CLASSIFIED, trace_id, {
            "symbol": symbol, "label": label,
            "adx": adx, "atr_ratio": atr_ratio, "confidence": confidence,
        })

    def record_signal(self, trace_id: Optional[str], symbol: str,
                      strategy: str, action: str, confidence: float) -> None:
        self.record(self.EVENT_SIGNAL_GENERATED, trace_id, {
            "symbol": symbol, "strategy": strategy,
            "action": action, "confidence": confidence,
        })

    def record_intent_verdict(self, trace_id: Optional[str],
                               intent_id: str, approved: bool,
                               reason: str, risk_scalar: float,
                               adjusted_size_pct: float) -> None:
        event = self.EVENT_INTENT_APPROVED if approved else self.EVENT_INTENT_REJECTED
        self.record(event, trace_id, {
            "intent_id": intent_id, "approved": approved,
            "reason": reason, "risk_scalar": risk_scalar,
            "adjusted_size_pct": adjusted_size_pct,
        })

    def record_capital_state(self, trace_id: Optional[str],
                              old_state: str, new_state: str,
                              trigger: str, equity: float) -> None:
        self.record(self.EVENT_CAPITAL_STATE, trace_id, {
            "old_state": old_state, "new_state": new_state,
            "trigger": trigger, "equity": equity,
        })

    def record_brain_call(self, trace_id: Optional[str],
                           model: str, prompt_tokens: int,
                           response_tokens: int, latency_ms: float,
                           routed_to: str) -> None:
        self.record(self.EVENT_BRAIN_INFERENCE, trace_id, {
            "model": model, "prompt_tokens": prompt_tokens,
            "response_tokens": response_tokens,
            "latency_ms": latency_ms, "routed_to": routed_to,
        })

    # ── Read API (for replay) ─────────────────────────────────────────────────

    def load_events(self, event_type: Optional[str] = None,
                    trace_id: Optional[str] = None,
                    limit: int = 1000) -> List[Dict[str, Any]]:
        """Load journal entries, optionally filtered."""
        results = []
        if not self._path.exists():
            return results
        with self._lock:
            try:
                lines = self._path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return results

        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type and entry.get("event_type") != event_type:
                continue
            if trace_id and entry.get("trace_id") != trace_id:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    def replay_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Return all events for a given trace_id, in chronological order."""
        events = self.load_events(trace_id=trace_id, limit=10_000)
        return list(reversed(events))

    def get_stats(self) -> Dict[str, Any]:
        """Return journal statistics."""
        if not self._path.exists():
            return {"exists": False, "size_mb": 0, "line_count": 0}
        stat = self._path.stat()
        size_mb = stat.st_size / (1024 * 1024)
        try:
            line_count = sum(1 for _ in self._path.open("rb"))
        except OSError:
            line_count = 0
        return {"exists": True, "size_mb": round(size_mb, 2),
                "line_count": line_count,
                "rotation_needed": stat.st_size > self._max_bytes}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _append(self, entry: Dict[str, Any]) -> None:
        line = json.dumps(entry, default=str) + "\n"
        with self._lock:
            try:
                # Rotate before write if file has grown past the size cap
                if self._path.exists() and self._path.stat().st_size >= self._max_bytes:
                    self._rotate()
                # Atomic append: open in append mode (OS-level atomic on Linux)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError as exc:
                logger.error("ReplayJournal write failed: %s", exc)

    def _rotate(self) -> None:
        """Rename the current journal to a dated archive and start fresh."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = self._path.with_name(f"{self._path.stem}_{ts}.jsonl")
        try:
            self._path.rename(archive)
            logger.info("ReplayJournal rotated → %s", archive.name)
        except OSError as exc:
            logger.error("ReplayJournal rotation failed: %s", exc)
