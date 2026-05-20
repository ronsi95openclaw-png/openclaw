"""Shared pytest fixtures for all OpenClaw test modules."""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone, timedelta
from typing import List

import pytest

# ── Candle factory ────────────────────────────────────────────────────────────

def make_candles(
    n: int = 100,
    base_price: float = 105_000.0,
    vol_std: float    = 0.009,
    trend:   float    = 0.0002,    # per-bar drift
    seed:    int      = 42,
) -> list:
    """Generate deterministic synthetic OHLCV candles.

    Returns plain dicts (compatible with research.types.Candle constructor).
    """
    rng    = random.Random(seed)
    price  = base_price
    ts     = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    step   = 15 * 60 * 1000   # 15 minutes in ms

    candles = []
    for _ in range(n):
        chg    = rng.gauss(trend, vol_std)
        open_p = price
        close  = price * (1 + chg)
        high   = max(open_p, close) * (1 + abs(rng.gauss(0, 0.002)))
        low    = min(open_p, close) * (1 - abs(rng.gauss(0, 0.002)))
        vol    = rng.uniform(100, 5_000)
        candles.append({
            "ts":     ts,
            "open":   round(open_p, 2),
            "high":   round(high,   2),
            "low":    round(low,    2),
            "close":  round(close,  2),
            "volume": round(vol,    4),
        })
        price = close
        ts   += step

    return candles


def make_trending_candles(n: int = 100, base: float = 100.0, seed: int = 1) -> list:
    """Strong uptrend — ADX will be high."""
    return make_candles(n, base_price=base, trend=0.005, vol_std=0.003, seed=seed)


def make_ranging_candles(n: int = 100, base: float = 100.0, seed: int = 2) -> list:
    """Mean-reverting oscillation — ADX will be low."""
    rng   = random.Random(seed)
    price = base
    ts    = 1_700_000_000_000
    step  = 15 * 60 * 1000
    candles = []
    for i in range(n):
        # Sine wave oscillation
        target = base * (1 + 0.02 * math.sin(i * 0.3))
        price  = price * 0.9 + target * 0.1 + rng.gauss(0, base * 0.001)
        open_p = price
        close  = price * (1 + rng.gauss(0, 0.002))
        high   = max(open_p, close) * 1.001
        low    = min(open_p, close) * 0.999
        candles.append({
            "ts": ts, "open": round(open_p, 4), "high": round(high, 4),
            "low": round(low, 4), "close": round(close, 4),
            "volume": round(rng.uniform(100, 1000), 2),
        })
        ts += step
    return candles


def make_panic_candles(n: int = 100, base: float = 100.0, seed: int = 3) -> list:
    """Candles with a sharp drop + volume spike at the end."""
    candles = make_candles(n - 5, base, seed=seed)
    # Add 5 panic bars
    rng   = random.Random(seed)
    price = candles[-1]["close"]
    ts    = candles[-1]["ts"] + 15 * 60 * 1000
    for _ in range(5):
        drop   = rng.uniform(0.04, 0.07)
        open_p = price
        close  = price * (1 - drop)
        high   = open_p * 1.001
        low    = close * 0.995
        vol    = rng.uniform(10_000, 50_000)
        candles.append({
            "ts": ts, "open": round(open_p, 4), "high": round(high, 4),
            "low": round(low, 4), "close": round(close, 4),
            "volume": round(vol, 2),
        })
        price = close
        ts   += 15 * 60 * 1000
    return candles


# ── Trade factories ───────────────────────────────────────────────────────────

def make_winning_trades(n: int = 10) -> list:
    """All-win trade log (plain dicts)."""
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "id":           f"T{i:03d}",
            "symbol":       "BTC-USDT",
            "strategy":     "EMA_CROSS",
            "side":         "long",
            "entry_price":  105_000.0,
            "exit_price":   106_050.0,
            "size":         0.01,
            "pnl":          10.5,
            "outcome":      "win",
            "opened_at":    (base_time + timedelta(hours=i)).strftime("%H:%M:%S"),
            "closed_at":    (base_time + timedelta(hours=i, minutes=30)).strftime("%H:%M:%S"),
        }
        for i in range(n)
    ]


def make_losing_trades(n: int = 10) -> list:
    """All-loss trade log."""
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "id":           f"L{i:03d}",
            "symbol":       "ETH-USDT",
            "strategy":     "BREAKOUT",
            "side":         "long",
            "entry_price":  3_500.0,
            "exit_price":   3_430.0,
            "size":         0.1,
            "pnl":          -7.0,
            "outcome":      "loss",
            "opened_at":    (base_time + timedelta(hours=i)).strftime("%H:%M:%S"),
            "closed_at":    (base_time + timedelta(hours=i, minutes=20)).strftime("%H:%M:%S"),
        }
        for i in range(n)
    ]


def make_mixed_trades(n_wins: int = 6, n_losses: int = 4) -> list:
    """Mixed win/loss trade log."""
    wins   = make_winning_trades(n_wins)
    losses = make_losing_trades(n_losses)
    combined = wins + losses
    random.shuffle(combined)
    return combined


# ── Pytest fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def candles():
    return make_candles(100)


@pytest.fixture
def trending_candles():
    return make_trending_candles(100)


@pytest.fixture
def ranging_candles():
    return make_ranging_candles(100)


@pytest.fixture
def panic_candles():
    return make_panic_candles(100)


@pytest.fixture
def mixed_trades():
    return make_mixed_trades(6, 4)


@pytest.fixture
def winning_trades():
    return make_winning_trades(10)


@pytest.fixture
def losing_trades():
    return make_losing_trades(10)


@pytest.fixture
def empty_trades():
    return []
