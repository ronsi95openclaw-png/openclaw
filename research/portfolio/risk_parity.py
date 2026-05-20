"""Risk-parity (equal risk contribution) portfolio weights.

Functions:
  risk_parity_weights        — inverse-volatility ERC weights
  compute_asset_volatilities — annualised return vol for each symbol
  portfolio_volatility       — portfolio vol from weights + vols + correlations
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

from research.types import Candle
from research.regimes.volatility import historical_volatility


# ── public API ────────────────────────────────────────────────────────────────

def risk_parity_weights(
    volatilities: Dict[str, float],
    target_vol: float = 0.15,
    max_weight: float = 0.40,
    min_weight: float = 0.05,
) -> Dict[str, float]:
    """Equal Risk Contribution (ERC) weights.

    Inverse-volatility weighting ensures each asset contributes equally to
    total portfolio risk (under the assumption of zero pairwise correlation).

    With correlations, this is an approximation; for exact ERC use an
    iterative solver.  The inverse-vol approach is robust and widely used.

    Parameters
    ----------
    volatilities:
        {symbol: annualised_volatility}.  Zero-vol assets are skipped.
    target_vol:
        Annualised target portfolio volatility (used for context only; the
        output weights are normalised regardless).
    max_weight:
        Maximum allocation to any single asset.
    min_weight:
        Minimum allocation to any single asset.

    Returns
    -------
    Dict[str, float]
        Normalised weights summing to 1.0.  Assets with vol=0 receive
        ``min_weight`` allocation.
    """
    if not volatilities:
        return {}

    # Inverse-vol weights
    raw: Dict[str, float] = {}
    for symbol, vol in volatilities.items():
        if vol > 0:
            raw[symbol] = 1.0 / vol
        else:
            # Zero-vol asset (constant price) — assign a tiny base weight
            raw[symbol] = 1.0 / 1e-6   # effectively min_weight after clamping

    total = sum(raw.values())
    if total <= 0:
        n = len(volatilities)
        return {s: 1.0 / n for s in volatilities}

    # Normalise to sum = 1
    weights = {s: raw[s] / total for s in raw}

    # Clamp to [min_weight, max_weight]
    for s in weights:
        weights[s] = max(min_weight, min(max_weight, weights[s]))

    # Re-normalise after clamping
    total2 = sum(weights.values())
    for s in weights:
        weights[s] = round(weights[s] / total2, 6)

    return weights


def compute_asset_volatilities(
    candles_dict: Dict[str, List[Candle]],
    window: int = 20,
    bars_per_year: float = 365 * 24 * 4,   # 15-minute bars
) -> Dict[str, float]:
    """Compute annualised return volatility for each symbol.

    Parameters
    ----------
    candles_dict:
        {symbol: List[Candle]}.
    window:
        Rolling window for volatility estimation.
    bars_per_year:
        Annualisation factor.  Default assumes 15-minute bars.

    Returns
    -------
    Dict[str, float]
        {symbol: annualised_vol}.  Returns 0.0 for symbols with insufficient data.
    """
    result: Dict[str, float] = {}
    for symbol, candles in candles_dict.items():
        closes = [c.close for c in candles]
        result[symbol] = historical_volatility(
            closes,
            window=window,
            annualized=True,
            bars_per_year=bars_per_year,
        )
    return result


def portfolio_volatility(
    weights: Dict[str, float],
    volatilities: Dict[str, float],
    correlations: Dict[Tuple[str, str], float],
) -> float:
    """Portfolio volatility from weights, individual vols, and correlation matrix.

    Parameters
    ----------
    weights:
        {symbol: portfolio_weight}.  Should sum to 1.0.
    volatilities:
        {symbol: annualised_vol}.
    correlations:
        {(sym_a, sym_b): pearson_correlation}.  Symmetric: (A,B) and (B,A) are
        the same; diagonal (A,A) assumed 1.0 if not provided.

    Returns
    -------
    float
        Annualised portfolio volatility.  Returns 0.0 when empty.

    Notes
    -----
    Portfolio variance = Σ_i Σ_j w_i w_j σ_i σ_j ρ_ij
    """
    symbols = [s for s in weights if s in volatilities]
    if not symbols:
        return 0.0

    variance = 0.0
    for i, sym_i in enumerate(symbols):
        for j, sym_j in enumerate(symbols):
            wi   = weights.get(sym_i, 0.0)
            wj   = weights.get(sym_j, 0.0)
            si   = volatilities.get(sym_i, 0.0)
            sj   = volatilities.get(sym_j, 0.0)

            if i == j:
                rho = 1.0
            else:
                rho = correlations.get((sym_i, sym_j),
                      correlations.get((sym_j, sym_i), 0.0))

            variance += wi * wj * si * sj * rho

    return math.sqrt(max(0.0, variance))
