"""Tests for trading.executor._place_order transient-network retry.

The retry policy:
  - On requests.ConnectionError or requests.ConnectTimeout (the request
    never reached the exchange), retry ONCE.
  - On requests.ReadTimeout / other ambiguous timeouts (the request may have
    reached the exchange and a response was just never read), do NOT retry —
    the order may already be accepted and a retry would double-fill.
  - On HTTPError (raise_for_status), do NOT retry — same reasoning.
  - After the second ConnectionError/ConnectTimeout failure, re-raise the
    exception so the caller can log via _log_trade.

Also covers the MARKET order sizing: BUY sends `notional` (quote currency),
SELL sends `quantity` (base currency, derived from notional_usd / price) —
Crypto.com v2 rejects a SELL sent as `notional`.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import requests

import trading.executor as executor


class _FakeResponse:
    """Stand-in for requests.Response."""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"code": 0, "result": {"order_id": "fake-123"}}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _patch_signing(monkeypatch):
    """Stub out key loading + signing so tests never touch real env / crypto."""
    def fake_get_keys():
        return ("key", "secret")

    def fake_sign(_method, params, _key, _secret):
        return {"signed": True, "params": params}

    # The imports inside _place_order are lazy, so patch the module they live in.
    import trading.exchange as exchange
    monkeypatch.setattr(exchange, "_get_keys", fake_get_keys, raising=True)
    monkeypatch.setattr(exchange, "_sign", fake_sign, raising=True)
    # Make time.sleep a no-op so tests run fast.
    monkeypatch.setattr(executor.time, "sleep", lambda _s: None)


def test_place_order_retries_on_connection_error_then_succeeds(monkeypatch):
    """Transient ConnectionError on first attempt -> retry -> success."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("transient blip")
        return _FakeResponse(200)

    monkeypatch.setattr(executor.requests, "post", fake_post)

    result = executor._place_order("BTC_USDT", "BUY", 25.0, price=50000.0)
    assert calls["n"] == 2, "should have retried exactly once"
    assert result == {"order_id": "fake-123"}


def test_place_order_retries_on_connect_timeout_then_succeeds(monkeypatch):
    """ConnectTimeout (never reached the server) on first attempt -> retry -> success."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectTimeout("slow to connect")
        return _FakeResponse(200)

    monkeypatch.setattr(executor.requests, "post", fake_post)

    result = executor._place_order("ETH_USDT", "BUY", 50.0, price=3000.0)
    assert calls["n"] == 2
    assert result == {"order_id": "fake-123"}


def test_place_order_does_not_retry_on_read_timeout(monkeypatch):
    """ReadTimeout is ambiguous (order may already be accepted) -> do NOT retry."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        raise requests.ReadTimeout("no response in time")

    monkeypatch.setattr(executor.requests, "post", fake_post)

    with pytest.raises(requests.ReadTimeout):
        executor._place_order("BTC_USDT", "BUY", 25.0, price=50000.0)

    assert calls["n"] == 1, "ReadTimeout must not trigger a retry (possible double-fill)"


def test_place_order_reraises_after_second_connection_error(monkeypatch):
    """Two consecutive ConnectionErrors -> re-raise. No third attempt."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        raise requests.ConnectionError(f"blip {calls['n']}")

    monkeypatch.setattr(executor.requests, "post", fake_post)

    with pytest.raises(requests.ConnectionError):
        executor._place_order("BTC_USDT", "BUY", 25.0, price=50000.0)

    assert calls["n"] == 2, "must retry exactly once, then raise"


def test_place_order_reraises_after_second_connect_timeout(monkeypatch):
    """Two consecutive ConnectTimeouts -> re-raise."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        raise requests.ConnectTimeout(f"slow {calls['n']}")

    monkeypatch.setattr(executor.requests, "post", fake_post)

    with pytest.raises(requests.ConnectTimeout):
        executor._place_order("BTC_USDT", "BUY", 25.0, price=50000.0)

    assert calls["n"] == 2


def test_place_order_does_not_retry_on_http_error(monkeypatch):
    """Server responded (e.g. 500) -> raise_for_status fires -> do NOT retry.

    Order may have reached the server; retrying could double-fill.
    """
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(500)

    monkeypatch.setattr(executor.requests, "post", fake_post)

    with pytest.raises(requests.HTTPError):
        executor._place_order("BTC_USDT", "BUY", 25.0, price=50000.0)

    assert calls["n"] == 1, "HTTPError after the wire-level success must NOT trigger retry"


def test_place_order_first_attempt_success_no_retry(monkeypatch):
    """Happy path: post returns 200 immediately, only one attempt."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(executor.requests, "post", fake_post)

    result = executor._place_order("BTC_USDT", "BUY", 25.0, price=50000.0)
    assert calls["n"] == 1
    assert result == {"order_id": "fake-123"}


def test_place_order_rejects_on_nonzero_code(monkeypatch):
    """API-level rejection (code != 0) -> ValueError, no retry."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(200, payload={"code": 309, "message": "insufficient balance"})

    monkeypatch.setattr(executor.requests, "post", fake_post)

    with pytest.raises(ValueError, match="insufficient balance"):
        executor._place_order("BTC_USDT", "BUY", 25.0, price=50000.0)

    assert calls["n"] == 1


def test_place_order_buy_sends_notional(monkeypatch):
    """MARKET BUY must send `notional` (quote currency), matching Crypto.com v2."""
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent.update(json["params"])
        return _FakeResponse(200)

    monkeypatch.setattr(executor.requests, "post", fake_post)

    executor._place_order("BTC_USDT", "BUY", 100.0, price=50000.0)
    assert sent["notional"] == "100.0"
    assert "quantity" not in sent


def test_place_order_sell_sends_quantity(monkeypatch):
    """MARKET SELL must send `quantity` (base currency) — `notional` is rejected by Crypto.com v2 for SELL."""
    sent = {}

    def fake_post(url, json=None, timeout=None):
        sent.update(json["params"])
        return _FakeResponse(200)

    monkeypatch.setattr(executor.requests, "post", fake_post)

    executor._place_order("BTC_USDT", "SELL", 100.0, price=50000.0)
    assert sent["quantity"] == str(round(100.0 / 50000.0, 8))
    assert "notional" not in sent


def test_place_order_sell_requires_valid_price(monkeypatch):
    """A SELL without a usable price can't be sized into a quantity — fail loudly, don't guess."""
    def fake_post(url, json=None, timeout=None):
        raise AssertionError("should not reach the network without a valid price")

    monkeypatch.setattr(executor.requests, "post", fake_post)

    with pytest.raises(ValueError):
        executor._place_order("BTC_USDT", "SELL", 100.0, price=0)
