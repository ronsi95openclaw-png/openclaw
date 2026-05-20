"""Market regime detection and classification.

Modules:
  volatility      — ATR, Bollinger width, vol regime flags
  trend           — ADX, EMA slope, trend direction
  momentum        — RSI, ROC, momentum score
  ranging         — range detection, range bounds, compression
  market_structure — liquidity drought, panic conditions, HTF trend
  classifier      — RegimeClassifier combining all signals → RegimeState
"""
from __future__ import annotations

from research.regimes.classifier import RegimeClassifier

__all__ = ["RegimeClassifier"]
