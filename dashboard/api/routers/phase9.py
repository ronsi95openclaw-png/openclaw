"""Phase 9 dashboard endpoints for OpenClaw.

Covers 9 endpoint groups:
  Section 1  — Global System Overview      /api/v2/overview
  Section 2  — Execution Telemetry         /api/v2/execution
  Section 3  — Balance + Capital           /api/v2/balance
  Section 4  — EventStore + Replay         /api/v2/eventstore
  Section 5  — Governance + Alpha          /api/v2/governance
  Section 6  — Deployment + Canary         /api/v2/deployment
  Section 7  — Distributed Coordination   /api/v2/coordination
  Section 8  — Chaos + Longhaul            /api/v2/chaos
  Section 9  — Security + Audit            /api/v2/security

Design rules (mandatory):
- Fail-closed: any subsystem unavailable → return {"status": "unavailable"} not 500.
- Lazy imports: all runtime modules imported inside functions with try/except.
- NEVER mutate EventStore, governance, or trading state via GET endpoints.
- POST endpoints that take privileged action: require _require_local_or_token dependency.
- All JSONL reads: try/except per line, skip malformed.
- advance-phase: state==CANARY_PHASE_4 → 403 always, never calls advance for Phase4→STABLE.
- chaos/inject: validate event_type against ChaosEventType enum; unknown type → 400.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("openclaw.dashboard.routers.phase9")

router = APIRouter()

# ── Shared helpers ─────────────────────────────────────────────────────────────

_DATA_DIR = Path("data")

_START_TIME = time.monotonic()


def _uptime_s() -> float:
    return round(time.monotonic() - _START_TIME, 1)


def _read_jsonl_tail(path: Path, n: int) -> List[dict]:
    """Read last N lines of a JSONL file. Returns [] on any error or missing file."""
    try:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
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
        logger.debug("_read_jsonl_tail(%s, %d): %s", path, n, exc)
        return []


def _count_jsonl_lines(path: Path) -> int:
    """Count non-empty lines in a JSONL file. Returns 0 on error."""
    try:
        if not path.exists():
            return 0
        count = 0
        with path.open("r", encoding="utf-8") as fh:
            for ln in fh:
                if ln.strip():
                    count += 1
        return count
    except Exception:
        return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Auth dependency (re-exported from server module) ──────────────────────────

def _require_local_or_token(request: Request) -> None:
    """Re-implements the server-level auth check for use in this router."""
    try:
        from security.auth import TokenAuth  # type: ignore[import]
        auth = TokenAuth()
        client_ip = request.client.host if request.client else ""
        if auth.is_local_request(client_ip):
            return
        token = request.headers.get("X-Dashboard-Token", "")
        if not auth.verify_token(token):
            raise HTTPException(status_code=401, detail="Invalid or missing X-Dashboard-Token")
    except HTTPException:
        raise
    except Exception:
        # If auth module unavailable, fall back to localhost-only check
        client_ip = request.client.host if request.client else ""
        if client_ip not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(status_code=401, detail="Auth module unavailable — localhost only")


# ── Audit helper ───────────────────────────────────────────────────────────────

def _audit(
    action: str,
    operator_id: str,
    client_ip: str,
    params: dict,
    result: str,
    detail: str,
) -> None:
    """Emit a dashboard audit event (best-effort, never raises)."""
    try:
        from dashboard.api.audit import DashboardAuditEvent, append_audit_event, make_trace_id, now_iso  # type: ignore[import]
        event = DashboardAuditEvent(
            ts=now_iso(),
            action=action,
            operator_id=operator_id,
            client_ip=client_ip,
            trace_id=make_trace_id(),
            params=params,
            result=result,
            detail=detail,
        )
        append_audit_event(event)
    except Exception as exc:
        logger.debug("_audit: failed to append audit event: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Section 1 — Global System Overview
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/v2/overview")
def v2_overview() -> Dict[str, Any]:
    """Aggregate system overview from all major subsystems."""

    # Survivability
    survivability_score: Optional[float] = None
    try:
        from runtime.survivability import get_survivability_engine  # type: ignore[import]
        report = get_survivability_engine().compute_score()
        survivability_score = report.current_score
    except Exception:
        pass

    # Integrity
    integrity_ok: Optional[bool] = None
    try:
        from runtime.integrity_monitor import get_monitor  # type: ignore[import]
        last = get_monitor().get_last_report()
        if last is not None:
            integrity_ok = last.overall_severity.value not in ("CRITICAL",)
        else:
            integrity_ok = True  # no scan yet = not known CRITICAL
    except Exception:
        pass

    # WS health
    ws_health: Optional[float] = None
    try:
        from runtime.ws_guardian import get_guardian  # type: ignore[import]
        ws_health = get_guardian().get_health_score().score
    except Exception:
        pass

    # Demo mode
    demo_mode = os.getenv("DEMO_MODE", "true").lower() not in ("false", "0", "no")

    # Leader state
    leader_state: Optional[str] = None
    try:
        from runtime.leader_election import LeaderElection  # type: ignore[import]
        node_id = os.getenv("NODE_ID", "dashboard")
        election = LeaderElection(node_id)
        leader_state = election.get_state().value
    except Exception:
        pass

    # Active rollback cooldowns
    active_rollback_cooldowns: Optional[int] = None
    try:
        from runtime.rollback_manager import get_rollback_manager  # type: ignore[import]
        auto_status = get_rollback_manager().get_automation_status()
        # Count triggers whose cooldown_remaining_s > 0
        active_rollback_cooldowns = sum(
            1 for v in auto_status.get("cooldown_remaining_s", {}).values()
            if v and v > 0
        )
    except Exception:
        pass

    # Active chaos incidents
    active_chaos_incidents: Optional[int] = None
    try:
        from runtime.chaos_runtime import get_chaos_runtime  # type: ignore[import]
        snapshot = get_chaos_runtime().take_health_snapshot()
        active_chaos_incidents = snapshot.active_chaos_events
    except Exception:
        pass

    # Balance guardian severity
    balance_guardian_severity: Optional[str] = None
    try:
        from runtime.live_balance_guardian import get_guardian as get_bg  # type: ignore[import]
        balance_guardian_severity = get_bg().get_status().get("last_severity")
    except Exception:
        pass

    # Deployment phase
    deployment_phase: Optional[str] = None
    try:
        from deployment.orchestrator.orchestrator import get_orchestrator  # type: ignore[import]
        status = get_orchestrator().get_deployment_status()
        deployment_phase = status.get("state")
    except Exception:
        pass

    return {
        "survivability_score":           survivability_score,
        "integrity_ok":                  integrity_ok,
        "ws_health":                     ws_health,
        "demo_mode":                     demo_mode,
        "leader_state":                  leader_state,
        "active_rollback_cooldowns":     active_rollback_cooldowns,
        "active_chaos_incidents":        active_chaos_incidents,
        "balance_guardian_severity":     balance_guardian_severity,
        "deployment_phase":              deployment_phase,
        "uptime_s":                      _uptime_s(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Section 2 — Execution Telemetry
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/v2/execution")
def v2_execution() -> Dict[str, Any]:
    """Latency stats (p50/p95/p99 per operation), slippage, fill rate, degradation."""
    try:
        from runtime.latency_profiler import get_profiler  # type: ignore[import]
        profiler = get_profiler()
        all_stats = profiler.get_all_stats()
        degradation = profiler.get_exchange_degradation_score()

        stats_list = [
            {
                "category":                s.category.value,
                "operation":               s.operation,
                "p50_ms":                  round(s.p50_ms, 3),
                "p95_ms":                  round(s.p95_ms, 3),
                "p99_ms":                  round(s.p99_ms, 3),
                "min_ms":                  round(s.min_ms, 3),
                "max_ms":                  round(s.max_ms, 3),
                "ewma_ms":                 round(s.ewma_ms, 3),
                "sample_count":            s.sample_count,
                "anomaly_detected":        s.anomaly_detected,
                "exchange_degradation_score": round(s.exchange_degradation_score, 4),
            }
            for s in all_stats
        ]

        # EWMA alerts: operations where anomaly_detected=True
        ewma_alerts = [s["operation"] for s in stats_list if s["anomaly_detected"]]

        return {
            "status":                    "ok",
            "exchange_degradation_score": round(degradation, 4),
            "operation_count":           len(stats_list),
            "ewma_alerts":               ewma_alerts,
            "stats":                     stats_list,
        }
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


@router.get("/api/v2/execution/stream")
def v2_execution_stream(limit: int = 50) -> Dict[str, Any]:
    """Last N latency events from data/latency_analytics.jsonl."""
    limit = max(1, min(limit, 200))
    path = _DATA_DIR / "latency_analytics.jsonl"
    records = _read_jsonl_tail(path, limit)
    return {"status": "ok", "count": len(records), "events": records}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 3 — Balance + Capital
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/v2/balance")
def v2_balance() -> Dict[str, Any]:
    """Balance guardian cross-validation status."""
    try:
        from runtime.live_balance_guardian import get_guardian  # type: ignore[import]
        guardian = get_guardian()
        status = guardian.get_status()

        last_check_ts: Optional[str] = None
        lkg = status.get("last_known_good")
        if lkg:
            last_check_ts = lkg.get("checked_at")

        # Stale seconds: derive from monotonic last_exchange_ts
        is_stale = False
        stale_seconds: Optional[float] = None
        last_ts = status.get("last_exchange_ts")
        if last_ts is not None:
            stale_elapsed = time.monotonic() - last_ts
            stale_seconds = round(stale_elapsed, 1)
            is_stale = stale_elapsed > 300.0  # default stale_threshold_s

        return {
            "status":                "ok",
            "exchange_balance":      lkg.get("exchange_balance") if lkg else None,
            "capital_engine_equity": lkg.get("capital_engine_equity") if lkg else None,
            "replay_equity":         None,  # not persisted in status snapshot
            "divergence_pct":        lkg.get("divergence_pct") if lkg else None,
            "ewma_divergence":       status.get("ewma_divergence"),
            "severity":              status.get("last_severity"),
            "is_stale":              is_stale,
            "stale_seconds":         stale_seconds,
            "negative_collateral":   None,  # computed per-check, not in status
            "consecutive_halts":     status.get("consecutive_halts"),
            "last_check_ts":         last_check_ts,
        }
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


@router.get("/api/v2/balance/history")
def v2_balance_history() -> Dict[str, Any]:
    """Last 20 balance audit records from data/balance_audit.jsonl."""
    records = _read_jsonl_tail(_DATA_DIR / "balance_audit.jsonl", 20)
    return {"status": "ok", "count": len(records), "records": records}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 4 — EventStore + Replay
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/v2/eventstore")
def v2_eventstore() -> Dict[str, Any]:
    """EventStore health: seq, checksum, divergence count, throughput."""
    latest_seq: Optional[int] = None
    checksum_ok: Optional[bool] = None
    snapshot_exists = (_DATA_DIR / "event_store_snapshot.json").exists()
    replay_divergence_count = 0
    last_event_ts: Optional[str] = None
    events_per_minute: Optional[float] = None

    try:
        from runtime.event_store import EventStore  # type: ignore[import]
        store = EventStore()
        latest_seq = store.get_latest_seq()

        ok, _errors = store.verify_integrity()
        checksum_ok = ok

        # Read last event timestamp and compute events per minute
        recent = store.read_from(seq=max(0, latest_seq - 60))
        if recent:
            last_event_ts = recent[-1].emitted_at
            # Count events in last 60s
            now_epoch = time.time()
            cutoff = now_epoch - 60.0
            count_in_window = 0
            for ev in recent:
                try:
                    ts = datetime.fromisoformat(ev.emitted_at.replace("Z", "+00:00"))
                    if ts.timestamp() >= cutoff:
                        count_in_window += 1
                except Exception:
                    pass
            events_per_minute = float(count_in_window)

    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        logger.debug("v2_eventstore: %s", exc)

    # Replay divergence count (best-effort)
    try:
        from runtime.replay_verifier import get_verifier  # type: ignore[import]
        rv_status = get_verifier().get_status()
        replay_divergence_count = rv_status.get("divergence_count", 0) or 0
    except Exception:
        pass

    return {
        "status":                  "ok",
        "latest_seq":              latest_seq,
        "snapshot_exists":         snapshot_exists,
        "checksum_ok":             checksum_ok,
        "replay_divergence_count": replay_divergence_count,
        "last_event_ts":           last_event_ts,
        "events_per_minute":       events_per_minute,
    }


@router.get("/api/v2/eventstore/recent")
def v2_eventstore_recent(limit: int = 20) -> Dict[str, Any]:
    """Last N events from EventStore (read-only, never mutates)."""
    limit = max(1, min(limit, 50))
    try:
        from runtime.event_store import EventStore  # type: ignore[import]
        store = EventStore()
        latest_seq = store.get_latest_seq()
        start = max(0, latest_seq - limit + 1)
        events = store.read_from(seq=start)
        # take only the last `limit`
        events = events[-limit:]
        return {
            "status": "ok",
            "count":  len(events),
            "events": [
                {
                    "seq":        e.seq,
                    "event_type": e.event_type.value,
                    "trace_id":   e.trace_id,
                    "symbol":     e.symbol,
                    "strategy":   e.strategy,
                    "emitted_at": e.emitted_at,
                }
                for e in events
            ],
        }
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


@router.get("/api/v2/eventstore/replay-reports")
def v2_eventstore_replay_reports() -> Dict[str, Any]:
    """Last 10 lines from data/replay_verification_audit.jsonl."""
    records = _read_jsonl_tail(_DATA_DIR / "replay_verification_audit.jsonl", 10)
    return {"status": "ok", "count": len(records), "reports": records}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 5 — Governance + Alpha
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/v2/governance")
def v2_governance() -> Dict[str, Any]:
    """Drift findings, quarantined strategies, alpha durability, regime."""
    drift_findings: List[dict] = []
    drift_overall_severity: Optional[str] = None
    quarantined_strategies: List[str] = []
    alpha_durability: Optional[dict] = None
    regime: Optional[str] = None

    # Drift engine
    try:
        from research.statistics.drift_engine import DriftEngine  # type: ignore[import]
        engine = DriftEngine()
        engine.load_outcomes()
        report = engine.generate_report()
        drift_overall_severity = report.overall_severity.value if hasattr(report, "overall_severity") else None
        drift_findings = [
            {
                "metric":             f.metric.value,
                "severity":           f.severity.value,
                "drift_ratio":        round(f.drift_ratio, 4),
                "z_score":            round(f.z_score, 4),
                "persistence_score":  round(f.persistence_score, 4),
                "description":        f.description,
                "recommended_action": f.recommended_action,
            }
            for f in (report.findings if hasattr(report, "findings") else [])
        ]
    except ImportError:
        drift_overall_severity = "unavailable"
    except Exception as exc:
        logger.debug("v2_governance drift_engine: %s", exc)

    # Alpha durability
    try:
        from research.statistics.live_alpha_lab import AlphaDurabilityLab  # type: ignore[import]
        lab = AlphaDurabilityLab()
        lab.load_outcomes()
        report = lab.generate_report()
        if report is not None:
            alpha_durability = {
                "portfolio_classification": (
                    report.portfolio_classification.value
                    if hasattr(report, "portfolio_classification") else None
                ),
                "strategies_analyzed": (
                    len(report.strategy_metrics)
                    if hasattr(report, "strategy_metrics") else None
                ),
            }
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("v2_governance alpha_lab: %s", exc)

    # Quarantined strategies from strategy_governance
    try:
        from runtime.strategy_governance import get_governance  # type: ignore[import]
        gov = get_governance()
        quarantined_strategies = list(gov.get_quarantined_strategies())
    except Exception:
        pass

    # Regime from bot state
    try:
        import json as _json
        state_path = _DATA_DIR / "cryptocom_state.json"
        if state_path.exists():
            state = _json.loads(state_path.read_text())
            regime = state.get("regime")
    except Exception:
        pass

    return {
        "status":                  "ok",
        "drift_findings":          drift_findings,
        "drift_overall_severity":  drift_overall_severity,
        "quarantined_strategies":  quarantined_strategies,
        "alpha_durability":        alpha_durability,
        "regime":                  regime,
    }


@router.get("/api/v2/governance/drift-history")
def v2_governance_drift_history() -> Dict[str, Any]:
    """Last 20 lines from data/drift_engine_audit.jsonl (if exists)."""
    records = _read_jsonl_tail(_DATA_DIR / "drift_engine_audit.jsonl", 20)
    return {"status": "ok", "count": len(records), "records": records}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 6 — Deployment + Canary
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/v2/deployment")
def v2_deployment() -> Dict[str, Any]:
    """Current deployment phase, health score, canary state, rollback history."""
    try:
        from deployment.orchestrator.orchestrator import get_orchestrator  # type: ignore[import]
        orch = get_orchestrator()
        status = orch.get_deployment_status()

        # Last 5 rollback records
        rollback_history = _read_jsonl_tail(_DATA_DIR / "rollback_audit.jsonl", 5)

        return {
            "status":                "ok",
            "current_deployment_id": status.get("current_deployment_id"),
            "state":                 status.get("state"),
            "health_score":          status.get("health_score"),
            "canary_phase":          status.get("canary_phase"),
            "freeze_window":         status.get("freeze_window_active"),
            "approval_quorum_status": None,  # not implemented in orchestrator
            "rollback_history":      rollback_history,
        }
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


@router.get("/api/v2/deployment/health")
def v2_deployment_health() -> Dict[str, Any]:
    """Full DeploymentHealthScore breakdown."""
    try:
        from deployment.orchestrator.orchestrator import get_orchestrator  # type: ignore[import]
        health = get_orchestrator().get_health_score()
        return {
            "status":               "ok",
            "survivability_score":  health.survivability_score,
            "integrity_ok":         health.integrity_ok,
            "ws_health":            health.ws_health,
            "latency_p99_ms":       health.latency_p99_ms,
            "execution_ok":         health.execution_ok,
            "composite_score":      health.composite_score,
        }
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


@router.get("/api/v2/deployment/rollback-history")
def v2_deployment_rollback_history() -> Dict[str, Any]:
    """Last 20 lines from data/rollback_audit.jsonl."""
    records = _read_jsonl_tail(_DATA_DIR / "rollback_audit.jsonl", 20)
    return {"status": "ok", "count": len(records), "records": records}


class AdvancePhaseRequest(BaseModel):
    deployment_id: str = Field(..., min_length=1, max_length=128)
    operator_id:   str = Field(..., min_length=1, max_length=64)


@router.post("/api/v2/deployment/advance-phase")
def v2_deployment_advance_phase(
    req: AdvancePhaseRequest,
    request: Request,
    _: None = Depends(_require_local_or_token),
) -> Dict[str, Any]:
    """Advance deployment to next canary phase.

    Phase 4 → STABLE is ALWAYS FORBIDDEN via this endpoint.
    Use /admin/canary/approve with an Ed25519 signed approval record.
    """
    client_ip = request.client.host if request.client else "unknown"

    # Hard guard: never allow Phase4→STABLE via this endpoint
    try:
        from deployment.orchestrator.orchestrator import (  # type: ignore[import]
            get_orchestrator,
            DeploymentState,
        )
        orch = get_orchestrator()

        # Check current state of the deployment
        with orch._lock:
            record = orch._deployments.get(req.deployment_id)

        if record is not None and record.state == DeploymentState.CANARY_PHASE_4:
            _audit(
                action="ADVANCE_PHASE",
                operator_id=req.operator_id,
                client_ip=client_ip,
                params={"deployment_id": req.deployment_id},
                result="BLOCKED",
                detail="Phase 4 requires Ed25519 approval via /admin/canary/approve",
            )
            raise HTTPException(
                status_code=403,
                detail="Phase 4 requires Ed25519 approval via /admin/canary/approve",
            )

        updated = orch.advance_phase(
            deployment_id=req.deployment_id,
            operator_id=req.operator_id,
        )

        _audit(
            action="ADVANCE_PHASE",
            operator_id=req.operator_id,
            client_ip=client_ip,
            params={"deployment_id": req.deployment_id},
            result="SUCCESS",
            detail=f"Transitioned to {updated.state.value}",
        )

        return {
            "status":        "ok",
            "deployment_id": updated.deployment_id,
            "state":         updated.state.value,
            "canary_phase":  updated.canary_phase,
            "health_score":  updated.health_score,
        }

    except HTTPException:
        raise
    except ImportError as exc:
        _audit("ADVANCE_PHASE", req.operator_id, client_ip,
               {"deployment_id": req.deployment_id}, "FAILURE", str(exc))
        return {"status": "unavailable", "error": str(exc)}
    except (KeyError, ValueError) as exc:
        _audit("ADVANCE_PHASE", req.operator_id, client_ip,
               {"deployment_id": req.deployment_id}, "FAILURE", str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        _audit("ADVANCE_PHASE", req.operator_id, client_ip,
               {"deployment_id": req.deployment_id}, "FAILURE", str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Section 7 — Distributed Coordination
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/v2/coordination")
def v2_coordination() -> Dict[str, Any]:
    """Leader election state, quorum health, fencing token, split-brain audit."""
    leader_node_id: Optional[str] = None
    is_leader: Optional[bool] = None
    election_epoch: Optional[int] = None
    quorum_health_score: Optional[float] = None
    fencing_token: Optional[str] = None
    lock_contention_count: Optional[int] = None
    split_brain_audit_count = _count_jsonl_lines(_DATA_DIR / "split_brain_audit.jsonl")

    try:
        from runtime.leader_election import LeaderElection  # type: ignore[import]
        node_id = os.getenv("NODE_ID", "dashboard")
        election = LeaderElection(node_id)
        status = election.get_status()
        leader_node_id     = status.get("current_leader")
        is_leader          = status.get("is_leader", False)
        election_epoch     = election.get_epoch()
        quorum_health_score = election.get_quorum_health_score()
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("v2_coordination leader_election: %s", exc)

    # Fencing token from fencing_tokens.json
    try:
        ft_path = _DATA_DIR / "fencing_tokens.json"
        if ft_path.exists():
            ft_data = json.loads(ft_path.read_text())
            fencing_token = str(ft_data.get("current_token", ""))
    except Exception:
        pass

    # Lock contention from rollback manager automation status
    try:
        from runtime.rollback_manager import get_rollback_manager  # type: ignore[import]
        auto = get_rollback_manager().get_automation_status()
        lock_contention_count = auto.get("total_automated_rollbacks", 0)
    except Exception:
        pass

    return {
        "status":                 "ok",
        "leader_node_id":         leader_node_id,
        "is_leader":              is_leader,
        "election_epoch":         election_epoch,
        "quorum_health_score":    quorum_health_score,
        "fencing_token":          fencing_token,
        "lock_contention_count":  lock_contention_count,
        "split_brain_audit_count": split_brain_audit_count,
    }


@router.get("/api/v2/coordination/split-brain-audit")
def v2_coordination_split_brain() -> Dict[str, Any]:
    """Last 10 lines from data/split_brain_audit.jsonl."""
    records = _read_jsonl_tail(_DATA_DIR / "split_brain_audit.jsonl", 10)
    return {"status": "ok", "count": len(records), "records": records}


# ═══════════════════════════════════════════════════════════════════════════════
# Section 8 — Chaos + Longhaul
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/v2/chaos")
def v2_chaos() -> Dict[str, Any]:
    """Chaos runtime incident report and health snapshot."""
    try:
        from runtime.chaos_runtime import get_chaos_runtime  # type: ignore[import]
        runtime = get_chaos_runtime()
        incident_report  = runtime.get_incident_report()
        health_snapshot  = runtime.take_health_snapshot()
        return {
            "status":          "ok",
            "incident_report": incident_report,
            "health_snapshot": {
                "snapshot_at":         health_snapshot.snapshot_at,
                "thread_count":        health_snapshot.thread_count,
                "open_fd_count":       health_snapshot.open_fd_count,
                "rss_mb":              health_snapshot.rss_mb,
                "survivability_score": health_snapshot.survivability_score,
                "active_chaos_events": health_snapshot.active_chaos_events,
                "total_chaos_events":  health_snapshot.total_chaos_events,
                "incident_count":      health_snapshot.incident_count,
            },
        }
    except ImportError as exc:
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


@router.get("/api/v2/chaos/events")
def v2_chaos_events() -> Dict[str, Any]:
    """Last 30 lines from data/chaos_audit.jsonl."""
    # The chaos runtime uses data/chaos_runtime_audit.jsonl
    records = _read_jsonl_tail(_DATA_DIR / "chaos_runtime_audit.jsonl", 30)
    if not records:
        # fallback to alternate name
        records = _read_jsonl_tail(_DATA_DIR / "chaos_audit.jsonl", 30)
    return {"status": "ok", "count": len(records), "events": records}


class ChaosInjectRequest(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=128)
    params:     Dict[str, Any] = Field(default_factory=dict)
    seed:       int = Field(default=42)


_LIVE_ONLY_CHAOS_EVENTS = {
    "BALANCE_CORRUPTION_SIMULATION",
    "SNAPSHOT_PARTIAL_TRUNCATION",
}


@router.post("/api/v2/chaos/inject")
def v2_chaos_inject(
    req: ChaosInjectRequest,
    request: Request,
    _: None = Depends(_require_local_or_token),
) -> Dict[str, Any]:
    """Inject a chaos event.

    Validates event_type against ChaosEventType enum.
    In DEMO_MODE=true: allows all events.
    In DEMO_MODE=false: BALANCE_CORRUPTION_SIMULATION and
    SNAPSHOT_PARTIAL_TRUNCATION are forbidden.
    """
    client_ip = request.client.host if request.client else "unknown"

    try:
        from runtime.chaos_runtime import (  # type: ignore[import]
            get_chaos_runtime,
            ChaosEventType,
            ChaosRuntimeConfig,
        )

        # Validate event_type against enum
        try:
            chaos_event_type = ChaosEventType(req.event_type)
        except ValueError:
            valid = [e.value for e in ChaosEventType]
            _audit(
                "INJECT_CHAOS",
                operator_id="SYSTEM",
                client_ip=client_ip,
                params={"event_type": req.event_type},
                result="BLOCKED",
                detail=f"Unknown event_type. Valid: {valid}",
            )
            raise HTTPException(
                status_code=400,
                detail=f"Unknown event_type '{req.event_type}'. Valid: {valid}",
            )

        # Guard: never inject destructive chaos events in live mode
        demo_mode = os.getenv("DEMO_MODE", "true").lower() not in ("false", "0", "no")
        if not demo_mode and req.event_type in _LIVE_ONLY_CHAOS_EVENTS:
            _audit(
                "INJECT_CHAOS",
                operator_id="SYSTEM",
                client_ip=client_ip,
                params={"event_type": req.event_type},
                result="BLOCKED",
                detail=f"{req.event_type} forbidden in DEMO_MODE=false",
            )
            raise HTTPException(
                status_code=403,
                detail=f"{req.event_type} is forbidden in live (non-demo) mode",
            )

        config = ChaosRuntimeConfig(seed=req.seed)
        runtime = get_chaos_runtime(config)
        event = runtime.run_event(chaos_event_type, req.params)

        _audit(
            "INJECT_CHAOS",
            operator_id="SYSTEM",
            client_ip=client_ip,
            params={"event_type": req.event_type, "seed": req.seed},
            result="SUCCESS",
            detail=f"outcome={event.outcome}",
        )

        return {
            "status":          "ok",
            "event_id":        event.event_id,
            "event_type":      event.event_type.value,
            "outcome":         event.outcome,
            "duration_ms":     round(event.duration_ms, 2),
            "subsystem_impact": event.subsystem_impact,
        }

    except HTTPException:
        raise
    except ImportError as exc:
        _audit("INJECT_CHAOS", "SYSTEM", client_ip,
               {"event_type": req.event_type}, "FAILURE", str(exc))
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        _audit("INJECT_CHAOS", "SYSTEM", client_ip,
               {"event_type": req.event_type}, "FAILURE", str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Section 9 — Security + Audit
# ═══════════════════════════════════════════════════════════════════════════════


def _count_recent_audit_events(path: Path, hours: int = 24, field: Optional[str] = None, value: Optional[str] = None) -> int:
    """Count JSONL events in the last `hours` hours, optionally filtered by field==value."""
    try:
        if not path.exists():
            return 0
        now_ts = time.time()
        cutoff = now_ts - hours * 3600.0
        count = 0
        with path.open("r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    record = json.loads(ln)
                    ts_str = record.get("ts") or record.get("triggered_at") or record.get("checked_at") or ""
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts.timestamp() < cutoff:
                            continue
                    if field is not None and value is not None:
                        if str(record.get(field, "")) != value:
                            continue
                    count += 1
                except Exception:
                    continue
        return count
    except Exception:
        return 0


@router.get("/api/v2/security")
def v2_security() -> Dict[str, Any]:
    """Security overview: approval audits, failed approvals, replay attacks, integrity criticals."""
    approvals_path = _DATA_DIR / "operator_approvals_audit.jsonl"
    # fallback to approval_audit.jsonl if the primary path doesn't exist
    if not approvals_path.exists():
        approvals_path = _DATA_DIR / "approval_audit.jsonl"

    rollback_path    = _DATA_DIR / "rollback_audit.jsonl"

    recent_approvals = _read_jsonl_tail(approvals_path, 5)
    failed_approvals_24h = _count_recent_audit_events(approvals_path, 24, "result", "REJECTED")

    # Integrity criticals in last 24h (from integrity_incidents.jsonl)
    integrity_path = _DATA_DIR / "governance" / "logs" / "integrity_incidents.jsonl"
    integrity_criticals_24h = _count_recent_audit_events(integrity_path, 24, "severity", "CRITICAL")

    # Rollback triggers in last 24h
    rollback_triggers_24h = _count_recent_audit_events(rollback_path, 24)

    # Replay attacks: count REPLAY_DIVERGENCE events from event_store
    replay_attacks_24h = 0
    try:
        from runtime.event_store import EventStore, EventType  # type: ignore[import]
        store = EventStore()
        latest = store.get_latest_seq()
        recent_events = store.read_from(seq=max(0, latest - 200))
        now_ts = time.time()
        cutoff = now_ts - 86400.0
        for ev in recent_events:
            if ev.event_type == EventType.RECONCILIATION_INCIDENT:
                try:
                    ts = datetime.fromisoformat(ev.emitted_at.replace("Z", "+00:00"))
                    if ts.timestamp() >= cutoff:
                        if "replay" in json.dumps(ev.payload).lower():
                            replay_attacks_24h += 1
                except Exception:
                    pass
    except Exception:
        pass

    return {
        "status":                    "ok",
        "recent_approval_audits":    recent_approvals,
        "failed_approvals_24h":      failed_approvals_24h,
        "replay_attacks_24h":        replay_attacks_24h,
        "integrity_criticals_24h":   integrity_criticals_24h,
        "rollback_triggers_24h":     rollback_triggers_24h,
    }


@router.get("/api/v2/security/approvals")
def v2_security_approvals() -> Dict[str, Any]:
    """Last 20 lines from data/operator_approvals_audit.jsonl."""
    path = _DATA_DIR / "operator_approvals_audit.jsonl"
    if not path.exists():
        path = _DATA_DIR / "approval_audit.jsonl"
    records = _read_jsonl_tail(path, 20)
    return {"status": "ok", "count": len(records), "approvals": records}


@router.get("/api/v2/security/integrity-findings")
def v2_security_integrity_findings() -> Dict[str, Any]:
    """Last 10 integrity CRITICAL findings from integrity monitor."""
    findings: List[dict] = []
    try:
        from runtime.integrity_monitor import get_monitor  # type: ignore[import]
        monitor = get_monitor()
        last_report = monitor.get_last_report()
        if last_report is not None:
            for f in last_report.findings:
                if hasattr(f, "severity") and f.severity.value == "CRITICAL":
                    findings.append({
                        "finding_id":       f.finding_id,
                        "severity":         f.severity.value,
                        "subsystem":        f.subsystem,
                        "description":      f.description,
                        "detected_at":      f.detected_at,
                        "remediation_hint": f.remediation_hint,
                        "auto_halt":        f.auto_halt,
                    })
        findings = findings[-10:]
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("v2_security_integrity_findings: %s", exc)

    return {"status": "ok", "count": len(findings), "findings": findings}


class TelegramValidateRequest(BaseModel):
    pass  # no body required


@router.post("/api/v2/security/validate-telegram")
def v2_security_validate_telegram(
    request: Request,
    _: None = Depends(_require_local_or_token),
) -> Dict[str, Any]:
    """Send a Telegram test message and return validation result."""
    client_ip = request.client.host if request.client else "unknown"
    try:
        from runtime.telegram_validator import validate_telegram  # type: ignore[import]
        result = validate_telegram()

        outcome = "SUCCESS" if result.message_sent else "FAILURE"
        _audit(
            "VALIDATE_TELEGRAM",
            operator_id="SYSTEM",
            client_ip=client_ip,
            params={"configured": result.configured},
            result=outcome,
            detail=result.error or "ok",
        )

        return {
            "status":          "ok",
            "configured":      result.configured,
            "message_sent":    result.message_sent,
            "response_status": result.response_status,
            "response_ok":     result.response_ok,
            "latency_ms":      round(result.latency_ms, 2),
            "error":           result.error,
            "token_prefix":    result.token_prefix,
        }
    except ImportError as exc:
        _audit("VALIDATE_TELEGRAM", "SYSTEM", client_ip, {}, "FAILURE", str(exc))
        return {"status": "unavailable", "error": str(exc)}
    except Exception as exc:
        _audit("VALIDATE_TELEGRAM", "SYSTEM", client_ip, {}, "FAILURE", str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
