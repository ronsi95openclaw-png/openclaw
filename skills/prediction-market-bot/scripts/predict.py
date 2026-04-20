"""Predict true probabilities and market edge for prediction market opportunities."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, stdev
from typing import Any, Dict, List, Optional


@dataclass
class PredictionResult:
    market_id: str
    title: str
    p_market: float
    p_model: float
    edge: float
    score: float
    confidence: float
    reasons: List[str]
    metadata: Dict[str, Any]


def ensemble_probability(estimates: List[float], weights: Optional[List[float]] = None) -> float:
    """Return a weighted ensemble probability from multiple model estimates."""
    if not estimates:
        return 0.0
    if weights is None or len(weights) != len(estimates):
        weights = [1.0 for _ in estimates]
    weighted = [p * w for p, w in zip(estimates, weights)]
    return sum(weighted) / sum(weights)


def compute_edge(p_model: float, p_market: float) -> float:
    """Compute the trading edge relative to the market probability."""
    return p_model - p_market


def mispricing_score(p_model: float, p_market: float, sdev: float = 0.05) -> float:
    """Return a z-style mispricing score normalized by volatility."""
    if sdev <= 0:
        return 0.0
    return (p_model - p_market) / sdev


def build_prediction_result(
    market_id: str,
    title: str,
    p_market: float,
    model_estimates: List[float],
    model_weights: Optional[List[float]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> PredictionResult:
    p_model = ensemble_probability(model_estimates, model_weights)
    edge = compute_edge(p_model, p_market)
    score = mispricing_score(p_model, p_market, sdev=max(stdev(model_estimates) if len(model_estimates) > 1 else 0.05, 0.05))
    confidence = min(max(abs(edge) * 2, 0.0), 1.0)

    return PredictionResult(
        market_id=market_id,
        title=title,
        p_market=p_market,
        p_model=p_model,
        edge=edge,
        score=score,
        confidence=confidence,
        reasons=[f"Ensemble estimate {p_model:.2%}", f"Market probability {p_market:.2%}", f"Edge {edge:.2%}"],
        metadata=metadata or {},
    )


def requires_trade(result: PredictionResult, minimum_edge: float = 0.04, minimum_confidence: float = 0.55) -> bool:
    """Return True only when the prediction passes edge and confidence thresholds."""
    return result.edge >= minimum_edge and result.confidence >= minimum_confidence


if __name__ == "__main__":
    print("Prediction market probability module loaded.")
