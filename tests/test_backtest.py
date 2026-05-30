import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading.backtest import BacktestResult, Trade, simulate_trade, summarize, walk_forward
from trading.strategy import RSIMACDStrategy


class TestSimulateTrade:
    def test_buy_profitable_when_price_rises(self):
        assert simulate_trade("BUY", 100.0, 110.0, 10.0) == 1.0  # +10% on $10

    def test_buy_loss_when_price_falls(self):
        assert simulate_trade("BUY", 100.0, 90.0, 10.0) == -1.0

    def test_sell_profitable_when_price_falls(self):
        # short-like: gain on drop
        assert simulate_trade("SELL", 100.0, 90.0, 10.0) == 1.0

    def test_sell_loss_when_price_rises(self):
        assert simulate_trade("SELL", 100.0, 110.0, 10.0) == -1.0

    def test_flat_returns_zero(self):
        assert simulate_trade("BUY", 100.0, 100.0, 50.0) == 0.0

    def test_zero_entry_price_safe(self):
        assert simulate_trade("BUY", 0.0, 100.0, 10.0) == 0.0


class TestSummarize:
    def _result_with(self, pnls):
        r = BacktestResult(coin="X", starting_balance=100.0)
        for p in pnls:
            r.trades.append(Trade(coin="X", direction="BUY", entry_idx=0, exit_idx=1,
                                  entry_price=100.0, exit_price=100.0 + p,
                                  risk_amount=1.0, pnl=p, rsi=25.0))
        return r

    def test_empty(self):
        s = summarize([], 96.0)
        assert s["total_trades"] == 0
        assert s["total_pnl_usd"] == 0.0
        assert s["return_pct"] == 0.0
        assert s["ending_balance_usd"] == 96.0

    def test_aggregate_winners_and_losers(self):
        a = self._result_with([1.0, -0.5, 2.0])
        b = self._result_with([-1.0, 0.5])
        s = summarize([a, b], 100.0)
        assert s["total_trades"] == 5
        assert s["total_wins"] == 3
        assert s["overall_win_rate"] == 60.0
        assert s["total_pnl_usd"] == 2.0
        assert s["ending_balance_usd"] == 102.0
        assert s["return_pct"] == 2.0
        assert s["expectancy_per_trade_usd"] == 0.4


class TestWalkForward:
    def test_no_trades_with_insufficient_data(self):
        # Strategy needs slow+signal+2 = 37 candles minimum for MACD crossover.
        closes = [100.0] * 20
        result = walk_forward("BTC_USDT", closes, starting_balance=96.0)
        assert result.n_trades == 0
        assert result.final_balance == 96.0

    def test_no_high_signal_on_flat_market(self):
        # Flat closes -> RSI hovers near 50 -> no oversold/overbought -> no HIGH signal.
        closes = [100.0] * 200
        result = walk_forward("BTC_USDT", closes, starting_balance=96.0)
        assert result.n_trades == 0

    def test_dataclass_helpers_on_empty(self):
        r = BacktestResult(coin="X", starting_balance=96.0)
        assert r.n_trades == 0
        assert r.wins == 0
        assert r.win_rate == 0.0
        assert r.total_pnl == 0.0
        assert r.expectancy == 0.0

    def test_dataclass_helpers_with_trades(self):
        r = BacktestResult(coin="X", starting_balance=100.0)
        r.trades = [
            Trade("X", "BUY", 1, 7, 100.0, 110.0, 1.0, +1.0, 25.0),
            Trade("X", "BUY", 8, 14, 110.0, 100.0, 1.0, -1.0, 28.0),
            Trade("X", "BUY", 15, 21, 100.0, 105.0, 1.0, +0.5, 27.0),
        ]
        assert r.n_trades == 3
        assert r.wins == 2
        assert math.isclose(r.win_rate, 200 / 3, abs_tol=0.1)
        assert r.total_pnl == 0.5
