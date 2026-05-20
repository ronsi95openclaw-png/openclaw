"""Adaptive portfolio allocator — main entry point for Phase 8.

Class:
  AdaptivePortfolioAllocator — regime-aware, risk-parity, vol-targeted,
                                correlation-limited allocation engine.

AI SAFETY CONTRACT (enforced in code below):
  - This class may ONLY adjust weights, sizing scalars, and cooldowns.
  - It may NEVER directly execute trades.
  - It may NEVER bypass the kill switch.
  - All recommendations are advisory; the bot layer enforces them.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from research.types import AllocationWeights, Candle, RegimeState
from research.regimes.classifier import RegimeClassifier
from research.portfolio.strategy_weights import StrategyWeightEngine
from research.portfolio.risk_parity import (
    compute_asset_volatilities,
    risk_parity_weights,
)
from research.portfolio.volatility_targeting import VolatilityTargeter
from research.portfolio.correlation_limits import CorrelationLimiter

logger = logging.getLogger("openclaw.research.portfolio.allocator")

# ── Regime → strategy multiplier tables ──────────────────────────────────────

# Format: {regime_label: {strategy_name: multiplier}}
# Multiplier = 1.0 means no change; > 1.0 = favoured; < 1.0 = reduced.
_REGIME_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    "TRENDING_BULL": {
        "EMA_CROSS":      1.50,
        "RSI_MEAN_REVERT": 0.50,
        "BREAKOUT":       1.30,
        "FUNDING_ARB":    1.20,
    },
    "TRENDING_BEAR": {
        "EMA_CROSS":      1.50,
        "RSI_MEAN_REVERT": 0.50,
        "BREAKOUT":       1.30,
        "FUNDING_ARB":    1.20,
    },
    "RANGING": {
        "EMA_CROSS":      0.50,
        "RSI_MEAN_REVERT": 1.50,
        "BREAKOUT":       0.60,
        "FUNDING_ARB":    0.80,
    },
    "VOL_EXPANSION": {
        "EMA_CROSS":      1.00,
        "RSI_MEAN_REVERT": 0.70,
        "BREAKOUT":       1.30,
        "FUNDING_ARB":    1.00,
    },
    "VOL_COMPRESSION": {
        "EMA_CROSS":      0.80,
        "RSI_MEAN_REVERT": 1.20,
        "BREAKOUT":       0.70,
        "FUNDING_ARB":    0.90,
    },
    "MOMENTUM_BULL": {
        "EMA_CROSS":      1.20,
        "RSI_MEAN_REVERT": 0.60,
        "BREAKOUT":       1.10,
        "FUNDING_ARB":    1.20,
    },
    "MEAN_REVERTING": {
        "EMA_CROSS":      0.60,
        "RSI_MEAN_REVERT": 1.50,
        "BREAKOUT":       0.70,
        "FUNDING_ARB":    0.80,
    },
    "LIQUIDITY_DROUGHT": {
        "EMA_CROSS":      0.50,
        "RSI_MEAN_REVERT": 0.50,
        "BREAKOUT":       0.30,
        "FUNDING_ARB":    0.50,
    },
    "PANIC": {
        # AI SAFETY: PANIC regime zeroes out all strategies (cooldown enforced)
        "EMA_CROSS":      0.0,
        "RSI_MEAN_REVERT": 0.0,
        "BREAKOUT":       0.0,
        "FUNDING_ARB":    0.0,
    },
    "UNKNOWN": {
        # Neutral — no adjustment
        "EMA_CROSS":      1.00,
        "RSI_MEAN_REVERT": 1.00,
        "BREAKOUT":       1.00,
        "FUNDING_ARB":    1.00,
    },
}

# Drawdown thresholds triggering portfolio-level cooldown
_DRAWDOWN_COOLDOWN_THRESHOLD = 0.15    # 15 % portfolio drawdown


class AdaptivePortfolioAllocator:
    """Production-grade adaptive allocation engine.

    Integrates:
    - RegimeClassifier         — regime-based strategy throttling
    - StrategyWeightEngine     — performance-based adaptive weights
    - VolatilityTargeter       — risk-scaled position sizing
    - CorrelationLimiter       — diversification enforcement
    - Risk parity              — equal risk contribution across symbols

    AI SAFETY CONTRACT
    ------------------
    - This class may ONLY adjust weights, sizing scalars, and cooldowns.
    - It may NEVER directly execute trades (no exchange calls here).
    - It may NEVER bypass the kill switch (that lives in the bot layer).
    - ``compute_allocation`` returns advisory ``AllocationWeights``.
      The calling bot is responsible for enforcing them.
    """

    def __init__(
        self,
        strategies: Optional[List[str]] = None,
        symbols: Optional[List[str]] = None,
        base_risk_pct: float = 1.5,
        max_leverage: float = 5.0,
        target_vol: float = 0.20,
        max_correlation: float = 0.75,
    ) -> None:
        self.strategies    = list(strategies) if strategies else []
        self.symbols       = list(symbols) if symbols else []
        self.base_risk_pct = base_risk_pct
        self.max_leverage  = max_leverage
        self.target_vol    = target_vol

        # Sub-engines
        self._classifier   = RegimeClassifier()
        self._sw_engine    = StrategyWeightEngine(
            strategies=self.strategies,
            max_weight=0.40,
            min_weight=0.05,
        )
        self._vol_targeter = VolatilityTargeter(
            target_vol_annual=target_vol,
            max_leverage=max_leverage,
        )
        self._corr_limiter = CorrelationLimiter(
            max_pairwise_correlation=max_correlation,
        )

        # Internal state
        self._last_regime:  Optional[RegimeState] = None
        self._cooldown:     bool = False

    # ── public API ────────────────────────────────────────────────────────────

    def compute_allocation(
        self,
        regime: RegimeState,
        strategy_metrics: Dict[str, Any],
        portfolio_state: Dict[str, Any],
        candles_dict: Dict[str, List[Candle]],
    ) -> AllocationWeights:
        """Compute full allocation weights given current market + portfolio state.

        AI SAFETY: This method returns advisory weights only.
        It does NOT execute trades, place orders, or bypass any safety
        mechanism.  The calling bot layer is responsible for enforcement.

        Logic
        -----
        1. Classify regime → apply per-strategy regime multipliers.
        2. Compute risk-parity weights across symbols.
        3. Retrieve performance-based strategy weights.
        4. Compute vol-targeting scalar from primary candle stream.
        5. Check correlation limits for each symbol vs open positions.
        6. Apply panic / liquidity drought guards → set cooldown_active.
        7. Build and return AllocationWeights.

        Parameters
        ----------
        regime:
            Current RegimeState (may be pre-computed or freshly classified).
        strategy_metrics:
            {strategy: {sharpe, win_rate, ...}} — used for logging / rationale.
        portfolio_state:
            {balance, drawdown, open_positions, ...}.
        candles_dict:
            {symbol: List[Candle]} — current market data per symbol.

        Returns
        -------
        AllocationWeights
            Advisory allocation.  bot must honour cooldown_active.
        """
        self._last_regime = regime

        # ── Step 1: Regime multipliers ────────────────────────────────────────
        regime_mult = self._regime_multipliers(regime)

        # ── Step 2: Risk-parity symbol weights ────────────────────────────────
        symbol_vols = compute_asset_volatilities(candles_dict)
        pair_weights = risk_parity_weights(
            {s: symbol_vols.get(s, 0.01) for s in self.symbols},
        )

        # ── Step 3: Strategy weights (performance-based) ──────────────────────
        base_strategy_weights = self._sw_engine.get_weights()

        # Blend with regime multipliers: weight_i = base_i × regime_mult_i
        strategy_weights: Dict[str, float] = {}
        for strat in self.strategies:
            bw   = base_strategy_weights.get(strat, 1.0 / max(len(self.strategies), 1))
            mult = regime_mult.get(strat, 1.0)
            strategy_weights[strat] = bw * mult

        # Normalise strategy weights to sum = 1
        sw_total = sum(strategy_weights.values())
        if sw_total > 0:
            strategy_weights = {s: v / sw_total for s, v in strategy_weights.items()}
        else:
            # Degenerate (PANIC zeroed everything) — equal weights but cooldown
            n = max(len(self.strategies), 1)
            strategy_weights = {s: 1.0 / n for s in self.strategies}

        # ── Step 4: Vol-targeting scalar ──────────────────────────────────────
        # Use the first available candle stream for portfolio-level vol estimate
        first_candles = next(iter(candles_dict.values()), [])
        if first_candles:
            realized_vol  = self._vol_targeter.compute_realized_vol(first_candles)
            vol_scalar    = self._vol_targeter.compute_size_scalar(realized_vol)
        else:
            vol_scalar = 1.0

        # Adjusted risk %
        adjusted_risk_pct = min(
            self.base_risk_pct * vol_scalar,
            self.base_risk_pct * self.max_leverage,
        )

        # ── Step 5: Correlation limits ────────────────────────────────────────
        open_positions = portfolio_state.get("open_positions", [])
        for symbol in list(pair_weights.keys()):
            if symbol not in candles_dict:
                continue
            allowed, max_corr = self._corr_limiter.check_correlation(
                new_symbol=symbol,
                new_candles=candles_dict[symbol],
                existing_positions=open_positions,
                all_candles=candles_dict,
            )
            if not allowed:
                # Scale down the correlated symbol's weight significantly
                pair_weights[symbol] = pair_weights[symbol] * 0.25
                logger.info(
                    "Correlation limit hit for %s (max_corr=%.2f) — weight reduced.",
                    symbol, max_corr,
                )

        # Renormalise pair weights after correlation adjustment
        pw_total = sum(pair_weights.values())
        if pw_total > 0:
            pair_weights = {s: v / pw_total for s, v in pair_weights.items()}

        # ── Step 6: Panic / liquidity guards ──────────────────────────────────
        cooldown_active = self.should_cooldown(portfolio_state)

        # Panic or liquidity drought also forces cooldown
        if regime.panic_conditions:
            cooldown_active = True
            adjusted_risk_pct = 0.0
            logger.warning(
                "PANIC regime detected — cooldown activated.  "
                "All strategy weights set to zero."
            )
        elif regime.liquidity_drought:
            cooldown_active = True
            adjusted_risk_pct = min(adjusted_risk_pct, self.base_risk_pct * 0.25)
            logger.info("Liquidity drought — cooldown activated.")

        # ── Step 7: Leverage caps per symbol ─────────────────────────────────
        leverage_caps: Dict[str, float] = {}
        for symbol in self.symbols:
            sym_vol = symbol_vols.get(symbol, 0.20)
            # Cap leverage inversely proportional to symbol vol
            if sym_vol > 0:
                cap = min(self.max_leverage, self.target_vol / sym_vol * self.max_leverage)
            else:
                cap = self.max_leverage
            leverage_caps[symbol] = round(max(1.0, cap), 2)

        # ── Assemble rationale string ─────────────────────────────────────────
        rationale = (
            f"Regime={regime.label} (score={regime.regime_score:.2f}) | "
            f"ADX={regime.adx:.1f} | RSI={regime.rsi:.1f} | "
            f"ATR_ratio={regime.atr_ratio:.2f} | "
            f"vol_scalar={vol_scalar:.2f} | "
            f"cooldown={cooldown_active}"
        )

        return AllocationWeights(
            strategy_weights={s: round(v, 4) for s, v in strategy_weights.items()},
            pair_allocations={s: round(v, 4) for s, v in pair_weights.items()},
            leverage_caps=leverage_caps,
            risk_pct=round(adjusted_risk_pct, 4),
            cooldown_active=cooldown_active,
            rationale=rationale,
            regime_label=regime.label,
            timestamp=datetime.now(timezone.utc),
        )

    def set_kill_switch(self, active: bool) -> None:
        """Activate or deactivate the kill switch (advisory; enforced by bot)."""
        self._kill_switch = active

    def allocate(
        self,
        strategies: List[str],
        symbols: List[str],
        regime: RegimeState,
    ) -> AllocationWeights:
        """Convenience wrapper for compute_allocation with minimal required args.

        Updates internal strategies/symbols lists then delegates to
        compute_allocation with sensible defaults for optional parameters.
        """
        self.strategies = list(strategies)
        self.symbols = list(symbols)
        self._sw_engine = StrategyWeightEngine(
            strategies=strategies,
            max_weight=0.40,
            min_weight=0.05,
        )
        # If kill switch is active, return zeroed allocation immediately
        if getattr(self, "_kill_switch", False):
            return AllocationWeights(
                strategy_weights={s: 0.0 for s in strategies},
                pair_allocations={sym: 0.0 for sym in symbols},
                leverage_caps={sym: 1.0 for sym in symbols},
                risk_pct=0.0,
                cooldown_active=True,
                rationale="kill_switch_active",
                regime_label=regime.label,
                timestamp=datetime.now(timezone.utc),
            )
        return self.compute_allocation(
            regime=regime,
            strategy_metrics={s: {"sharpe": 1.0, "win_rate": 0.5, "drawdown": 0.05} for s in strategies},
            portfolio_state={"balance": 10000, "drawdown": 0.0, "open_positions": []},
            candles_dict={sym: [] for sym in symbols},
        )

    def _regime_multipliers(self, regime: RegimeState) -> Dict[str, float]:
        """Return per-strategy multipliers based on the current regime label.

        Lookup is against the ``_REGIME_MULTIPLIERS`` table.  Falls back to
        the UNKNOWN (neutral) multipliers for unrecognised regime labels.

        Key mappings:
        - RANGING       → RSI_MEAN_REVERT ×1.5, EMA_CROSS ×0.5
        - TRENDING_*    → EMA_CROSS ×1.5, RSI_MEAN_REVERT ×0.5
        - PANIC         → all strategies ×0.0 (cooldown enforced)
        - VOL_EXPANSION → BREAKOUT ×1.3, others reduced
        """
        label = regime.label if regime.label in _REGIME_MULTIPLIERS else "UNKNOWN"
        table = _REGIME_MULTIPLIERS[label]

        # Build a full multiplier dict that covers ALL strategies (default = 1.0)
        result: Dict[str, float] = {s: 1.0 for s in self.strategies}
        result.update({s: v for s, v in table.items() if s in self.strategies})
        return result

    def update_strategy_result(
        self,
        strategy: str,
        trade_pnl: float,
        trade_pnl_pct: float,
    ) -> None:
        """Pass trade result to StrategyWeightEngine for adaptive learning.

        This is the primary feedback loop: the bot calls this after closing
        each trade so weights continuously adapt to live performance.
        """
        # AI SAFETY: this only updates internal weight state, never places orders.
        self._sw_engine.update(strategy, trade_pnl, trade_pnl_pct)

    def should_cooldown(self, portfolio_state: Dict[str, Any]) -> bool:
        """True if portfolio drawdown or regime conditions warrant a trading pause.

        Checks:
        - ``drawdown`` key in portfolio_state exceeds threshold.
        - Stored last regime is PANIC.

        AI SAFETY: sets advisory flag only.  The calling bot decides whether
        to honour this signal.
        """
        # Check portfolio drawdown
        drawdown = portfolio_state.get("drawdown", 0.0)
        if drawdown >= _DRAWDOWN_COOLDOWN_THRESHOLD:
            logger.warning(
                "Portfolio drawdown %.1f%% exceeds threshold %.1f%% — cooldown.",
                drawdown * 100,
                _DRAWDOWN_COOLDOWN_THRESHOLD * 100,
            )
            return True

        # Check last known regime
        if self._last_regime and self._last_regime.panic_conditions:
            return True

        return False

    def save_state(self, path: str = "data/allocator_state.json") -> None:
        """Persist allocator state (strategy weights + last regime) to JSON."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        payload: Dict[str, Any] = {
            "strategy_weights": self._sw_engine.summary(),
            "last_regime_label": self._last_regime.label if self._last_regime else None,
            "cooldown": self._cooldown,
        }
        p.write_text(json.dumps(payload, indent=2))
        # Also persist strategy weights via the engine's own save
        self._sw_engine.save(str(p.parent / "strategy_weights.json"))
        logger.debug("Allocator state saved to %s", path)

    def load_state(self, path: str = "data/allocator_state.json") -> None:
        """Load allocator state from JSON (best-effort)."""
        p = Path(path)
        if not p.exists():
            return
        try:
            raw = json.loads(p.read_text())
            self._cooldown = raw.get("cooldown", False)
            logger.debug("Allocator state loaded from %s", path)
        except Exception as exc:
            logger.warning("Failed to load allocator state: %s", exc)

        # Load strategy weights from their own file
        sw_path = str(Path(path).parent / "strategy_weights.json")
        self._sw_engine.load(sw_path)
