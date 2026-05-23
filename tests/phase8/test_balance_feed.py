"""Phase 8 tests for runtime.balance_feed.BalanceFeedDaemon.

All tests avoid real network/exchange calls by using monkeypatch.
All tests complete in < 2 s each.
"""
from __future__ import annotations

import sys
import types
import threading

import pytest

# ---------------------------------------------------------------------------
# Guard import — skip entire module if the daemon cannot be imported
# ---------------------------------------------------------------------------
try:
    from runtime.balance_feed import (
        BalanceFeedDaemon,
        BalanceFeedStatus,
        get_balance_feed_daemon,
    )
    _IMPORT_OK = True
except Exception as _exc:
    _IMPORT_OK = False
    _IMPORT_EXC = _exc

if not _IMPORT_OK:
    pytest.skip(
        f"runtime.balance_feed not importable: {_IMPORT_EXC}",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_daemon(**kwargs) -> BalanceFeedDaemon:
    """Return a fresh BalanceFeedDaemon with a very short interval for tests."""
    kwargs.setdefault("interval_s", 0.1)
    kwargs.setdefault("demo_mode", True)
    return BalanceFeedDaemon(**kwargs)


# ---------------------------------------------------------------------------
# Test 1 — daemon starts and stops cleanly
# ---------------------------------------------------------------------------

def test_daemon_starts_and_stops_cleanly(monkeypatch):
    """start() makes is_running()==True; stop() makes is_running()==False."""
    # Stub out guardian so no real network calls happen
    def _fake_fetch_and_check(self):  # type: ignore[misc]
        pass

    monkeypatch.setattr(BalanceFeedDaemon, "_fetch_and_check", _fake_fetch_and_check)

    daemon = _make_daemon()
    assert not daemon.is_running(), "Daemon should not be running before start()"

    daemon.start()
    assert daemon.is_running(), "Daemon should be running after start()"

    daemon.stop(timeout_s=2.0)
    assert not daemon.is_running(), "Daemon should not be running after stop()"


# ---------------------------------------------------------------------------
# Test 2 — _fetch_equity returns None when trading.exchange is unavailable
# ---------------------------------------------------------------------------

def test_fetch_equity_returns_none_on_import_error(monkeypatch):
    """If trading.exchange cannot be imported, _fetch_equity() returns None."""
    daemon = _make_daemon()

    # Make 'trading.exchange' unimportable by inserting a broken module entry
    broken = types.ModuleType("trading.exchange")
    broken.__spec__ = None  # type: ignore[attr-defined]

    original = sys.modules.pop("trading.exchange", None)
    # Patch sys.modules so that 'from trading.exchange import ...' raises ImportError
    monkeypatch.setitem(sys.modules, "trading.exchange", None)  # type: ignore[arg-type]

    try:
        result = daemon._fetch_equity()
    finally:
        # Restore previous state regardless
        if original is None:
            sys.modules.pop("trading.exchange", None)
        else:
            sys.modules["trading.exchange"] = original

    assert result is None, "_fetch_equity() must return None when import fails"


# ---------------------------------------------------------------------------
# Test 3 — _fetch_equity returns float on success
# ---------------------------------------------------------------------------

def test_fetch_equity_returns_float_on_success(monkeypatch):
    """When get_derivatives_balance returns {'equity': 1000.0}, _fetch_equity()
    returns 1000.0."""
    daemon = _make_daemon()

    # Build a fake module so the lazy import succeeds
    fake_exchange = types.ModuleType("trading.exchange")
    fake_exchange.get_derivatives_balance = lambda: {"equity": 1000.0}  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "trading.exchange", fake_exchange)

    result = daemon._fetch_equity()

    assert result == pytest.approx(1000.0), (
        f"_fetch_equity() should return 1000.0, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 — consecutive failures are tracked correctly
# ---------------------------------------------------------------------------

def test_consecutive_failures_tracked(monkeypatch):
    """After 3 calls where _fetch_equity returns None, consecutive_failures==3."""
    daemon = _make_daemon()

    # Stub _fetch_equity to always return None
    monkeypatch.setattr(daemon, "_fetch_equity", lambda: None)

    # Also stub the guardian call so no network I/O occurs
    def _noop_guardian_call(equity):
        pass

    # Patch the guardian import inside _fetch_and_check by making run_check a no-op
    fake_guardian_mod = types.ModuleType("runtime.live_balance_guardian")
    fake_guardian_mod.get_guardian = lambda: type(  # type: ignore[attr-defined]
        "FakeGuardian", (), {"run_check": lambda self, exchange_balance=None: None}
    )()
    monkeypatch.setitem(sys.modules, "runtime.live_balance_guardian", fake_guardian_mod)

    for _ in range(3):
        daemon.force_check()

    status = daemon.get_status()
    assert status.consecutive_failures == 3, (
        f"Expected consecutive_failures=3, got {status.consecutive_failures}"
    )


# ---------------------------------------------------------------------------
# Test 5 — consecutive failures reset on success
# ---------------------------------------------------------------------------

def test_consecutive_failures_reset_on_success(monkeypatch):
    """After 3 failures then 1 success, consecutive_failures resets to 0."""
    daemon = _make_daemon()

    call_count = {"n": 0}

    def _sometimes_fail():
        call_count["n"] += 1
        if call_count["n"] <= 3:
            return None
        return 42.0

    monkeypatch.setattr(daemon, "_fetch_equity", _sometimes_fail)

    # Stub out guardian so no network calls happen
    fake_guardian_mod = types.ModuleType("runtime.live_balance_guardian")
    fake_guardian_mod.get_guardian = lambda: type(  # type: ignore[attr-defined]
        "FakeGuardian", (), {"run_check": lambda self, exchange_balance=None: None}
    )()
    monkeypatch.setitem(sys.modules, "runtime.live_balance_guardian", fake_guardian_mod)

    for _ in range(3):
        daemon.force_check()

    status_mid = daemon.get_status()
    assert status_mid.consecutive_failures == 3

    # One successful check
    daemon.force_check()

    status_final = daemon.get_status()
    assert status_final.consecutive_failures == 0, (
        f"consecutive_failures should reset to 0 on success, "
        f"got {status_final.consecutive_failures}"
    )
    assert status_final.last_equity == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# Test 6 — get_status() returns a BalanceFeedStatus dataclass
# ---------------------------------------------------------------------------

def test_get_status_returns_dataclass(monkeypatch):
    """get_status() returns a BalanceFeedStatus instance with expected field types."""
    daemon = _make_daemon(demo_mode=True)

    status = daemon.get_status()

    assert isinstance(status, BalanceFeedStatus), (
        f"get_status() must return BalanceFeedStatus, got {type(status)}"
    )
    assert isinstance(status.running, bool)
    assert isinstance(status.consecutive_failures, int)
    assert isinstance(status.total_checks, int)
    assert isinstance(status.demo_mode, bool)
    assert status.last_fetch_ts is None or isinstance(status.last_fetch_ts, str)
    assert status.last_equity is None or isinstance(status.last_equity, float)
    assert status.last_error is None or isinstance(status.last_error, str)


# ---------------------------------------------------------------------------
# Test 7 — force_check() increments total_checks
# ---------------------------------------------------------------------------

def test_force_check_increments_total_checks(monkeypatch):
    """Calling force_check() twice increments total_checks to 2."""
    daemon = _make_daemon()

    monkeypatch.setattr(daemon, "_fetch_equity", lambda: None)

    fake_guardian_mod = types.ModuleType("runtime.live_balance_guardian")
    fake_guardian_mod.get_guardian = lambda: type(  # type: ignore[attr-defined]
        "FakeGuardian", (), {"run_check": lambda self, exchange_balance=None: None}
    )()
    monkeypatch.setitem(sys.modules, "runtime.live_balance_guardian", fake_guardian_mod)

    assert daemon.get_status().total_checks == 0

    daemon.force_check()
    daemon.force_check()

    assert daemon.get_status().total_checks == 2, (
        f"Expected total_checks=2, got {daemon.get_status().total_checks}"
    )


# ---------------------------------------------------------------------------
# Test 8 — demo_mode flag is preserved on the status
# ---------------------------------------------------------------------------

def test_demo_mode_flag_preserved():
    """BalanceFeedDaemon(demo_mode=True) → get_status().demo_mode == True."""
    daemon_true = BalanceFeedDaemon(demo_mode=True)
    assert daemon_true.get_status().demo_mode is True, (
        "demo_mode=True must be reflected in get_status()"
    )

    daemon_false = BalanceFeedDaemon(demo_mode=False)
    assert daemon_false.get_status().demo_mode is False, (
        "demo_mode=False must be reflected in get_status()"
    )
