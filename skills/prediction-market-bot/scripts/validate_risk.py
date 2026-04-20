"""Deterministic trade risk validation for prediction market positions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str]
    warnings: List[str]
    details: Dict[str, float]


def validate_position_rules(
    p_model: float,
    p_market: float,
    bankroll: float,
    stake: float,
    existing_exposure: float,
    max_position_pct: float = 0.05,
    max_total_exposure_pct: float = 0.15,
    daily_loss_pct: float = 0.15,
    max_drawdown_pct: float = 0.08,
    edge_threshold: float = 0.04,
    loss_pct: float = 0.0,
) -> ValidationResult:
    errors: List[str] = []
    warnings: List[str] = []

    edge = p_model - p_market
    if edge < edge_threshold:
        errors.append(f"Edge below threshold: {edge:.2%} < {edge_threshold:.2%}")

    if stake > bankroll * max_position_pct:
        errors.append(f"Stake exceeds max single position: {stake:.2f} > {max_position_pct:.2%} bankroll")

    if existing_exposure + stake > bankroll * max_total_exposure_pct:
        errors.append(
            f"Total exposure exceeds max: {existing_exposure + stake:.2f} > {max_total_exposure_pct:.2%} bankroll"
        )

    if loss_pct >= daily_loss_pct:
        errors.append(f"Daily loss limit exceeded: {loss_pct:.2%} >= {daily_loss_pct:.2%}")

    if loss_pct >= max_drawdown_pct:
        errors.append(f"Max drawdown exceeded: {loss_pct:.2%} >= {max_drawdown_pct:.2%}")

    if stake < bankroll * 0.001:
        warnings.append("Stake is very small relative to bankroll; confirm transaction minimums.")

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        details={
            "edge": edge,
            "stake": stake,
            "max_position_pct": max_position_pct,
            "total_exposure_pct": (existing_exposure + stake) / bankroll if bankroll else 0.0,
            "daily_loss_pct": loss_pct,
            "max_drawdown_pct": max_drawdown_pct,
        },
    )


def validate_order_slippage(actual_price: float, limit_price: float, max_slippage_pct: float = 0.02) -> ValidationResult:
    slippage = abs(actual_price - limit_price) / limit_price if limit_price else 0.0
    errors = []
    if slippage > max_slippage_pct:
        errors.append(f"Slippage too high: {slippage:.2%} > {max_slippage_pct:.2%}")
    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=[],
        details={"slippage": slippage},
    )


if __name__ == "__main__":
    print("Prediction market risk validation module loaded.")
