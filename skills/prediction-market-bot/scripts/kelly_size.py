"""Kelly Criterion helpers for position sizing."""
from __future__ import annotations


def kelly_fraction(p: float, b: float) -> float:
    """Compute the full Kelly fraction for a given probability and net odds."""
    q = 1.0 - p
    if b <= 0 or p <= 0 or p >= 1:
        return 0.0
    return max(0.0, (p * (b + 1) - 1) / b)


def fractional_kelly(p: float, b: float, fraction: float = 0.25) -> float:
    """Return a fractional Kelly position size factor."""
    return max(0.0, kelly_fraction(p, b) * fraction)


def stake_from_bankroll(p: float, b: float, bankroll: float, fraction: float = 0.25) -> float:
    """Return a stake amount based on fractional Kelly sizing."""
    return bankroll * fractional_kelly(p, b, fraction=fraction)


if __name__ == "__main__":
    print("Kelly sizing module loaded.")
