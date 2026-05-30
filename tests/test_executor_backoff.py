"""Tests for trading.executor._place_order transient-network retry.

The retry policy:
  - On requests.ConnectionError or requests.Timeout, retry ONCE.
  - On HTTPError (raise_for_status), do NOT retry — the server may have
    accepted the order and a retry would double-fill (create-order is
    not idempotent).
  - After the second failure, re-raise the exception so the caller can
    log via _log_trade.
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

    def fake_sign(_method, _params, _key, _secret):
        return {"signed": True}

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

    result = executor._place_order("BTC_USDT", "BUY", 25.0)
    assert calls["n"] == 2, "should have retried exactly once"
    assert result == {"order_id": "fake-123"}


def test_place_order_retries_on_timeout_then_succeeds(monkeypatch):
    """Transient Timeout on first attempt -> retry -> success."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.Timeout("slow")
        return _FakeResponse(200)

    monkeypatch.setattr(executor.requests, "post", fake_post)

    result = executor._place_order("ETH_USDT", "SELL", 50.0)
    assert calls["n"] == 2
    assert result == {"order_id": "fake-123"}


def test_place_order_reraises_after_second_connection_error(monkeypatch):
    """Two consecutive ConnectionErrors -> re-raise. No third attempt."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        raise requests.ConnectionError(f"blip {calls['n']}")

    monkeypatch.setattr(executor.requests, "post", fake_post)

    with pytest.raises(requests.ConnectionError):
        executor._place_order("BTC_USDT", "BUY", 25.0)

    assert calls["n"] == 2, "must retry exactly once, then raise"


def test_place_order_reraises_after_second_timeout(monkeypatch):
    """Two consecutive Timeouts -> re-raise."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        raise requests.Timeout(f"slow {calls['n']}")

    monkeypatch.setattr(executor.requests, "post", fake_post)

    with pytest.raises(requests.Timeout):
        executor._place_order("BTC_USDT", "BUY", 25.0)

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
        executor._place_order("BTC_USDT", "BUY", 25.0)

    assert calls["n"] == 1, "HTTPError after the wire-level success must NOT trigger retry"


def test_place_order_first_attempt_success_no_retry(monkeypatch):
    """Happy path: post returns 200 immediately, only one attempt."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        calls["n"] += 1
        return _FakeResponse(200)

    monkeypatch.setattr(executor.requests, "post", fake_post)

    result = executor._place_order("BTC_USDT", "BUY", 25.0)
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
        executor._place_order("BTC_USDT", "BUY", 25.0)

    assert calls["n"] == 1
