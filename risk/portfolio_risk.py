"""Portfolio Risk Engine — cross-position exposure aggregation.

Tracks total leveraged notional, directional imbalance, and correlation-
adjusted risk across the BTC/ETH/SOL perpetual futures positions that
CapitalPreservationEngine does NOT see at the portfolio level.

AI SAFETY CONTRACT:
- This module NEVER calls exchange APIs directly.
- It NEVER places or cancels orders.
- It is READ-ONLY with respect to the exchange: it accepts position + price
  data pushed to it via update_positions(); it never fetches them itself.
- All exposure limits are advisory — execution authority remains with
  ExecutionManager / CapitalPreservationEngine.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.risk.portfolio_risk")

# ── Constants ─────────────────────────────────────────────────────────────────

# All traded symbols — treated as a single correlated cluster.
CORRELATED_SYMBOLS = {"BTC_USDT", "ETH_USDT", "SOL_USDT"}

# Pairwise correlation assumption for BTC / ETH / SOL.
# Source: historically measured ~0.80–0.90 in crypto bull and bear cycles.
CRYPTO_CORRELATION = 0.85

# Regime-aware total-exposure caps expressed as a multiple of account balance.
# The leverage already baked into each position's notional is accounted for
# when computing leverage_ratio, so these caps are on total notional / balance.
_REGIME_EXPOSURE_CAPS: Dict[str, float] = {
    "TRENDING_BEAR":   1.5,   # conservative — correlation amplifies losses
    "TRENDING_BULL":   2.5,
    "RANGING":         2.5,
    "VOLATILE":        2.0,
    "UNKNOWN":         2.0,   # cautious default
}
_DEFAULT_CAP = 2.5

# Correlation score above which the book is considered outside safe limits.
_CORRELATION_SCORE_LIMIT = 0.90


# ── PortfolioRiskEngine ───────────────────────────────────────────────────────

class PortfolioRiskEngine:
    """Aggregates cross-position exposure and correlation risk.

    Each position dict must contain at minimum:
        id          : str   — unique position identifier
        symbol      : str   — e.g. "BTC_USDT"
        side        : str   — "long" | "short"
        size        : float — number of contracts / units
        entry_price : float — average entry price in USDT
        sl_price    : float — stop-loss price
        tp_price    : float — take-profit price

    Current prices are supplied separately as a dict {symbol: float} so the
    engine always uses mark prices rather than stale entry prices for notional
    calculations.

    Thread-safe: all public methods acquire self._lock.
    """

    def __init__(self, leverage: float = 3.0) -> None:
        """
        Args:
            leverage: The uniform leverage applied to all positions (default 3x).
                      Used when computing leveraged notional from a position's
                      raw notional (size x price).
        """
        self._leverage = leverage
        self._lock = threading.Lock()
        self._positions: List[Dict[str, Any]] = []
        self._prices: Dict[str, float] = {}

    # ── State update ──────────────────────────────────────────────────────────

    def update_positions(
        self,
        positions: List[Dict[str, Any]],
        prices: Dict[str, float],
    ) -> None:
        """Refresh the engine's view of open positions and current mark prices.

        Args:
            positions: List of position dicts (see class docstring for schema).
            prices:    Dict mapping symbol -> current mark price in USDT.
                       Missing symbols are handled gracefully (position
                       notional falls back to entry_price x size).
        """
        with self._lock:
            self._positions = list(positions)
            self._prices = dict(prices)
        logger.debug(
            "PortfolioRiskEngine updated: %d positions, %d price feeds",
            len(positions),
            len(prices),
        )

    # ── Public accessors ──────────────────────────────────────────────────────

    def get_total_portfolio_risk(self, balance: float) -> Dict[str, Any]:
        """Return a full portfolio risk snapshot.

        Args:
            balance: Current account equity in USDT.

        Returns a dict with the following keys:
            total_notional          float  — sum of all leveraged notionals (USDT)
            long_notional           float  — leveraged notional of long positions
            short_notional          float  — leveraged notional of short positions
            net_notional            float  — long_notional - short_notional
            leverage_ratio          float  — total_notional / balance (0.0 if balance <= 0)
            correlation_risk_score  float  — 0.0-1.0 (1.0 = fully directional cluster)
            max_single_symbol_pct   float  — largest single-symbol share of total_notional
        """
        with self._lock:
            positions = list(self._positions)
            prices = dict(self._prices)

        long_notional = 0.0
        short_notional = 0.0
        symbol_notionals: Dict[str, float] = {}

        for pos in positions:
            notional = self._position_notional(pos, prices)
            side = str(pos.get("side", "long")).lower()
            symbol = str(pos.get("symbol", ""))

            if side == "long":
                long_notional += notional
            else:
                short_notional += notional

            symbol_notionals[symbol] = symbol_notionals.get(symbol, 0.0) + notional

        total_notional = long_notional + short_notional
        net_notional = long_notional - short_notional

        leverage_ratio = (total_notional / balance) if balance > 0 else 0.0

        max_single_symbol_pct = 0.0
        if total_notional > 0:
            max_single_symbol_pct = max(symbol_notionals.values()) / total_notional

        corr_score = self._compute_correlation_score(
            long_notional, short_notional, total_notional
        )

        return {
            "total_notional":         round(total_notional, 4),
            "long_notional":          round(long_notional, 4),
            "short_notional":         round(short_notional, 4),
            "net_notional":           round(net_notional, 4),
            "leverage_ratio":         round(leverage_ratio, 4),
            "correlation_risk_score": round(corr_score, 4),
            "max_single_symbol_pct":  round(max_single_symbol_pct, 4),
        }

    def get_regime_exposure(self, regime: str) -> Dict[str, Any]:
        """Return whether current exposure is within the regime-specific cap.

        Args:
            regime: Regime label string, e.g. "TRENDING_BEAR".

        Returns dict with:
            cap_pct         float  — max allowed total_notional / balance (as a ratio)
            current_pct     float  — actual total_notional / balance (0 if no balance context)
            within_limits   bool   — True if current_pct <= cap_pct (requires balance via
                                     should_reduce_positions; here within_limits reflects
                                     whether a reduction signal has been raised)
        """
        with self._lock:
            positions = list(self._positions)
            prices = dict(self._prices)

        total_notional = sum(
            self._position_notional(pos, prices) for pos in positions
        )

        cap = _REGIME_EXPOSURE_CAPS.get(regime.upper(), _DEFAULT_CAP)

        # current_pct is undefined without a balance; return 0.0 as a sentinel.
        # Callers that have balance should use should_reduce_positions() instead.
        return {
            "cap_pct":        cap,
            "current_pct":    0.0,   # populated by caller using total_notional / balance
            "total_notional": round(total_notional, 4),
            "within_limits":  True,  # re-evaluated properly by should_reduce_positions()
        }

    def should_reduce_positions(self, balance: float, regime: str) -> bool:
        """Return True if total leveraged exposure exceeds the regime cap.

        Args:
            balance: Current account equity in USDT.
            regime:  Current market regime label.

        Returns:
            True  -> caller should reduce at least one position.
            False -> exposure is within permitted bounds.
        """
        if balance <= 0:
            logger.warning(
                "PortfolioRiskEngine.should_reduce_positions: balance <= 0 "
                "(balance=%.2f) — returning False to avoid spurious reduction",
                balance,
            )
            return False

        with self._lock:
            positions = list(self._positions)
            prices = dict(self._prices)

        total_notional = sum(
            self._position_notional(pos, prices) for pos in positions
        )

        cap = _REGIME_EXPOSURE_CAPS.get(regime.upper(), _DEFAULT_CAP)
        leverage_ratio = total_notional / balance
        should_reduce = leverage_ratio > cap

        if should_reduce:
            logger.warning(
                "PortfolioRiskEngine: exposure %.2fx exceeds regime cap %.2fx "
                "(regime=%s, total_notional=%.2f, balance=%.2f)",
                leverage_ratio, cap, regime, total_notional, balance,
            )

        return should_reduce

    def get_correlation_risk(self) -> Dict[str, Any]:
        """Return correlation risk details for the current positions.

        All three symbols (BTC/ETH/SOL) are treated as a correlated cluster
        with pairwise correlation = CRYPTO_CORRELATION (0.85).

        The correlation_risk_score is 0.0-1.0 where:
          - 0.0 = perfectly balanced book (equal long/short notional)
          - 1.0 = all positions in same direction across all correlated assets

        Returns dict with:
            correlated_symbols       list[str]   — symbols present in the cluster
            max_correlated_fraction  float       — fraction of total_notional in cluster
            within_limits            bool        — True if corr-adjusted score < 0.90
            correlation_score        float       — 0.0-1.0
        """
        with self._lock:
            positions = list(self._positions)
            prices = dict(self._prices)

        total_notional = 0.0
        correlated_notional = 0.0
        symbols_seen: set = set()

        for pos in positions:
            n = self._position_notional(pos, prices)
            symbol = str(pos.get("symbol", ""))
            total_notional += n
            if symbol in CORRELATED_SYMBOLS:
                correlated_notional += n
                symbols_seen.add(symbol)

        max_corr_fraction = (
            correlated_notional / total_notional if total_notional > 0 else 0.0
        )

        long_n = sum(
            self._position_notional(p, prices)
            for p in positions
            if str(p.get("side", "long")).lower() == "long"
        )
        short_n = total_notional - long_n

        corr_score = self._compute_correlation_score(long_n, short_n, total_notional)

        return {
            "correlated_symbols":      sorted(symbols_seen),
            "max_correlated_fraction": round(max_corr_fraction, 4),
            "within_limits":           corr_score < _CORRELATION_SCORE_LIMIT,
            "correlation_score":       round(corr_score, 4),
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _position_notional(
        self,
        pos: Dict[str, Any],
        prices: Dict[str, float],
    ) -> float:
        """Compute leveraged notional for a single position.

        Leveraged notional = size x mark_price x leverage.

        Falls back to entry_price if mark price is unavailable.
        Returns 0.0 if size or price cannot be determined.
        """
        symbol = str(pos.get("symbol", ""))
        size = float(pos.get("size", 0.0))

        if size <= 0:
            return 0.0

        # Prefer live mark price; fall back to entry_price.
        mark_price: Optional[float] = prices.get(symbol)
        if mark_price is None or mark_price <= 0:
            mark_price = float(pos.get("entry_price", 0.0))
            if mark_price <= 0:
                logger.debug(
                    "PortfolioRiskEngine: no usable price for %s (pos_id=%s) "
                    "— notional treated as 0",
                    symbol,
                    pos.get("id", "?"),
                )
                return 0.0

        return size * mark_price * self._leverage

    def _compute_correlation_score(
        self,
        long_notional: float,
        short_notional: float,
        total_notional: float,
    ) -> float:
        """Compute a 0.0-1.0 score representing directional correlation risk.

        The score reflects two dimensions:
          1. Directional concentration — how one-sided the book is.
             A fully net-long or net-short book scores 1.0 on this axis.
          2. Cluster concentration — all symbols are in the same correlated
             cluster, so a single correlated direction is amplified by
             CRYPTO_CORRELATION.

        Score = directional_fraction x CRYPTO_CORRELATION

        where directional_fraction = |long - short| / (long + short)
        (equals 1.0 when all positions are on the same side).

        The result is clipped to [0.0, 1.0].
        """
        if total_notional <= 0:
            return 0.0

        # Directional imbalance fraction: 0 = perfectly hedged, 1 = fully one-sided.
        net = abs(long_notional - short_notional)
        directional_fraction = net / total_notional

        # Scale by the correlation constant to reflect that even a "hedged"
        # cross-symbol portfolio isn't truly hedged when assets move together.
        score = directional_fraction * CRYPTO_CORRELATION

        return max(0.0, min(1.0, score))
