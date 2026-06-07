"""Append-only audit log for privileged shell/python commands.

Every /run and /py invocation — allowed *or* blocked — is recorded as a single
JSON line in ``data/logs/audit.log``. The log path is overridable for tests via
``set_log_path``.

Design rules:
    * Best-effort: this module must never raise. If we can't write, we swallow.
    * Dependency-free (stdlib only).
    * One JSON object per line (jsonl) so it's easy to grep/parse later.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Default log path: <repo_root>/data/logs/audit.log
_DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "logs" / "audit.log"
_log_path: Path = _DEFAULT_LOG_PATH

_MAX_COMMAND_LEN = 1000


def set_log_path(path: os.PathLike | str) -> None:
    """Override the audit log path (used by tests to keep them hermetic)."""
    global _log_path
    _log_path = Path(path)


def get_log_path() -> Path:
    """Return the current audit log path (handy for tests / debugging)."""
    return _log_path


def log_command(
    actor: str,
    command: str,
    source: str = "run",
    outcome: str = "allowed",
) -> None:
    """Append a JSON audit record. Best-effort — never raises.

    Args:
        actor:   Telegram chat_id (as str) or any identifier; falls back to
                 ``"unknown"`` if empty.
        command: The command string (truncated to 1000 chars).
        source:  ``"run"`` or ``"py"``.
        outcome: ``"allowed"`` or ``"blocked"`` (or any other tag callers want).
    """
    try:
        cmd = command or ""
        if len(cmd) > _MAX_COMMAND_LEN:
            cmd = cmd[:_MAX_COMMAND_LEN] + "...(truncated)"

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor or "unknown",
            "source": source,
            "outcome": outcome,
            "command": cmd,
        }

        _log_path.parent.mkdir(parents=True, exist_ok=True)
        with _log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Audit failures must never break command execution. Swallow.
        return
