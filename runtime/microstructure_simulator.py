"""Deterministic exchange microstructure simulator for OpenClaw.

Simulates realistic fill quality, latency, slippage, and market-stress conditions
across five market modes.  All randomness is seeded for deterministic replay.

This module is SIMULATION ONLY.  It does NOT place orders, touch the exchange,
or affect live capital state.

Thread-safety
-------------
All public methods are thread-safe.  The singleton is created under a
double-checked lock.  File writes use fcntl advisory locks + atomic rename.

Module singleton
----------------
    from runtime.microstructure_simulator import get_simulator
    sim = get_simulator()
    result = sim.simulate_fill("BTCUSD-PERP", "BUY", 0.01, 67_000.0)
"""
from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import random as _random_module

logger = logging.getLogger("openclaw.runtime.microstructure_simulator")


# ── Market mode ───────────────────────────────────────────────────────────────

class MarketMode(str, Enum):
    NORMAL             = "NORMAL"
    VOLATILE           = "VOLATILE"
    PANIC              = "PANIC"
    LIQUIDITY_CRISIS   = "LIQUIDITY_CRISIS"
    EXCHANGE_DEGRADED  = "EXCHANGE_DEGRADED"


# ── Stress profile ────────────────────────────────────────────────────────────

@dataclass
class StressProfile:
    """Per-mode exchange microstructure configuration."""
    spread_bps_base:           float  # centre of spread distribution
    spread_bps_std:            float  # std-dev noise on spread
    fill_probability:          float  # fraction 0-1; probability order fills at all
    partial_fill_prob:         float  # probability of a partial fill
    latency_ms_p50:            float
    latency_ms_p99:            float
    queue_depth_multiplier:    float  # 1.0 = normal depth, 0.1 = thin book
    liquidation_cascade_prob:  float
    ack_delay_ms:              float
    precision_rounding_lots:   float  # exchange lot size
    cancel_race_prob:          float
    packet_disorder_prob:      float


# ── Hardcoded mode profiles ───────────────────────────────────────────────────

_PROFILES: Dict[MarketMode, StressProfile] = {
    MarketMode.NORMAL: StressProfile(
        spread_bps_base=2.0,
        spread_bps_std=0.5,
        fill_probability=0.97,
        partial_fill_prob=0.05,
        latency_ms_p50=8.0,
        latency_ms_p99=40.0,
        queue_depth_multiplier=1.0,
        liquidation_cascade_prob=0.001,
        ack_delay_ms=20.0,
        precision_rounding_lots=0.001,
        cancel_race_prob=0.01,
        packet_disorder_prob=0.005,
    ),
    MarketMode.VOLATILE: StressProfile(
        spread_bps_base=8.0,
        spread_bps_std=3.0,
        fill_probability=0.88,
        partial_fill_prob=0.20,
        latency_ms_p50=30.0,
        latency_ms_p99=180.0,
        queue_depth_multiplier=0.6,
        liquidation_cascade_prob=0.02,
        ack_delay_ms=60.0,
        precision_rounding_lots=0.001,
        cancel_race_prob=0.05,
        packet_disorder_prob=0.03,
    ),
    MarketMode.PANIC: StressProfile(
        spread_bps_base=25.0,
        spread_bps_std=10.0,
        fill_probability=0.60,
        partial_fill_prob=0.50,
        latency_ms_p50=120.0,
        latency_ms_p99=800.0,
        queue_depth_multiplier=0.2,
        liquidation_cascade_prob=0.12,
        ack_delay_ms=250.0,
        precision_rounding_lots=0.001,
        cancel_race_prob=0.15,
        packet_disorder_prob=0.10,
    ),
    MarketMode.LIQUIDITY_CRISIS: StressProfile(
        spread_bps_base=50.0,
        spread_bps_std=20.0,
        fill_probability=0.40,
        partial_fill_prob=0.70,
        latency_ms_p50=250.0,
        latency_ms_p99=1500.0,
        queue_depth_multiplier=0.08,
        liquidation_cascade_prob=0.25,
        ack_delay_ms=500.0,
        precision_rounding_lots=0.001,
        cancel_race_prob=0.25,
        packet_disorder_prob=0.20,
    ),
    MarketMode.EXCHANGE_DEGRADED: StressProfile(
        spread_bps_base=15.0,
        spread_bps_std=8.0,
        fill_probability=0.70,
        partial_fill_prob=0.30,
        latency_ms_p50=200.0,
        latency_ms_p99=2000.0,
        queue_depth_multiplier=0.4,
        liquidation_cascade_prob=0.05,
        ack_delay_ms=400.0,
        precision_rounding_lots=0.001,
        cancel_race_prob=0.10,
        packet_disorder_prob=0.15,
    ),
}


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class FillResult:
    """Outcome of a single simulated order fill."""
    filled_qty:          float
    avg_fill_price:      float
    slippage_bps:        float
    latency_ms:          float
    queue_position:      int
    partial_fill:        bool
    liquidation_cascade: bool
    cancel_raced:        bool
    ack_delayed:         bool


@dataclass
class ExecutionQualityReport:
    """Aggregate statistics over a batch of simulated fills."""
    mode:                    MarketMode
    seed:                    int
    n_simulations:           int
    avg_fill_rate:           float   # avg(filled_qty / requested_qty)
    avg_slippage_bps:        float
    p50_latency_ms:          float
    p95_latency_ms:          float
    p99_latency_ms:          float
    fill_degradation_score:  float   # 0=good, 1=terrible
    queue_priority_score:    float   # 0=bad, 1=front of queue
    execution_realism_score: float   # 0-100 composite
    cascade_frequency:       float
    cancel_race_frequency:   float


# ── Simulator ─────────────────────────────────────────────────────────────────

class MicrostructureSimulator:
    """Deterministic exchange microstructure simulator.

    Parameters
    ----------
    seed:
        RNG seed for deterministic replay.
    analytics_path:
        JSONL file path where batch reports are persisted.
    """

    def __init__(
        self,
        seed: int = 42,
        analytics_path: str = "data/microstructure_analytics.jsonl",
    ) -> None:
        self._seed = seed
        self._rng  = _random_module.Random(seed)
        self._lock = threading.Lock()
        self._analytics_path = Path(analytics_path)
        self._default_mode   = MarketMode.NORMAL

        # Ensure analytics directory exists (best-effort)
        try:
            self._analytics_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    # ── Mode access ───────────────────────────────────────────────────────────

    def set_mode(self, mode: MarketMode) -> None:
        """Set the default market mode used by simulate_fill when mode is omitted."""
        with self._lock:
            self._default_mode = mode

    def get_stress_profile(self, mode: MarketMode) -> StressProfile:
        """Return the hardcoded StressProfile for *mode*."""
        return _PROFILES[mode]

    # ── Core simulation ───────────────────────────────────────────────────────

    def simulate_fill(
        self,
        symbol: str,
        side: str,
        qty: float,
        ref_price: float,
        mode: Optional[MarketMode] = None,
    ) -> FillResult:
        """Simulate a single order fill under the specified market mode.

        All randomness is drawn from self._rng so results are deterministic.

        Parameters
        ----------
        symbol:     Instrument symbol (informational, included in analytics).
        side:       "BUY" or "SELL".
        qty:        Requested order quantity.
        ref_price:  Mid-market reference price.
        mode:       Market mode.  Defaults to the mode set via set_mode().
        """
        if mode is None:
            mode = self._default_mode

        profile = _PROFILES[mode]

        with self._lock:
            result = self._simulate_fill_unlocked(
                symbol, side, qty, ref_price, profile
            )

        # Non-blocking integration hook
        try:
            from runtime.execution_optimizer import get_optimizer  # noqa: PLC0415
            get_optimizer().update_from_analytics({
                "avg_slippage_bps": result.slippage_bps,
                "avg_fill_efficiency": (
                    result.filled_qty / qty if qty > 0 else 1.0
                ),
            })
        except Exception:  # noqa: BLE001
            pass

        return result

    def _simulate_fill_unlocked(
        self,
        symbol: str,
        side: str,
        qty: float,
        ref_price: float,
        profile: StressProfile,
    ) -> FillResult:
        """Internal fill simulation.  Caller must hold self._lock."""
        rng = self._rng

        # ── Spread ────────────────────────────────────────────────────────────
        raw_spread_bps = rng.gauss(profile.spread_bps_base, profile.spread_bps_std)
        spread_bps = max(0.1, raw_spread_bps)  # never negative
        spread_fraction = spread_bps / 10_000.0

        # ── Fill direction: BUY pays ask, SELL gets bid ────────────────────────
        # Ask = ref_price * (1 + half-spread); Bid = ref_price * (1 - half-spread)
        direction = 1.0 if side.upper() == "BUY" else -1.0
        avg_fill_price = ref_price * (1.0 + direction * spread_fraction * 0.5)

        # Round to lot size
        lot = profile.precision_rounding_lots
        if lot > 0:
            avg_fill_price = round(avg_fill_price / lot) * lot

        slippage_bps = abs(avg_fill_price - ref_price) / ref_price * 10_000.0

        # ── Partial fill logic ────────────────────────────────────────────────
        partial_fill = rng.random() < profile.partial_fill_prob
        if partial_fill:
            fill_fraction = rng.uniform(0.3, 0.9)
        else:
            fill_fraction = 1.0

        # ── Fill / no-fill gate ───────────────────────────────────────────────
        if rng.random() > profile.fill_probability:
            filled_qty = 0.0
            partial_fill = False
        else:
            filled_qty = qty * fill_fraction

        # ── Latency: approximate truncated normal from p50 / p99 ──────────────
        # Derive σ from p50 (μ) and p99 (μ + 2.326σ)
        mu    = profile.latency_ms_p50
        sigma = max(1.0, (profile.latency_ms_p99 - mu) / 2.326)
        raw_latency = rng.gauss(mu, sigma)
        latency_ms  = max(0.5, raw_latency)  # never sub-zero

        # ── Queue position ────────────────────────────────────────────────────
        max_queue = max(2, int(1.0 / profile.queue_depth_multiplier * 50))
        queue_position = int(rng.uniform(1, max_queue))

        # ── Cascade / cancel race / ack delay ─────────────────────────────────
        cascade     = rng.random() < profile.liquidation_cascade_prob
        cancel_raced = rng.random() < profile.cancel_race_prob
        ack_delayed  = latency_ms > profile.ack_delay_ms

        return FillResult(
            filled_qty=filled_qty,
            avg_fill_price=avg_fill_price,
            slippage_bps=slippage_bps,
            latency_ms=latency_ms,
            queue_position=queue_position,
            partial_fill=partial_fill,
            liquidation_cascade=cascade,
            cancel_raced=cancel_raced,
            ack_delayed=ack_delayed,
        )

    # ── Batch execution quality report ────────────────────────────────────────

    def run_batch(
        self,
        symbol: str,
        side: str,
        qty: float,
        ref_price: float,
        mode: MarketMode,
        n: int = 1000,
    ) -> ExecutionQualityReport:
        """Run *n* fill simulations and return an aggregate quality report.

        The report is appended to the analytics JSONL in an fcntl-locked atomic write.
        """
        if qty <= 0:
            qty = 1.0

        fills: List[FillResult] = []
        for _ in range(n):
            fills.append(self.simulate_fill(symbol, side, qty, ref_price, mode))

        # ── Aggregate stats ───────────────────────────────────────────────────
        fill_rates   = [f.filled_qty / qty for f in fills]
        slippages    = [f.slippage_bps for f in fills]
        latencies    = sorted(f.latency_ms for f in fills)
        queue_pos    = [f.queue_position for f in fills]
        cascades     = [f.liquidation_cascade for f in fills]
        cancel_races = [f.cancel_raced for f in fills]

        avg_fill_rate     = sum(fill_rates) / n
        avg_slippage_bps  = sum(slippages) / n

        p50_lat = latencies[int(n * 0.50)]
        p95_lat = latencies[int(n * 0.95)]
        p99_lat = latencies[min(int(n * 0.99), n - 1)]

        fill_degradation_score = 1.0 - avg_fill_rate  # 0=good, 1=terrible
        avg_queue_pos          = sum(queue_pos) / n
        queue_priority_score   = max(0.0, min(1.0, 1.0 - avg_queue_pos / 100.0))

        execution_realism_score = 100.0 * (
            avg_fill_rate * 0.4
            + (1.0 - fill_degradation_score) * 0.3
            + queue_priority_score * 0.3
        )
        execution_realism_score = max(0.0, min(100.0, execution_realism_score))

        cascade_frequency     = sum(cascades) / n
        cancel_race_frequency = sum(cancel_races) / n

        report = ExecutionQualityReport(
            mode=mode,
            seed=self._seed,
            n_simulations=n,
            avg_fill_rate=avg_fill_rate,
            avg_slippage_bps=avg_slippage_bps,
            p50_latency_ms=p50_lat,
            p95_latency_ms=p95_lat,
            p99_latency_ms=p99_lat,
            fill_degradation_score=fill_degradation_score,
            queue_priority_score=queue_priority_score,
            execution_realism_score=execution_realism_score,
            cascade_frequency=cascade_frequency,
            cancel_race_frequency=cancel_race_frequency,
        )

        self._persist_report(report, symbol, side, qty, ref_price)
        return report

    # ── Convenience helper ────────────────────────────────────────────────────

    def get_execution_realism_score(
        self,
        symbol: str,
        mode: MarketMode = MarketMode.NORMAL,
    ) -> float:
        """Run 100 simulations and return the execution_realism_score (0-100)."""
        report = self.run_batch(symbol, "BUY", 1.0, 1.0, mode, n=100)
        return report.execution_realism_score

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist_report(
        self,
        report: ExecutionQualityReport,
        symbol: str,
        side: str,
        qty: float,
        ref_price: float,
    ) -> None:
        """Append the report to analytics_path using fcntl-locked atomic JSONL write."""
        try:
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "ref_price": ref_price,
                **{k: (v.value if isinstance(v, MarketMode) else v)
                   for k, v in asdict(report).items()},
            }
            line = json.dumps(record) + "\n"

            path = self._analytics_path
            path.parent.mkdir(parents=True, exist_ok=True)

            # Write to temp file then rename atomically
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), prefix=".ms_analytics_"
            )
            try:
                # Copy existing content if present
                if path.exists():
                    with open(str(path), "rb") as src:
                        fcntl.flock(src.fileno(), fcntl.LOCK_SH)
                        existing = src.read()
                        fcntl.flock(src.fileno(), fcntl.LOCK_UN)
                else:
                    existing = b""

                with os.fdopen(fd, "wb") as dst:
                    fcntl.flock(dst.fileno(), fcntl.LOCK_EX)
                    dst.write(existing)
                    dst.write(line.encode())
                    fcntl.flock(dst.fileno(), fcntl.LOCK_UN)
                fd = -1  # fd is now owned by the context manager

                os.replace(tmp, str(path))
            except Exception:
                if fd >= 0:
                    os.close(fd)
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
                raise
        except Exception:
            logger.debug("microstructure_simulator: failed to persist report", exc_info=True)


# ── Module singleton ──────────────────────────────────────────────────────────

_instance: Optional[MicrostructureSimulator] = None
_instance_lock = threading.Lock()


def get_simulator(
    seed: int = 42,
    analytics_path: str = "data/microstructure_analytics.jsonl",
) -> MicrostructureSimulator:
    """Return the module-level singleton MicrostructureSimulator.

    Double-checked locking ensures exactly one instance is created.
    """
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = MicrostructureSimulator(
                    seed=seed, analytics_path=analytics_path
                )
    return _instance
