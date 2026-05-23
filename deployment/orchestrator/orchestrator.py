"""OpenClaw Deployment Orchestrator — Phase 6 hardening.

Manages canary deployments, health-gated phase advancement, automatic rollback,
and an immutable audit trail.  All external module imports are lazy and
fail-safe so the orchestrator remains operational even if subsystems are
unavailable.

Usage:
    from deployment.orchestrator.orchestrator import get_orchestrator

    orch = get_orchestrator()
    record = orch.start_deployment(operator_id="ops@openclaw", config={...})
    record = orch.advance_phase(record.deployment_id, operator_id="ops@openclaw")
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.deployment.orchestrator")

# ── Enums ─────────────────────────────────────────────────────────────────────


class DeploymentState(str, Enum):
    PENDING = "PENDING"
    CANARY_PHASE_1 = "CANARY_PHASE_1"
    CANARY_PHASE_2 = "CANARY_PHASE_2"
    CANARY_PHASE_3 = "CANARY_PHASE_3"
    CANARY_PHASE_4 = "CANARY_PHASE_4"
    STABLE = "STABLE"
    ROLLING_BACK = "ROLLING_BACK"
    FROZEN = "FROZEN"
    FAILED = "FAILED"


class RollbackTriggerType(str, Enum):
    """Deployment-layer rollback trigger types (distinct from runtime RollbackTrigger)."""

    SURVIVABILITY_BELOW_THRESHOLD = "SURVIVABILITY_BELOW_THRESHOLD"
    INTEGRITY_CRITICAL = "INTEGRITY_CRITICAL"
    REPLAY_DIVERGENCE = "REPLAY_DIVERGENCE"
    WS_INSTABILITY = "WS_INSTABILITY"
    LATENCY_EXPLOSION = "LATENCY_EXPLOSION"
    RECONCILIATION_INSTABILITY = "RECONCILIATION_INSTABILITY"
    EXECUTION_DEGRADATION = "EXECUTION_DEGRADATION"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


# ── Phase transition map ───────────────────────────────────────────────────────

_PHASE_TRANSITIONS: Dict[DeploymentState, DeploymentState] = {
    DeploymentState.PENDING: DeploymentState.CANARY_PHASE_1,
    DeploymentState.CANARY_PHASE_1: DeploymentState.CANARY_PHASE_2,
    DeploymentState.CANARY_PHASE_2: DeploymentState.CANARY_PHASE_3,
    DeploymentState.CANARY_PHASE_3: DeploymentState.CANARY_PHASE_4,
    DeploymentState.CANARY_PHASE_4: DeploymentState.STABLE,
}

_STATE_TO_PHASE: Dict[DeploymentState, int] = {
    DeploymentState.PENDING: 0,
    DeploymentState.CANARY_PHASE_1: 1,
    DeploymentState.CANARY_PHASE_2: 2,
    DeploymentState.CANARY_PHASE_3: 3,
    DeploymentState.CANARY_PHASE_4: 4,
    DeploymentState.STABLE: 4,
    DeploymentState.ROLLING_BACK: 0,
    DeploymentState.FROZEN: 0,
    DeploymentState.FAILED: 0,
}

# Per-phase minimum composite health score required to advance
_PHASE_THRESHOLDS: Dict[DeploymentState, float] = {
    DeploymentState.CANARY_PHASE_1: 60.0,
    DeploymentState.CANARY_PHASE_2: 70.0,
    DeploymentState.CANARY_PHASE_3: 80.0,
    DeploymentState.CANARY_PHASE_4: 85.0,
}


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class DeploymentRecord:
    """Immutable-append deployment lifecycle record."""

    deployment_id: str
    release_trace_id: str
    started_at: str
    completed_at: Optional[str]
    state: DeploymentState
    canary_phase: int
    health_score: float
    rollback_trigger: Optional[RollbackTriggerType]
    rollback_reason: Optional[str]
    operator_id: str
    config_snapshot: dict

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        d["rollback_trigger"] = (
            self.rollback_trigger.value if self.rollback_trigger else None
        )
        return d


@dataclass
class DeploymentHealthScore:
    """Point-in-time composite health assessment for a deployment phase gate.

    Composite formula:
        survivability_score * 0.40
        + (20 if integrity_ok else 0)
        + ws_health * 20
        + (10 if latency_p99_ms < 500 else 0)
        + (10 if execution_ok else 0)

    Max = 40 + 20 + 20 + 10 + 10 = 100
    """

    survivability_score: float
    integrity_ok: bool
    ws_health: float
    latency_p99_ms: float
    execution_ok: bool
    composite_score: float = field(init=False)

    def __post_init__(self) -> None:
        latency_ok_pts = 10.0 if self.latency_p99_ms < 500.0 else 0.0
        self.composite_score = (
            self.survivability_score * 0.40
            + (20.0 if self.integrity_ok else 0.0)
            + self.ws_health * 20.0
            + latency_ok_pts
            + (10.0 if self.execution_ok else 0.0)
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _append_jsonl(path: Path, record: dict) -> None:
    """Atomically append a JSON line to an audit file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


# ── Main class ────────────────────────────────────────────────────────────────


class DeploymentOrchestrator:
    """Manages canary deployments with health-gated phase advancement.

    Parameters
    ----------
    audit_path:
        Path to the JSONL append-only audit log.
    freeze_windows:
        List of dicts with keys ``start`` (HH:MM), ``end`` (HH:MM), and
        ``days`` (list of weekday names).  Deployments attempted inside a
        freeze window are rejected with state=FROZEN.
    """

    def __init__(
        self,
        audit_path: str = "data/deployment_audit.jsonl",
        freeze_windows: Optional[List[dict]] = None,
    ) -> None:
        self._audit_path = Path(audit_path)
        self._freeze_windows: List[dict] = freeze_windows or []
        self._lock = threading.Lock()
        # In-memory store: deployment_id → DeploymentRecord
        self._deployments: Dict[str, DeploymentRecord] = {}
        self._current_deployment_id: Optional[str] = None
        logger.info(
            "DeploymentOrchestrator initialised  audit=%s  freeze_windows=%d",
            self._audit_path,
            len(self._freeze_windows),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def start_deployment(self, operator_id: str, config: dict) -> DeploymentRecord:
        """Initiate a new deployment.

        If called inside a freeze window the record is written with
        state=FROZEN and no phase advancement occurs.

        Parameters
        ----------
        operator_id: Identifier of the human or service initiating the deploy.
        config:      Configuration snapshot to embed in the audit record.

        Returns
        -------
        DeploymentRecord
        """
        with self._lock:
            if self.is_in_freeze_window():
                record = DeploymentRecord(
                    deployment_id=_new_uuid(),
                    release_trace_id=_new_uuid(),
                    started_at=_now_iso(),
                    completed_at=_now_iso(),
                    state=DeploymentState.FROZEN,
                    canary_phase=0,
                    health_score=0.0,
                    rollback_trigger=None,
                    rollback_reason="Blocked by active freeze window",
                    operator_id=operator_id,
                    config_snapshot=config,
                )
                self._deployments[record.deployment_id] = record
                self._append_audit(
                    record,
                    from_state=DeploymentState.PENDING,
                    to_state=DeploymentState.FROZEN,
                    health_score=0.0,
                )
                logger.warning(
                    "Deployment blocked by freeze window  operator=%s  id=%s",
                    operator_id,
                    record.deployment_id,
                )
                return record

            deployment_id = _new_uuid()
            release_trace_id = _new_uuid()
            record = DeploymentRecord(
                deployment_id=deployment_id,
                release_trace_id=release_trace_id,
                started_at=_now_iso(),
                completed_at=None,
                state=DeploymentState.PENDING,
                canary_phase=0,
                health_score=0.0,
                rollback_trigger=None,
                rollback_reason=None,
                operator_id=operator_id,
                config_snapshot=config,
            )
            self._deployments[deployment_id] = record
            self._current_deployment_id = deployment_id

            # Persist the initial PENDING record before advancing
            self._append_audit(
                record,
                from_state=DeploymentState.PENDING,
                to_state=DeploymentState.PENDING,
                health_score=0.0,
            )

            logger.info(
                "Deployment started  id=%s  trace=%s  operator=%s",
                deployment_id,
                release_trace_id,
                operator_id,
            )

            # Immediately advance to CANARY_PHASE_1
            record = self._transition_phase(record, operator_id="SYSTEM")
            return record

    def advance_phase(
        self, deployment_id: str, operator_id: str
    ) -> DeploymentRecord:
        """Advance a deployment to the next canary phase.

        Health is evaluated before each transition.  If the composite score is
        below the phase threshold (minimum 60) an automatic rollback is
        triggered.  Phase 4 → STABLE requires a non-SYSTEM operator_id.

        Parameters
        ----------
        deployment_id: ID returned by start_deployment().
        operator_id:   Human or service requesting the advance.

        Returns
        -------
        Updated DeploymentRecord.

        Raises
        ------
        KeyError if the deployment_id is unknown.
        ValueError if the record is not in an advanceable state.
        """
        with self._lock:
            record = self._get_record(deployment_id)

            if record.state not in _PHASE_TRANSITIONS:
                raise ValueError(
                    f"Cannot advance deployment in state {record.state.value}"
                )

            # Phase 4 → STABLE requires explicit human approval
            if (
                record.state == DeploymentState.CANARY_PHASE_4
                and operator_id == "SYSTEM"
            ):
                raise ValueError(
                    "Phase 4 → STABLE transition requires an explicit human operator_id"
                )

            health = self.get_health_score()
            threshold = _PHASE_THRESHOLDS.get(record.state, 60.0)

            if health.composite_score < threshold:
                trigger = self._select_rollback_trigger(health)
                reason = (
                    f"Health composite_score {health.composite_score:.1f} below "
                    f"threshold {threshold:.1f} for {record.state.value}"
                )
                logger.warning(
                    "Auto-rollback triggered  id=%s  score=%.1f  threshold=%.1f",
                    deployment_id,
                    health.composite_score,
                    threshold,
                )
                return self._do_rollback(record, trigger, reason, operator_id="SYSTEM")

            return self._transition_phase(record, operator_id)

    def rollback_deployment(
        self,
        deployment_id: str,
        trigger: RollbackTriggerType,
        reason: str,
        operator_id: str,
    ) -> DeploymentRecord:
        """Manually trigger a rollback for an active deployment.

        Parameters
        ----------
        deployment_id: Target deployment ID.
        trigger:       Reason category for the rollback.
        reason:        Human-readable explanation.
        operator_id:   Who is initiating the rollback.

        Returns
        -------
        Updated DeploymentRecord with state=FAILED.
        """
        with self._lock:
            record = self._get_record(deployment_id)
            return self._do_rollback(record, trigger, reason, operator_id)

    def get_health_score(self) -> DeploymentHealthScore:
        """Collect live health signals from all subsystems.

        Each subsystem import is lazy and fail-safe: if a module is
        unavailable or raises an exception, a conservative default is used
        rather than crashing the orchestrator.

        Returns
        -------
        DeploymentHealthScore with a composite score 0–100.
        """
        # 1. Survivability
        survivability_score = 50.0
        try:
            from runtime.survivability import get_survivability_engine  # type: ignore[import]

            engine = get_survivability_engine()
            report = engine.compute_score()
            survivability_score = float(report.current_score)
        except Exception as exc:
            logger.debug("survivability unavailable: %s", exc)

        # 2. Integrity
        integrity_ok = True
        try:
            from runtime.integrity_monitor import (  # type: ignore[import]
                IntegritySeverity,
                get_monitor,
            )

            monitor = get_monitor()
            last_report = monitor.get_last_report()
            if last_report is not None:
                integrity_ok = (
                    last_report.overall_severity != IntegritySeverity.CRITICAL
                )
        except Exception as exc:
            logger.debug("integrity_monitor unavailable: %s", exc)

        # 3. WebSocket health
        ws_health = 0.5
        try:
            from runtime.ws_guardian import get_guardian  # type: ignore[import]

            guardian = get_guardian()
            ws_score = guardian.get_health_score()
            ws_health = float(ws_score.score)
        except Exception as exc:
            logger.debug("ws_guardian unavailable: %s", exc)

        # 4. Latency p99
        latency_p99_ms = 100.0
        try:
            from runtime.latency_profiler import (  # type: ignore[import]
                LatencyProfiler,
                OperationCategory,
                get_profiler,
            )

            profiler = get_profiler()
            all_stats = profiler.get_all_stats()
            if all_stats:
                # Use the worst p99 across all recorded categories
                latency_p99_ms = max(s.p99_ms for s in all_stats)
        except Exception as exc:
            logger.debug("latency_profiler unavailable: %s", exc)

        # 5. Execution health — derived from survivability execution subscore
        execution_ok = True
        try:
            from runtime.survivability import get_survivability_engine  # type: ignore[import]

            engine = get_survivability_engine()
            report = engine.compute_score()
            exec_sub = report.subsystem_scores.get("execution_stability")
            if exec_sub is not None:
                execution_ok = exec_sub.score >= 40.0
        except Exception as exc:
            logger.debug("execution health via survivability unavailable: %s", exc)

        health = DeploymentHealthScore(
            survivability_score=survivability_score,
            integrity_ok=integrity_ok,
            ws_health=ws_health,
            latency_p99_ms=latency_p99_ms,
            execution_ok=execution_ok,
        )
        logger.debug(
            "health_score  composite=%.1f  surv=%.1f  integrity=%s  ws=%.2f  "
            "lat_p99=%.1f  exec=%s",
            health.composite_score,
            health.survivability_score,
            health.integrity_ok,
            health.ws_health,
            health.latency_p99_ms,
            health.execution_ok,
        )
        return health

    def is_in_freeze_window(self) -> bool:
        """Return True if the current UTC time falls inside any configured freeze window."""
        if not self._freeze_windows:
            return False
        now_utc = datetime.now(timezone.utc)
        current_day = now_utc.strftime("%A")  # e.g. "Monday"
        current_time = now_utc.strftime("%H:%M")

        for window in self._freeze_windows:
            days: List[str] = window.get("days", [])
            if days and current_day not in days:
                continue
            start: str = window.get("start", "00:00")
            end: str = window.get("end", "00:00")
            if start <= current_time < end:
                return True
        return False

    def get_deployment_status(self) -> dict:
        """Return a summary of the current deployment state."""
        record: Optional[DeploymentRecord] = None
        if self._current_deployment_id:
            record = self._deployments.get(self._current_deployment_id)

        health_score: Optional[float] = None
        try:
            health_score = self.get_health_score().composite_score
        except Exception:
            pass

        return {
            "current_deployment_id": self._current_deployment_id,
            "state": record.state.value if record else None,
            "health_score": health_score,
            "canary_phase": record.canary_phase if record else 0,
            "freeze_window_active": self.is_in_freeze_window(),
        }

    def validate_convergence(
        self, deployment_id: str, checks: int = 5, interval_s: float = 1.0
    ) -> bool:
        """Run repeated health evaluations and return True if all pass.

        Parameters
        ----------
        deployment_id: Deployment to validate (must exist).
        checks:        Number of health score samples to collect.
        interval_s:    Seconds to wait between samples.

        Returns
        -------
        True only if every sample has composite_score > 60.
        """
        self._get_record(deployment_id)  # validates existence

        for i in range(checks):
            score = self.get_health_score()
            if score.composite_score <= 60.0:
                logger.warning(
                    "validate_convergence FAIL  check=%d/%d  score=%.1f  id=%s",
                    i + 1,
                    checks,
                    score.composite_score,
                    deployment_id,
                )
                return False
            if i < checks - 1:
                time.sleep(interval_s)

        logger.info(
            "validate_convergence PASS  checks=%d  id=%s", checks, deployment_id
        )
        return True

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_record(self, deployment_id: str) -> DeploymentRecord:
        record = self._deployments.get(deployment_id)
        if record is None:
            raise KeyError(f"Unknown deployment_id: {deployment_id}")
        return record

    def _transition_phase(
        self, record: DeploymentRecord, operator_id: str
    ) -> DeploymentRecord:
        """Advance record to next state and persist audit entry."""
        from_state = record.state
        next_state = _PHASE_TRANSITIONS[from_state]

        health = self.get_health_score()
        record.state = next_state
        record.canary_phase = _STATE_TO_PHASE[next_state]
        record.health_score = health.composite_score

        if next_state == DeploymentState.STABLE:
            record.completed_at = _now_iso()

        self._deployments[record.deployment_id] = record
        self._append_audit(
            record,
            from_state=from_state,
            to_state=next_state,
            health_score=health.composite_score,
        )

        logger.info(
            "Phase transition  id=%s  %s → %s  score=%.1f  operator=%s",
            record.deployment_id,
            from_state.value,
            next_state.value,
            health.composite_score,
            operator_id,
        )
        return record

    def _do_rollback(
        self,
        record: DeploymentRecord,
        trigger: RollbackTriggerType,
        reason: str,
        operator_id: str,
    ) -> DeploymentRecord:
        """Execute rollback sequence: set ROLLING_BACK → call RollbackManager → FAILED."""
        from_state = record.state
        record.state = DeploymentState.ROLLING_BACK
        record.rollback_trigger = trigger
        record.rollback_reason = reason
        record.completed_at = _now_iso()
        self._deployments[record.deployment_id] = record

        self._append_audit(
            record,
            from_state=from_state,
            to_state=DeploymentState.ROLLING_BACK,
            health_score=record.health_score,
            extra={
                "rollback_trigger": trigger.value,
                "rollback_reason": reason,
            },
        )

        # Attempt emergency rollback via RollbackManager
        try:
            from runtime.rollback_manager import get_rollback_manager  # type: ignore[import]

            rm = get_rollback_manager()
            rm.emergency_rollback(
                operator_id=operator_id,
                reason=f"[DeploymentOrchestrator] {trigger.value}: {reason}",
            )
            logger.info(
                "emergency_rollback executed  id=%s  trigger=%s",
                record.deployment_id,
                trigger.value,
            )
        except Exception as exc:
            logger.error(
                "emergency_rollback call failed  id=%s  error=%s",
                record.deployment_id,
                exc,
            )

        record.state = DeploymentState.FAILED
        self._deployments[record.deployment_id] = record
        self._append_audit(
            record,
            from_state=DeploymentState.ROLLING_BACK,
            to_state=DeploymentState.FAILED,
            health_score=record.health_score,
            extra={
                "rollback_trigger": trigger.value,
                "rollback_reason": reason,
                "release_trace_id": record.release_trace_id,
            },
        )

        logger.warning(
            "Deployment FAILED after rollback  id=%s  trace=%s  trigger=%s",
            record.deployment_id,
            record.release_trace_id,
            trigger.value,
        )
        return record

    def _select_rollback_trigger(
        self, health: DeploymentHealthScore
    ) -> RollbackTriggerType:
        """Choose the most specific trigger type based on which signal failed."""
        if health.survivability_score < 40.0:
            return RollbackTriggerType.SURVIVABILITY_BELOW_THRESHOLD
        if not health.integrity_ok:
            return RollbackTriggerType.INTEGRITY_CRITICAL
        if health.ws_health < 0.3:
            return RollbackTriggerType.WS_INSTABILITY
        if health.latency_p99_ms >= 2000.0:
            return RollbackTriggerType.LATENCY_EXPLOSION
        if not health.execution_ok:
            return RollbackTriggerType.EXECUTION_DEGRADATION
        # Generic fallback
        return RollbackTriggerType.SURVIVABILITY_BELOW_THRESHOLD

    def _append_audit(
        self,
        record: DeploymentRecord,
        from_state: DeploymentState,
        to_state: DeploymentState,
        health_score: float,
        extra: Optional[dict] = None,
    ) -> None:
        """Write an immutable audit entry to the JSONL file."""
        entry: Dict[str, Any] = {
            "timestamp": _now_iso(),
            "deployment_id": record.deployment_id,
            "release_trace_id": record.release_trace_id,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "canary_phase": record.canary_phase,
            "health_score": health_score,
            "operator_id": record.operator_id,
        }
        if extra:
            entry.update(extra)
        try:
            _append_jsonl(self._audit_path, entry)
        except Exception as exc:
            logger.error("audit write failed: %s  entry=%s", exc, entry)


# ── Module-level singleton ────────────────────────────────────────────────────

_orchestrator_instance: Optional[DeploymentOrchestrator] = None
_orchestrator_lock = threading.Lock()


def get_orchestrator(
    audit_path: str = "data/deployment_audit.jsonl",
    freeze_windows: Optional[List[dict]] = None,
) -> DeploymentOrchestrator:
    """Return the process-wide DeploymentOrchestrator singleton.

    Parameters
    ----------
    audit_path:
        Path to audit JSONL (used only on first call; ignored on subsequent calls).
    freeze_windows:
        Freeze window configuration (used only on first call).

    Returns
    -------
    DeploymentOrchestrator singleton instance.
    """
    global _orchestrator_instance
    if _orchestrator_instance is None:
        with _orchestrator_lock:
            if _orchestrator_instance is None:
                _orchestrator_instance = DeploymentOrchestrator(
                    audit_path=audit_path,
                    freeze_windows=freeze_windows,
                )
    return _orchestrator_instance
