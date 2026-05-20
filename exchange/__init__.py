"""Exchange intelligence package.

Exports:
    SmartOrderRouter       — multi-venue routing (venue selection only, no execution)
    ExecutionQualityTracker — tracks fill quality, slippage, latency
    LiquidityMonitor        — detects adverse liquidity conditions
    VenueScoringEngine      — composite venue scoring
    LatencyTracker          — per-venue EMA latency tracking
    RoutingDecision         — routing result dataclass
"""
from __future__ import annotations

from exchange.execution_quality import ExecutionQualityTracker
from exchange.latency_tracker import LatencyTracker
from exchange.liquidity import LiquidityMonitor
from exchange.smart_router import RoutingDecision, SmartOrderRouter
from exchange.venue_scoring import VenueScoringEngine

__all__ = [
    "SmartOrderRouter",
    "ExecutionQualityTracker",
    "LiquidityMonitor",
    "VenueScoringEngine",
    "LatencyTracker",
    "RoutingDecision",
]
