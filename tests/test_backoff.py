import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

from trading.backoff import is_rate_limit_error, is_retryable, with_backoff


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeHTTPError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.response = _FakeResponse(status_code)


class TestIsRateLimitError:
    def test_429_response_is_rate_limit(self):
        assert is_rate_limit_error(_FakeHTTPError(429)) is True

    def test_200_response_not_rate_limit(self):
        assert is_rate_limit_error(_FakeHTTPError(200)) is False

    def test_message_mentioning_429(self):
        assert is_rate_limit_error(ValueError("Crypto.com error: 429 too many requests")) is True

    def test_rate_limit_phrase(self):
        assert is_rate_limit_error(Exception("Rate limit exceeded")) is True

    def test_unrelated_error_not_rate_limit(self):
        assert is_rate_limit_error(ValueError("No candle data for BTC")) is False


class TestIsRetryable:
    def test_rate_limit_is_retryable(self):
        assert is_retryable(_FakeHTTPError(429)) is True

    def test_timeout_is_retryable(self):
        assert is_retryable(requests.Timeout("slow")) is True

    def test_connection_error_is_retryable(self):
        assert is_retryable(requests.ConnectionError("down")) is True

    def test_value_error_not_retryable(self):
        assert is_retryable(ValueError("No candle data")) is False


class TestWithBackoff:
    def test_returns_value_on_first_success(self):
        calls = []

        @with_backoff(sleeper=lambda d: None)
        def f():
            calls.append(1)
            return "ok"

        assert f() == "ok"
        assert len(calls) == 1

    def test_retries_then_succeeds(self):
        attempts = {"n": 0}
        sleeps = []

        @with_backoff(max_retries=5, sleeper=lambda d: sleeps.append(d))
        def f():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise Exception("rate limit")
            return "done"

        assert f() == "done"
        assert attempts["n"] == 3
        assert len(sleeps) == 2

    def test_non_retryable_raises_immediately(self):
        attempts = {"n": 0}

        @with_backoff(max_retries=5, sleeper=lambda d: None)
        def f():
            attempts["n"] += 1
            raise ValueError("No candle data")

        try:
            f()
            assert False, "should have raised"
        except ValueError:
            pass
        assert attempts["n"] == 1

    def test_exhausts_retries_and_raises(self):
        attempts = {"n": 0}

        @with_backoff(max_retries=3, sleeper=lambda d: None)
        def f():
            attempts["n"] += 1
            raise Exception("429")

        try:
            f()
            assert False, "should have raised"
        except Exception:
            pass
        assert attempts["n"] == 3

    def test_preserves_function_name(self):
        @with_backoff(sleeper=lambda d: None)
        def my_func():
            return 1

        assert my_func.__name__ == "my_func"
