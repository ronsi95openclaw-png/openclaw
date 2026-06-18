"""Unit tests for the TJR trade-setup builder (trading/setup.py).

Covers entry/stop/target/RR math for both BUY and SELL, the percent-fallback
buffer path, HOLD/empty guards, the Telegram formatter, and the JSONL record.
All series are deterministic and small. SEND-ONLY: nothing here executes.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from trading.setup import (  # noqa: E402
    DEFAULT_ATR_BUFFER_MULT,
    DEFAULT_STOP_BUFFER_PCT,
    _atr_from_closes,
    build_trade_setup,
    format_trade_setup_telegram,
    to_jsonl_record,
)
from trading.strategy import Signal, TradeSetup  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _signal(action: str, coin: str = "BTC_USDT", confidence: str = "HIGH") -> Signal:
    return Signal(
        coin=coin, action=action,
        rsi=42.0, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
        reason="Liquidity sweep test setup.", confidence=confidence,
    )


# ── BUY math ─────────────────────────────────────────────────────────────────

class TestBuySetup:
    def test_entry_stop_targets_buy(self):
        # swing_low = 90, entry = 110. Buffer is ATR-of-closes based.
        closes = [100.0] * 20 + [90.0, 110.0]
        setup = build_trade_setup(_signal("BUY"), closes, lookback=22)

        assert setup.direction == "BUY"
        assert setup.entry == pytest.approx(110.0)
        # swing_low over the 22-close window is 90; buffer derived from ATR.
        buf = _atr_from_closes(closes) * DEFAULT_ATR_BUFFER_MULT
        assert setup.stop == pytest.approx(90.0 - buf)

        risk = setup.entry - setup.stop
        assert risk > 0
        assert setup.targets[0] == pytest.approx(110.0 + risk * 1)
        assert setup.targets[1] == pytest.approx(110.0 + risk * 2)
        assert setup.targets[2] == pytest.approx(110.0 + risk * 3)

    def test_buy_targets_above_entry_and_ordered(self):
        closes = [100.0] * 20 + [95.0, 105.0]
        setup = build_trade_setup(_signal("BUY"), closes, lookback=22)
        assert setup.stop < setup.entry
        assert setup.targets == sorted(setup.targets)
        assert all(t > setup.entry for t in setup.targets)

    def test_buy_reward_to_risk_is_final_multiple(self):
        closes = [100.0] * 20 + [90.0, 110.0]
        setup = build_trade_setup(_signal("BUY"), closes, lookback=22)
        assert setup.reward_to_risk == pytest.approx(3.0)


# ── SELL math ────────────────────────────────────────────────────────────────

class TestSellSetup:
    def test_entry_stop_targets_sell(self):
        # swing_high = 120, entry = 100.
        closes = [100.0] * 20 + [120.0, 100.0]
        setup = build_trade_setup(_signal("SELL"), closes, lookback=22)

        assert setup.direction == "SELL"
        assert setup.entry == pytest.approx(100.0)
        buf = _atr_from_closes(closes) * DEFAULT_ATR_BUFFER_MULT
        assert setup.stop == pytest.approx(120.0 + buf)

        risk = setup.stop - setup.entry
        assert risk > 0
        assert setup.targets[0] == pytest.approx(100.0 - risk * 1)
        assert setup.targets[1] == pytest.approx(100.0 - risk * 2)
        assert setup.targets[2] == pytest.approx(100.0 - risk * 3)

    def test_sell_targets_below_entry_and_ordered(self):
        closes = [100.0] * 20 + [110.0, 100.0]
        setup = build_trade_setup(_signal("SELL"), closes, lookback=22)
        assert setup.stop > setup.entry
        assert setup.targets == sorted(setup.targets, reverse=True)
        assert all(t < setup.entry for t in setup.targets)


# ── Buffer / degenerate handling ─────────────────────────────────────────────

class TestBuffersAndGuards:
    def test_atr_buffer_widens_stop_when_volatile(self):
        # Alternating series -> non-zero ATR-of-closes -> ATR buffer applied,
        # which is wider than the tiny percent buffer on a flat series.
        closes = [100.0, 104.0] * 11  # swing_low ~100, entry 104
        setup = build_trade_setup(_signal("BUY"), closes, lookback=22)
        percent_buf = 104.0 * (DEFAULT_STOP_BUFFER_PCT / 100.0)
        # ATR buffer should push the stop further below swing_low than percent.
        assert setup.stop < 100.0 - percent_buf

    def test_buy_degenerate_entry_at_swing_low_falls_back(self):
        # entry equals swing low -> risk would be negative w/o buffer; the
        # fallback must yield a positive risk and a stop below entry.
        closes = [100.0] * 22  # swing_low == entry == 100, flat -> tiny buffer
        setup = build_trade_setup(_signal("BUY"), closes, lookback=22)
        assert setup.stop < setup.entry
        assert setup.entry - setup.stop > 0

    def test_hold_signal_returns_none(self):
        assert build_trade_setup(_signal("HOLD"), [100.0] * 22) is None

    def test_empty_closes_returns_none(self):
        assert build_trade_setup(_signal("BUY"), []) is None


# ── Formatter ────────────────────────────────────────────────────────────────

class TestFormatter:
    def test_buy_message_contains_levels_and_manual_note(self):
        closes = [100.0] * 20 + [90.0, 110.0]
        sig = _signal("BUY")
        setup = build_trade_setup(sig, closes, lookback=22)
        msg = format_trade_setup_telegram(setup, sig)

        assert "TJR Trade Setup" in msg
        assert "BTC_USDT" in msg
        assert "BUY" in msg
        assert "Entry" in msg and "Stop" in msg
        assert "T1" in msg and "T2" in msg and "T3" in msg
        assert "1R" in msg and "3R" in msg
        assert "HIGH" in msg
        # The send-only / manual-execution note must be present.
        assert "manually" in msg.lower()
        assert "no order" in msg.lower()

    def test_sell_message_direction(self):
        closes = [100.0] * 20 + [120.0, 100.0]
        sig = _signal("SELL")
        setup = build_trade_setup(sig, closes, lookback=22)
        msg = format_trade_setup_telegram(setup, sig)
        assert "SELL" in msg
        assert "🔴" in msg


# ── JSONL record ─────────────────────────────────────────────────────────────

class TestJsonlRecord:
    def test_record_roundtrips_and_has_fields(self):
        closes = [100.0] * 20 + [90.0, 110.0]
        sig = _signal("BUY")
        setup = build_trade_setup(sig, closes, lookback=22)
        line = to_jsonl_record(setup, sig)

        data = json.loads(line)
        assert data["coin"] == "BTC_USDT"
        assert data["direction"] == "BUY"
        assert data["entry"] == pytest.approx(setup.entry)
        assert data["stop"] == pytest.approx(setup.stop)
        assert len(data["targets"]) == 3
        assert data["reward_to_risk"] == pytest.approx(3.0)
        assert data["confidence"] == "HIGH"
        assert "ts" in data and "reason" in data
