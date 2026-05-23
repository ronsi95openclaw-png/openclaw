"""
OpenClaw Canary Deployment Orchestrator
========================================
Drives the phased canary deployment described in canary_config.yaml.

Usage:
    python deployment/canary/deploy.py [--config PATH] [--phase PHASE]

    # Run all phases in order
    python deployment/canary/deploy.py

    # Run a single named phase (useful for re-entry after manual recovery)
    python deployment/canary/deploy.py --phase paper_shadow

The orchestrator:
  1. Loads canary_config.yaml.
  2. Iterates phases in declaration order.
  3. For each phase runs health checks every health_checks.interval_seconds.
  4. Fails into rollback if consecutive_failures_before_rollback is exceeded.
  5. On clean completion appends a PASSED audit record and notifies Telegram.
  6. On rollback appends a ROLLBACK audit record, writes
     data/rollback_state.json, notifies Telegram, and exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("canary_deployer")

# ---------------------------------------------------------------------------
# Path constants (relative to project root, resolved at runtime)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RECONCILIATION_LOG = _PROJECT_ROOT / "data" / "reconciliation.jsonl"
_DRIFT_EVENTS_LOG = _PROJECT_ROOT / "data" / "drift_events.jsonl"
_STATE_FILE = _PROJECT_ROOT / "data" / "cryptocom_state.json"
_AUDIT_LOG = _PROJECT_ROOT / "data" / "deployment_audit.jsonl"
_ROLLBACK_STATE = _PROJECT_ROOT / "data" / "rollback_state.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_last_jsonl_line(path: Path) -> Optional[dict]:
    """Return the last valid JSON object from a JSONL file, or None."""
    if not path.exists():
        return None
    last: Optional[dict] = None
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if raw:
                    try:
                        last = json.loads(raw)
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return last


def _count_jsonl_lines(path: Path, predicate=None) -> int:
    """Count lines in a JSONL file matching an optional predicate."""
    count = 0
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    if predicate is None or predicate(obj):
                        count += 1
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return count


def _append_audit(record: dict) -> None:
    """Append a single JSON record to the audit JSONL log."""
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _AUDIT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _send_telegram(message: str) -> None:
    """Send a Telegram alert if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.debug("Telegram not configured — skipping alert.")
        return
    try:
        import urllib.request
        payload = json.dumps({"chat_id": chat_id, "text": message}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        log.info("Telegram alert sent.")
    except Exception as exc:  # noqa: BLE001
        log.warning("Telegram alert failed: %s", exc)


# ---------------------------------------------------------------------------
# CanaryDeployer
# ---------------------------------------------------------------------------

class CanaryDeployer:
    """Orchestrates the OpenClaw canary deployment lifecycle."""

    def __init__(
        self,
        config_path: str = "deployment/canary/canary_config.yaml",
        trace_id: Optional[str] = None,
    ) -> None:
        config_file = Path(config_path)
        if not config_file.is_absolute():
            config_file = _PROJECT_ROOT / config_file

        with config_file.open("r", encoding="utf-8") as fh:
            self._config: dict[str, Any] = yaml.safe_load(fh)

        self._trace_id: str = trace_id or str(uuid.uuid4())
        self._deployment_id: str = str(uuid.uuid4())
        self._phases: list[dict] = self._config.get("phases", [])
        self._hc_cfg: dict = self._config.get("health_checks", {})
        self._rollback_cfg: dict = self._config.get("rollback", {})
        self._audit_cfg: dict = self._config.get("audit", {})

        self._hc_interval: int = int(self._hc_cfg.get("interval_seconds", 30))
        self._max_consec_failures: int = int(
            self._hc_cfg.get("consecutive_failures_before_rollback", 3)
        )

        self._current_phase: Optional[str] = None
        self._phase_results: dict[str, bool] = {}  # phase_name -> passed

        log.info(
            "CanaryDeployer initialised | deployment_id=%s trace_id=%s",
            self._deployment_id,
            self._trace_id,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_current_phase(self) -> Optional[str]:
        """Return the name of the currently executing phase, or None."""
        return self._current_phase

    def get_deployment_status(self) -> dict:
        """Return a snapshot of deployment state."""
        return {
            "deployment_id": self._deployment_id,
            "trace_id": self._trace_id,
            "current_phase": self._current_phase,
            "phase_results": dict(self._phase_results),
            "ts": _utcnow(),
        }

    def run_phase(self, phase_name: str) -> bool:
        """
        Execute a single named phase.

        Runs health checks every interval until the phase duration elapses
        (or indefinitely if duration_minutes is null).  Returns True if the
        phase completed without triggering rollback.
        """
        phase = self._get_phase_config(phase_name)
        if phase is None:
            log.error("Unknown phase: %s", phase_name)
            return False

        self._current_phase = phase_name
        duration_min = phase.get("duration_minutes")
        deadline: Optional[float] = (
            time.monotonic() + duration_min * 60 if duration_min is not None else None
        )

        self._audit(
            event="phase_start",
            phase=phase_name,
            reason=f"Starting phase {phase_name}",
        )
        log.info("Phase START: %s (duration=%s min)", phase_name, duration_min)

        consec_failures = 0

        while True:
            # Check deadline
            if deadline is not None and time.monotonic() >= deadline:
                log.info("Phase COMPLETE (duration elapsed): %s", phase_name)
                break

            # Run health checks
            all_ok = self._run_health_checks()
            if all_ok:
                consec_failures = 0
            else:
                consec_failures += 1
                log.warning(
                    "Health check failure %d/%d for phase %s",
                    consec_failures,
                    self._max_consec_failures,
                    phase_name,
                )
                if consec_failures >= self._max_consec_failures:
                    reason = (
                        f"Phase {phase_name}: {consec_failures} consecutive "
                        "health check failures"
                    )
                    self.rollback(reason=reason)
                    return False

            # For indefinite phases (supervised_full) we keep looping until
            # a rollback trigger fires or the operator interrupts the process.
            if deadline is None:
                time.sleep(self._hc_interval)
                continue

            time.sleep(min(self._hc_interval, max(0.0, deadline - time.monotonic())))

        # Phase passed
        self._phase_results[phase_name] = True
        self._audit(event="phase_passed", phase=phase_name, reason="Duration elapsed without rollback triggers")
        _send_telegram(f"[OpenClaw Canary] Phase PASSED: {phase_name} | trace={self._trace_id}")
        log.info("Phase PASSED: %s", phase_name)
        return True

    def run_all_phases(self) -> bool:
        """
        Run all configured phases in declaration order.

        Stops and triggers rollback on the first phase failure.
        Returns True only if every phase passes.
        """
        log.info("Starting full canary deployment — %d phases", len(self._phases))
        for phase_cfg in self._phases:
            phase_name = phase_cfg["name"]
            passed = self.run_phase(phase_name)
            if not passed:
                log.error("Deployment aborted at phase: %s", phase_name)
                return False
        log.info("All canary phases PASSED — deployment complete.")
        return True

    def rollback(self, reason: str) -> None:
        """
        Trigger a controlled rollback.

        Writes rollback_state.json, appends to audit log, sends Telegram
        alert, and exits with a non-zero status code.
        """
        log.error("ROLLBACK triggered: %s", reason)

        rollback_state = {
            "deployment_id": self._deployment_id,
            "trace_id": self._trace_id,
            "ts": _utcnow(),
            "phase_at_failure": self._current_phase,
            "reason": reason,
            "phase_results": dict(self._phase_results),
        }

        # Write rollback state file
        _ROLLBACK_STATE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with _ROLLBACK_STATE.open("w", encoding="utf-8") as fh:
                json.dump(rollback_state, fh, indent=2)
            log.info("Rollback state written to %s", _ROLLBACK_STATE)
        except OSError as exc:
            log.error("Failed to write rollback state: %s", exc)

        # Audit record
        self._audit(
            event="rollback",
            phase=self._current_phase or "unknown",
            reason=reason,
        )

        # Telegram alert
        if self._rollback_cfg.get("notify_telegram", True):
            _send_telegram(
                f"[OpenClaw Canary] ROLLBACK triggered\n"
                f"Phase: {self._current_phase}\n"
                f"Reason: {reason}\n"
                f"trace_id: {self._trace_id}"
            )

        sys.exit(1)

    # ------------------------------------------------------------------
    # Health checks
    # ------------------------------------------------------------------

    def _run_health_checks(self) -> bool:
        """Run all configured health checks; return True if all pass."""
        results = {
            "reconciliation_passed": self._check_reconciliation_passed(),
            "ws_health_score": self._check_ws_health(),
            "drift_events_active": self._check_drift_events(),
            "capital_state": self._check_capital_state(),
            "survivability_score": self._check_survivability(),
        }
        failed = [k for k, v in results.items() if not v]
        if failed:
            log.warning("Health checks FAILED: %s", failed)
            return False
        log.debug("Health checks OK: %s", list(results.keys()))
        return True

    def _check_reconciliation_passed(self) -> bool:
        """
        Read the last entry from data/reconciliation.jsonl.
        Returns True if the entry has "passed": true (or the file is absent,
        which means reconciliation has not run yet — treated as OK in early
        phases).
        """
        entry = _read_last_jsonl_line(_RECONCILIATION_LOG)
        if entry is None:
            log.debug("reconciliation.jsonl absent or empty — treating as OK.")
            return True
        passed = bool(entry.get("passed", True))
        if not passed:
            log.warning("Last reconciliation entry shows passed=false: %s", entry)
        return passed

    def _check_ws_health(self, threshold: float = 0.4) -> bool:
        """
        Import WSGuardian, retrieve the health score, and check it meets
        the minimum threshold (default 0.4 — the absolute floor).
        Falls back to True if WSGuardian is unavailable in this environment.
        """
        try:
            sys.path.insert(0, str(_PROJECT_ROOT))
            from runtime.ws_guardian import get_guardian  # type: ignore[import]
            guardian = get_guardian()
            health = guardian.get_health_score()
            score = float(health.score)
            log.debug("WSGuardian health score: %.4f (threshold=%.2f)", score, threshold)
            if score < threshold:
                log.warning("WSGuardian score %.4f below threshold %.2f", score, threshold)
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("WSGuardian unavailable (%s) — treating as OK.", exc)
            return True

    def _check_drift_events(self, max_active: int = 3) -> bool:
        """
        Count active drift events in data/drift_events.jsonl.
        An event is considered active if it has no "resolved_at" field.
        Returns True if the active count is within the allowed maximum.
        """
        def _is_active(obj: dict) -> bool:
            return "resolved_at" not in obj or obj.get("resolved_at") is None

        active = _count_jsonl_lines(_DRIFT_EVENTS_LOG, predicate=_is_active)
        log.debug("Active drift events: %d (max=%d)", active, max_active)
        if active > max_active:
            log.warning("Too many active drift events: %d > %d", active, max_active)
            return False
        return True

    def _check_capital_state(self) -> bool:
        """
        Read data/cryptocom_state.json and check that capital_state is not
        EMERGENCY_HALT.  Returns True if the file is absent or the state is
        not EMERGENCY_HALT.
        """
        if not _STATE_FILE.exists():
            log.debug("cryptocom_state.json absent — treating capital state as OK.")
            return True
        try:
            with _STATE_FILE.open("r", encoding="utf-8") as fh:
                state = json.load(fh)
            capital_state = state.get("capital_state", "UNKNOWN")
            log.debug("Capital state: %s", capital_state)
            if capital_state == "EMERGENCY_HALT":
                log.error("Capital state is EMERGENCY_HALT — failing health check.")
                return False
            return True
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read capital state (%s) — treating as OK.", exc)
            return True

    def _check_survivability(self, min_score: float = 0.0) -> bool:
        """
        Import SurvivabilityEngine (from runtime.survivability) if available
        and call compute_score().  Returns True if the score is above
        min_score, or if the module is not yet present.
        """
        try:
            sys.path.insert(0, str(_PROJECT_ROOT))
            from runtime.survivability import SurvivabilityEngine  # type: ignore[import]
            engine = SurvivabilityEngine()
            score = float(engine.compute_score())
            log.debug("Survivability score: %.2f (min=%.2f)", score, min_score)
            if score < min_score:
                log.warning("Survivability score %.2f below minimum %.2f", score, min_score)
                return False
            return True
        except ImportError:
            log.debug("runtime.survivability not available — skipping check.")
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Survivability check failed (%s) — treating as OK.", exc)
            return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_phase_config(self, phase_name: str) -> Optional[dict]:
        for phase in self._phases:
            if phase.get("name") == phase_name:
                return phase
        return None

    def _audit(self, event: str, phase: str, reason: str) -> None:
        """Append an immutable audit record to the deployment audit JSONL."""
        record: dict[str, Any] = {
            "ts": _utcnow(),
            "trace_id": self._trace_id,
            "deployment_id": self._deployment_id,
            "event": event,
            "phase": phase,
            "reason": reason,
        }
        _append_audit(record)
        log.info(
            "AUDIT | event=%s phase=%s trace=%s reason=%s",
            event,
            phase,
            self._trace_id,
            reason,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenClaw Canary Deployment Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default="deployment/canary/canary_config.yaml",
        help="Path to canary_config.yaml (default: deployment/canary/canary_config.yaml)",
    )
    parser.add_argument(
        "--phase",
        default=None,
        help="Run a single named phase instead of all phases",
    )
    parser.add_argument(
        "--trace-id",
        default=None,
        help="Trace ID for this deployment run (auto-generated if omitted)",
    )
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    deployer = CanaryDeployer(
        config_path=args.config,
        trace_id=args.trace_id,
    )

    if args.phase:
        passed = deployer.run_phase(args.phase)
        sys.exit(0 if passed else 1)
    else:
        passed = deployer.run_all_phases()
        sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
