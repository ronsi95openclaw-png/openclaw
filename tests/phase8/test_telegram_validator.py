"""Phase 8 tests for runtime.telegram_validator.

6 tests covering:
  1. Not configured → configured=False, message_sent=False
  2. Token prefix truncated to 8 chars
  3. Successful send with mocked urlopen (status=200, ok=true)
  4. HTTP error → message_sent=False, error is not None
  5. Latency measured (latency_ms > 0)
  6. check_telegram_config() returns expected dict keys

No real network calls are made.
"""
from __future__ import annotations

import io
import time
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Guard import
# ---------------------------------------------------------------------------
try:
    from runtime.telegram_validator import (
        TelegramValidationResult,
        check_telegram_config,
        validate_telegram,
    )
    _IMPORT_OK = True
except Exception as _exc:
    _IMPORT_OK = False
    _IMPORT_EXC = _exc

if not _IMPORT_OK:
    pytest.skip(
        f"runtime.telegram_validator not importable: {_IMPORT_EXC}",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_response(status: int = 200, body: bytes = b'{"ok":true}') -> MagicMock:
    """Build a context-manager mock that looks like urllib.request.urlopen's response."""
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNotConfigured:
    def test_not_configured_returns_false(self, monkeypatch):
        """When TOKEN and CHAT_ID are missing, configured and message_sent are False."""
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

        result = validate_telegram()

        assert result.configured is False
        assert result.message_sent is False
        assert result.response_status is None
        assert result.error is None
        assert result.latency_ms == 0.0


class TestTokenPrefix:
    def test_token_prefix_truncated(self, monkeypatch):
        """token_prefix should be exactly the first 8 characters of the token."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abcdefghijklmn")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

        cfg = check_telegram_config()

        assert cfg["token_prefix"] == "abcdefgh"


class TestValidationSucceeds:
    def test_validation_succeeds_with_mock(self, monkeypatch):
        """With mocked urlopen returning 200 + ok:true, message_sent is True."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "testtoken123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "99999")

        with patch("urllib.request.urlopen", return_value=_fake_response(200, b'{"ok":true}')):
            result = validate_telegram()

        assert result.configured is True
        assert result.message_sent is True
        assert result.response_status == 200
        assert result.response_ok is True
        assert result.error is None


class TestValidationFailsOnHTTPError:
    def test_validation_fails_on_http_error(self, monkeypatch):
        """When urlopen raises URLError, message_sent=False and error is not None."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "testtoken456")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "88888")

        url_error = urllib.error.URLError("connection refused")
        with patch("urllib.request.urlopen", side_effect=url_error):
            result = validate_telegram()

        assert result.configured is True
        assert result.message_sent is False
        assert result.error is not None
        assert len(result.error) > 0


class TestLatencyMeasured:
    def test_latency_measured(self, monkeypatch):
        """Latency should be > 0 ms even with a fast mock response."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "latencytoken")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "77777")

        def _slow_urlopen(*args, **kwargs):
            # Simulate a tiny network delay so latency_ms is measurable
            time.sleep(0.01)
            return _fake_response(200, b'{"ok":true}')

        with patch("urllib.request.urlopen", side_effect=_slow_urlopen):
            result = validate_telegram()

        assert result.latency_ms > 0.0


class TestCheckConfigReturnsKeys:
    def test_check_config_returns_dict_keys(self, monkeypatch):
        """check_telegram_config() must return a dict with the four expected keys."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mytoken")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

        cfg = check_telegram_config()

        assert isinstance(cfg, dict)
        assert "configured" in cfg
        assert "token_set" in cfg
        assert "chat_id_set" in cfg
        assert "token_prefix" in cfg
        assert cfg["configured"] is True
        assert cfg["token_set"] is True
        assert cfg["chat_id_set"] is True
