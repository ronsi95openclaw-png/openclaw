"""Risk validation and position sizing for prediction market trades."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .kelly_size import fractional_kelly, kelly_fraction


@dataclass
class RiskCheckResult:
    valid: bool
    errors: list[str]
    position_size: float
    reason: Optional[str]


def validate_trade(
    p_model: float,
    p_market: float,
    bankroll: float,
    odds: float,
    current_exposure: float,
    max_exposure: float = 0.15,
    max_single_position: float = 0.05,
    daily_loss_limit: float = 0.15,
    edge_threshold: float = 0.04,
    max_drawdown: float = 0.08,
    loss_rate: float = 0.0,
) -> RiskCheckResult:
    """Validate a trade against deterministic risk rules."""
    errors: list[str] = []
    edge = p_model - p_market

    if edge < edge_threshold:
        errors.append(f"Edge too low: {edge:.2%} < {edge_threshold:.2%}")

    if current_exposure >= max_exposure * bankroll:
        errors.append(f"Exposure limit reached: {current_exposure:.2f} >= {max_exposure:.2f} bankroll")

    if loss_rate >= daily_loss_limit:
        errors.append(f"Daily loss limit exceeded: {loss_rate:.2%} >= {daily_loss_limit:.2%}")

    if loss_rate >= max_drawdown:
        errors.append(f"Drawdown limit exceeded: {loss_rate:.2%} >= {max_drawdown:.2%}")

    bet_size = fractional_kelly(p_model, odds, fraction=0.25) * bankroll
    if bet_size > max_single_position * bankroll:
        bet_size = max_single_position * bankroll

    return RiskCheckResult(
        valid=len(errors) == 0,
        errors=errors,
        position_size=bet_size,
        reason=None if errors else "Risk checks passed",
    )


def check_var(positions: list[float], confidence: float = 0.95) -> float:
    """Estimate a simplistic VaR for current positions."""
    # This is a placeholder. Replace with a proper statistical VaR implementation.
    total = sum(positions)
    return total * (1 - confidence)


if __name__ == "__main__":
    print("Prediction market risk module loaded.")
