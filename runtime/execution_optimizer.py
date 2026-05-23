"""Execution Optimizer — advisory-only order parameter advisor for OpenClaw.

This module is ADVISORY ONLY.  It analyses execution analytics and market
conditions to recommend *order parameters* (quantity, spread threshold,
timeout).  It does NOT:
  - Place orders directly
  - Bypass the CapitalPreservationEngine
  - Bypass the IntentPipeline
  - Override governance decisions

All policy adaptations are bounded to ±30% of defaults to prevent runaway
drift.  Demo-mode passthrough ensures the optimizer never influences
execution in demo mode (all advice returns original_qty verbatim).

Thread-safety
-------------
All policy reads/writes are protected by a threading.Lock.
Atomic file writes use tmp + os.replace.

Module singleton
----------------
    from runtime.execution_optimizer import get_optimizer
    opt = get_optimizer()
    advice = opt.get_advice("BTCUSD-PERP", qty=0.01, current_spread_bps=45.0)
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.runtime.execution_optimizer")

# ── File paths ─────────────────────────────────────────────────────────────────

_ANALYTICS_JSONL = Path("data/execution_analytics.jsonl")
_POLICY_JSON     = Path("data/execution_policy.json")

# ── Default policy constants (used for bounded-adaptation guards) ──────────────

_DEFAULT_SPREAD_THRESHOLD_BPS: float = 50.0
_DEFAULT_SLIPPAGE_BUDGET_BPS:  float = 30.0
_DEFAULT_MIN_FILL_EFFICIENCY:  float = 0.85
_DEFAULT_MAX_ORDER_SIZE_PCT:   float = 100.0
_DEFAULT_TIMEOUT_MS:           int   = 5000
_DEFAULT_RETRY_DELAY_MS:       int   = 500

# ── Bounded-adaptation guardrails ─────────────────────────────────────────────

# Maximum tightening: spread threshold may shrink to at most 50% of default
_SPREAD_THRESHOLD_FLOOR: float = _DEFAULT_SPREAD_THRESHOLD_BPS * 0.50   # 25 bps
# Maximum loosening: spread threshold may grow to at most 130% of default
_SPREAD_THRESHOLD_CEIL:  float = _DEFAULT_SPREAD_THRESHOLD_BPS * 1.30   # 65 bps

_ORDER_SIZE_FLOOR: float = 50.0   # hard floor for max_order_size_pct
_ORDER_SIZE_CEIL:  float = _DEFAULT_MAX_ORDER_SIZE_PCT * 1.30  # 130 %

# Minimum number of recent analytics records before confidence exceeds 0.5
_MIN_SAMPLES_FOR_CONFIDENCE: int = 5
_HIGH_SAMPLES_FOR_CONFIDENCE: int = 20


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class OptimizationPolicy:
    """Runtime-tunable execution parameters.

    All fields are advisory targets; the actual executor enforces its own
    hard limits independently.
    """

    spread_threshold_bps: float = _DEFAULT_SPREAD_THRESHOLD_BPS
    """Reject entry if market spread exceeds this (basis points)."""

    slippage_budget_bps: float = _DEFAULT_SLIPPAGE_BUDGET_BPS
    """Target maximum acceptable slippage per fill (basis points)."""

    min_fill_efficiency: float = _DEFAULT_MIN_FILL_EFFICIENCY
    """Minimum acceptable fill ratio (0.0–1.0). Below this → reduce size."""

    max_order_size_pct: float = _DEFAULT_MAX_ORDER_SIZE_PCT
    """Maximum fraction of computed size to submit at once (0–100 %)."""

    timeout_ms: int = _DEFAULT_TIMEOUT_MS
    """Order-level timeout in milliseconds before cancel+retry."""

    retry_delay_ms: int = _DEFAULT_RETRY_DELAY_MS
    """Pause before issuing a cancel+retry after timeout (milliseconds)."""

    prefer_maker: bool = False
    """Prefer maker pricing when spread is tight enough to warrant it."""

    enabled: bool = True
    """Master switch — when False the optimizer passes through all advice."""


@dataclass
class ExecutionAdvice:
    """Point-in-time advisory for a single order submission."""

    symbol:                      str
    original_qty:                float
    advised_qty:                 float
    """May be smaller than original_qty if order slicing is recommended."""

    advised_spread_threshold_bps: float
    advised_timeout_ms:           int
    should_wait:                  bool
    """True when the current spread is too wide to submit safely."""

    wait_reason:                  str
    confidence:                   float
    """0.0–1.0 optimizer confidence in this advice (low sample → low)."""

    policy_version:               str
    """SHA-256 fingerprint of the policy used to generate this advice."""


# ── Engine ────────────────────────────────────────────────────────────────────

class ExecutionOptimizer:
    """Advisory-only execution parameter optimizer.

    Parameters
    ----------
    analytics_path:
        Path to execution_analytics.jsonl (line-delimited ExecutionRecord JSON).
    policy_path:
        Path to execution_policy.json (persisted OptimizationPolicy).
    """

    def __init__(
        self,
        analytics_path: str | Path = _ANALYTICS_JSONL,
        policy_path:    str | Path = _POLICY_JSON,
    ) -> None:
        self._analytics_path = Path(analytics_path)
        self._policy_path    = Path(policy_path)
        self._lock           = threading.Lock()
        self._policy         = self.load_policy()

        # Ensure parent directories exist
        self._policy_path.parent.mkdir(parents=True, exist_ok=True)
        self._analytics_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(
            "ExecutionOptimizer initialised — policy_path=%s analytics_path=%s",
            self._policy_path, self._analytics_path,
        )

    # ── Policy persistence ────────────────────────────────────────────────────

    def load_policy(self) -> OptimizationPolicy:
        """Load OptimizationPolicy from JSON; fall back to defaults if corrupt/missing."""
        try:
            if self._policy_path.exists():
                with self._policy_path.open("r", encoding="utf-8") as fh:
                    fcntl.flock(fh, fcntl.LOCK_SH)
                    try:
                        raw = json.load(fh)
                    finally:
                        fcntl.flock(fh, fcntl.LOCK_UN)

                policy = OptimizationPolicy(
                    spread_threshold_bps = float(raw.get("spread_threshold_bps", _DEFAULT_SPREAD_THRESHOLD_BPS)),
                    slippage_budget_bps  = float(raw.get("slippage_budget_bps",  _DEFAULT_SLIPPAGE_BUDGET_BPS)),
                    min_fill_efficiency  = float(raw.get("min_fill_efficiency",  _DEFAULT_MIN_FILL_EFFICIENCY)),
                    max_order_size_pct   = float(raw.get("max_order_size_pct",   _DEFAULT_MAX_ORDER_SIZE_PCT)),
                    timeout_ms           = int(raw.get("timeout_ms",             _DEFAULT_TIMEOUT_MS)),
                    retry_delay_ms       = int(raw.get("retry_delay_ms",         _DEFAULT_RETRY_DELAY_MS)),
                    prefer_maker         = bool(raw.get("prefer_maker",          False)),
                    enabled              = bool(raw.get("enabled",               True)),
                )
                logger.debug("ExecutionOptimizer: loaded policy from %s", self._policy_path)
                return policy
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ExecutionOptimizer: could not load policy from %s (%s) — using defaults",
                self._policy_path, exc,
            )
        return OptimizationPolicy()

    def save_policy(self, policy: OptimizationPolicy) -> None:
        """Persist policy to JSON atomically (tmp + os.replace)."""
        self._policy_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(policy)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir    = self._policy_path.parent,
                prefix = ".tmp_exec_policy_",
                suffix = ".json",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fcntl.flock(fh, fcntl.LOCK_EX)
                    try:
                        json.dump(payload, fh, indent=2, default=str)
                    finally:
                        fcntl.flock(fh, fcntl.LOCK_UN)
                os.replace(tmp_path, self._policy_path)
                logger.debug("ExecutionOptimizer: policy persisted to %s", self._policy_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.error("ExecutionOptimizer: save_policy failed — %s", exc)

    # ── Core advisory API ─────────────────────────────────────────────────────

    def get_advice(
        self,
        symbol:             str,
        qty:                float,
        current_spread_bps: float,
        demo_mode:          bool = True,
    ) -> ExecutionAdvice:
        """Return advisory execution parameters for a proposed order.

        This method is ADVISORY ONLY.  The caller (intent pipeline / executor)
        decides whether to act on the advice.

        Parameters
        ----------
        symbol:
            Instrument identifier, e.g. "BTCUSD-PERP".
        qty:
            Proposed order quantity (in contract units).
        current_spread_bps:
            Current market bid-ask spread in basis points.
        demo_mode:
            When True (default) the optimizer is a transparent passthrough —
            no adaptation logic runs and no exchange interaction occurs.
        """
        with self._lock:
            policy = self._policy

        policy_version = self._fingerprint_policy(policy)

        # ── 1. Passthrough when disabled or in demo mode ──────────────────────
        if not policy.enabled or demo_mode:
            reason = "demo_mode" if demo_mode else "optimizer_disabled"
            logger.debug("ExecutionOptimizer: passthrough — reason=%s", reason)
            return ExecutionAdvice(
                symbol                       = symbol,
                original_qty                 = qty,
                advised_qty                  = qty,
                advised_spread_threshold_bps = policy.spread_threshold_bps,
                advised_timeout_ms           = policy.timeout_ms,
                should_wait                  = False,
                wait_reason                  = "",
                confidence                   = 1.0,
                policy_version               = policy_version,
            )

        # ── 2. Load recent analytics from JSONL ───────────────────────────────
        recent_records = self._load_recent_analytics(n=50)
        sample_count   = len(recent_records)

        # ── 3. Spread gate ────────────────────────────────────────────────────
        should_wait = False
        wait_reason = ""
        if current_spread_bps > policy.spread_threshold_bps:
            should_wait = True
            wait_reason = (
                f"spread {current_spread_bps:.1f} bps exceeds threshold "
                f"{policy.spread_threshold_bps:.1f} bps"
            )
            logger.info(
                "ExecutionOptimizer[%s]: waiting — %s", symbol, wait_reason
            )

        # ── 4. Advised quantity (slicing) ─────────────────────────────────────
        advised_qty = qty
        if sample_count > 0:
            avg_slip = self._avg_field(recent_records, "slippage_bps")
            if avg_slip > policy.slippage_budget_bps * 1.5:
                sliced = qty * 0.80
                logger.info(
                    "ExecutionOptimizer[%s]: slicing qty %.4f → %.4f "
                    "(avg_slippage=%.1f bps > budget×1.5=%.1f bps)",
                    symbol, qty, sliced,
                    avg_slip, policy.slippage_budget_bps * 1.5,
                )
                advised_qty = sliced

        # ── 5. Advised timeout ────────────────────────────────────────────────
        advised_timeout_ms = policy.timeout_ms
        if sample_count > 0:
            p95_latency = self._p95_field(recent_records, "latency_ms")
            if p95_latency > 3000.0:
                extended = int(policy.timeout_ms * 1.5)
                logger.info(
                    "ExecutionOptimizer[%s]: extending timeout %d → %d ms "
                    "(P95 latency=%.0f ms > 3000 ms)",
                    symbol, policy.timeout_ms, extended, p95_latency,
                )
                advised_timeout_ms = extended

        # ── 6. Confidence ──────────────────────────────────────────────────────
        if sample_count == 0:
            confidence = 0.2
        elif sample_count < _MIN_SAMPLES_FOR_CONFIDENCE:
            # Linear ramp from 0.2 → 0.5 as samples grow toward threshold
            confidence = 0.2 + 0.3 * (sample_count / _MIN_SAMPLES_FOR_CONFIDENCE)
        elif sample_count < _HIGH_SAMPLES_FOR_CONFIDENCE:
            # Linear ramp from 0.5 → 1.0
            fraction   = (sample_count - _MIN_SAMPLES_FOR_CONFIDENCE) / (
                _HIGH_SAMPLES_FOR_CONFIDENCE - _MIN_SAMPLES_FOR_CONFIDENCE
            )
            confidence = 0.5 + 0.5 * fraction
        else:
            confidence = 1.0

        confidence = round(min(1.0, max(0.0, confidence)), 4)

        return ExecutionAdvice(
            symbol                       = symbol,
            original_qty                 = qty,
            advised_qty                  = advised_qty,
            advised_spread_threshold_bps = policy.spread_threshold_bps,
            advised_timeout_ms           = advised_timeout_ms,
            should_wait                  = should_wait,
            wait_reason                  = wait_reason,
            confidence                   = confidence,
            policy_version               = policy_version,
        )

    # ── Bounded policy adaptation ─────────────────────────────────────────────

    def update_from_analytics(self, report: dict) -> None:
        """Adapt policy based on an analytics report.

        All changes are bounded to prevent runaway drift.  Every change is
        logged at INFO level showing old → new value.

        Parameters
        ----------
        report:
            A dict compatible with ExecutionAnalyticsReport.asdict() output,
            containing at minimum ``avg_slippage_bps`` and ``fill_efficiency``.
        """
        with self._lock:
            policy = self._policy

        changed = False

        # ── Slippage gate: tighten spread threshold when slippage too high ────
        avg_slippage = float(report.get("avg_slippage_bps", 0.0))
        if avg_slippage > policy.slippage_budget_bps * 2.0:
            old_val = policy.spread_threshold_bps
            # Tighten by 10%, but floor at 50% of default
            new_val = max(
                _SPREAD_THRESHOLD_FLOOR,
                old_val * 0.90,
            )
            # Also cap at ±30% of default
            new_val = max(
                _DEFAULT_SPREAD_THRESHOLD_BPS * 0.70,
                min(_DEFAULT_SPREAD_THRESHOLD_BPS * 1.30, new_val),
            )
            if abs(new_val - old_val) > 0.01:
                logger.info(
                    "ExecutionOptimizer.update_from_analytics: "
                    "spread_threshold_bps %.2f → %.2f "
                    "(avg_slippage=%.1f bps > budget×2.0=%.1f bps)",
                    old_val, new_val,
                    avg_slippage, policy.slippage_budget_bps * 2.0,
                )
                policy.spread_threshold_bps = new_val
                changed = True

        # ── Fill efficiency gate: reduce max order size when fill is poor ─────
        fill_efficiency = float(report.get("fill_efficiency", 1.0))
        if fill_efficiency < policy.min_fill_efficiency:
            old_val = policy.max_order_size_pct
            new_val = max(_ORDER_SIZE_FLOOR, old_val - 5.0)
            # Bound to ±30% from default
            new_val = max(
                _DEFAULT_MAX_ORDER_SIZE_PCT * 0.70,
                min(_DEFAULT_MAX_ORDER_SIZE_PCT * 1.30, new_val),
            )
            if abs(new_val - old_val) > 0.01:
                logger.info(
                    "ExecutionOptimizer.update_from_analytics: "
                    "max_order_size_pct %.1f → %.1f "
                    "(fill_efficiency=%.3f < min=%.3f)",
                    old_val, new_val,
                    fill_efficiency, policy.min_fill_efficiency,
                )
                policy.max_order_size_pct = new_val
                changed = True

        if changed:
            with self._lock:
                self._policy = policy
            self.save_policy(policy)
        else:
            logger.debug(
                "ExecutionOptimizer.update_from_analytics: no adaptation required "
                "(avg_slippage=%.1f fill_efficiency=%.3f)",
                avg_slippage, fill_efficiency,
            )

    # ── Accessors ─────────────────────────────────────────────────────────────

    def get_policy(self) -> OptimizationPolicy:
        """Return a snapshot of the current active policy (thread-safe)."""
        with self._lock:
            # Return a copy to prevent external mutation
            return OptimizationPolicy(**asdict(self._policy))

    def get_status(self) -> dict:
        """Return a JSON-serialisable diagnostic status dict."""
        policy = self.get_policy()
        policy_dict = asdict(policy)
        analytics_count = self._count_analytics_lines()
        return {
            "optimizer_enabled":       policy.enabled,
            "policy_version":          self._fingerprint_policy(policy),
            "policy":                  policy_dict,
            "analytics_records_total": analytics_count,
            "policy_path":             str(self._policy_path),
            "analytics_path":          str(self._analytics_path),
            "generated_at":            datetime.now(timezone.utc).isoformat(),
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_recent_analytics(self, n: int = 50) -> List[dict]:
        """Return the last *n* lines from execution_analytics.jsonl as dicts.

        Returns an empty list if the file is missing or corrupt.
        """
        records: List[dict] = []
        if not self._analytics_path.exists():
            return records
        try:
            with self._analytics_path.open("r", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_SH)
                try:
                    lines = fh.readlines()
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)

            for line in lines[-n:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError as exc:
            logger.warning(
                "ExecutionOptimizer._load_recent_analytics: cannot read %s — %s",
                self._analytics_path, exc,
            )
        return records

    def _count_analytics_lines(self) -> int:
        """Return a rough count of lines in analytics file (best-effort)."""
        if not self._analytics_path.exists():
            return 0
        try:
            with self._analytics_path.open("r", encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())
        except OSError:
            return 0

    @staticmethod
    def _avg_field(records: List[dict], field_name: str) -> float:
        """Compute the mean of a numeric field across records; returns 0.0 if empty."""
        values = [
            float(r[field_name])
            for r in records
            if field_name in r and r[field_name] is not None
        ]
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _p95_field(records: List[dict], field_name: str) -> float:
        """Return the 95th-percentile of a numeric field across records."""
        values = sorted(
            float(r[field_name])
            for r in records
            if field_name in r and r[field_name] is not None
        )
        if not values:
            return 0.0
        idx = max(0, int(len(values) * 0.95) - 1)
        return values[idx]

    @staticmethod
    def _fingerprint_policy(policy: OptimizationPolicy) -> str:
        """Return a short SHA-256 hex fingerprint of the policy state."""
        serialised = json.dumps(asdict(policy), sort_keys=True, default=str)
        return hashlib.sha256(serialised.encode()).hexdigest()[:12]


# ── Module-level singleton ────────────────────────────────────────────────────

_optimizer: Optional[ExecutionOptimizer] = None
_optimizer_lock = threading.Lock()


def get_optimizer() -> ExecutionOptimizer:
    """Return the module-level ExecutionOptimizer singleton (double-checked locking)."""
    global _optimizer
    if _optimizer is None:
        with _optimizer_lock:
            if _optimizer is None:
                _optimizer = ExecutionOptimizer()
    return _optimizer
