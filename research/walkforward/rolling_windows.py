"""Rolling and anchored walk-forward window generation."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from research.types import Candle, WalkForwardWindow


def _ts_to_dt(ts_ms: int) -> datetime:
    """Convert Unix ms timestamp to UTC datetime."""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)


def generate_windows(
    candles: List[Candle],
    train_bars: int = 2016,
    test_bars: int = 672,
    step_bars: int = 336,
    min_train_bars: int = 500,
) -> List[WalkForwardWindow]:
    """Generate rolling train/test window pairs.

    Each window contains the actual candles for training and testing.

    Args:
        candles: Full candle series in chronological order.
        train_bars: Number of bars for the training window.
        test_bars: Number of bars for the test (OOS) window.
        step_bars: How many bars to advance between windows.
        min_train_bars: Minimum acceptable training bars; windows below this
            threshold are dropped.

    Returns:
        List of WalkForwardWindow objects ordered chronologically.
    """
    if len(candles) < min_train_bars + test_bars:
        return []

    windows: List[WalkForwardWindow] = []
    window_id = 0
    train_start_idx = 0

    while True:
        train_end_idx = train_start_idx + train_bars
        test_start_idx = train_end_idx
        test_end_idx = test_start_idx + test_bars

        # Stop when we run out of candles for a full test window
        if test_end_idx > len(candles):
            break

        actual_train_bars = train_end_idx - train_start_idx
        if actual_train_bars < min_train_bars:
            train_start_idx += step_bars
            continue

        train_slice = candles[train_start_idx:train_end_idx]
        test_slice = candles[test_start_idx:test_end_idx]

        windows.append(
            WalkForwardWindow(
                window_id=window_id,
                train_start=_ts_to_dt(train_slice[0].ts),
                train_end=_ts_to_dt(train_slice[-1].ts),
                test_start=_ts_to_dt(test_slice[0].ts),
                test_end=_ts_to_dt(test_slice[-1].ts),
                train_candles=train_slice,
                test_candles=test_slice,
                best_params={},
            )
        )
        window_id += 1
        train_start_idx += step_bars

    return windows


def anchored_windows(
    candles: List[Candle],
    test_bars: int = 672,
    min_train_bars: int = 500,
) -> List[WalkForwardWindow]:
    """Anchored walk-forward: train window always starts at t=0, expands forward.

    The training window grows with each iteration while the test window slides
    forward, always immediately following the growing training window.

    Args:
        candles: Full candle series in chronological order.
        test_bars: Number of bars for each test (OOS) window.
        min_train_bars: Minimum bars before the first test window is generated.

    Returns:
        List of WalkForwardWindow objects ordered chronologically.
    """
    if len(candles) < min_train_bars + test_bars:
        return []

    windows: List[WalkForwardWindow] = []
    window_id = 0

    # First test window starts right after min_train_bars
    train_end_idx = min_train_bars

    while True:
        test_start_idx = train_end_idx
        test_end_idx = test_start_idx + test_bars

        if test_end_idx > len(candles):
            break

        train_slice = candles[0:train_end_idx]
        test_slice = candles[test_start_idx:test_end_idx]

        windows.append(
            WalkForwardWindow(
                window_id=window_id,
                train_start=_ts_to_dt(train_slice[0].ts),
                train_end=_ts_to_dt(train_slice[-1].ts),
                test_start=_ts_to_dt(test_slice[0].ts),
                test_end=_ts_to_dt(test_slice[-1].ts),
                train_candles=train_slice,
                test_candles=test_slice,
                best_params={},
            )
        )
        window_id += 1
        # Expand training window by one test-window worth of bars
        train_end_idx += test_bars

    return windows
