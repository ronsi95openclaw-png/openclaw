"""Dashboard action audit logger.

Appends immutable JSONL records for all privileged dashboard actions.
File: data/dashboard_audit.jsonl

Design rules:
- Atomic JSONL append with fcntl.LOCK_EX.
- Never raises — all errors are swallowed after logging.
- Thread-safe: append uses fcntl advisory lock so concurrent processes are safe.
- 30-day retention: files older than 30 days are archived to
  data/dashboard_audit_YYYYMM.jsonl before each write.
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
from typing import List, Optional

logger = logging.getLogger("openclaw.dashboard.audit")

_AUDIT_PATH = Path("data/dashboard_audit.jsonl")


# ── Rotation helpers ──────────────────────────────────────────────────────────


def _get_file_age_days(path: Path) -> float:
    """Return age in days of the file (from mtime). Returns 0 if not exists."""
    try:
        if not path.exists():
            return 0.0
        mtime = path.stat().st_mtime
        now   = datetime.now(timezone.utc).timestamp()
        return max(0.0, (now - mtime) / 86400.0)
    except Exception:
        return 0.0


def _archive_audit_file(path: Path) -> Optional[Path]:
    """Archive audit file to data/dashboard_audit_YYYYMM.jsonl.

    Steps:
    1. Compute archive filename from file's mtime (not current date).
    2. If archive does not exist: os.replace(path, archive_path) — atomic.
    3. If archive exists (merge case): append path's lines to archive with
       fcntl.LOCK_EX, then delete path.
    4. Return archive_path, or None on failure.

    Never raises.
    """
    try:
        if not path.exists():
            return None
        # Derive archive name from file's own mtime
        mtime_ts   = path.stat().st_mtime
        mtime_dt   = datetime.fromtimestamp(mtime_ts, tz=timezone.utc)
        archive_path = path.parent / f"dashboard_audit_{mtime_dt.strftime('%Y%m')}.jsonl"

        if not archive_path.exists():
            # Atomic rename — no data loss
            os.replace(str(path), str(archive_path))
            logger.info("audit: rotated %s → %s", path, archive_path)
            return archive_path

        # Archive already exists — merge by appending
        try:
            lines = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.debug("audit: could not read %s for merge: %s", path, exc)
            return None

        with open(archive_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(lines)
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

        path.unlink(missing_ok=True)
        logger.info("audit: merged %s into existing archive %s", path, archive_path)
        return archive_path

    except Exception as exc:
        logger.debug("audit: _archive_audit_file failed (non-fatal): %s", exc)
        return None


def _check_and_rotate(path: Path, max_age_days: int = 30) -> bool:
    """Rotate audit file to archive if age > max_age_days.

    Returns True if rotation occurred.
    Called inside append_audit_event before each write.
    """
    try:
        age = _get_file_age_days(path)
        if age <= max_age_days:
            return False
        result = _archive_audit_file(path)
        return result is not None
    except Exception as exc:
        logger.debug("audit: _check_and_rotate failed (non-fatal): %s", exc)
        return False


# ── Dataclass ─────────────────────────────────────────────────────────────────


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
    """Atomic JSONL append with fcntl.LOCK_EX. Rotates file if older than 30 days. Never raises."""
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Rotation is best-effort: if it fails, still append to current file
        try:
            _check_and_rotate(_AUDIT_PATH)
        except Exception as _re:
            logger.debug("audit: rotation pre-check failed (non-fatal): %s", _re)
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


def get_retention_status() -> dict:
    """Return audit file age, line count, and archive list."""
    try:
        age   = _get_file_age_days(_AUDIT_PATH)
        count = (sum(1 for ln in _AUDIT_PATH.open(encoding="utf-8", errors="replace")
                     if ln.strip())
                 if _AUDIT_PATH.exists() else 0)
        archives = sorted(_AUDIT_PATH.parent.glob("dashboard_audit_*.jsonl"))
        return {
            "current_file":  str(_AUDIT_PATH),
            "age_days":      round(age, 1),
            "line_count":    count,
            "max_age_days":  30,
            "archive_count": len(archives),
            "archives":      [str(a) for a in archives],
        }
    except Exception:
        return {"status": "unavailable"}
