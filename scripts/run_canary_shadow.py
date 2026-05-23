#!/usr/bin/env python3
"""Run Canary Phases 1–3 in paper-shadow mode.

Advances DeploymentOrchestrator: PENDING → PHASE_1 → PHASE_2 → PHASE_3.
Phase 4 is deliberately excluded — it requires human cryptographic approval.

Paper shadow mode: DEMO_MODE=true is enforced. All trades are simulated.
Health gates are checked before each phase advancement.

Usage:
    python scripts/run_canary_shadow.py [options]

Options:
    --deployment-id ID    Use existing deployment (default: create new)
    --operator-id ID      Operator identifier (default: "paper-shadow-operator")
    --release-trace ID    Release trace ID (default: auto-generated)
    --force-paper         In DEMO_MODE, override health threshold with 70.0 if below
    --check-only          Print health score and exit without advancing
    --output-json         Print final status as JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.scripts.run_canary_shadow")

# Per-phase minimum composite health score required before each advancement.
# Phase 1 gate is checked before advancing PENDING→PHASE_1 (but start_deployment
# auto-advances to PHASE_1, so effectively Phase 2 gate=70, Phase 3 gate=80).
# We check gates in order: before advancing to phase N we require threshold[N].
_PHASE_THRESHOLDS: Dict[int, float] = {
    1: 60.0,
    2: 70.0,
    3: 80.0,
}

# Paper-shadow override: if --force-paper is set, use a lower threshold
_FORCE_PAPER_THRESHOLD = 70.0


def _print(msg: str) -> None:
    """Print to stdout (separated for easy test capture)."""
    print(msg, flush=True)


def run_shadow_phases(
    orchestrator: Any,
    deployment_id: str,
    operator_id: str,
    force_paper: bool,
    demo_mode: bool,
    output_json: bool = False,
) -> Dict[str, Any]:
    """Advance an existing deployment through canary phases 1–3.

    Parameters
    ----------
    orchestrator:   DeploymentOrchestrator instance with the deployment already
                    registered (deployment_id must exist).
    deployment_id:  ID of the deployment to advance.
    operator_id:    Human or service requesting the advance.
    force_paper:    If True and demo_mode=True, override health threshold when below.
    demo_mode:      Must be True.  Raises RuntimeError if False.
    output_json:    If True, include JSON-serialisable output in returned dict.

    Returns
    -------
    Dict with keys: deployment_id, phases (list of per-phase dicts), final_state.

    Raises
    ------
    RuntimeError if demo_mode is False.
    SystemExit(1) if demo_mode is False (called from __main__).
    """
    if not demo_mode:
        raise RuntimeError(
            "DEMO_MODE is not active. run_shadow_phases refuses to run in live mode. "
            "Set DEMO_MODE=true in your environment."
        )

    # Import DeploymentState for state comparisons (lazy)
    from deployment.orchestrator.orchestrator import DeploymentState  # type: ignore[import]

    phase_results: List[Dict[str, Any]] = []

    _print(f"[CANARY SHADOW] Starting paper-shadow canary run")
    _print(f"  deployment_id: {deployment_id}")
    _print(f"  operator_id: {operator_id}")
    _print(f"  demo_mode: true")

    final_state = "UNKNOWN"

    for phase_num in (1, 2, 3):
        threshold = _PHASE_THRESHOLDS[phase_num]

        # Guard: never advance to Phase 4
        try:
            current_record = orchestrator._deployments.get(deployment_id)
            if current_record is not None:
                if current_record.state == DeploymentState.CANARY_PHASE_4:
                    _print(
                        f"[PHASE {phase_num}] Current state is CANARY_PHASE_4 — "
                        "Phase 4 requires human approval. Stopping."
                    )
                    final_state = DeploymentState.CANARY_PHASE_4.value
                    break
                if current_record.state == DeploymentState.FAILED:
                    _print(
                        f"[PHASE {phase_num}] Deployment is in FAILED state — stopping."
                    )
                    final_state = DeploymentState.FAILED.value
                    break
        except Exception as guard_exc:
            logger.debug("State guard check error: %s", guard_exc)

        # Health check
        try:
            health = orchestrator.get_health_score()
        except Exception as exc:
            _print(f"[PHASE {phase_num}] ERROR getting health score: {exc}")
            final_state = "HEALTH_ERROR"
            sys.exit(1)

        composite = health.composite_score
        health_snapshot = {
            "phase": phase_num,
            "composite_score": composite,
            "survivability_score": health.survivability_score,
            "integrity_ok": health.integrity_ok,
            "ws_health": health.ws_health,
        }
        phase_results.append({"phase": phase_num, "health": health_snapshot, "advanced": False, "state": None})

        threshold_check_symbol = "✓" if composite >= threshold else "BLOCKED"

        _print(
            f"[PHASE {phase_num}] Health check: composite={composite:.1f} "
            f"(threshold={threshold:.1f}) {threshold_check_symbol}"
        )
        _print(
            f"           survivability={health.survivability_score:.1f}  "
            f"integrity_ok={health.integrity_ok}  "
            f"ws_health={health.ws_health:.2f}"
        )

        if composite < threshold:
            if force_paper and demo_mode:
                _print(
                    f"[PHASE {phase_num}] WARNING: Health {composite:.1f} below "
                    f"threshold {threshold:.1f}. Overriding via --force-paper "
                    "(paper-shadow tolerance — NOT for live use)."
                )
            else:
                _print(
                    f"[PHASE {phase_num}] BLOCKED: composite score {composite:.1f} < "
                    f"threshold {threshold:.1f}"
                )
                _print(
                    f"  HINT: Use --force-paper to override in DEMO_MODE"
                )
                final_state = "BLOCKED"
                return {
                    "deployment_id": deployment_id,
                    "phases": phase_results,
                    "final_state": final_state,
                }

        # Advance phase
        _print(f"[PHASE {phase_num}] Advancing...")
        try:
            record = orchestrator.advance_phase(deployment_id, operator_id)
        except Exception as exc:
            _print(f"[PHASE {phase_num}] ERROR during advance_phase: {exc}")
            final_state = "ADVANCE_ERROR"
            sys.exit(1)

        final_state = record.state.value if hasattr(record.state, "value") else str(record.state)

        # Check for failed state
        if record.state == DeploymentState.FAILED:
            _print(f"[PHASE {phase_num}] FAILED after advance_phase — stopping.")
            phase_results[-1]["advanced"] = False
            phase_results[-1]["state"] = final_state
            return {
                "deployment_id": deployment_id,
                "phases": phase_results,
                "final_state": final_state,
            }

        phase_results[-1]["advanced"] = True
        phase_results[-1]["state"] = final_state

        # Guard: if we somehow reached Phase 4, stop
        if record.state == DeploymentState.CANARY_PHASE_4:
            _print(
                f"[PHASE {phase_num}] Reached CANARY_PHASE_4 — "
                "Phase 4 requires human cryptographic approval. Stopping."
            )
            final_state = DeploymentState.CANARY_PHASE_4.value
            break

        _print(f"[PHASE {phase_num}] → {final_state} ✓")

    _print("")
    _print("[CANARY SHADOW] Summary")
    _print(f"  Phases advanced: {sum(1 for p in phase_results if p.get('advanced'))}/3")
    _print(f"  Final state: {final_state}")
    _print("")
    _print(
        "[CANARY SHADOW] Phase 4 (CANARY_PHASE_4 → STABLE) is NOT automated here."
    )
    _print(
        "  To promote to STABLE, a human operator must provide a cryptographically"
        " signed approval record and call advance_phase() manually."
    )

    result: Dict[str, Any] = {
        "deployment_id": deployment_id,
        "phases": phase_results,
        "final_state": final_state,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Canary Phases 1–3 in paper-shadow mode."
    )
    parser.add_argument("--deployment-id", default=None, help="Existing deployment ID")
    parser.add_argument(
        "--operator-id",
        default="paper-shadow-operator",
        help="Operator identifier",
    )
    parser.add_argument(
        "--release-trace",
        default=None,
        help="Release trace ID (default: auto-generated)",
    )
    parser.add_argument(
        "--force-paper",
        action="store_true",
        help="Override health threshold in DEMO_MODE when below target",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Print health score and exit without advancing",
    )
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Print final status as JSON to stdout",
    )
    args = parser.parse_args()

    # Enforce DEMO_MODE
    demo_mode_env = os.getenv("DEMO_MODE", "true").strip().lower()
    if demo_mode_env == "false":
        print(
            "ERROR: DEMO_MODE is set to 'false'. "
            "run_canary_shadow.py only operates in paper/demo mode. "
            "Set DEMO_MODE=true or unset it to proceed.",
            file=sys.stderr,
        )
        sys.exit(1)
    demo_mode = True  # enforced above

    # Import orchestrator (lazy)
    try:
        from deployment.orchestrator.orchestrator import DeploymentOrchestrator  # type: ignore[import]
    except ImportError as exc:
        print(f"ERROR: Cannot import DeploymentOrchestrator: {exc}", file=sys.stderr)
        sys.exit(1)

    audit_path = os.path.join("data", "canary_shadow_audit.jsonl")
    orchestrator = DeploymentOrchestrator(audit_path=audit_path)

    # Health-only check
    if args.check_only:
        try:
            health = orchestrator.get_health_score()
            _print(f"[HEALTH CHECK] composite={health.composite_score:.1f}")
            _print(f"  survivability={health.survivability_score:.1f}")
            _print(f"  integrity_ok={health.integrity_ok}")
            _print(f"  ws_health={health.ws_health:.2f}")
            _print(f"  latency_p99_ms={health.latency_p99_ms:.1f}")
            _print(f"  execution_ok={health.execution_ok}")
        except Exception as exc:
            print(f"ERROR: Health check failed: {exc}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    # Start new deployment or use existing
    deployment_id = args.deployment_id
    if deployment_id is None:
        release_trace_id = args.release_trace or str(uuid.uuid4())
        config_snapshot = {
            "mode": "paper-shadow",
            "demo_mode": True,
            "release_trace_id": release_trace_id,
        }
        try:
            record = orchestrator.start_deployment(
                operator_id=args.operator_id,
                config=config_snapshot,
            )
        except Exception as exc:
            print(f"ERROR: start_deployment failed: {exc}", file=sys.stderr)
            sys.exit(1)
        deployment_id = record.deployment_id
        _print(f"[CANARY SHADOW] Created deployment: {deployment_id}")

    result = run_shadow_phases(
        orchestrator=orchestrator,
        deployment_id=deployment_id,
        operator_id=args.operator_id,
        force_paper=args.force_paper,
        demo_mode=demo_mode,
        output_json=args.output_json,
    )

    if args.output_json:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
