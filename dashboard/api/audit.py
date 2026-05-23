"""Dashboard action audit logger.

Appends immutable JSONL records for all privileged dashboard actions.
File: data/dashboard_audit.jsonl

Design rules:
- Atomic JSONL append with fcntl.LOCK_EX.
- Never raises — all errors are swallowed after logging.
- Thread-safe: append uses fcntl advisory lock so concurrent processes are safe.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List

logger = logging.getLogger("openclaw.dashboard.audit")

_AUDIT_PATH = Path("data/dashboard_audit.jsonl")


@dataclass
class DashboardAuditEvent:
    ts: str            # ISO8601
    action: str        # e.g., "ADVANCE_PHASE", "INJECT_CHAOS", "HALT_RELEASE"
    operator_id: str   # from request or "SYSTEM"
    client_ip: str
    trace_id: str      # UUID4
    params: dict       # sanitized action params (no secrets)
    result: str        # "SUCCESS", "FAILURE", "BLOCKED"
    detail: str        # short reason


def append_audit_event(event: DashboardAuditEvent) -> None:
    """Atomic JSONL append with fcntl.LOCK_EX. Never raises."""
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = asdict(event)
        with open(_AUDIT_PATH, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(json.dumps(record) + "\n")
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except Exception as exc:
        logger.debug("dashboard audit append failed: %s", exc)


def get_recent_events(n: int = 20) -> List[dict]:
    """Read last N lines from audit JSONL. Returns [] on any error."""
    try:
        if not _AUDIT_PATH.exists():
            return []
        lines = _AUDIT_PATH.read_text(encoding="utf-8").splitlines()
        records: List[dict] = []
        for ln in lines[-n:]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                records.append(json.loads(ln))
            except Exception:
                continue
        return list(reversed(records))
    except Exception as exc:
        logger.debug("dashboard audit read failed: %s", exc)
        return []


def make_trace_id() -> str:
    """Return a new UUID4 trace id string."""
    return str(uuid.uuid4())


def now_iso() -> str:
    """Return current UTC time as ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()
