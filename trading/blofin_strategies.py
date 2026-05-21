"""Backward-compatibility shim — all logic moved to trading.strategies."""
from trading.strategies import *  # noqa: F401, F403
from trading.strategies import (  # noqa: F401
    STRATEGIES, _WEIGHTS_FILE, StrategyStats, StrategyWeightEngine,
    StrategySignal, ema_cross_strategy, rsi_mean_revert_strategy,
    breakout_strategy, bollinger_band_strategy, trend_follow_strategy,
    _rsi,
)

# funding_arb_strategy replaced by trend_follow_strategy
funding_arb_strategy = trend_follow_strategy  # noqa: F811
