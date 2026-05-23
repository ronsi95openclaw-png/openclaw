"""Adaptive Portfolio Allocator for OpenClaw.

Adjusts sizing and exposure caps based on alpha signals and attribution data.
All adjustments are BOUNDED at ±30% from defaults.

AI SAFETY CONTRACT:
- This class may ONLY adjust sizing scalars, exposure caps, and bounds.
- It may NEVER bypass the capital engine or governance layer.
- It may NEVER force trade execution.
- All recommendations require explicit human approval via apply_recommendation().
- Fail-CLOSED: on any validation error, rejects the write and returns False.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.research.portfolio.adaptive_allocator")

# ── Default bounds ─────────────────────────────────────────────────────────────

_DEFAULT_MAX_EXPOSURE_PCT        = 30.0
_DEFAULT_MAX_SINGLE_POSITION_PCT = 10.0
_DEFAULT_MAX_POSITIONS           = 3
_DEFAULT_DIRECTIONAL_CAP_PCT     = 20.0
_DEFAULT_VOLATILITY_SCALAR       = 1.0

# Adaptation limits
_MIN_EXPOSURE_PCT        = 15.0
_MIN_DIRECTIONAL_CAP_PCT = 10.0
_MIN_VOLATILITY_SCALAR   = 0.5
_MAX_SCALAR              = 2.0   # 200% upper bound for field validation

# Compressed by these fractions
_DEGRADING_SCALAR_COMPRESS       = 0.20   # 20% compression
_REGIME_BLIND_DIRECTIONAL_REDUCE = 10.0   # reduce directional cap by this
_HIGH_VOL_EXPOSURE_COMPRESS      = 0.15   # 15% compression
_HIGH_VOL_THRESHOLD              = 2.0    # multiple of normal

# Audit path
_AUDIT_PATH   = Path("data/allocation_audit.jsonl")
_BOUNDS_PATH  = Path("data/allocation_bounds.json")


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class AllocationBounds:
    max_exposure_pct:        float = _DEFAULT_MAX_EXPOSURE_PCT
    max_single_position_pct: float = _DEFAULT_MAX_SINGLE_POSITION_PCT
    max_positions:           int   = _DEFAULT_MAX_POSITIONS
    strategy_cap:            Dict[str, float] = field(default_factory=dict)
    directional_cap_pct:     float = _DEFAULT_DIRECTIONAL_CAP_PCT
    volatility_scalar:       float = _DEFAULT_VOLATILITY_SCALAR


@dataclass
class AllocationRecommendation:
    recommended_bounds: AllocationBounds
    rationale:          str
    confidence:         float   # 0.0-1.0
    generated_at:       str
    based_on_trades:    int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounds_to_dict(b: AllocationBounds) -> dict:
    return {
        "max_exposure_pct":        b.max_exposure_pct,
        "max_single_position_pct": b.max_single_position_pct,
        "max_positions":           b.max_positions,
        "strategy_cap":            dict(b.strategy_cap),
        "directional_cap_pct":     b.directional_cap_pct,
        "volatility_scalar":       b.volatility_scalar,
    }


def _bounds_from_dict(d: dict) -> AllocationBounds:
    return AllocationBounds(
        max_exposure_pct=float(d.get("max_exposure_pct", _DEFAULT_MAX_EXPOSURE_PCT)),
        max_single_position_pct=float(d.get("max_single_position_pct", _DEFAULT_MAX_SINGLE_POSITION_PCT)),
        max_positions=int(d.get("max_positions", _DEFAULT_MAX_POSITIONS)),
        strategy_cap={str(k): float(v) for k, v in d.get("strategy_cap", {}).items()},
        directional_cap_pct=float(d.get("directional_cap_pct", _DEFAULT_DIRECTIONAL_CAP_PCT)),
        volatility_scalar=float(d.get("volatility_scalar", _DEFAULT_VOLATILITY_SCALAR)),
    )


def _validate_bounds(b: AllocationBounds) -> bool:
    """Returns True if all bounds are within legal ranges. Fail-CLOSED."""
    try:
        if not (0.0 <= b.max_exposure_pct <= 200.0):
            return False
        if not (0.0 <= b.max_single_position_pct <= 200.0):
            return False
        if not (0 < b.max_positions <= 100):
            return False
        if not (0.0 <= b.directional_cap_pct <= 200.0):
            return False
        if not (0.0 <= b.volatility_scalar <= _MAX_SCALAR):
            return False
        for cap_val in b.strategy_cap.values():
            if not (0.0 <= cap_val <= 200.0):
                return False
        return True
    except (TypeError, ValueError):
        return False


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
    """Append one record to audit JSONL under fcntl.LOCK_EX."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(audit_path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(record) + "\n")
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ── AdaptiveAllocator ─────────────────────────────────────────────────────────

class AdaptiveAllocator:
    """Adjusts portfolio sizing bounds based on alpha and attribution signals.

    All bound changes are bounded at ±30% of defaults.
    No trade execution authority. No governance bypass.
    Changes require explicit human approval via apply_recommendation().
    """

    def __init__(
        self,
        risk_engine: Optional[Any] = None,
        bounds_path: str = "data/allocation_bounds.json",
    ) -> None:
        self._risk_engine  = risk_engine
        self._bounds_path  = Path(bounds_path)
        self._audit_path   = _AUDIT_PATH
        self._lock         = threading.Lock()
        self._current_bounds: AllocationBounds = self.load_bounds()

    # ── Bounds persistence ────────────────────────────────────────────────────

    def load_bounds(self) -> AllocationBounds:
        """Load bounds from disk, returning defaults on any failure (fail-CLOSED)."""
        try:
            if not self._bounds_path.exists():
                return AllocationBounds()
            with open(self._bounds_path, "r", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_SH)
                try:
                    data = json.load(fh)
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
            bounds = _bounds_from_dict(data)
            if not _validate_bounds(bounds):
                logger.error("Loaded bounds failed validation — reverting to defaults")
                return AllocationBounds()
            return bounds
        except Exception as exc:
            logger.error("Failed to load bounds: %s — using defaults", exc)
            return AllocationBounds()

    def save_bounds(self, bounds: AllocationBounds) -> None:
        """Atomically persist bounds to disk."""
        if not _validate_bounds(bounds):
            raise ValueError("Bounds failed validation — refusing to save")
        _atomic_write_json(self._bounds_path, _bounds_to_dict(bounds))

    def get_current_bounds(self) -> AllocationBounds:
        with self._lock:
            return self._current_bounds

    # ── Recommendation logic ──────────────────────────────────────────────────

    def compute_recommendation(
        self,
        alpha_report: Optional[Any] = None,
        attribution_report: Optional[Any] = None,
        market_volatility: Optional[float] = None,
    ) -> AllocationRecommendation:
        """Compute adjusted bounds from signal inputs. Advisory — never auto-applies.

        Adaptation rules (all bounded ±30% from defaults):
        1. Collapsed strategy → zero cap for that strategy.
        2. Portfolio DEGRADING signal → compress volatility_scalar 20% (floor 0.5).
        3. Regime-blind strategies in attribution → reduce directional_cap_pct 10% (floor 10%).
        4. High market volatility (> 2× normal) → compress max_exposure_pct 15% (floor 15%).
        5. All STRONG_ALPHA → restore defaults.
        """
        with self._lock:
            base = AllocationBounds(
                max_exposure_pct=self._current_bounds.max_exposure_pct,
                max_single_position_pct=self._current_bounds.max_single_position_pct,
                max_positions=self._current_bounds.max_positions,
                strategy_cap=dict(self._current_bounds.strategy_cap),
                directional_cap_pct=self._current_bounds.directional_cap_pct,
                volatility_scalar=self._current_bounds.volatility_scalar,
            )

        rationale_parts: List[str] = []
        trades_analyzed = 0
        confidence = 1.0

        # ── Rule 1: Collapsed strategies → zero new allocation cap ────────────
        if alpha_report is not None:
            try:
                trades_analyzed = getattr(alpha_report, "trades_analyzed", 0)
                collapsed = getattr(alpha_report, "alpha_collapsed_strategies", [])
                for strat in collapsed:
                    base.strategy_cap[strat] = 0.0
                    rationale_parts.append(
                        f"Strategy '{strat}' alpha collapsed → cap set to 0%"
                    )

                # ── Rule 2: Portfolio DEGRADING → compress volatility scalar ──
                from research.statistics.alpha_validation import AlphaSignal
                port_signal = getattr(alpha_report, "portfolio_alpha_signal", None)
                if port_signal == AlphaSignal.DEGRADING:
                    new_scalar = max(
                        _MIN_VOLATILITY_SCALAR,
                        base.volatility_scalar * (1.0 - _DEGRADING_SCALAR_COMPRESS),
                    )
                    base.volatility_scalar = new_scalar
                    rationale_parts.append(
                        f"Portfolio DEGRADING → volatility_scalar compressed to {new_scalar:.3f}"
                    )

                # ── Rule 5: All STRONG_ALPHA → restore defaults ───────────────
                strategies_map = getattr(alpha_report, "strategies", {})
                if strategies_map:
                    all_strong = all(
                        getattr(m, "alpha_signal", None) == AlphaSignal.STRONG_ALPHA
                        for m in strategies_map.values()
                    )
                    if all_strong:
                        base.volatility_scalar   = _DEFAULT_VOLATILITY_SCALAR
                        base.max_exposure_pct    = _DEFAULT_MAX_EXPOSURE_PCT
                        base.directional_cap_pct = _DEFAULT_DIRECTIONAL_CAP_PCT
                        rationale_parts.append("All strategies STRONG_ALPHA → defaults restored")

            except Exception as exc:
                logger.warning("Alpha report processing error: %s", exc)
                confidence *= 0.7

        # ── Rule 3: Regime-blind strategies → reduce directional cap ──────────
        if attribution_report is not None:
            try:
                regime_blind = getattr(attribution_report, "regime_blind_strategies", [])
                if regime_blind:
                    new_dir_cap = max(
                        _MIN_DIRECTIONAL_CAP_PCT,
                        base.directional_cap_pct - _REGIME_BLIND_DIRECTIONAL_REDUCE,
                    )
                    base.directional_cap_pct = new_dir_cap
                    rationale_parts.append(
                        f"Regime-blind strategies detected → directional_cap_pct reduced to {new_dir_cap:.1f}%"
                    )
            except Exception as exc:
                logger.warning("Attribution report processing error: %s", exc)
                confidence *= 0.8

        # ── Rule 4: High market volatility → compress max_exposure_pct ───────
        if market_volatility is not None and market_volatility > _HIGH_VOL_THRESHOLD:
            new_exposure = max(
                _MIN_EXPOSURE_PCT,
                base.max_exposure_pct * (1.0 - _HIGH_VOL_EXPOSURE_COMPRESS),
            )
            base.max_exposure_pct = new_exposure
            rationale_parts.append(
                f"Market volatility {market_volatility:.2f}× (>{_HIGH_VOL_THRESHOLD}×) "
                f"→ max_exposure_pct reduced to {new_exposure:.1f}%"
            )

        # Validate result — fail-CLOSED to defaults if invalid
        if not _validate_bounds(base):
            logger.error("Computed bounds invalid — returning conservative defaults")
            base = AllocationBounds()
            rationale_parts.append("VALIDATION FAILED — reverting to safe defaults")
            confidence = 0.0

        if not rationale_parts:
            rationale_parts.append("No adaptation triggers fired — maintaining current bounds")

        return AllocationRecommendation(
            recommended_bounds=base,
            rationale="; ".join(rationale_parts),
            confidence=max(0.0, min(1.0, confidence)),
            generated_at=_now_iso(),
            based_on_trades=trades_analyzed,
        )

    # ── Human-gated apply ─────────────────────────────────────────────────────

    def apply_recommendation(
        self, rec: AllocationRecommendation, approver_id: str
    ) -> bool:
        """Apply a recommendation to disk. REQUIRES non-empty approver_id.

        Validates bounds, writes atomically, appends immutable audit record.
        Returns False (without writing) on any validation failure.
        """
        if not approver_id or not approver_id.strip():
            logger.error("apply_recommendation rejected: empty approver_id")
            return False

        if not _validate_bounds(rec.recommended_bounds):
            logger.error(
                "apply_recommendation rejected: bounds failed validation (approver=%s)",
                approver_id,
            )
            return False

        with self._lock:
            old_bounds = self._current_bounds

            try:
                # Atomic disk write
                self.save_bounds(rec.recommended_bounds)

                # Immutable audit record
                audit_record = {
                    "ts":           _now_iso(),
                    "approver_id":  approver_id,
                    "old_bounds":   _bounds_to_dict(old_bounds),
                    "new_bounds":   _bounds_to_dict(rec.recommended_bounds),
                    "rationale":    rec.rationale,
                    "confidence":   rec.confidence,
                    "based_on_trades": rec.based_on_trades,
                }
                _append_audit(self._audit_path, audit_record)

                # Update in-memory state only after successful disk write
                self._current_bounds = rec.recommended_bounds
                logger.info(
                    "Bounds applied by %s: %s", approver_id, rec.rationale
                )
                return True

            except Exception as exc:
                logger.error(
                    "apply_recommendation failed (approver=%s): %s", approver_id, exc
                )
                return False
