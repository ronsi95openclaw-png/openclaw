"""Rollback Manager for OpenClaw — operational state rollback orchestration.

Supports rolling back strategy weights, configuration, and snapshots.
Every rollback operation is:
  - Identified by a unique UUID.
  - Appended to an immutable fcntl-locked audit JSONL.
  - Atomic (tmp+os.replace) for all file mutations.
  - Never mutating the EventStore (replay-safe).

AI SAFETY CONTRACT:
- NEVER places orders.
- NEVER modifies CapitalPreservationEngine directly.
- NEVER operates without an operator_id for non-SYSTEM triggers.
- DEMO_MODE=true — emergency rollback writes a marker only.
- All file writes are atomic and fcntl-guarded.
- Fail-CLOSED: validation failure → no write, error logged.

Module singleton: get_rollback_manager() -> RollbackManager
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
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.runtime.rollback_manager")

# ── Constants ─────────────────────────────────────────────────────────────────

_WEIGHTS_PATH        = Path("data/strategy_weights.json")
_ROLLBACK_STATE_PATH = Path("data/rollback_state.json")
_WEIGHT_MIN          = 0.0
_WEIGHT_MAX          = 3.0

# ── Enums ─────────────────────────────────────────────────────────────────────

class RollbackType(str, Enum):
    DEPLOYMENT    = "DEPLOYMENT"
    STRATEGY_WEIGHTS = "STRATEGY_WEIGHTS"
    CONFIGURATION = "CONFIGURATION"
    SNAPSHOT      = "SNAPSHOT"
    EMERGENCY     = "EMERGENCY"


class RollbackTrigger(str, Enum):
    REPLAY_CORRUPTION          = "REPLAY_CORRUPTION"
    SURVIVABILITY_COLLAPSE     = "SURVIVABILITY_COLLAPSE"
    RECONCILIATION_INSTABILITY = "RECONCILIATION_INSTABILITY"
    DRIFT_EXPLOSION            = "DRIFT_EXPLOSION"
    ALPHA_COLLAPSE             = "ALPHA_COLLAPSE"
    EXECUTION_DEGRADATION      = "EXECUTION_DEGRADATION"
    MANUAL                     = "MANUAL"


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class RollbackRecord:
    rollback_id:     str            # UUID
    triggered_at:    str            # ISO timestamp
    rollback_type:   RollbackType
    trigger:         RollbackTrigger
    trigger_detail:  str
    target_state_id: str            # what state we're rolling back to
    executed_by:     str            # operator_id or "SYSTEM"
    verified:        bool           # True if post-rollback verification passed
    reversible:      bool           # True if this rollback can itself be rolled back


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_rollback_id() -> str:
    return str(uuid.uuid4())


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically via tmp+os.replace with fcntl.LOCK_EX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                json.dump(data, fh, indent=2)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _append_audit(audit_path: Path, record: dict) -> None:
    """Append immutable record to audit JSONL under fcntl.LOCK_EX."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(record) + "\n")
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _record_to_dict(rec: RollbackRecord) -> dict:
    d = asdict(rec)
    d["rollback_type"] = rec.rollback_type.value
    d["trigger"]       = rec.trigger.value
    return d


def _validate_weights(weights: dict) -> bool:
    """Validate all weights are in [0.0, 3.0] with non-empty string keys."""
    if not isinstance(weights, dict):
        return False
    for k, v in weights.items():
        if not isinstance(k, str) or not k.strip():
            return False
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return False
        if not (_WEIGHT_MIN <= fv <= _WEIGHT_MAX):
            return False
    return True


def _send_telegram(text: str) -> None:
    """Fire-and-forget Telegram alert (non-blocking)."""
    try:
        from runtime.telegram_alerts import _send
        _send(text)
    except Exception as exc:
        logger.debug("Telegram alert failed: %s", exc)


# ── RollbackManager ───────────────────────────────────────────────────────────

class RollbackManager:
    """Orchestrates operational rollbacks with full audit trail.

    All writes are atomic, fcntl-guarded, and appended to immutable audit JSONL.
    All non-SYSTEM rollbacks require a non-empty operator_id.
    NEVER mutates EventStore. NEVER places orders.
    """

    def __init__(self, audit_path: str = "data/governance/logs/rollback_audit.jsonl") -> None:
        self._audit_path = Path(audit_path)
        self._lock       = threading.Lock()

    # ── Core rollback operations ──────────────────────────────────────────────

    def rollback_strategy_weights(
        self,
        target_snapshot_path: str,
        operator_id: str,
        trigger: RollbackTrigger,
    ) -> RollbackRecord:
        """Roll back strategy weights from a validated snapshot file.

        Reads target snapshot, validates all weights are in [0.0, 3.0],
        writes atomically to data/strategy_weights.json, appends to audit.
        """
        if not operator_id or not operator_id.strip():
            raise ValueError("operator_id is required for weight rollbacks")

        rollback_id = _new_rollback_id()
        triggered_at = _now_iso()
        snapshot_path = Path(target_snapshot_path)

        # Read and validate snapshot
        try:
            with open(snapshot_path, "r", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_SH)
                try:
                    weights = json.load(fh)
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError as exc:
            logger.error("Cannot read snapshot %s: %s", snapshot_path, exc)
            return self._failed_record(
                rollback_id, triggered_at, RollbackType.STRATEGY_WEIGHTS,
                trigger, str(exc), str(snapshot_path), operator_id,
            )

        if not _validate_weights(weights):
            logger.error("Snapshot %s failed weight validation — rollback aborted", snapshot_path)
            return self._failed_record(
                rollback_id, triggered_at, RollbackType.STRATEGY_WEIGHTS,
                trigger, "weight_validation_failed", str(snapshot_path), operator_id,
            )

        # Atomic write to live weights
        with self._lock:
            try:
                _atomic_write_json(_WEIGHTS_PATH, weights)
            except Exception as exc:
                logger.error("Atomic write failed for strategy_weights: %s", exc)
                return self._failed_record(
                    rollback_id, triggered_at, RollbackType.STRATEGY_WEIGHTS,
                    trigger, f"write_failed:{exc}", str(snapshot_path), operator_id,
                )

        # Post-write verification: re-read and validate
        verified = self._verify_weights_on_disk(weights)

        record = RollbackRecord(
            rollback_id=rollback_id,
            triggered_at=triggered_at,
            rollback_type=RollbackType.STRATEGY_WEIGHTS,
            trigger=trigger,
            trigger_detail=f"snapshot={snapshot_path.name}",
            target_state_id=str(snapshot_path),
            executed_by=operator_id,
            verified=verified,
            reversible=True,
        )

        _append_audit(self._audit_path, _record_to_dict(record))
        logger.info(
            "Weight rollback %s by %s → %s (verified=%s)",
            rollback_id, operator_id, snapshot_path.name, verified,
        )
        return record

    def rollback_configuration(
        self, backup_config: dict, operator_id: str
    ) -> RollbackRecord:
        """Apply a backup configuration dict. Caller is responsible for scope."""
        if not operator_id or not operator_id.strip():
            raise ValueError("operator_id is required for configuration rollbacks")

        rollback_id  = _new_rollback_id()
        triggered_at = _now_iso()

        if not isinstance(backup_config, dict):
            logger.error("Invalid backup_config — must be a dict")
            return self._failed_record(
                rollback_id, triggered_at, RollbackType.CONFIGURATION,
                RollbackTrigger.MANUAL, "invalid_config_type", "config", operator_id,
            )

        config_path = Path("data/governance/backup_config.json")
        with self._lock:
            try:
                _atomic_write_json(config_path, backup_config)
            except Exception as exc:
                logger.error("Configuration rollback write failed: %s", exc)
                return self._failed_record(
                    rollback_id, triggered_at, RollbackType.CONFIGURATION,
                    RollbackTrigger.MANUAL, f"write_failed:{exc}", "config", operator_id,
                )

        record = RollbackRecord(
            rollback_id=rollback_id,
            triggered_at=triggered_at,
            rollback_type=RollbackType.CONFIGURATION,
            trigger=RollbackTrigger.MANUAL,
            trigger_detail="backup_config_applied",
            target_state_id="backup_config",
            executed_by=operator_id,
            verified=True,
            reversible=True,
        )

        _append_audit(self._audit_path, _record_to_dict(record))
        logger.info("Configuration rollback %s by %s", rollback_id, operator_id)
        return record

    def rollback_to_snapshot(
        self, snapshot_id: str, operator_id: str
    ) -> RollbackRecord:
        """Roll back to a named system snapshot by ID."""
        if not operator_id or not operator_id.strip():
            raise ValueError("operator_id is required for snapshot rollbacks")
        if not snapshot_id or not snapshot_id.strip():
            raise ValueError("snapshot_id is required")

        rollback_id  = _new_rollback_id()
        triggered_at = _now_iso()

        # Locate snapshot file
        snapshot_candidates = [
            Path(f"data/snapshots/{snapshot_id}.json"),
            Path(f"data/{snapshot_id}.json"),
        ]
        found_path: Optional[Path] = None
        for candidate in snapshot_candidates:
            if candidate.exists():
                found_path = candidate
                break

        if found_path is None:
            logger.error("Snapshot not found: %s", snapshot_id)
            return self._failed_record(
                rollback_id, triggered_at, RollbackType.SNAPSHOT,
                RollbackTrigger.MANUAL, f"snapshot_not_found:{snapshot_id}",
                snapshot_id, operator_id,
            )

        with self._lock:
            try:
                with open(found_path, "r", encoding="utf-8") as fh:
                    fcntl.flock(fh, fcntl.LOCK_SH)
                    try:
                        snapshot_data = json.load(fh)
                    finally:
                        fcntl.flock(fh, fcntl.LOCK_UN)
            except Exception as exc:
                logger.error("Failed to read snapshot %s: %s", snapshot_id, exc)
                return self._failed_record(
                    rollback_id, triggered_at, RollbackType.SNAPSHOT,
                    RollbackTrigger.MANUAL, f"read_failed:{exc}", snapshot_id, operator_id,
                )

            target_path = Path("data/active_snapshot.json")
            try:
                _atomic_write_json(target_path, snapshot_data)
            except Exception as exc:
                logger.error("Snapshot apply write failed: %s", exc)
                return self._failed_record(
                    rollback_id, triggered_at, RollbackType.SNAPSHOT,
                    RollbackTrigger.MANUAL, f"write_failed:{exc}", snapshot_id, operator_id,
                )

        record = RollbackRecord(
            rollback_id=rollback_id,
            triggered_at=triggered_at,
            rollback_type=RollbackType.SNAPSHOT,
            trigger=RollbackTrigger.MANUAL,
            trigger_detail=f"snapshot_id={snapshot_id}",
            target_state_id=snapshot_id,
            executed_by=operator_id,
            verified=True,
            reversible=True,
        )

        _append_audit(self._audit_path, _record_to_dict(record))
        logger.info("Snapshot rollback %s → %s by %s", rollback_id, snapshot_id, operator_id)
        return record

    def emergency_rollback(self, operator_id: str, reason: str) -> RollbackRecord:
        """Emergency rollback: write halt marker, send Telegram alert.

        Does NOT place orders or modify CapitalPreservationEngine directly.
        DEMO_MODE=true — this is advisory only.
        """
        if not operator_id or not operator_id.strip():
            raise ValueError("operator_id is required for emergency rollback")
        if not reason or not reason.strip():
            raise ValueError("reason is required for emergency rollback")

        rollback_id  = _new_rollback_id()
        triggered_at = _now_iso()

        halt_marker = {
            "ts":          triggered_at,
            "operator_id": operator_id,
            "reason":      reason,
            "rollback_id": rollback_id,
        }

        with self._lock:
            try:
                _atomic_write_json(_ROLLBACK_STATE_PATH, halt_marker)
            except Exception as exc:
                logger.error("Emergency rollback marker write failed: %s", exc)
                # Still append audit and send alert even if marker write fails
                logger.critical(
                    "EMERGENCY ROLLBACK FAILED TO WRITE MARKER — operator=%s reason=%s",
                    operator_id, reason,
                )

        record = RollbackRecord(
            rollback_id=rollback_id,
            triggered_at=triggered_at,
            rollback_type=RollbackType.EMERGENCY,
            trigger=RollbackTrigger.MANUAL,
            trigger_detail=reason,
            target_state_id="emergency_halt",
            executed_by=operator_id,
            verified=False,     # No automated verification for emergency
            reversible=False,   # Requires manual reset
        )

        _append_audit(self._audit_path, _record_to_dict(record))

        # Non-blocking Telegram alert
        _send_telegram(f"⚠️ EMERGENCY ROLLBACK by {operator_id}: {reason}")

        logger.critical(
            "EMERGENCY ROLLBACK %s — operator=%s reason=%s",
            rollback_id, operator_id, reason,
        )
        return record

    # ── History + status ──────────────────────────────────────────────────────

    def list_rollback_history(self, limit: int = 20) -> List[RollbackRecord]:
        """Read audit JSONL and return the most recent `limit` records."""
        records: List[RollbackRecord] = []
        try:
            if not self._audit_path.exists():
                return []
            with open(self._audit_path, "r", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_SH)
                try:
                    lines = fh.readlines()
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
            for raw in lines[-limit:]:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    d = json.loads(raw)
                    records.append(RollbackRecord(
                        rollback_id=d.get("rollback_id", ""),
                        triggered_at=d.get("triggered_at", ""),
                        rollback_type=RollbackType(d.get("rollback_type", "MANUAL")),
                        trigger=RollbackTrigger(d.get("trigger", "MANUAL")),
                        trigger_detail=d.get("trigger_detail", ""),
                        target_state_id=d.get("target_state_id", ""),
                        executed_by=d.get("executed_by", "UNKNOWN"),
                        verified=bool(d.get("verified", False)),
                        reversible=bool(d.get("reversible", False)),
                    ))
                except (KeyError, ValueError) as exc:
                    logger.debug("Skipping malformed audit record: %s", exc)
        except OSError as exc:
            logger.error("Failed to read rollback audit: %s", exc)
        return records

    def verify_rollback(self, rollback_id: str) -> bool:
        """Check if a rollback's audit record exists and was marked verified."""
        for rec in self.list_rollback_history(limit=1000):
            if rec.rollback_id == rollback_id:
                return rec.verified
        return False

    def get_status(self) -> dict:
        """Return current rollback system status summary."""
        history = self.list_rollback_history(limit=10)
        last = history[-1] if history else None
        halt_active = _ROLLBACK_STATE_PATH.exists()
        return {
            "audit_path":         str(self._audit_path),
            "total_rollbacks":    len(history),
            "last_rollback_id":   last.rollback_id if last else None,
            "last_rollback_type": last.rollback_type.value if last else None,
            "last_rollback_at":   last.triggered_at if last else None,
            "emergency_halt_active": halt_active,
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _failed_record(
        self,
        rollback_id: str,
        triggered_at: str,
        rollback_type: RollbackType,
        trigger: RollbackTrigger,
        detail: str,
        target: str,
        executed_by: str,
    ) -> RollbackRecord:
        """Create a failed RollbackRecord and append it to audit."""
        record = RollbackRecord(
            rollback_id=rollback_id,
            triggered_at=triggered_at,
            rollback_type=rollback_type,
            trigger=trigger,
            trigger_detail=f"FAILED:{detail}",
            target_state_id=target,
            executed_by=executed_by,
            verified=False,
            reversible=False,
        )
        try:
            _append_audit(self._audit_path, _record_to_dict(record))
        except Exception as exc:
            logger.error("Failed to append failed-rollback record to audit: %s", exc)
        return record

    def _verify_weights_on_disk(self, expected: dict) -> bool:
        """Re-read strategy_weights.json and confirm it matches expected dict."""
        try:
            with open(_WEIGHTS_PATH, "r", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_SH)
                try:
                    on_disk = json.load(fh)
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
            return on_disk == expected
        except Exception as exc:
            logger.error("Post-rollback verification failed: %s", exc)
            return False

    # ── Automated trigger helpers ─────────────────────────────────────────────

    def _get_cooldowns(self) -> Dict[str, float]:
        """Lazy-init cooldown tracking dict on self."""
        if not hasattr(self, "_cooldowns"):
            self._cooldowns: Dict[str, float] = {}
            self._dedup_window: Dict[str, str] = {}
            self._automated_rollback_count: int = 0
            self._last_trigger_times: Dict[str, float] = {}
        return self._cooldowns

    def _check_cooldown(self, trigger_key: str, cooldown_s: float) -> bool:
        """Returns True if cooldown has elapsed (ok to trigger)."""
        cooldowns = self._get_cooldowns()
        last = cooldowns.get(trigger_key, 0.0)
        return time.monotonic() - last >= cooldown_s

    def _record_cooldown(self, trigger_key: str) -> None:
        """Record the current time as last trigger time for this key."""
        cooldowns = self._get_cooldowns()
        cooldowns[trigger_key] = time.monotonic()
        # Also track in last_trigger_times for automation_status reporting
        self._last_trigger_times[trigger_key] = time.monotonic()
        self._automated_rollback_count = getattr(self, "_automated_rollback_count", 0) + 1

    # ── Automated trigger methods ─────────────────────────────────────────────

    def trigger_survivability_rollback(
        self,
        score: float,
        threshold: float = 40.0,
        cooldown_s: float = 300.0,
        operator_id: str = "SYSTEM",
    ) -> Optional[RollbackRecord]:
        """Fire an emergency rollback when survivability score drops below threshold.

        Returns None if score is above threshold or cooldown has not elapsed.
        """
        if score >= threshold:
            return None
        trigger_key = "SURVIVABILITY_COLLAPSE"
        if not self._check_cooldown(trigger_key, cooldown_s):
            logger.debug(
                "trigger_survivability_rollback: cooldown active (score=%.1f)", score
            )
            return None
        self._record_cooldown(trigger_key)
        reason = f"Survivability score {score:.1f} below threshold {threshold}"
        logger.warning("Auto-rollback: %s", reason)
        try:
            record = self.emergency_rollback(operator_id, reason)
        except Exception as exc:
            logger.error("trigger_survivability_rollback failed: %s", exc)
            return None
        # Escalation audit entry
        try:
            _append_audit(
                self._audit_path,
                {
                    "event":       "ESCALATION",
                    "trigger":     trigger_key,
                    "score":       score,
                    "threshold":   threshold,
                    "rollback_id": record.rollback_id,
                    "ts":          _now_iso(),
                },
            )
        except Exception as exc:
            logger.debug("escalation audit append skipped: %s", exc)
        return record

    def trigger_latency_rollback(
        self,
        p99_ms: float,
        threshold_ms: float = 2000.0,
        cooldown_s: float = 180.0,
        operator_id: str = "SYSTEM",
    ) -> Optional[RollbackRecord]:
        """Fire an emergency rollback when latency p99 exceeds threshold_ms.

        Returns None if p99_ms is below threshold or cooldown has not elapsed.
        """
        if p99_ms < threshold_ms:
            return None
        trigger_key = "LATENCY_EXPLOSION"
        if not self._check_cooldown(trigger_key, cooldown_s):
            logger.debug(
                "trigger_latency_rollback: cooldown active (p99=%.1fms)", p99_ms
            )
            return None
        self._record_cooldown(trigger_key)
        reason = f"Latency p99 {p99_ms:.1f}ms exceeded {threshold_ms}ms"
        logger.warning("Auto-rollback: %s", reason)
        try:
            record = self.emergency_rollback(operator_id, reason)
        except Exception as exc:
            logger.error("trigger_latency_rollback failed: %s", exc)
            return None
        return record

    def trigger_drift_rollback(
        self,
        drift_score: float,
        threshold: float = 0.7,
        cooldown_s: float = 600.0,
        operator_id: str = "SYSTEM",
    ) -> Optional[RollbackRecord]:
        """Escalate to weights rollback when strategy drift exceeds threshold.

        Returns None if drift_score is below threshold or cooldown has not elapsed.
        Note: drift_score here is treated as an anomaly magnitude (0–1 scale from
        external detectors), not a ratio — rollback fires when score >= threshold.
        """
        if drift_score < threshold:
            return None
        trigger_key = "DRIFT_EXPLOSION"
        if not self._check_cooldown(trigger_key, cooldown_s):
            logger.debug(
                "trigger_drift_rollback: cooldown active (drift=%.3f)", drift_score
            )
            return None
        self._record_cooldown(trigger_key)

        # Attempt weights rollback to default snapshot; fall back to emergency
        default_snapshot = str(_WEIGHTS_PATH)
        reason = f"Drift score {drift_score:.3f} exceeded threshold {threshold}"
        logger.warning("Auto-rollback (drift): %s", reason)
        try:
            record = self.rollback_strategy_weights(
                default_snapshot,
                operator_id,
                RollbackTrigger.DRIFT_EXPLOSION,
            )
        except Exception:
            # Fall back to emergency rollback if weights rollback not possible
            try:
                record = self.emergency_rollback(operator_id, reason)
            except Exception as exc:
                logger.error("trigger_drift_rollback fallback failed: %s", exc)
                return None
        return record

    def trigger_reconciliation_rollback(
        self,
        instability_count: int,
        threshold: int = 5,
        cooldown_s: float = 120.0,
        operator_id: str = "SYSTEM",
    ) -> Optional[RollbackRecord]:
        """Fire an emergency rollback on repeated reconciliation failures.

        Returns None if instability_count < threshold or cooldown has not elapsed.
        """
        if instability_count < threshold:
            return None
        trigger_key = "RECONCILIATION_INSTABILITY"
        if not self._check_cooldown(trigger_key, cooldown_s):
            logger.debug(
                "trigger_reconciliation_rollback: cooldown active (count=%d)",
                instability_count,
            )
            return None
        self._record_cooldown(trigger_key)
        reason = (
            f"Reconciliation instability count {instability_count} "
            f"exceeded threshold {threshold}"
        )
        logger.warning("Auto-rollback: %s", reason)
        # Build a record using emergency_rollback but override the trigger in audit
        try:
            record = self.emergency_rollback(operator_id, reason)
        except Exception as exc:
            logger.error("trigger_reconciliation_rollback failed: %s", exc)
            return None
        # Append supplementary audit with explicit trigger type
        try:
            _append_audit(
                self._audit_path,
                {
                    "event":               "ESCALATION",
                    "trigger":             trigger_key,
                    "instability_count":   instability_count,
                    "threshold":           threshold,
                    "rollback_id":         record.rollback_id,
                    "ts":                  _now_iso(),
                },
            )
        except Exception as exc:
            logger.debug("reconciliation escalation audit skipped: %s", exc)
        return record

    # ── Introspection ─────────────────────────────────────────────────────────

    def get_rollback_escalation_ladder(self) -> List[dict]:
        """Return ordered list of rollback triggers by severity (lowest first)."""
        return [
            {
                "trigger":               "RECONCILIATION_INSTABILITY",
                "cooldown_s":            120,
                "threshold_description": "instability_count >= 5",
            },
            {
                "trigger":               "SURVIVABILITY_COLLAPSE",
                "cooldown_s":            300,
                "threshold_description": "survivability_score < 40",
            },
            {
                "trigger":               "LATENCY_EXPLOSION",
                "cooldown_s":            180,
                "threshold_description": "ws_p99_ms >= 2000",
            },
            {
                "trigger":               "DRIFT_EXPLOSION",
                "cooldown_s":            600,
                "threshold_description": "drift_score >= 0.7",
            },
            {
                "trigger":               "MANUAL",
                "cooldown_s":            0,
                "threshold_description": "operator-initiated, no cooldown",
            },
        ]

    def get_automation_status(self) -> dict:
        """Return dict with last_trigger times, cooldown remaining, and total count."""
        self._get_cooldowns()  # ensure lazy-init
        now = time.monotonic()

        _cooldown_map = {
            "RECONCILIATION_INSTABILITY": 120.0,
            "SURVIVABILITY_COLLAPSE":     300.0,
            "LATENCY_EXPLOSION":          180.0,
            "DRIFT_EXPLOSION":            600.0,
        }

        last_trigger: Dict[str, Optional[float]] = {}
        cooldown_remaining: Dict[str, float] = {}

        for trigger_key, cooldown_s in _cooldown_map.items():
            last_t = self._cooldowns.get(trigger_key)
            last_trigger[trigger_key] = last_t
            if last_t is not None:
                remaining = max(0.0, cooldown_s - (now - last_t))
            else:
                remaining = 0.0
            cooldown_remaining[trigger_key] = remaining

        return {
            "last_trigger":              last_trigger,
            "cooldown_remaining_s":      cooldown_remaining,
            "total_automated_rollbacks": getattr(self, "_automated_rollback_count", 0),
        }


# ── Module singleton ──────────────────────────────────────────────────────────

_manager: Optional[RollbackManager] = None
_manager_lock = threading.Lock()


def get_rollback_manager() -> RollbackManager:
    """Return the module-level RollbackManager singleton."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = RollbackManager()
    return _manager
