"""Execution quality tracking and analysis.

Tracks realized slippage, fill latency, adverse selection, and other
execution quality metrics across all real trades. Persists to disk.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from research.types import ExecutionRecord


class ExecutionQualityTracker:
    """Tracks and analyzes execution quality across all real trades.

    Metrics tracked:
    - realized slippage (actual fill vs intended price)
    - expected slippage (from model)
    - latency to fill
    - adverse excursion (price move before fill)
    - fill efficiency (MFE/MAE ratio)
    - rejection rate

    Persists to data/execution_quality.json
    """

    def __init__(self, persist_path: str = "data/execution_quality.json") -> None:
        self._path = Path(persist_path)
        self._records: List[Dict[str, Any]] = []
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._records = json.loads(self._path.read_text())
            except Exception:
                self._records = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._records, indent=2, default=str))
        tmp.replace(self._path)

    # ── Recording ─────────────────────────────────────────────────────────────

    def record_fill(self, record: ExecutionRecord) -> None:
        """Record a completed fill. Computes derived metrics."""
        entry: Dict[str, Any] = {
            "order_id":         record.order_id,
            "symbol":           record.symbol,
            "side":             record.side,
            "intended_price":   record.intended_price,
            "fill_price":       record.fill_price,
            "size":             record.size,
            "latency_ms":       record.latency_ms,
            "slippage_bps":     record.slippage_bps,
            "adverse_selection": record.adverse_selection,
            "venue":            record.venue,
            "timestamp":        record.timestamp.isoformat(),
            "fill_status":      record.fill_status,
            "reject_reason":    record.reject_reason,
        }

        # Compute derived: was fill adversely selected?
        entry["is_adverse"] = record.adverse_selection > 0

        self._records.append(entry)
        # Keep last 10 000 records in memory / on disk
        if len(self._records) > 10_000:
            self._records = self._records[-10_000:]
        self._save()

    # ── Analytics ─────────────────────────────────────────────────────────────

    def _recent(self, last_n: int) -> List[Dict[str, Any]]:
        return self._records[-last_n:] if self._records else []

    def summary(self, last_n: int = 100) -> Dict[str, Any]:
        """Returns aggregated metrics over the most recent fills."""
        recs = self._recent(last_n)
        if not recs:
            return {
                "avg_slippage_bps": 0.0,
                "avg_latency_ms":   0.0,
                "rejection_rate":   0.0,
                "fill_efficiency":  1.0,
                "n_fills":          0,
            }

        total      = len(recs)
        rejected   = sum(1 for r in recs if r.get("fill_status") == "rejected")
        filled     = [r for r in recs if r.get("fill_status") != "rejected"]

        avg_slip  = (
            sum(r["slippage_bps"] for r in filled) / len(filled)
            if filled else 0.0
        )
        avg_lat   = (
            sum(r["latency_ms"] for r in filled) / len(filled)
            if filled else 0.0
        )
        rej_rate  = rejected / total if total > 0 else 0.0

        # Fill efficiency: proportion of fills that were NOT adversely selected
        adv_count = sum(1 for r in filled if r.get("is_adverse", False))
        fill_eff  = 1.0 - (adv_count / len(filled)) if filled else 1.0

        return {
            "avg_slippage_bps": round(avg_slip, 4),
            "avg_latency_ms":   round(avg_lat, 4),
            "rejection_rate":   round(rej_rate, 4),
            "fill_efficiency":  round(fill_eff, 4),
            "n_fills":          total,
        }

    def slippage_by_venue(self) -> Dict[str, float]:
        """Average slippage bps per venue."""
        venue_sums: Dict[str, List[float]] = {}
        for r in self._records:
            if r.get("fill_status") == "rejected":
                continue
            v = r.get("venue", "unknown")
            venue_sums.setdefault(v, []).append(r.get("slippage_bps", 0.0))
        return {
            v: round(sum(vals) / len(vals), 4)
            for v, vals in venue_sums.items()
            if vals
        }

    def latency_percentiles(self) -> Dict[str, float]:
        """p50, p95, p99 latency in ms."""
        lats = sorted(
            r["latency_ms"]
            for r in self._records
            if r.get("fill_status") != "rejected"
        )
        if not lats:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

        def _pct(data: List[float], p: float) -> float:
            idx = max(0, int(math.ceil(p / 100.0 * len(data))) - 1)
            return data[min(idx, len(data) - 1)]

        return {
            "p50": round(_pct(lats, 50), 2),
            "p95": round(_pct(lats, 95), 2),
            "p99": round(_pct(lats, 99), 2),
        }

    def adverse_selection_rate(self) -> float:
        """Fraction of fills where price moved adversely before fill."""
        filled = [r for r in self._records if r.get("fill_status") != "rejected"]
        if not filled:
            return 0.0
        adverse = sum(1 for r in filled if r.get("is_adverse", False))
        return round(adverse / len(filled), 4)

    def quality_score(self) -> float:
        """0–1 composite execution quality score.

        Components:
        - low slippage (target < 5 bps → score 1.0)
        - low latency  (target < 100 ms → score 1.0)
        - low rejection rate (target < 2% → score 1.0)
        - low adverse selection rate (target < 20% → score 1.0)
        """
        s = self.summary()
        if s["n_fills"] == 0:
            return 1.0  # no data ⇒ assume perfect

        # Slippage component: 0 bps = 1.0, 20 bps = 0.0
        slip_score = max(0.0, 1.0 - s["avg_slippage_bps"] / 20.0)

        # Latency component: 0 ms = 1.0, 500 ms = 0.0
        lat_score = max(0.0, 1.0 - s["avg_latency_ms"] / 500.0)

        # Rejection component: 0% = 1.0, 10% = 0.0
        rej_score = max(0.0, 1.0 - s["rejection_rate"] / 0.10)

        # Adverse selection: 0% = 1.0, 50% = 0.0
        adv_score = max(0.0, 1.0 - self.adverse_selection_rate() / 0.50)

        composite = (slip_score * 0.30 + lat_score * 0.25
                     + rej_score * 0.25 + adv_score * 0.20)
        return round(min(1.0, max(0.0, composite)), 4)
