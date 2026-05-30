"""Strategy plugins for ClawBot.

Each module here exports a strategy class compatible with
``trading.backtest.walk_forward``: it must implement
``evaluate(coin, closes) -> Signal`` and expose a ``warmup`` int.
"""
