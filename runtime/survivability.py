"""Survivability Engine — operational health scoring for OpenClaw.

Computes a weighted 0–100 score across eight subsystems and classifies the
bot as STABLE / DEGRADED / CRITICAL / UNSAFE.  Consumers can use this to
gate deployments, surface alerts, and drive the dashboard health widget.

All subsystem checks are wrapped in individual try/except so a broken
subsystem never crashes the scoring pass (fail-CLOSED: a crashing subsystem
returns a conservatively low score, not zero, unless the spec says 0).

Design rules
------------
- NEVER makes live exchange API calls — reads cached state only.
- DEMO_MODE has no effect on scoring (scoring is purely observational).
- Fail-CLOSED: ambiguous / unreadable data returns a degraded score, not 100.
- Thread-safe via threading.Lock.

Usage
-----
    from runtime.survivability import get_survivability_engine
    engine = get_survivability_engine()
    report = engine.compute_score()
    print(report.classification.value, report.current_score)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("openclaw.runtime.survivability")

# ── File paths (same as diagnostics.py conventions) ───────────────────────────

_RECONCILIATION_JSONL  = Path("data/reconciliation.jsonl")
_DRIFT_EVENTS_JSONL    = Path("data/drift_events.jsonl")
_EXEC_ANALYTICS_JSONL  = Path("data/execution_analytics.jsonl")

# ── Enums ─────────────────────────────────────────────────────────────────────

class SurvivabilityClassification(str, Enum):
    """Coarse operational health classification."""
    STABLE   = "STABLE"    # 80–100 — deploy-ready, normal operations
    DEGRADED = "DEGRADED"  # 60–79  — functional but some subsystems impaired
    CRITICAL = "CRITICAL"  # 40–59  — significant impairment, halt recommended
    UNSAFE   = "UNSAFE"    # 0–39   — do not trade


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class SubsystemScore:
    """Health score for a single subsystem."""
    name:          str
    score:         float   # 0–100
    weight:        float   # relative weight (0–100, percentages sum to 100)
    status_detail: str     # human-readable diagnosis
    last_updated:  str     # ISO-8601 UTC timestamp of this score


@dataclass
class SurvivabilityReport:
    """Full point-in-time survivability assessment."""
    current_score:      float
    """Weighted average across all subsystems (0–100)."""

    classification:     SurvivabilityClassification

    subsystem_scores:   Dict[str, SubsystemScore]
    """Keyed by subsystem name (e.g. "reconciliation", "ws_health")."""

    degradation_trend:  str
    """'IMPROVING' | 'STABLE' | 'DEGRADING' based on last 5 scores."""

    critical_subsystems: List[str]
    """Names of subsystems whose score < 40."""

    deployment_ready:   bool
    """True only when current_score >= 80 (STABLE)."""

    generated_at:       str
    """ISO-8601 UTC timestamp of report generation."""


# ── Engine ────────────────────────────────────────────────────────────────────

class SurvivabilityEngine:
    """Computes and tracks operational survivability scores.

    Parameters
    ----------
    score_history_size:
        How many (timestamp, score) tuples to retain in memory for trend
        analysis.  Default 20.
    """

    # Subsystem names and their percentage weights (must sum to 100)
    _WEIGHTS: Dict[str, float] = {
        "reconciliation":        20.0,
        "ws_health":             15.0,
        "drift":                 15.0,
        "execution_stability":   15.0,
        "memory_stability":      10.0,
        "thread_stability":       5.0,
        "snapshot_integrity":    10.0,
        "exchange_connectivity": 10.0,
    }

    def __init__(self, score_history_size: int = 20) -> None:
        self._lock    = threading.Lock()
        self._history: Deque[Tuple[float, float]] = deque(maxlen=score_history_size)
        self._last_report: Optional[SurvivabilityReport] = None

        logger.info(
            "SurvivabilityEngine initialised (history_size=%d, total_weight=%.0f)",
            score_history_size,
            sum(self._WEIGHTS.values()),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def compute_score(self) -> SurvivabilityReport:
        """Run all subsystem checks and return a full SurvivabilityReport.

        Thread-safe; safe to call from any thread simultaneously.  Each
        subsystem check is isolated — a crash in one does NOT abort others.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        # ── Run all checks ────────────────────────────────────────────────────
        subsystems: Dict[str, SubsystemScore] = {}
        subsystems["reconciliation"]        = self._score_reconciliation()
        subsystems["ws_health"]             = self._score_ws_health()
        subsystems["drift"]                 = self._score_drift()
        subsystems["execution_stability"]   = self._score_execution_stability()
        subsystems["memory_stability"]      = self._score_memory_stability()
        subsystems["thread_stability"]      = self._score_thread_stability()
        subsystems["snapshot_integrity"]    = self._score_snapshot_integrity()
        subsystems["exchange_connectivity"] = self._score_exchange_connectivity()

        # ── Weighted aggregate ────────────────────────────────────────────────
        total_weight = sum(self._WEIGHTS.values())
        weighted_sum = sum(
            subsystems[name].score * self._WEIGHTS.get(name, 0.0)
            for name in subsystems
        )
        current_score = weighted_sum / total_weight if total_weight > 0 else 0.0
        current_score = round(min(100.0, max(0.0, current_score)), 2)

        # ── Classification ────────────────────────────────────────────────────
        classification = self._classify(current_score)

        # ── Critical subsystems ───────────────────────────────────────────────
        critical_subsystems = [
            name for name, ss in subsystems.items() if ss.score < 40.0
        ]

        # ── History + trend ───────────────────────────────────────────────────
        with self._lock:
            self._history.append((time.time(), current_score))
            trend = self._compute_trend_unlocked()

        report = SurvivabilityReport(
            current_score        = current_score,
            classification       = classification,
            subsystem_scores     = subsystems,
            degradation_trend    = trend,
            critical_subsystems  = critical_subsystems,
            deployment_ready     = current_score >= 80.0,
            generated_at         = now_iso,
        )

        with self._lock:
            self._last_report = report

        logger.info(
            "SurvivabilityEngine: score=%.1f class=%s trend=%s critical=%s",
            current_score, classification.value, trend,
            critical_subsystems or "none",
        )

        return report

    def get_last_report(self) -> Optional[SurvivabilityReport]:
        """Return the most recently computed SurvivabilityReport, or None."""
        with self._lock:
            return self._last_report

    def get_trend(self) -> str:
        """Return the current trend based on the last 5 scores in history."""
        with self._lock:
            return self._compute_trend_unlocked()

    def get_status(self) -> dict:
        """Return a JSON-serialisable diagnostic summary."""
        with self._lock:
            last = self._last_report
            history = list(self._history)
            trend   = self._compute_trend_unlocked()

        if last is None:
            return {
                "status":           "no_report_yet",
                "trend":            "STABLE",
                "history_length":   len(history),
                "generated_at":     datetime.now(timezone.utc).isoformat(),
            }

        return {
            "current_score":        last.current_score,
            "classification":       last.classification.value,
            "deployment_ready":     last.deployment_ready,
            "degradation_trend":    trend,
            "critical_subsystems":  last.critical_subsystems,
            "subsystem_scores": {
                name: {
                    "score":         ss.score,
                    "weight":        ss.weight,
                    "status_detail": ss.status_detail,
                    "last_updated":  ss.last_updated,
                }
                for name, ss in last.subsystem_scores.items()
            },
            "history_length":       len(history),
            "generated_at":         last.generated_at,
        }

    # ── Subsystem scorers ─────────────────────────────────────────────────────

    def _score_reconciliation(self) -> SubsystemScore:
        """Score based on last reconciliation.jsonl entry age and result."""
        weight   = self._WEIGHTS["reconciliation"]
        now_iso  = datetime.now(timezone.utc).isoformat()

        try:
            entry = self._last_jsonl_entry(_RECONCILIATION_JSONL)
            if entry is None:
                return SubsystemScore(
                    name          = "reconciliation",
                    score         = 40.0,
                    weight        = weight,
                    status_detail = "reconciliation.jsonl missing or empty",
                    last_updated  = now_iso,
                )

            # Parse timestamp
            ts_str = (
                entry.get("timestamp")
                or entry.get("generated_at")
                or entry.get("ts")
                or ""
            )
            age_s = self._age_seconds(ts_str)

            passed = str(entry.get("status", "")).upper() == "PASSED" or bool(
                entry.get("passed", False)
            )

            if not passed:
                return SubsystemScore(
                    name          = "reconciliation",
                    score         = 20.0,
                    weight        = weight,
                    status_detail = f"last reconciliation FAILED (age={age_s:.0f}s)",
                    last_updated  = now_iso,
                )

            if age_s < 300:        # < 5 min
                score, detail = 100.0, f"PASSED {age_s:.0f}s ago (<5 min)"
            elif age_s < 900:      # < 15 min
                score, detail = 80.0, f"PASSED {age_s:.0f}s ago (<15 min)"
            elif age_s < 3600:     # < 60 min
                score, detail = 60.0, f"PASSED {age_s:.0f}s ago (<60 min)"
            else:
                score, detail = 40.0, f"PASSED but stale ({age_s:.0f}s ago)"

            return SubsystemScore(
                name          = "reconciliation",
                score         = score,
                weight        = weight,
                status_detail = detail,
                last_updated  = now_iso,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning("_score_reconciliation error: %s", exc)
            return SubsystemScore(
                name          = "reconciliation",
                score         = 40.0,
                weight        = weight,
                status_detail = f"error reading reconciliation data: {exc}",
                last_updated  = now_iso,
            )

    def _score_ws_health(self) -> SubsystemScore:
        """Score based on WSGuardian.get_health_score().score (0-1 → 0-100)."""
        weight  = self._WEIGHTS["ws_health"]
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            from runtime.ws_guardian import get_guardian  # type: ignore[import]
            guardian     = get_guardian()
            health_score = guardian.get_health_score()
            score        = round(health_score.score * 100.0, 2)
            detail       = (
                f"WS score={health_score.score:.4f} "
                f"hb_status={health_score.heartbeat_status.value} "
                f"gaps={health_score.sequence_gaps_detected}"
            )
            return SubsystemScore(
                name          = "ws_health",
                score         = score,
                weight        = weight,
                status_detail = detail,
                last_updated  = now_iso,
            )

        except ImportError as exc:
            logger.warning("_score_ws_health: import error — %s", exc)
            return SubsystemScore(
                name          = "ws_health",
                score         = 60.0,
                weight        = weight,
                status_detail = f"ws_guardian unavailable (import error): {exc}",
                last_updated  = now_iso,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("_score_ws_health error: %s", exc)
            return SubsystemScore(
                name          = "ws_health",
                score         = 60.0,
                weight        = weight,
                status_detail = f"ws_guardian error: {exc}",
                last_updated  = now_iso,
            )

    def _score_drift(self) -> SubsystemScore:
        """Score based on unresolved drift events in the last 2 hours."""
        weight   = self._WEIGHTS["drift"]
        now_iso  = datetime.now(timezone.utc).isoformat()
        now_s    = time.time()
        cutoff_s = now_s - 7200.0  # 2 hours

        try:
            entries = self._read_all_jsonl(_DRIFT_EVENTS_JSONL)

            unresolved_count = 0
            for entry in entries:
                # Only count recent events
                ts_str = (
                    entry.get("timestamp")
                    or entry.get("ts")
                    or entry.get("detected_at")
                    or ""
                )
                event_ts = self._parse_iso_ts(ts_str)
                if event_ts is not None and event_ts < cutoff_s:
                    continue  # older than 2h — ignore

                # Count unresolved: resolved events typically have resolved=True or
                # severity changes; we count everything in the window as "unresolved"
                # unless explicitly marked resolved
                resolved = bool(entry.get("resolved", False))
                if not resolved:
                    unresolved_count += 1

            if unresolved_count == 0:
                score, detail = 100.0, "no unresolved drift events in last 2h"
            elif unresolved_count == 1:
                score, detail = 80.0, "1 unresolved drift event in last 2h"
            elif unresolved_count == 2:
                score, detail = 60.0, "2 unresolved drift events in last 2h"
            else:
                score  = 20.0
                detail = f"{unresolved_count} unresolved drift events in last 2h"

            return SubsystemScore(
                name          = "drift",
                score         = score,
                weight        = weight,
                status_detail = detail,
                last_updated  = now_iso,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning("_score_drift error: %s", exc)
            return SubsystemScore(
                name          = "drift",
                score         = 60.0,
                weight        = weight,
                status_detail = f"error reading drift events: {exc}",
                last_updated  = now_iso,
            )

    def _score_execution_stability(self) -> SubsystemScore:
        """Score based on last entry in execution_analytics.jsonl."""
        weight  = self._WEIGHTS["execution_stability"]
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            entry = self._last_jsonl_entry(_EXEC_ANALYTICS_JSONL)
            if entry is None:
                return SubsystemScore(
                    name          = "execution_stability",
                    score         = 70.0,
                    weight        = weight,
                    status_detail = "no execution analytics data (unknown, not critical)",
                    last_updated  = now_iso,
                )

            # Rejection rate sub-score
            rejection_pct = float(entry.get("rejection_pct", 0.0))
            if rejection_pct < 5.0:
                rej_score = 100.0
            elif rejection_pct < 15.0:
                rej_score = 70.0
            else:
                rej_score = 30.0

            # Slippage sub-score — prefer avg_slippage_bps; fall back to slippage_bps
            avg_slippage = float(
                entry.get("avg_slippage_bps")
                or entry.get("slippage_bps")
                or 0.0
            )
            if avg_slippage < 20.0:
                slip_score = 100.0
            elif avg_slippage < 50.0:
                slip_score = 70.0
            else:
                slip_score = 40.0

            score  = round((rej_score + slip_score) / 2.0, 2)
            detail = (
                f"rejection_pct={rejection_pct:.1f}% "
                f"avg_slippage={avg_slippage:.1f}bps "
                f"(rej_score={rej_score:.0f} slip_score={slip_score:.0f})"
            )
            return SubsystemScore(
                name          = "execution_stability",
                score         = score,
                weight        = weight,
                status_detail = detail,
                last_updated  = now_iso,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning("_score_execution_stability error: %s", exc)
            return SubsystemScore(
                name          = "execution_stability",
                score         = 70.0,
                weight        = weight,
                status_detail = f"error reading execution analytics: {exc}",
                last_updated  = now_iso,
            )

    def _score_memory_stability(self) -> SubsystemScore:
        """Score based on process RSS memory consumption."""
        weight  = self._WEIGHTS["memory_stability"]
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            rss_mb = self._get_rss_mb()
            if rss_mb is None:
                return SubsystemScore(
                    name          = "memory_stability",
                    score         = 70.0,
                    weight        = weight,
                    status_detail = "could not read process RSS",
                    last_updated  = now_iso,
                )

            if rss_mb < 200.0:
                score, detail = 100.0, f"RSS={rss_mb:.0f} MB (<200 MB)"
            elif rss_mb < 400.0:
                score, detail = 80.0,  f"RSS={rss_mb:.0f} MB (<400 MB)"
            elif rss_mb < 600.0:
                score, detail = 60.0,  f"RSS={rss_mb:.0f} MB (<600 MB)"
            else:
                score, detail = 30.0,  f"RSS={rss_mb:.0f} MB (>=600 MB — high)"

            return SubsystemScore(
                name          = "memory_stability",
                score         = score,
                weight        = weight,
                status_detail = detail,
                last_updated  = now_iso,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning("_score_memory_stability error: %s", exc)
            return SubsystemScore(
                name          = "memory_stability",
                score         = 70.0,
                weight        = weight,
                status_detail = f"error reading memory usage: {exc}",
                last_updated  = now_iso,
            )

    def _score_thread_stability(self) -> SubsystemScore:
        """Score based on threading.active_count()."""
        weight  = self._WEIGHTS["thread_stability"]
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            count = threading.active_count()
            if count < 20:
                score, detail = 100.0, f"active_threads={count} (<20)"
            elif count < 40:
                score, detail = 80.0,  f"active_threads={count} (<40)"
            elif count < 60:
                score, detail = 60.0,  f"active_threads={count} (<60)"
            else:
                score, detail = 30.0,  f"active_threads={count} (>=60 — high)"

            return SubsystemScore(
                name          = "thread_stability",
                score         = score,
                weight        = weight,
                status_detail = detail,
                last_updated  = now_iso,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning("_score_thread_stability error: %s", exc)
            return SubsystemScore(
                name          = "thread_stability",
                score         = 70.0,
                weight        = weight,
                status_detail = f"error counting threads: {exc}",
                last_updated  = now_iso,
            )

    def _score_snapshot_integrity(self) -> SubsystemScore:
        """Score based on EventSnapshotEngine integrity checks on last 3 snapshots."""
        weight  = self._WEIGHTS["snapshot_integrity"]
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            from runtime.event_snapshot import EventSnapshotEngine  # type: ignore[import]
            engine    = EventSnapshotEngine()
            snapshots = engine.list_snapshots()

            if not snapshots:
                return SubsystemScore(
                    name          = "snapshot_integrity",
                    score         = 60.0,
                    weight        = weight,
                    status_detail = "no snapshots found",
                    last_updated  = now_iso,
                )

            recent   = snapshots[:3]  # list_snapshots returns newest first
            failures = 0
            for snap in recent:
                try:
                    if not engine.verify_snapshot(snap):
                        failures += 1
                except Exception:  # noqa: BLE001
                    failures += 1

            # Check age of newest snapshot
            newest_age_s: Optional[float] = None
            newest_ts_str = recent[0].created_at if recent else ""
            if newest_ts_str:
                ts = self._parse_iso_ts(newest_ts_str)
                if ts is not None:
                    newest_age_s = time.time() - ts

            if failures == 0:
                score  = 100.0
                detail = f"all {len(recent)} checked snapshots pass integrity"
            elif failures == 1:
                score  = 60.0
                detail = f"1 of {len(recent)} snapshots failed integrity check"
            else:
                score  = 20.0
                detail = f"{failures} of {len(recent)} snapshots failed integrity check"

            # Cap at 70 if newest snapshot is older than 48h
            if newest_age_s is not None and newest_age_s > 172_800:  # 48h
                score  = min(score, 70.0)
                detail += f" (newest snapshot age={newest_age_s/3600:.1f}h >48h)"

            return SubsystemScore(
                name          = "snapshot_integrity",
                score         = score,
                weight        = weight,
                status_detail = detail,
                last_updated  = now_iso,
            )

        except ImportError as exc:
            logger.warning("_score_snapshot_integrity: import error — %s", exc)
            return SubsystemScore(
                name          = "snapshot_integrity",
                score         = 60.0,
                weight        = weight,
                status_detail = f"event_snapshot unavailable: {exc}",
                last_updated  = now_iso,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("_score_snapshot_integrity error: %s", exc)
            return SubsystemScore(
                name          = "snapshot_integrity",
                score         = 60.0,
                weight        = weight,
                status_detail = f"error checking snapshots: {exc}",
                last_updated  = now_iso,
            )

    def _score_exchange_connectivity(self) -> SubsystemScore:
        """Score based on cached exchange_reachable field in reconciliation.jsonl.

        NEVER makes live API calls — reads only the cached reconciliation record.
        """
        weight  = self._WEIGHTS["exchange_connectivity"]
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            entry = self._last_jsonl_entry(_RECONCILIATION_JSONL)
            if entry is None:
                return SubsystemScore(
                    name          = "exchange_connectivity",
                    score         = 60.0,
                    weight        = weight,
                    status_detail = "no reconciliation data — connectivity unknown",
                    last_updated  = now_iso,
                )

            reachable = entry.get("exchange_reachable")
            if reachable is None:
                return SubsystemScore(
                    name          = "exchange_connectivity",
                    score         = 60.0,
                    weight        = weight,
                    status_detail = "exchange_reachable field missing in reconciliation data",
                    last_updated  = now_iso,
                )

            if bool(reachable):
                return SubsystemScore(
                    name          = "exchange_connectivity",
                    score         = 100.0,
                    weight        = weight,
                    status_detail = "exchange_reachable=True (cached)",
                    last_updated  = now_iso,
                )
            else:
                return SubsystemScore(
                    name          = "exchange_connectivity",
                    score         = 0.0,
                    weight        = weight,
                    status_detail = "exchange_reachable=False (cached)",
                    last_updated  = now_iso,
                )

        except Exception as exc:  # noqa: BLE001
            logger.warning("_score_exchange_connectivity error: %s", exc)
            return SubsystemScore(
                name          = "exchange_connectivity",
                score         = 60.0,
                weight        = weight,
                status_detail = f"error reading connectivity data: {exc}",
                last_updated  = now_iso,
            )

    # ── Trend calculation ─────────────────────────────────────────────────────

    def _compute_trend_unlocked(self) -> str:
        """Compare the last 5 score history entries to derive a trend.

        Must be called with self._lock held.

        Returns one of: 'IMPROVING', 'STABLE', 'DEGRADING'.
        """
        if len(self._history) < 2:
            return "STABLE"

        recent = list(self._history)[-5:]  # up to last 5
        if len(recent) < 2:
            return "STABLE"

        scores = [s for _, s in recent]

        # All increasing: each score > previous by ≥ 2 points
        all_increasing = all(
            scores[i] > scores[i - 1] + 2.0
            for i in range(1, len(scores))
        )
        # All decreasing: each score < previous by ≥ 2 points
        all_decreasing = all(
            scores[i] < scores[i - 1] - 2.0
            for i in range(1, len(scores))
        )

        if all_increasing:
            return "IMPROVING"
        if all_decreasing:
            return "DEGRADING"
        return "STABLE"

    # ── Utility helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _classify(score: float) -> SurvivabilityClassification:
        """Map a 0–100 score to a SurvivabilityClassification."""
        if score >= 80.0:
            return SurvivabilityClassification.STABLE
        if score >= 60.0:
            return SurvivabilityClassification.DEGRADED
        if score >= 40.0:
            return SurvivabilityClassification.CRITICAL
        return SurvivabilityClassification.UNSAFE

    @staticmethod
    def _last_jsonl_entry(path: Path) -> Optional[dict]:
        """Return the last non-empty JSON line from a .jsonl file, or None."""
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                last_entry: Optional[dict] = None
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        last_entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
            return last_entry
        except OSError:
            return None

    @staticmethod
    def _read_all_jsonl(path: Path) -> List[dict]:
        """Return all parseable JSON entries from a .jsonl file."""
        entries: List[dict] = []
        if not path.exists():
            return entries
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return entries

    @staticmethod
    def _parse_iso_ts(ts_str: str) -> Optional[float]:
        """Parse an ISO-8601 timestamp string into a Unix epoch float.

        Returns None if parsing fails.
        """
        if not ts_str:
            return None
        try:
            from datetime import datetime as _dt
            ts_str = ts_str.replace("Z", "+00:00")
            dt     = _dt.fromisoformat(ts_str)
            return dt.timestamp()
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _age_seconds(cls, ts_str: str) -> float:
        """Return age in seconds from a timestamp string; returns 9999 if unparseable."""
        ts = cls._parse_iso_ts(ts_str)
        if ts is None:
            return 9999.0
        return max(0.0, time.time() - ts)

    @staticmethod
    def _get_rss_mb() -> Optional[float]:
        """Return process RSS in megabytes.

        Tries /proc/self/status first (Linux), then falls back to psutil.
        Returns None if neither is available.
        """
        # /proc/self/status approach (Linux-native, no extra deps)
        try:
            with open(f"/proc/{os.getpid()}/status", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        # Parts: ["VmRSS:", "<kb>", "kB"]
                        if len(parts) >= 2:
                            kb = float(parts[1])
                            return kb / 1024.0
        except (OSError, ValueError):
            pass

        # psutil fallback
        try:
            import psutil  # type: ignore[import]
            proc = psutil.Process(os.getpid())
            rss_bytes = proc.memory_info().rss
            return rss_bytes / (1024 * 1024)
        except Exception:  # noqa: BLE001
            pass

        return None


# ── Module-level singleton ────────────────────────────────────────────────────

_engine: Optional[SurvivabilityEngine] = None
_engine_lock = threading.Lock()


def get_survivability_engine(score_history_size: int = 20) -> SurvivabilityEngine:
    """Return the module-level SurvivabilityEngine singleton.

    Uses double-checked locking for thread-safe lazy initialisation.

    Parameters
    ----------
    score_history_size:
        Only used on first construction; ignored on subsequent calls.
    """
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = SurvivabilityEngine(score_history_size=score_history_size)
    return _engine
