"""Backward-compatibility shim — all logic moved to trading.sim_engine."""
from trading.sim_engine import *  # noqa: F401, F403
from trading.sim_engine import (  # noqa: F401
    BloFinBot, BotState, SYMBOLS, LEVERAGE, MAX_POSITIONS, CONF_THRESHOLD,
    _STATE_FILE, _LOG_FILE, _JOURNAL_FILE, _OUTCOMES_FILE,
)
