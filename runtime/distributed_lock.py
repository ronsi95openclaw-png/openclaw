"""Filesystem-based distributed lock for single-leader coordination.

Uses atomic file operations and split-brain detection to provide safe
single-leader election across processes on the same filesystem.

Key properties:
- Atomic acquire via tmp+os.replace with fcntl.LOCK_EX
- Split-brain prevention: re-reads lock within 50ms after write to verify
- TTL-based expiry: stale locks are force-expired before re-acquisition
- Fail-CLOSED: any ambiguous state returns False (not acquired)

Thread safety: all public methods acquire _lock.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger("openclaw.runtime.distributed_lock")

# ── Constants ─────────────────────────────────────────────────────────────────

_SPLIT_BRAIN_VERIFY_DELAY_S = 0.050    # 50ms re-read window
_DEFAULT_TTL_SECONDS         = 60
_DEFAULT_RETRY_INTERVAL_MS   = 500
_DEFAULT_MAX_RETRIES         = 10
_LOCK_DIR_DEFAULT            = "data/locks"


# ── Enums ─────────────────────────────────────────────────────────────────────

class LockStatus(str, Enum):
    ACQUIRED  = "ACQUIRED"
    RELEASED  = "RELEASED"
    EXPIRED   = "EXPIRED"
    CONFLICT  = "CONFLICT"


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class LockRecord:
    lock_id:       str    # UUID
    holder:        str
    acquired_at:   float  # monotonic time
    ttl_seconds:   int
    resource_name: str
    fencing_token: int = 0  # monotonically increasing per resource


# ── Fencing token store ───────────────────────────────────────────────────────

_FENCING_TOKENS: Dict[str, int] = {}   # resource_name → next token value
_FENCING_LOCK = threading.Lock()


def _get_next_fencing_token(resource_name: str) -> int:
    """Returns a monotonically increasing token for this resource.
    Persisted to data/fencing_tokens.json atomically."""
    with _FENCING_LOCK:
        token = _FENCING_TOKENS.get(resource_name, 0) + 1
        _FENCING_TOKENS[resource_name] = token
        try:
            _persist_fencing_tokens()
        except Exception:
            pass
        return token


def _persist_fencing_tokens() -> None:
    """Atomically write fencing tokens to disk."""
    path = Path("data/fencing_tokens.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(_FENCING_TOKENS, f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def _load_fencing_tokens() -> None:
    """Load persisted fencing tokens at startup."""
    global _FENCING_TOKENS
    path = Path("data/fencing_tokens.json")
    if not path.exists():
        return
    try:
        with open(path) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
                _FENCING_TOKENS.update(data)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception:
        pass


# ── DistributedLock ───────────────────────────────────────────────────────────

class DistributedLock:
    """Filesystem-based distributed lock.

    Lock file schema:
        {
            "lock_id": "<uuid>",
            "holder": "<holder_id>",
            "acquired_at": <float monotonic>,
            "ttl_seconds": <int>,
            "expires_at": <float monotonic>,
            "resource_name": "<str>"
        }

    Acquire guarantee: after writing, re-reads within 50ms to detect split-brain.
    Fail-CLOSED: returns False if any ambiguity is detected.
    """

    def __init__(
        self,
        resource_name: str,
        lock_dir: str = _LOCK_DIR_DEFAULT,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        retry_interval_ms: int = _DEFAULT_RETRY_INTERVAL_MS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        if not resource_name or not resource_name.strip():
            raise ValueError("resource_name must be a non-empty string")
        self._resource_name    = resource_name
        self._lock_dir         = Path(lock_dir)
        self._ttl_seconds      = ttl_seconds
        self._retry_interval_s = retry_interval_ms / 1000.0
        self._max_retries      = max_retries
        self._thread_lock      = threading.Lock()
        self._held_fencing_token: Optional[int] = None  # set on acquire_with_fencing

        # Ensure lock directory exists
        try:
            self._lock_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Cannot create lock dir %s: %s", self._lock_dir, exc)

    @property
    def _lock_file(self) -> Path:
        return self._lock_dir / f"{self._resource_name}.lock"

    # ── Read helpers ──────────────────────────────────────────────────────────

    def _read_lock_file(self) -> Optional[dict]:
        """Read and parse lock file. Returns None if not found or invalid."""
        try:
            with open(self._lock_file, "r", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_SH)
                try:
                    data = json.load(fh)
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
            return data
        except (OSError, json.JSONDecodeError):
            return None

    def _is_expired(self, lock_data: dict) -> bool:
        """True if the lock's TTL has elapsed (using monotonic clock)."""
        expires_at = lock_data.get("expires_at", 0.0)
        return time.monotonic() > expires_at

    # ── Write helpers ─────────────────────────────────────────────────────────

    def _write_lock(self, holder_id: str, lock_id: str, ttl: int) -> bool:
        """Atomically write a new lock file. Returns False on any failure."""
        now        = time.monotonic()
        lock_data  = {
            "lock_id":       lock_id,
            "holder":        holder_id,
            "acquired_at":   now,
            "ttl_seconds":   ttl,
            "expires_at":    now + ttl,
            "resource_name": self._resource_name,
        }
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._lock_dir), suffix=".lock.tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fcntl.flock(fh, fcntl.LOCK_EX)
                    try:
                        json.dump(lock_data, fh)
                    finally:
                        fcntl.flock(fh, fcntl.LOCK_UN)
                os.replace(tmp_path, self._lock_file)
                return True
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return False
        except OSError as exc:
            logger.error("Lock write failed for %s: %s", self._resource_name, exc)
            return False

    # ── Public interface ──────────────────────────────────────────────────────

    def acquire(self, holder_id: str) -> bool:
        """Try to acquire the lock. Returns False if held by another or error.

        Split-brain prevention: re-reads lock file within 50ms after write.
        If the holder on disk doesn't match self, returns False.
        """
        if not holder_id or not holder_id.strip():
            logger.error("acquire(): holder_id must be non-empty")
            return False

        with self._thread_lock:
            # Check for existing lock
            existing = self._read_lock_file()
            if existing is not None:
                if not self._is_expired(existing):
                    if existing.get("holder") != holder_id:
                        logger.debug(
                            "Lock %s held by %s — cannot acquire for %s",
                            self._resource_name, existing.get("holder"), holder_id,
                        )
                        return False
                    # Already held by this holder
                    return True
                else:
                    # Expired — force-expire before re-acquiring
                    logger.warning(
                        "Stale lock found for %s (holder=%s, expired) — force-expiring",
                        self._resource_name, existing.get("holder", "unknown"),
                    )
                    self.force_expire(reason="stale_on_acquire")

            lock_id = str(uuid.uuid4())
            ttl     = self._ttl_seconds

            if not self._write_lock(holder_id, lock_id, ttl):
                return False

            # Split-brain detection: re-read within 50ms
            time.sleep(_SPLIT_BRAIN_VERIFY_DELAY_S)
            verify = self._read_lock_file()
            if verify is None or verify.get("holder") != holder_id or verify.get("lock_id") != lock_id:
                found_lock_id = verify.get("lock_id", "") if verify is not None else ""
                logger.warning(
                    "Split-brain detected for %s — another process won the race",
                    self._resource_name,
                )
                self._append_split_brain_audit(holder_id, lock_id, found_lock_id)
                return False

            logger.debug("Lock acquired: %s by %s", self._resource_name, holder_id)
            return True

    def release(self, holder_id: str) -> bool:
        """Release the lock if holder matches. Returns False if not the holder."""
        if not holder_id or not holder_id.strip():
            return False

        with self._thread_lock:
            existing = self._read_lock_file()
            if existing is None:
                self._held_fencing_token = None
                return True  # Already released
            if existing.get("holder") != holder_id:
                logger.warning(
                    "release(): %s is not the holder of %s (holder=%s)",
                    holder_id, self._resource_name, existing.get("holder"),
                )
                return False
            try:
                os.unlink(self._lock_file)
                self._held_fencing_token = None
                logger.debug("Lock released: %s by %s", self._resource_name, holder_id)
                return True
            except OSError as exc:
                logger.error("Failed to release lock %s: %s", self._resource_name, exc)
                return False

    def renew(self, holder_id: str, ttl_seconds: Optional[int] = None) -> bool:
        """Extend TTL for an existing held lock. Requires matching holder.

        Does NOT renew an expired lock — require fresh acquire instead.
        """
        if not holder_id or not holder_id.strip():
            return False

        with self._thread_lock:
            existing = self._read_lock_file()
            if existing is None:
                logger.warning("renew(): no lock exists for %s", self._resource_name)
                return False
            if existing.get("holder") != holder_id:
                logger.warning(
                    "renew(): %s is not the holder of %s", holder_id, self._resource_name
                )
                return False
            if self._is_expired(existing):
                logger.warning(
                    "renew(): lock %s is expired — must re-acquire", self._resource_name
                )
                return False

            ttl      = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
            lock_id  = existing.get("lock_id", str(uuid.uuid4()))
            now      = time.monotonic()
            renewed  = {
                "lock_id":       lock_id,
                "holder":        holder_id,
                "acquired_at":   existing.get("acquired_at", now),
                "ttl_seconds":   ttl,
                "expires_at":    now + ttl,
                "resource_name": self._resource_name,
            }

            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._lock_dir), suffix=".lock.tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fcntl.flock(fh, fcntl.LOCK_EX)
                    try:
                        json.dump(renewed, fh)
                    finally:
                        fcntl.flock(fh, fcntl.LOCK_UN)
                os.replace(tmp_path, self._lock_file)
                logger.debug("Lock renewed: %s by %s (ttl=%ds)", self._resource_name, holder_id, ttl)
                return True
            except Exception as exc:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                logger.error("Lock renewal failed for %s: %s", self._resource_name, exc)
                return False

    def is_held_by(self, holder_id: str) -> bool:
        """True if this lock is currently held by holder_id and not expired."""
        existing = self._read_lock_file()
        if existing is None:
            return False
        if self._is_expired(existing):
            return False
        return existing.get("holder") == holder_id

    def get_current_holder(self) -> Optional[str]:
        """Return the holder_id of the current lock, or None if free/expired."""
        existing = self._read_lock_file()
        if existing is None:
            return None
        if self._is_expired(existing):
            return None
        return existing.get("holder")

    def force_expire(self, reason: str = "") -> bool:
        """Forcibly expire a stale lock. ONLY allowed if TTL has elapsed.

        Logs a WARNING with the expired holder and duration.
        Returns False if the lock is not yet expired (fail-CLOSED).
        """
        existing = self._read_lock_file()
        if existing is None:
            return True  # Already gone

        if not self._is_expired(existing):
            logger.error(
                "force_expire() refused for %s — lock is NOT expired (holder=%s)",
                self._resource_name, existing.get("holder"),
            )
            return False

        expired_holder  = existing.get("holder", "unknown")
        acquired_at     = existing.get("acquired_at", 0.0)
        held_duration   = time.monotonic() - acquired_at

        try:
            os.unlink(self._lock_file)
            logger.warning(
                "force_expire: lock %s expired (holder=%s, held_for=%.1fs, reason=%s)",
                self._resource_name, expired_holder, held_duration, reason,
            )
            return True
        except OSError as exc:
            logger.error(
                "force_expire: failed to delete lock file %s: %s", self._lock_file, exc
            )
            return False

    # ── Fencing token interface ───────────────────────────────────────────────

    def get_fencing_token(self) -> Optional[int]:
        """Return the fencing token of the currently held lock, or None if not held."""
        with self._thread_lock:
            return self._held_fencing_token

    def is_write_safe(self, expected_fencing_token: int) -> bool:
        """True if this lock is held AND the fencing token matches expected.

        Fail-closed: returns False on any mismatch or if not held.
        Used by writers to validate they still hold the current epoch before writing.
        """
        try:
            with self._thread_lock:
                if self._held_fencing_token is None:
                    return False
                return self._held_fencing_token == expected_fencing_token
        except Exception:
            return False

    def acquire_with_fencing(self, holder: str) -> Tuple[bool, int]:
        """Acquire the lock and return a monotonically increasing fencing token.

        Returns (True, token) on success, (False, 0) on failure.
        The fencing token is persisted atomically and is always strictly increasing.
        """
        if not holder or not holder.strip():
            logger.error("acquire_with_fencing(): holder must be non-empty")
            return (False, 0)

        # Get next fencing token before acquiring (pre-increment ensures monotonicity
        # even across failed acquisition attempts)
        token = _get_next_fencing_token(self._resource_name)

        acquired = self.acquire(holder)
        if not acquired:
            # Token was incremented but lock not acquired — token is still consumed
            # to maintain global monotonicity. Do not expose it to caller.
            with self._thread_lock:
                self._held_fencing_token = None
            return (False, 0)

        with self._thread_lock:
            self._held_fencing_token = token

        # Persist the fencing token in the lock file (best-effort)
        try:
            lock_data = self._read_lock_file()
            if lock_data is not None:
                lock_data["fencing_token"] = token
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(self._lock_dir), suffix=".lock.tmp"
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        fcntl.flock(fh, fcntl.LOCK_EX)
                        try:
                            json.dump(lock_data, fh)
                        finally:
                            fcntl.flock(fh, fcntl.LOCK_UN)
                    os.replace(tmp_path, self._lock_file)
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
        except Exception:
            pass

        logger.debug(
            "acquire_with_fencing: %s acquired by %s (token=%d)",
            self._resource_name, holder, token,
        )
        return (True, token)

    # ── Split-brain audit ─────────────────────────────────────────────────────

    def _append_split_brain_audit(
        self, holder: str, own_lock_id: str, found_lock_id: str
    ) -> None:
        """Persist split-brain detection event to audit log."""
        try:
            audit_path = Path("data/split_brain_audit.jsonl")
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "resource": self._resource_name,
                "attempted_holder": holder,
                "own_lock_id": own_lock_id,
                "found_lock_id": found_lock_id,
            }
            with open(audit_path, "a") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(entry) + "\n")
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            pass

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "DistributedLock":
        """Acquire with the resource_name as holder_id (single-process convenience)."""
        if not self.acquire(holder_id=self._resource_name):
            raise RuntimeError(
                f"Failed to acquire distributed lock: {self._resource_name}"
            )
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[object],
    ) -> bool:
        self.release(holder_id=self._resource_name)
        return False  # Never suppress exceptions


# ── Module initialisation ─────────────────────────────────────────────────────

_load_fencing_tokens()
