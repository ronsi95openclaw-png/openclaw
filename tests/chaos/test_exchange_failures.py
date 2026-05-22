"""Chaos tests: exchange API failure scenarios (429, 503, timeouts, corrupt data)."""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_response(status_code: int, json_body: dict | None = None, raise_exc=None):
    resp = MagicMock()
    resp.status_code = status_code
    if raise_exc:
        resp.raise_for_status.side_effect = raise_exc
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = json_body or {}
    return resp


# ── fetch_ticker chaos ─────────────────────────────────────────────────────────

class TestFetchTickerChaos:
    def test_429_raises_http_error(self):
        from trading.exchange import fetch_ticker
        exc = requests.HTTPError(response=MagicMock(status_code=429))
        with patch("requests.get", return_value=_mock_response(429, raise_exc=exc)):
            with pytest.raises(requests.HTTPError):
                fetch_ticker("BTC_USDT")

    def test_503_raises_http_error(self):
        from trading.exchange import fetch_ticker
        exc = requests.HTTPError(response=MagicMock(status_code=503))
        with patch("requests.get", return_value=_mock_response(503, raise_exc=exc)):
            with pytest.raises(requests.HTTPError):
                fetch_ticker("BTC_USDT")

    def test_empty_data_raises_value_error(self):
        from trading.exchange import fetch_ticker
        payload = {"result": {"data": []}}
        with patch("requests.get", return_value=_mock_response(200, payload)):
            with pytest.raises(ValueError, match="No ticker data"):
                fetch_ticker("BTC_USDT")

    def test_timeout_propagates(self):
        from trading.exchange import fetch_ticker
        with patch("requests.get", side_effect=requests.Timeout("timed out")):
            with pytest.raises(requests.Timeout):
                fetch_ticker("BTC_USDT")

    def test_malformed_json_raises(self):
        from trading.exchange import fetch_ticker
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.side_effect = json.JSONDecodeError("bad json", "", 0)
        with patch("requests.get", return_value=resp):
            with pytest.raises(json.JSONDecodeError):
                fetch_ticker("BTC_USDT")

    def test_zero_values_returned_intact(self):
        """Zero bid/ask should not be masked by `or` operator (regression guard)."""
        from trading.exchange import fetch_ticker
        payload = {"result": {"data": [{"a": 0, "b": 0, "v": 0, "c": 0}]}}
        with patch("requests.get", return_value=_mock_response(200, payload)):
            result = fetch_ticker("BTC_USDT")
        assert result["last"] == 0.0
        assert result["bid"] == 0.0


# ── fetch_candles chaos ────────────────────────────────────────────────────────

class TestFetchCandlesChaos:
    def _make_candle(self, t=1700000000000, o=100, h=110, l=90, c=105, v=1000):
        return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}

    def test_403_raises_http_error(self):
        from trading.exchange import fetch_candles
        exc = requests.HTTPError(response=MagicMock(status_code=403))
        with patch("requests.get", return_value=_mock_response(403, raise_exc=exc)):
            with pytest.raises(requests.HTTPError):
                fetch_candles("BTC_USDT")

    def test_empty_candles_raises(self):
        from trading.exchange import fetch_candles
        payload = {"code": 0, "result": {"data": []}}
        with patch("requests.get", return_value=_mock_response(200, payload)):
            with pytest.raises(ValueError, match="No candle data"):
                fetch_candles("BTC_USDT")

    def test_api_error_code_raises(self):
        from trading.exchange import fetch_candles
        payload = {"code": 10004, "message": "Instrument not found"}
        with patch("requests.get", return_value=_mock_response(200, payload)):
            with pytest.raises(ValueError, match="candles error"):
                fetch_candles("FAKE_USDT")

    def test_partial_candle_fields_handled(self):
        """Candles with only 'open' key (not 'o') should still parse."""
        from trading.exchange import fetch_candles
        raw = [{"t": 1700000000000, "open": 100, "high": 110,
                "low": 90, "close": 105, "volume": 500}]
        payload = {"code": 0, "result": {"data": raw}}
        with patch("requests.get", return_value=_mock_response(200, payload)):
            candles = fetch_candles("BTC_USDT")
        assert len(candles) == 1
        assert candles[0]["open"] == 100.0

    def test_network_error_propagates(self):
        from trading.exchange import fetch_candles
        with patch("requests.get", side_effect=ConnectionError("network down")):
            with pytest.raises(ConnectionError):
                fetch_candles("BTC_USDT")


# ── get_open_orders chaos ─────────────────────────────────────────────────────

class TestGetOpenOrdersChaos:
    def test_timeout_returns_empty_list(self):
        """get_open_orders should swallow timeouts and return []."""
        from trading.exchange import get_open_orders
        with patch("requests.post", side_effect=requests.Timeout("timeout")):
            with patch("trading.exchange._get_keys", return_value=("k", "s")):
                result = get_open_orders()
        assert result == []

    def test_503_returns_empty_list(self):
        from trading.exchange import get_open_orders
        exc = requests.HTTPError(response=MagicMock(status_code=503))
        with patch("requests.post", return_value=_mock_response(503, raise_exc=exc)):
            with patch("trading.exchange._get_keys", return_value=("k", "s")):
                result = get_open_orders()
        assert result == []

    def test_api_error_returns_empty_list(self):
        from trading.exchange import get_open_orders
        payload = {"code": 40401, "message": "Not authorized"}
        with patch("requests.post", return_value=_mock_response(200, payload)):
            with patch("trading.exchange._get_keys", return_value=("k", "s")):
                result = get_open_orders()
        assert result == []


# ── Reconciliation chaos ───────────────────────────────────────────────────────

class TestReconciliationChaos:
    def test_exchange_timeout_marks_unreachable(self):
        from runtime.reconciliation import ReconciliationEngine, MismatchType
        engine = ReconciliationEngine()
        with patch("trading.exchange.get_positions",
                   side_effect=requests.Timeout("exchange down")):
            with patch("trading.exchange.get_open_orders",
                       side_effect=requests.Timeout("exchange down")):
                with patch("trading.exchange.get_derivatives_balance",
                           return_value={}):
                    report = engine.reconcile([], 1000.0)

        assert not report.exchange_reachable

    def test_demo_mode_skips_exchange(self):
        from runtime.reconciliation import reconcile_on_startup
        # In demo mode, reconcile should never call the exchange
        with patch("trading.exchange.get_positions") as mock_pos:
            report = reconcile_on_startup([], 1000.0, demo_mode=True)
        mock_pos.assert_not_called()
        assert report.passed

    def test_corrupt_local_position_flagged(self):
        from runtime.reconciliation import reconcile_on_startup, MismatchType
        bad_positions = [
            {"id": "x1"},  # missing all required fields
            {"id": "x2", "symbol": "BTC_USDT"},  # missing size/side etc.
        ]
        report = reconcile_on_startup(bad_positions, 1000.0, demo_mode=True)
        types = {m.mismatch_type for m in report.mismatches}
        assert MismatchType.CORRUPT_STATE in types

    def test_demo_mode_passes_with_valid_positions(self):
        from runtime.reconciliation import reconcile_on_startup
        good = [{
            "id": "t001", "symbol": "BTC_USDT", "side": "long",
            "size": 0.001, "entry_price": 50000.0,
            "sl_price": 48000.0, "tp_price": 53000.0, "strategy": "EMA_CROSS",
        }]
        report = reconcile_on_startup(good, 1000.0, demo_mode=True)
        assert report.passed


# ── Portfolio risk chaos ───────────────────────────────────────────────────────

class TestPortfolioRiskChaos:
    def _make_position(self, symbol="BTC_USDT", side="long", size=0.001, entry=50000.0):
        return {
            "id": f"t-{symbol}-{side}",
            "symbol": symbol, "side": side, "size": size,
            "entry_price": entry, "sl_price": entry * 0.95,
            "tp_price": entry * 1.05, "strategy": "EMA_CROSS",
        }

    def test_no_positions_zero_exposure(self):
        from risk.portfolio_risk import PortfolioRiskEngine
        engine = PortfolioRiskEngine()
        engine.update_positions([], {})
        risk = engine.get_total_portfolio_risk(1000.0)
        assert risk["total_notional"] == 0.0
        assert risk["leverage_ratio"] == 0.0

    def test_missing_price_uses_entry_price(self):
        from risk.portfolio_risk import PortfolioRiskEngine
        engine = PortfolioRiskEngine()
        pos = [self._make_position("BTC_USDT", "long", 0.001, 50000.0)]
        engine.update_positions(pos, {})  # no prices dict
        risk = engine.get_total_portfolio_risk(1000.0)
        # Should use entry_price fallback: 0.001 * 50000 * 3 = 150
        assert risk["total_notional"] > 0

    def test_zero_balance_does_not_crash(self):
        from risk.portfolio_risk import PortfolioRiskEngine
        engine = PortfolioRiskEngine()
        pos = [self._make_position()]
        engine.update_positions(pos, {"BTC_USDT": 50000.0})
        # should_reduce_positions with balance=0 should return False (not crash)
        result = engine.should_reduce_positions(0.0, "RANGING")
        assert result is False

    def test_all_same_direction_max_correlation(self):
        from risk.portfolio_risk import PortfolioRiskEngine
        engine = PortfolioRiskEngine()
        positions = [
            self._make_position("BTC_USDT", "long", 0.001, 50000.0),
            self._make_position("ETH_USDT", "long", 0.01,  3000.0),
            self._make_position("SOL_USDT", "long", 1.0,   150.0),
        ]
        prices = {"BTC_USDT": 50000.0, "ETH_USDT": 3000.0, "SOL_USDT": 150.0}
        engine.update_positions(positions, prices)
        risk = engine.get_total_portfolio_risk(1000.0)
        # All longs = max correlation risk
        assert risk["correlation_risk_score"] > 0.5

    def test_opposing_directions_reduce_net_exposure(self):
        from risk.portfolio_risk import PortfolioRiskEngine
        engine = PortfolioRiskEngine()
        positions = [
            self._make_position("BTC_USDT", "long",  0.001, 50000.0),
            self._make_position("ETH_USDT", "short", 0.01,  3000.0),
        ]
        prices = {"BTC_USDT": 50000.0, "ETH_USDT": 3000.0}
        engine.update_positions(positions, prices)
        risk = engine.get_total_portfolio_risk(1000.0)
        # Net notional should be less than total notional
        assert abs(risk["net_notional"]) < risk["total_notional"]

    def test_trending_bear_regime_lower_cap(self):
        from risk.portfolio_risk import PortfolioRiskEngine
        engine = PortfolioRiskEngine()
        bear = engine.get_regime_exposure("TRENDING_BEAR")
        bull = engine.get_regime_exposure("TRENDING_BULL")
        assert bear["cap_pct"] < bull["cap_pct"]
