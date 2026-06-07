import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading.risk import circuit_breaker_message, drawdown_pct, is_circuit_tripped


class TestDrawdownPct:
    def test_no_loss_is_zero(self):
        assert drawdown_pct(96, 96) == 0.0

    def test_half_loss(self):
        assert drawdown_pct(48, 96) == 0.5

    def test_profit_is_negative(self):
        assert drawdown_pct(120, 96) < 0

    def test_zero_starting_balance_is_safe(self):
        assert drawdown_pct(50, 0) == 0.0


class TestIsCircuitTripped:
    def test_no_loss_not_tripped(self):
        assert is_circuit_tripped(96, starting_balance=96, max_drawdown_pct=0.20) is False

    def test_small_loss_not_tripped(self):
        # 16.7% drawdown < 20% limit
        assert is_circuit_tripped(80, starting_balance=96, max_drawdown_pct=0.20) is False

    def test_large_loss_trips(self):
        # 20.8% drawdown >= 20% limit
        assert is_circuit_tripped(76, starting_balance=96, max_drawdown_pct=0.20) is True

    def test_severe_loss_trips(self):
        assert is_circuit_tripped(50, starting_balance=96, max_drawdown_pct=0.20) is True

    def test_profit_not_tripped(self):
        assert is_circuit_tripped(120, starting_balance=96, max_drawdown_pct=0.20) is False

    def test_zero_starting_balance_not_tripped(self):
        assert is_circuit_tripped(0, starting_balance=0, max_drawdown_pct=0.20) is False

    def test_reads_env_defaults(self):
        old = dict(os.environ)
        try:
            os.environ["STARTING_BALANCE_USD"] = "100"
            os.environ["MAX_DRAWDOWN_PCT"] = "0.10"
            assert is_circuit_tripped(85) is True   # 15% > 10%
            assert is_circuit_tripped(95) is False  # 5% < 10%
        finally:
            os.environ.clear()
            os.environ.update(old)


class TestCircuitBreakerMessage:
    def test_mentions_circuit_breaker(self):
        msg = circuit_breaker_message(50, starting_balance=96, max_drawdown_pct=0.20)
        assert "CIRCUIT BREAKER" in msg.upper()

    def test_includes_balances(self):
        msg = circuit_breaker_message(50, starting_balance=96, max_drawdown_pct=0.20)
        assert "96" in msg and "50" in msg


class TestExecuteSignalsCircuitBreaker:
    def test_halts_when_drawdown_exceeds_limit(self):
        from trading.executor import execute_signals

        # default starting balance $96; $10 portfolio ~= 90% drawdown -> halt
        results = execute_signals([], portfolio_usd=10.0)
        assert results == [{"status": "halted", "reason": "circuit_breaker", "portfolio_usd": 10.0}]

    def test_does_not_halt_when_healthy(self):
        from trading.executor import execute_signals

        # portfolio above starting balance -> not tripped; no signals -> empty
        results = execute_signals([], portfolio_usd=200.0)
        assert results == []
