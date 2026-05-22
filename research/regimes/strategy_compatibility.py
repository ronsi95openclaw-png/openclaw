"""Defines which regimes each strategy SUPPORTS and FORBIDS.

The risk engine must check this before allowing a signal to proceed.
"""
from __future__ import annotations

from typing import Dict, List, Optional

STRATEGY_REGIME_COMPATIBILITY: Dict[str, Dict[str, List[str]]] = {
    "EMA_CROSS": {
        "supported": ["TRENDING_BULL", "TRENDING_BEAR", "MOMENTUM_BULL"],
        "forbidden": ["RANGING", "VOL_COMPRESSION", "PANIC", "LIQUIDITY_DROUGHT", "UNKNOWN"],
    },
    "RSI_MEAN_REVERT": {
        "supported": ["RANGING", "MEAN_REVERTING", "VOL_COMPRESSION"],
        "forbidden": ["TRENDING_BULL", "TRENDING_BEAR", "PANIC", "LIQUIDATION_CASCADE"],
    },
    "BREAKOUT": {
        "supported": ["VOL_EXPANSION", "TRENDING_BULL", "TRENDING_BEAR", "NEWS_SPIKE"],
        "forbidden": ["RANGING", "VOL_COMPRESSION", "PANIC", "LIQUIDITY_DROUGHT", "UNKNOWN"],
    },
    "BOLLINGER_BAND": {
        "supported": ["RANGING", "MEAN_REVERTING", "VOL_COMPRESSION", "VOL_EXPANSION"],
        # Mean-reversion logic breaks down in strong trends and crisis regimes
        "forbidden": ["TRENDING_BULL", "TRENDING_BEAR", "PANIC", "LIQUIDATION_CASCADE", "UNKNOWN"],
    },
    "TREND_FOLLOW": {
        "supported": ["TRENDING_BULL", "TRENDING_BEAR", "MOMENTUM_BULL", "MOMENTUM_BEAR"],
        # Historically 0% WR in UNKNOWN regime; also dangerous in crisis/reversal regimes
        "forbidden": ["RANGING", "MEAN_REVERTING", "VOL_COMPRESSION", "PANIC",
                      "LIQUIDATION_CASCADE", "LIQUIDITY_DROUGHT", "UNKNOWN"],
    },
    "FUNDING_ARB": {
        "supported": ["FUNDING_RATE_EXTREME", "RANGING", "MEAN_REVERTING"],
        # Requires liquid markets; thin/illiquid conditions cause adverse slippage
        "forbidden": ["PANIC", "LIQUIDATION_CASCADE", "NEWS_SPIKE", "LIQUIDITY_DROUGHT"],
    },
}


def is_strategy_compatible(strategy: str, regime_label: str) -> bool:
    """Returns True if strategy can trade in this regime."""
    compat = STRATEGY_REGIME_COMPATIBILITY.get(strategy)
    if compat is None:
        return True  # Unknown strategy — allow with warning
    if regime_label in compat["forbidden"]:
        return False
    return True  # Not forbidden, and either explicitly supported or neutral


def get_incompatibility_reason(strategy: str, regime_label: str) -> str:
    """Returns human-readable reason why strategy cannot trade in this regime."""
    compat = STRATEGY_REGIME_COMPATIBILITY.get(strategy, {})
    if regime_label in compat.get("forbidden", []):
        return f"{strategy} is FORBIDDEN in {regime_label} regime"
    return ""
