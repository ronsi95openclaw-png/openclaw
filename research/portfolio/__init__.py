"""Adaptive portfolio allocation engine.

Modules:
  strategy_weights     — multi-factor strategy weight manager
  risk_parity          — equal risk contribution (ERC) allocation
  volatility_targeting — dynamic position sizing for target vol
  correlation_limits   — diversification enforcement via correlation
  allocator            — AdaptivePortfolioAllocator (main entry point)
"""
from __future__ import annotations

from research.portfolio.allocator import AdaptivePortfolioAllocator

__all__ = ["AdaptivePortfolioAllocator"]
