"""Defines which regimes each strategy SUPPORTS and FORBIDS.

The risk engine must check this before allowing a signal to proceed.

Regime labels returned by RegimeClassifier:
  TRENDING_BULL, TRENDING_BEAR, RANGING, VOL_EXPANSION, VOL_COMPRESSION,
  MOMENTUM_BULL, MEAN_REVERTING, LIQUIDITY_DROUGHT, PANIC, UNKNOWN,
  FUNDING_RATE_EXTREME, LIQUIDATION_CASCADE, NEWS_SPIKE
"""
from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger("openclaw.regimes.strategy_compat")

# All regime labels that the classifier can produce — used to reject typos
KNOWN_REGIMES = frozenset({
    "TRENDING_BULL", "TRENDING_BEAR", "RANGING", "VOL_EXPANSION", "VOL_COMPRESSION",
    "MOMENTUM_BULL", "MEAN_REVERTING", "LIQUIDITY_DROUGHT", "PANIC", "UNKNOWN",
    "FUNDING_RATE_EXTREME", "LIQUIDATION_CASCADE", "NEWS_SPIKE",
})

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
        # Note: MOMENTUM_BEAR is not returned by current RegimeClassifier; removed from supported.
        "supported": ["TRENDING_BULL", "TRENDING_BEAR", "MOMENTUM_BULL"],
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
    """Returns True if strategy can trade in this regime.

    Fail-safe: unknown regime labels are denied unless strategy explicitly supports them.
    """
    if regime_label not in KNOWN_REGIMES:
        logger.warning(
            "Unknown regime label '%s' for strategy '%s' — blocking as fail-safe",
            regime_label, strategy,
        )
        return False

    compat = STRATEGY_REGIME_COMPATIBILITY.get(strategy)
    if compat is None:
        logger.debug("No compat rules for strategy '%s' — allowing all known regimes", strategy)
        return True
    if regime_label in compat["forbidden"]:
        return False
    return True  # Not forbidden, and either explicitly supported or neutral


def get_incompatibility_reason(strategy: str, regime_label: str) -> str:
    """Returns human-readable reason why strategy cannot trade in this regime."""
    if regime_label not in KNOWN_REGIMES:
        return f"Unknown regime label '{regime_label}' — blocked as fail-safe"
    compat = STRATEGY_REGIME_COMPATIBILITY.get(strategy, {})
    if regime_label in compat.get("forbidden", []):
        return f"{strategy} is FORBIDDEN in {regime_label} regime"
    return ""
