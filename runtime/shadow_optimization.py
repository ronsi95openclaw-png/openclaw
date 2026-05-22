"""Shadow Weight Optimization Engine — prevents adaptive drift in strategy weights.

Shadow flow:
  1. apply_candidate()    — registers a pending change (no live effect yet)
  2. promote()            — validates then atomically applies to live weights
  3. rollback()           — reverts a strategy to its snapshot weight

Validation gates before promotion:
  - Minimum trade sample  (min_trades ≥ 10 by default)
  - Confidence threshold  (≥ 0.65, EWMA-biased)
  - Step-size cap         (|Δweight| ≤ 0.30 per promotion)
  - Boundary guard        (cannot jump > 0.20 when old_weight is at 0.0 or 1.0)

EWMA bias: the most-recent half of raw_outcomes are weighted at 1.5× the older
half when computing the effective confidence score.

Persistence: snapshots written atomically to data/shadow_weights.json via
temp-file + os.replace().  Thread-safe: all mutating operations hold _lock.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("openclaw.runtime.shadow_optimization")

_SHADOW_FILE = Path(__file__).parent.parent / "data" / "shadow_weights.json"

# ── Status literals ───────────────────────────────────────────────────────────

STATUS_PENDING     = "PENDING"
STATUS_APPROVED    = "APPROVED"
STATUS_REJECTED    = "REJECTED"
STATUS_ROLLED_BACK = "ROLLED_BACK"


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class ShadowCandidate:
    """A pending (or resolved) weight change for a single strategy.

    Fields
    ------
    strategy            : strategy name (e.g. "EMA_CROSS")
    old_weight          : weight recorded at snapshot time
    new_weight          : proposed weight from the candidate source
    source              : origin of the suggestion ("claude_analyst", "manual", …)
    created_at          : ISO-8601 UTC timestamp string
    min_trades_required : minimum trades needed before promotion is allowed
    actual_trades       : trades in the sample that produced new_weight
    confidence          : 0.0–1.0 EWMA-biased confidence score
    status              : PENDING | APPROVED | REJECTED | ROLLED_BACK
    rejection_reason    : human-readable explanation when status != APPROVED
    """
    strategy:            str
    old_weight:          float
    new_weight:          float
    source:              str
    created_at:          str   = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    min_trades_required: int   = 10
    actual_trades:       int   = 0
    confidence:          float = 0.0
    status:              str   = STATUS_PENDING
    rejection_reason:    str   = ""

    # ── serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy":            self.strategy,
            "old_weight":          self.old_weight,
            "new_weight":          self.new_weight,
            "source":              self.source,
            "created_at":          self.created_at,
            "min_trades_required": self.min_trades_required,
            "actual_trades":       self.actual_trades,
            "confidence":          self.confidence,
            "status":              self.status,
            "rejection_reason":    self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ShadowCandidate":
        return cls(
            strategy=            d["strategy"],
            old_weight=          float(d["old_weight"]),
            new_weight=          float(d["new_weight"]),
            source=              d.get("source", "unknown"),
            created_at=          d.get(
                "created_at", datetime.now(timezone.utc).isoformat()
            ),
            min_trades_required= int(d.get("min_trades_required", 10)),
            actual_trades=       int(d.get("actual_trades", 0)),
            confidence=          float(d.get("confidence", 0.0)),
            status=              d.get("status", STATUS_PENDING),
            rejection_reason=    d.get("rejection_reason", ""),
        )


# ── Engine ────────────────────────────────────────────────────────────────────

class ShadowOptimizationEngine:
    """Validation gate between Claude Opus recommendations and live strategy weights.

    Parameters
    ----------
    live_weights_path :
        Path to the live strategy_weights.json maintained by the bot.
    shadow_file :
        Path for shadow snapshot persistence.
        Defaults to data/shadow_weights.json relative to the project root.
    min_trades :
        Minimum number of trades a candidate must reference before promotion.
    min_confidence :
        Minimum EWMA-biased confidence score required for promotion.
    max_weight_step :
        Maximum absolute weight change |Δweight| allowed in a single promotion.
    boundary_jump_limit :
        Extra-strict cap applied when old_weight is already at 0.0 or 1.0.
        Must be < max_weight_step to have any effect.
    ewma_recent_multiplier :
        Multiplier applied to the most-recent half of outcomes when computing
        the effective confidence score.  Default 1.5 = 50 % recency bonus.
    """

    def __init__(
        self,
        live_weights_path: str = "data/strategy_weights.json",
        shadow_file: Optional[str] = None,
        min_trades: int = 10,
        min_confidence: float = 0.65,
        max_weight_step: float = 0.30,
        boundary_jump_limit: float = 0.20,
        ewma_recent_multiplier: float = 1.5,
    ) -> None:
        self._live_path    = Path(live_weights_path)
        self._shadow_path  = Path(shadow_file) if shadow_file else _SHADOW_FILE
        self._min_trades   = min_trades
        self._min_conf     = min_confidence
        self._max_step     = max_weight_step
        self._boundary_lim = boundary_jump_limit
        self._ewma_mult    = ewma_recent_multiplier

        self._lock     = threading.Lock()
        self._snapshot: Dict[str, float] = {}
        self._snapshot_ts: str = datetime.now(timezone.utc).isoformat()
        self._candidates: Dict[str, ShadowCandidate] = {}

        self._approved_count = 0
        self._rejected_count = 0

        self._load_snapshot_or_init()

    # ── Public API ────────────────────────────────────────────────────────────

    def apply_candidate(
        self,
        strategy_name: str,
        new_weight: float,
        source: str,
        actual_trades: int = 0,
        raw_outcomes: Optional[List[bool]] = None,
    ) -> ShadowCandidate:
        """Register a pending weight change.  Does NOT modify live weights.

        Parameters
        ----------
        strategy_name :
            The strategy whose weight is being proposed (e.g. "EMA_CROSS").
        new_weight :
            Proposed weight, clamped internally to [0.0, 1.0].
        source :
            Origin of the recommendation ("claude_analyst", "manual", etc.).
        actual_trades :
            Number of trades in the sample that generated this recommendation.
        raw_outcomes :
            Optional ordered list of booleans (True = win, False = loss),
            oldest first.  When provided, the EWMA-biased confidence is
            computed automatically.  When omitted, confidence defaults to 0.0
            and the candidate will be rejected for insufficient confidence
            unless the caller later mutates candidate.confidence before promote().

        Returns
        -------
        ShadowCandidate with status=PENDING.
        """
        new_weight = max(0.0, min(1.0, float(new_weight)))
        old_weight = self._snapshot.get(strategy_name, 0.25)

        confidence = 0.0
        if raw_outcomes:
            confidence = self._ewma_confidence(raw_outcomes)

        candidate = ShadowCandidate(
            strategy=            strategy_name,
            old_weight=          old_weight,
            new_weight=          new_weight,
            source=              source,
            min_trades_required= self._min_trades,
            actual_trades=       actual_trades,
            confidence=          confidence,
        )

        with self._lock:
            self._candidates[strategy_name] = candidate
            self._persist()

        logger.info(
            "[shadow] candidate registered: %s  %.4f → %.4f  "
            "trades=%d  conf=%.3f  source=%s",
            strategy_name, old_weight, new_weight,
            actual_trades, confidence, source,
        )
        return candidate

    def promote(self, strategy_name: str) -> Tuple[bool, str]:
        """Validate and apply a pending candidate to the live weights file.

        All four validation gates must pass:
          1. Minimum trade sample
          2. Confidence threshold
          3. Step-size cap
          4. Boundary guard

        Returns
        -------
        (True, "")            on success — candidate status becomes APPROVED.
        (False, reason_str)   on failure — candidate status becomes REJECTED.
        """
        with self._lock:
            candidate = self._candidates.get(strategy_name)
            if candidate is None:
                return False, f"No candidate found for strategy '{strategy_name}'"

            if candidate.status != STATUS_PENDING:
                return (
                    False,
                    f"Candidate is not PENDING (current status: {candidate.status})",
                )

            ok, reason = self._validate(candidate)
            if not ok:
                candidate.status           = STATUS_REJECTED
                candidate.rejection_reason = reason
                self._rejected_count      += 1
                self._persist()
                logger.warning("[shadow] REJECTED %s: %s", strategy_name, reason)
                return False, reason

            applied = self._apply_to_live(strategy_name, candidate.new_weight)
            if not applied:
                reason = (
                    "Failed to write live weights — file may be locked or missing"
                )
                candidate.status           = STATUS_REJECTED
                candidate.rejection_reason = reason
                self._rejected_count      += 1
                self._persist()
                logger.error(
                    "[shadow] REJECTED %s (write failure): %s", strategy_name, reason
                )
                return False, reason

            candidate.status  = STATUS_APPROVED
            self._approved_count += 1
            self._persist()

        logger.info(
            "[shadow] PROMOTED %s: %.4f → %.4f (source=%s)",
            strategy_name,
            candidate.old_weight,
            candidate.new_weight,
            candidate.source,
        )
        return True, ""

    def rollback(self, strategy_name: str) -> Tuple[bool, str]:
        """Revert a strategy's live weight to the value recorded at snapshot time.

        Works even when no candidate exists for the strategy — the engine only
        needs the strategy to appear in the snapshot.

        Returns
        -------
        (True, "")          on success.
        (False, reason_str) on failure.
        """
        with self._lock:
            if strategy_name not in self._snapshot:
                return False, f"No snapshot weight for strategy '{strategy_name}'"

            snapshot_weight = self._snapshot[strategy_name]
            applied = self._apply_to_live(strategy_name, snapshot_weight)
            if not applied:
                return False, "Failed to write rollback weight to live file"

            candidate = self._candidates.get(strategy_name)
            if candidate is not None:
                candidate.status           = STATUS_ROLLED_BACK
                candidate.rejection_reason = "Manually rolled back to snapshot weight"

            self._persist()

        logger.info(
            "[shadow] ROLLED BACK %s → %.4f (snapshot)",
            strategy_name, snapshot_weight,
        )
        return True, ""

    def promote_all_eligible(self) -> Dict[str, Tuple[bool, str]]:
        """Attempt to promote every PENDING candidate that passes validation.

        Returns
        -------
        Mapping of strategy_name → (promoted: bool, reason: str) for every
        candidate that was attempted.  Non-PENDING candidates are skipped.
        """
        with self._lock:
            pending_names = [
                name
                for name, c in self._candidates.items()
                if c.status == STATUS_PENDING
            ]

        results: Dict[str, Tuple[bool, str]] = {}
        for name in pending_names:
            results[name] = self.promote(name)  # promote() acquires its own lock

        promoted = sum(1 for ok, _ in results.values() if ok)
        rejected = len(results) - promoted
        logger.info(
            "[shadow] promote_all_eligible: %d attempted, %d promoted, %d rejected",
            len(results), promoted, rejected,
        )
        return results

    def get_pending(self) -> List[ShadowCandidate]:
        """Return a list of all candidates currently in PENDING status."""
        with self._lock:
            return [
                c for c in self._candidates.values()
                if c.status == STATUS_PENDING
            ]

    def get_status(self) -> Dict[str, Any]:
        """Return a summary dict of engine state.

        Keys
        ----
        snapshot_ts    : ISO-8601 timestamp when snapshot was taken
        snapshot       : dict of strategy → snapshotted weight
        pending_count  : number of PENDING candidates
        approved_count : cumulative approvals this session
        rejected_count : cumulative rejections this session
        candidates     : full serialised candidate dict
        """
        with self._lock:
            return {
                "snapshot_ts":   self._snapshot_ts,
                "snapshot":      dict(self._snapshot),
                "pending_count": sum(
                    1 for c in self._candidates.values()
                    if c.status == STATUS_PENDING
                ),
                "approved_count": self._approved_count,
                "rejected_count": self._rejected_count,
                "candidates": {
                    name: c.to_dict()
                    for name, c in self._candidates.items()
                },
            }

    def get_candidate(self, strategy_name: str) -> Optional[ShadowCandidate]:
        """Return the latest candidate for a strategy, or None if not found."""
        with self._lock:
            return self._candidates.get(strategy_name)

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate(self, candidate: ShadowCandidate) -> Tuple[bool, str]:
        """Run all pre-promotion gates.  Must be called while holding _lock.

        Gates (in order):
          1. actual_trades >= min_trades_required
          2. confidence   >= _min_conf
          3. |Δweight|    <= _max_step
          4. Boundary guard: when old_weight ∈ {0.0, 1.0}, |Δweight| <= _boundary_lim
        """
        # Gate 1 — minimum trade sample
        if candidate.actual_trades < candidate.min_trades_required:
            return (
                False,
                f"Insufficient trades: {candidate.actual_trades} < "
                f"{candidate.min_trades_required} required",
            )

        # Gate 2 — confidence threshold
        if candidate.confidence < self._min_conf:
            return (
                False,
                f"Confidence {candidate.confidence:.3f} below minimum "
                f"{self._min_conf:.3f}",
            )

        # Gate 3 — step-size cap
        delta = abs(candidate.new_weight - candidate.old_weight)
        if delta > self._max_step:
            return (
                False,
                f"Weight change {delta:.4f} exceeds max step {self._max_step:.4f}",
            )

        # Gate 4 — boundary guard
        at_boundary = (
            candidate.old_weight <= 0.0 or candidate.old_weight >= 1.0
        )
        if at_boundary and delta > self._boundary_lim:
            return (
                False,
                f"Weight change {delta:.4f} exceeds boundary limit "
                f"{self._boundary_lim:.4f} (current weight at boundary "
                f"{candidate.old_weight:.4f})",
            )

        return True, ""

    # ── EWMA confidence ───────────────────────────────────────────────────────

    def _ewma_confidence(self, outcomes: List[bool]) -> float:
        """Compute an EWMA-biased win-rate as a confidence proxy.

        The list is split at the midpoint; the more-recent half is weighted at
        ``_ewma_mult`` times the older half.

        Returns
        -------
        float in [0.0, 1.0], rounded to 6 decimal places.
        """
        n = len(outcomes)
        if n == 0:
            return 0.0
        if n == 1:
            return 1.0 if outcomes[0] else 0.0

        split  = n // 2
        older  = outcomes[:split]
        recent = outcomes[split:]

        older_score  = (
            sum(1 for o in older  if o) / len(older)
            if older else 0.0
        )
        recent_score = (
            sum(1 for o in recent if o) / len(recent)
            if recent else 0.0
        )

        total_weight   = len(older) + len(recent) * self._ewma_mult
        weighted_score = (
            len(older)              * older_score
            + len(recent) * self._ewma_mult * recent_score
        ) / total_weight

        return round(min(1.0, max(0.0, weighted_score)), 6)

    # ── Live-weights I/O ──────────────────────────────────────────────────────

    def _apply_to_live(self, strategy_name: str, new_weight: float) -> bool:
        """Write a single weight update to the live strategy_weights.json.

        Uses read → patch → atomic write to avoid clobbering other fields.
        Returns True on success, False on any error.
        """
        try:
            if self._live_path.exists():
                raw: Dict[str, Any] = json.loads(self._live_path.read_text())
            else:
                raw = {}

            if strategy_name in raw:
                if isinstance(raw[strategy_name], dict):
                    raw[strategy_name]["weight"] = new_weight
                else:
                    raw[strategy_name] = {"weight": new_weight, "trades": 0}
            else:
                raw[strategy_name] = {"weight": new_weight, "trades": 0}

            self._atomic_write(self._live_path, raw)
            return True

        except Exception as exc:
            logger.error(
                "[shadow] _apply_to_live failed for %s: %s", strategy_name, exc
            )
            return False

    # ── Persistence ───────────────────────────────────────────────────────────

    def _atomic_write(self, path: Path, data: Dict[str, Any]) -> None:
        """Write *data* as JSON to *path* using a temp-file + os.replace() pattern."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".shadow_tmp_"
        )
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _persist(self) -> None:
        """Serialise engine state to shadow_weights.json.  Caller holds _lock."""
        payload: Dict[str, Any] = {
            "snapshot_ts":    self._snapshot_ts,
            "snapshot":       self._snapshot,
            "approved_count": self._approved_count,
            "rejected_count": self._rejected_count,
            "candidates": {
                name: c.to_dict()
                for name, c in self._candidates.items()
            },
        }
        try:
            self._atomic_write(self._shadow_path, payload)
        except Exception as exc:
            logger.error("[shadow] Failed to persist shadow state: %s", exc)

    def _load_snapshot_or_init(self) -> None:
        """Load engine state from disk if it exists; otherwise build from live weights."""
        if self._shadow_path.exists():
            try:
                raw = json.loads(self._shadow_path.read_text())
                self._snapshot_ts    = raw.get("snapshot_ts", self._snapshot_ts)
                self._approved_count = int(raw.get("approved_count", 0))
                self._rejected_count = int(raw.get("rejected_count", 0))
                snap = raw.get("snapshot", {})
                self._snapshot = {k: float(v) for k, v in snap.items()}
                for name, d in raw.get("candidates", {}).items():
                    try:
                        self._candidates[name] = ShadowCandidate.from_dict(d)
                    except Exception as exc:
                        logger.warning(
                            "[shadow] Could not restore candidate '%s': %s",
                            name, exc,
                        )
                logger.info(
                    "[shadow] Loaded existing snapshot (%d strategies, "
                    "%d candidates) from %s",
                    len(self._snapshot),
                    len(self._candidates),
                    self._shadow_path,
                )
                return
            except Exception as exc:
                logger.warning(
                    "[shadow] Failed to load existing snapshot (%s) — "
                    "rebuilding from live weights.",
                    exc,
                )

        self._build_snapshot_from_live()

    def _build_snapshot_from_live(self) -> None:
        """Read strategy_weights.json and record current weights as the snapshot."""
        self._snapshot_ts = datetime.now(timezone.utc).isoformat()
        if not self._live_path.exists():
            logger.warning(
                "[shadow] Live weights file not found at %s — "
                "snapshot will be empty.",
                self._live_path,
            )
            return
        try:
            raw = json.loads(self._live_path.read_text())
            for strategy, data in raw.items():
                if isinstance(data, dict):
                    self._snapshot[strategy] = float(data.get("weight", 1.0))
                elif isinstance(data, (int, float)):
                    self._snapshot[strategy] = float(data)
            self._persist()
            logger.info(
                "[shadow] Snapshot built from live weights: "
                "%d strategies at %s",
                len(self._snapshot), self._snapshot_ts,
            )
        except Exception as exc:
            logger.error(
                "[shadow] Failed to build snapshot from live weights: %s", exc
            )
