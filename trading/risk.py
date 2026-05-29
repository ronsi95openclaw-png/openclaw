"""
Risk management — circuit breaker / drawdown halt.

If the portfolio falls more than MAX_DRAWDOWN_PCT below the configured starting
balance, trading is halted until a human reviews. The decision logic is pure and
unit-tested; the executor calls it before placing any orders.

Drawdown is measured from a fixed starting balance (STARTING_BALANCE_USD), not a
rolling high-water mark — intentionally simple and stateless.
"""
from __future__ import annotations

import os
from typing import Optional

_DEFAULT_STARTING = 96.0
_DEFAULT_MAX_DRAWDOWN = 0.20


def _resolve_starting(explicit: Optional[float]) -> float:
    if explicit is not None:
        return explicit
    try:
        return float(os.getenv("STARTING_BALANCE_USD", str(_DEFAULT_STARTING)))
    except ValueError:
        return _DEFAULT_STARTING


def _resolve_max_drawdown(explicit: Optional[float]) -> float:
    if explicit is not None:
        return explicit
    try:
        return float(os.getenv("MAX_DRAWDOWN_PCT", str(_DEFAULT_MAX_DRAWDOWN)))
    except ValueError:
        return _DEFAULT_MAX_DRAWDOWN


def drawdown_pct(current_balance: float, starting_balance: float) -> float:
    """Fractional loss from starting balance; negative when in profit."""
    if starting_balance <= 0:
        return 0.0
    return (starting_balance - current_balance) / starting_balance


def is_circuit_tripped(
    current_balance: float,
    starting_balance: Optional[float] = None,
    max_drawdown_pct: Optional[float] = None,
) -> bool:
    """True if drawdown has reached the halt threshold (trading should stop)."""
    start = _resolve_starting(starting_balance)
    if start <= 0:
        return False
    return drawdown_pct(current_balance, start) >= _resolve_max_drawdown(max_drawdown_pct)


def circuit_breaker_message(
    current_balance: float,
    starting_balance: Optional[float] = None,
    max_drawdown_pct: Optional[float] = None,
) -> str:
    start = _resolve_starting(starting_balance)
    maxdd = _resolve_max_drawdown(max_drawdown_pct)
    dd = drawdown_pct(current_balance, start)
    return (
        f"🛑 CIRCUIT BREAKER TRIPPED\n"
        f"Starting: ${start:.2f}\n"
        f"Current:  ${current_balance:.2f}\n"
        f"Drawdown: {dd * 100:.1f}% (limit {maxdd * 100:.0f}%)\n"
        f"Trading halted — manual review required."
    )
