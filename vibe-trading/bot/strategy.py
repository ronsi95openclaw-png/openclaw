#!/usr/bin/env python3
"""
TJR/ICT Lucid 25K — Strategy Module (``strategy.py``)
=====================================================
PURE, deterministic, side-effect-free signal generator for the live/paper bot.

This module is the leaf of the bot dependency graph (see ``bot/ARCHITECTURE.md``
§3). It has **zero** project imports and performs **no I/O, no network, no order
placement, and no logging** — it only reads pandas DataFrames and returns a
Signal ``dict`` (the LOCKED schema in §2) or ``None``.

It re-implements, against pandas DataFrames keyed by timeframe, the same
*detection* AND *order-geometry* logic proven in ``backtest/tjr_backtest.py``
(kill zone, HTF bias, liquidity sweep, FVG, OTE, 1M MSB), so the live path is
bar-for-bar consistent with the validated backtest — the "live == backtest" goal:

  - kill-zone time gate          (same logic as ``is_in_kill_zone`` / ``KILL_ZONES``)
  - HTF bias via discount/premium of the swing range (50% midpoint)
  - liquidity sweep              (same logic as ``detect_liquidity_sweep``)
  - 3-candle Fair Value Gap      (same logic as ``detect_fvg``) — trigger OR confirm
  - Order Block                  (last opposing candle) — trigger OR confirm (either
                                  the FVG or the OB may trigger; 2026-07-08 change)
  - OTE fib zone 61.8%-79%       (TJR spec Step 5)
  - 1M Market Structure Break    (same logic as ``detect_msb``)
  - protective stop beyond the sweep (sweep level ±1 tick)  — same as ``_open_trade``
  - FIXED R in points (``stop_ticks * tick_size``; ES default 8 ticks = 2.0 pts),
    then TP1 = entry ± 2R, TP2 = entry ± 4R (same as ``stop_points`` /
    ``tp1_points`` / ``tp2_points`` in ``tjr_backtest.TJRBacktester``).

ORDER GEOMETRY — matches ``backtest/tjr_backtest.py`` exactly:

  - R / TP basis. R is a FIXED stop distance in points,
    ``R = stop_ticks * tick_size`` (8 ticks = 2.0 pts on ES), read from
    :class:`StrategyConfig`. Targets are ``TP1 = entry ± 2R`` and
    ``TP2 = entry ± 4R`` — identical to the backtest's ``tp1_points =
    stop_points*2`` / ``tp2_points = stop_points*4``. R is NOT derived from the
    entry->sweep distance; the sweep only sets the protective ``stop`` price.
  - Protective stop. Placed at ``sweep_level ± 1 tick`` (long: below the swept
    swing low; short: above the swept swing high), exactly as
    ``tjr_backtest._open_trade``. This is the stop-out price, not the R basis.
  - Entry zone. EITHER a direction-matched 3-candle FVG OR a direction-matched
    Order Block may trigger a setup (2026-07-08: FVG is no longer a hard
    requirement -- it demoted from mandatory-trigger to confirm-only, mirroring
    the role the OB always had). If both are present the reason is annotated
    ``fvg+ob`` (strongest confirmation); if only one is present the reason is
    ``fvg`` or ``ob``. Every OTHER ICT condition (kill-zone timing, HTF bias,
    liquidity sweep, OTE 61.8%-79%, closed 1M MSB) remains a hard requirement.

ONE deliberate, documented divergence remains (a leaf, pure, no-look-ahead
signal generator cannot do otherwise):

  - Entry price. Live enters off the CONFIRMED 1M close of the signal bar
    (``df_1m['close'].iloc[-1]``) — the only price available without looking
    ahead. The backtest enters on the NEXT bar's ``open`` (signal scheduled at
    bar ``i``, filled at ``i+1``). The R, TP geometry, stop placement, and the
    FVG-required entry rule are otherwise identical.

LOCKED Signal schema (§2 of ARCHITECTURE.md)::

    {
      "side":       'long'|'short'|None,
      "instrument": 'ES'|'NQ'|'MES'|'MNQ',
      "entry":      float,
      "stop":       float,          # != entry, finite
      "tp1":        float,          # entry ± 2R
      "tp2":        float,          # entry ± 4R
      "size":       int,            # >= 1 (risk_guard owns clamping to mandate)
      "reason":     str,            # which TJR rules fired
      "ts":         str,            # ISO-8601 ET, e.g. '2026-06-21T09:42:00-04:00'
    }

``generate_signal`` returns ``None`` when there is no setup. The strategy NEVER
reads the mandate (``risk_guard`` owns mandate clamping) and NEVER carries
secrets, balances, or webhook info.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

import math

import pandas as pd

# Eastern Time — the ONLY wall-clock the kill-zone gate and signal ``ts`` may use.
_ET = ZoneInfo("America/New_York")

# ── Constants (kill-zone tables ported from tjr_backtest.KILL_ZONES) ──────────
# Kill-zone bounds are STRATEGY config (NOT mandate). Times are ET. These mirror
# the backtest exactly so live == backtest.
KILL_ZONES: dict[str, tuple[time, time]] = {
    "ny_open":     (time(8, 30),  time(11, 0)),
    "london_open": (time(2, 0),   time(5, 0)),
    "ny_pm":       (time(13, 30), time(16, 0)),
    "london_close": (time(10, 0), time(12, 0)),
}

# Per-instrument tick size (for the 1-tick stop offset beyond the sweep).
# Mirrors INSTRUMENTS in tjr_backtest.py (all ES/NQ futures use a 0.25 tick).
_TICK_SIZE: dict[str, float] = {
    "ES": 0.25,
    "MES": 0.25,
    "NQ": 0.25,
    "MNQ": 0.25,
}
_DEFAULT_TICK = 0.25

_OHLC = ("open", "high", "low", "close")


# ── Strategy-local config (NOT the mandate) ──────────────────────────────────
@dataclass(frozen=True)
class StrategyConfig:
    """Operational tunables for the strategy (kept OUT of the mandate).

    Defaults match the TJR spec and the reference backtest:
    NY-Open kill zone, 20-bar swing lookback, 2-bar sweep window, 5-bar MSB
    window, OTE 61.8%-79%, TP1=2R, TP2=4R, 1 contract.
    """

    kill_zones: tuple[str, ...] = ("ny_open", "london_open")
    lookback: int = 20
    sweep_bars: int = 2
    msb_bars: int = 5
    ob_bars: int = 6          # back-scan window (bars) for the Order Block
    ote_low: float = 0.618
    ote_high: float = 0.79
    stop_ticks: int = 8       # MIN protective stop in ticks (floor; 8 ticks = 2.0 pts on ES)
    max_stop_points: float = 6.0  # CAP on stop distance from entry (= $300/contract on ES, per spec)
    tp1_rr: float = 2.0
    tp2_rr: float = 4.0
    default_contracts: int = 1
    max_minutes_in_kz: int = 75  # reject entries more than N min into any kill zone (0 = off)


# ── Internal helpers (pure; mirror the backtest semantics) ───────────────────
def _as_et(now_et: datetime) -> Optional[datetime]:
    """Return ``now_et`` expressed in America/New_York, or ``None`` if naive.

    The contract says ``now_et`` is tz-aware ET, but a tz-aware datetime in a
    *different* zone would make the wall-clock ``.time()`` wrong and silently
    misfire the kill-zone gate. So we convert any tz-aware input to ET before
    reading the wall clock. A NAIVE datetime has no defined offset and cannot be
    safely interpreted, so we fail closed (return ``None``); callers must treat
    that as "no signal" rather than guess an offset.
    """
    if not isinstance(now_et, datetime) or now_et.tzinfo is None:
        return None
    return now_et.astimezone(_ET)


def minutes_into_kill_zone(now_et: datetime, zones: tuple[str, ...] | list[str]) -> Optional[int]:
    """Return how many minutes ``now_et`` is past the START of the active kill zone.

    Returns ``None`` when not currently in any kill zone. Used by ``generate_signal``
    to enforce ``StrategyConfig.max_minutes_in_kz`` — the late-entry filter that
    drops setups triggered long after the session open, where liquidity is thin and
    stop-runs have already played out.
    """
    et = _as_et(now_et)
    if et is None:
        return None
    t = et.time()
    for zone in zones:
        bounds = KILL_ZONES.get(zone)
        if bounds is None:
            continue
        lo, hi = bounds
        if lo <= t < hi:
            return (t.hour * 60 + t.minute) - (lo.hour * 60 + lo.minute)
    return None


def in_kill_zone(now_et: datetime, zones: tuple[str, ...] | list[str]) -> bool:
    """Return True if ``now_et`` falls inside any named kill zone.

    Half-open interval ``lo <= t < hi`` on the ET wall-clock time (same logic as
    ``tjr_backtest.is_in_kill_zone``). Unknown zone names are ignored. The input
    is converted to America/New_York first, so a tz-aware datetime in any zone is
    evaluated on the correct ET wall clock; a naive datetime fails closed
    (returns ``False``) because its offset is undefined.
    """
    et = _as_et(now_et)
    if et is None:
        return False
    t = et.time()
    for zone in zones:
        bounds = KILL_ZONES.get(zone)
        if bounds is None:
            continue
        lo, hi = bounds
        if lo <= t < hi:
            return True
    return False


def swing_range(df: pd.DataFrame, lookback: int) -> Optional[tuple[float, float]]:
    """Return ``(swing_low, swing_high)`` over the last ``lookback`` bars.

    Requires the FULL ``lookback`` window: a 20-bar swing is never computed from
    fewer than 20 bars (an under-sampled range would yield a biased HTF read /
    OTE band that the backtest, which always has the full window, would not).
    Returns ``None`` if there are not enough bars, the window is malformed, or
    the values are non-finite.
    """
    if df is None or lookback <= 0 or len(df) < lookback:
        return None
    window = df.iloc[-lookback:]
    if window.empty or not _bars_sane(window):
        return None
    swing_low = float(window["low"].min())
    swing_high = float(window["high"].max())
    if not (_finite(swing_low) and _finite(swing_high)) or swing_high <= swing_low:
        return None
    return swing_low, swing_high


def htf_bias(htf_df: pd.DataFrame, lookback: int = 20) -> Optional[str]:
    """Determine HTF directional bias from discount/premium of the swing range.

    Per TJR spec Step 1: take the most recent swing high/low; if the latest
    close is in *discount* (below the 50% midpoint of the range) look LONG; if in
    *premium* (above 50%) look SHORT. A close sitting exactly on the midpoint (or
    a degenerate/zero-width range) yields no bias.

    Returns ``'long'``, ``'short'``, or ``None``.
    """
    rng = swing_range(htf_df, lookback)
    if rng is None:
        return None
    swing_low, swing_high = rng
    midpoint = (swing_low + swing_high) / 2.0
    last_close = float(htf_df["close"].iloc[-1])
    if not _finite(last_close):
        return None
    if last_close < midpoint:
        return "long"       # discount -> look long
    if last_close > midpoint:
        return "short"      # premium -> look short
    return None             # exactly mid -> no clear bias


def find_liquidity_sweep(
    df: pd.DataFrame,
    side: str,
    lookback: int = 20,
    sweep_bars: int = 3,
) -> Optional[tuple[str, float, float, float]]:
    """Detect a liquidity sweep at the latest bar, direction-matched to ``side``.

    Same logic as ``tjr_backtest.detect_liquidity_sweep`` (evaluated at the final
    bar):

      - LONG  (``side='long'``):  recent low dipped BELOW the swing low of the
        reference window, but the current close is back ABOVE that swing low
        (sell-side liquidity swept, reversing up).
      - SHORT (``side='short'``): recent high spiked ABOVE the swing high, but the
        current close is back BELOW it (buy-side liquidity swept, reversing down).

    Returns ``(sweep_kind, sweep_level, ref_low, ref_high)`` where ``sweep_kind``
    is ``'bullish_sweep'`` / ``'bearish_sweep'``, ``sweep_level`` is the swept
    swing low/high (used for stop placement), and ``(ref_low, ref_high)`` is the
    swing of the REFERENCE window that was swept — used by the caller to measure
    the OTE band off the swing that was actually swept (TJR Step 5), rather than
    off the full last-``lookback`` range. Returns ``None`` if no sweep.
    """
    n = len(df)
    if n < lookback + sweep_bars + 1:
        return None

    # Reference window precedes the sweep window (same slicing as the backtest,
    # evaluated at the final bar i = n-1:
    #   ref    = bars[i-lookback-sweep_bars : i-sweep_bars]
    #   recent = bars[i-sweep_bars : i+1]   (sweep_bars+1 bars, INCLUDING current)).
    ref = df.iloc[n - lookback - sweep_bars - 1: n - sweep_bars - 1]
    recent = df.iloc[n - sweep_bars - 1: n]
    if ref.empty or recent.empty or not _bars_sane(ref) or not _bars_sane(recent):
        return None

    swing_low = float(ref["low"].min())
    swing_high = float(ref["high"].max())
    recent_low = float(recent["low"].min())
    recent_high = float(recent["high"].max())
    current_close = float(df["close"].iloc[-1])

    if not all(_finite(v) for v in (swing_low, swing_high, recent_low,
                                    recent_high, current_close)):
        return None
    if swing_high <= swing_low:
        return None

    if side == "long":
        if recent_low < swing_low and current_close > swing_low:
            return "bullish_sweep", swing_low, swing_low, swing_high
    elif side == "short":
        if recent_high > swing_high and current_close < swing_high:
            return "bearish_sweep", swing_high, swing_low, swing_high
    return None


def detect_fvg(df: pd.DataFrame) -> Optional[tuple[float, float, str]]:
    """Detect a 3-candle Fair Value Gap ending at the latest bar.

    Mirrors ``tjr_backtest.detect_fvg`` on the last three candles (c1, c2, c3):

      - Bullish FVG: ``c1.high < c3.low``  -> gap = (c1.high, c3.low)
      - Bearish FVG: ``c1.low  > c3.high`` -> gap = (c3.high, c1.low)

    Returns ``(gap_low, gap_high, 'bullish'|'bearish')`` or ``None``.
    """
    if len(df) < 3:
        return None
    c1 = df.iloc[-3]
    c3 = df.iloc[-1]
    c1_high, c1_low = float(c1["high"]), float(c1["low"])
    c3_high, c3_low = float(c3["high"]), float(c3["low"])
    if not all(_finite(v) for v in (c1_high, c1_low, c3_high, c3_low)):
        return None

    if c1_high < c3_low:                       # bullish imbalance below price
        return c1_high, c3_low, "bullish"
    if c1_low > c3_high:                        # bearish imbalance above price
        return c3_high, c1_low, "bearish"
    return None


def detect_order_block(df: pd.DataFrame, direction: str,
                       ob_bars: int = 6) -> Optional[tuple[float, float]]:
    """Find the Order Block range for ``direction`` near the latest bar.

    Per TJR spec Step 4 the OB is the *last opposing candle* before the strong
    move:

      - Bullish OB (direction='long'):  the most recent DOWN candle
        (``close < open``) in the recent window — price returns to its range.
      - Bearish OB (direction='short'): the most recent UP candle
        (``close > open``).

    The back-scan window is config-driven (``cfg.ob_bars``, default 6 bars) so it
    is no longer a hardcoded constant. Bars whose OHLC is non-finite or violates
    ``high >= low`` / positivity (see :func:`_row_sane`) are skipped, so a
    malformed-but-finite feed cannot produce a nonsensical OB range.

    Returns ``(ob_low, ob_high)`` of that candle, or ``None`` if none found.

    NOTE (2026-07-08): the OB may now TRIGGER a setup on its own, exactly as the
    FVG does, when no direction-matched FVG is present -- ``generate_signal`` no
    longer requires an FVG (that requirement demoted to confirm-only). When both
    are present the reason is annotated ``fvg+ob``.
    """
    if len(df) < 2 or ob_bars < 2:
        return None
    # Scan backwards over a bounded, config-sized window (excluding the current
    # bar) for the last opposing candle. O(ob_bars).
    window = df.iloc[-ob_bars:]
    rows = list(window.itertuples(index=False))
    # Drop the final (current) bar so the OB is the candle *before* the move.
    for row in reversed(rows[:-1]):
        o, h, l, c = float(row.open), float(row.high), float(row.low), float(row.close)
        if not _row_sane(o, h, l, c):
            continue
        if direction == "long" and c < o:       # last bearish candle
            return l, h
        if direction == "short" and c > o:       # last bullish candle
            return l, h
    return None


def ote_zone(swing_hi: float, swing_lo: float, side: str) -> tuple[float, float]:
    """Return the OTE (Optimal Trade Entry) price band: 61.8%-79% retracement.

    Per TJR spec Step 5. For a LONG, the retrace is measured down from the swing
    high; for a SHORT, up from the swing low.

      - long:  band = [swing_hi - 0.79*R, swing_hi - 0.618*R]
      - short: band = [swing_lo + 0.618*R, swing_lo + 0.79*R]

    where ``R = swing_hi - swing_lo``. Returns ``(band_low, band_high)`` with
    ``band_low <= band_high``.
    """
    rng = swing_hi - swing_lo
    if side == "long":
        lo = swing_hi - 0.79 * rng
        hi = swing_hi - 0.618 * rng
    else:  # short
        lo = swing_lo + 0.618 * rng
        hi = swing_lo + 0.79 * rng
    return (min(lo, hi), max(lo, hi))


def in_ote_zone(price: float, swing_lo: float, swing_hi: float, direction: str,
                ote_low: float = 0.618, ote_high: float = 0.79) -> bool:
    """Return True if ``price`` sits inside the 61.8%-79% fib retracement band."""
    if not all(_finite(v) for v in (price, swing_lo, swing_hi)):
        return False
    rng = swing_hi - swing_lo
    if rng <= 0:
        return False
    if direction == "long":
        band_lo = swing_hi - ote_high * rng
        band_hi = swing_hi - ote_low * rng
    else:
        band_lo = swing_lo + ote_low * rng
        band_hi = swing_lo + ote_high * rng
    return band_lo <= price <= band_hi


def msb_confirmed(df_1m: pd.DataFrame, side: str, msb_bars: int = 5) -> bool:
    """Confirm a 1M Market Structure Break by a CLOSED candle beyond the swing.

    Mirrors ``tjr_backtest.detect_msb`` on the latest 1M bar:

      - long:  current close > highest high of the prior ``msb_bars`` bars
      - short: current close < lowest  low  of the prior ``msb_bars`` bars

    Uses the CONFIRMED close of the final bar (anticipated/unclosed breaks do not
    qualify — TJR spec Step 6).
    """
    n = len(df_1m)
    if n < msb_bars + 1:
        return False
    look = df_1m.iloc[n - msb_bars - 1: n - 1]   # the msb_bars bars before current
    current_close = float(df_1m["close"].iloc[-1])
    if not _finite(current_close) or look.empty:
        return False
    if side == "long":
        return current_close > float(look["high"].max())
    if side == "short":
        return current_close < float(look["low"].min())
    return False


# ── Public entry point (LOCKED §3.1) ─────────────────────────────────────────
def generate_signal(
    bars_by_tf: dict[str, pd.DataFrame],
    now_et: datetime,
    *,
    instrument: str = "ES",
    config: Optional[StrategyConfig] = None,
) -> Optional[dict]:
    """Run the TJR/ICT Kill Zone checklist and return a Signal dict or ``None``.

    PURE: does not read files, open sockets, mutate inputs, place orders, or log.

    Parameters
    ----------
    bars_by_tf:
        ``{'1h': df, '15m': df, '5m': df, '1m': df}`` — each value a pandas
        DataFrame indexed by a tz-aware ET ``DatetimeIndex`` with lowercase
        columns ``['open','high','low','close','volume']``. HTF bias uses
        ``'1h'`` (falls back to ``'4h'`` if present); setup uses ``'5m'``; entry
        trigger uses ``'1m'``. A missing required timeframe -> ``None``.
    now_et:
        tz-aware ET ``datetime`` for the evaluation instant (kill-zone gate +
        signal ``ts``).
    instrument:
        One of ``'ES'|'NQ'|'MES'|'MNQ'``. The mandate allowlist is enforced by
        ``risk_guard`` — the strategy only uses it for the tick-size stop offset
        and to stamp the signal.
    config:
        Optional :class:`StrategyConfig`. Defaults used if ``None``.

    Returns
    -------
    Signal ``dict`` (see module docstring / §2) with all price fields finite and
    ``side`` in ``('long','short')``, or ``None`` when no setup exists.
    """
    cfg = config or StrategyConfig()

    # Step 0a — fail closed on a non-ET / naive instant. ``ts`` MUST be ET with a
    # real offset (LOCKED §2: "Never emit a naive timestamp"), and the kill-zone
    # gate is only meaningful on an ET wall clock. A naive datetime has no defined
    # offset, so there is no safe interpretation: return None instead of guessing.
    now_et_norm = _as_et(now_et)
    if now_et_norm is None:
        return None

    # Step 0b — required data present and well-formed.
    df_htf = _get_tf(bars_by_tf, ("1h", "4h"))
    df_5m = _get_tf(bars_by_tf, ("5m",))
    df_1m = _get_tf(bars_by_tf, ("1m",))
    if df_htf is None or df_5m is None or df_1m is None:
        return None

    # Step 1 — Kill-zone time gate (no trades outside the window).
    if not in_kill_zone(now_et_norm, cfg.kill_zones):
        return None

    # Step 1b — Late-entry filter: reject if we are past max_minutes_in_kz into
    # the active kill zone. Setups triggered >75 min after session open tend to
    # chase already-extended moves where the sweep liquidity has been consumed
    # and the reversal lacks follow-through. Loss analysis confirmed 2 of 3
    # stopped trades were entered at 125–135 min into NY open.
    if cfg.max_minutes_in_kz > 0:
        mins_in = minutes_into_kill_zone(now_et_norm, cfg.kill_zones)
        if mins_in is not None and mins_in > cfg.max_minutes_in_kz:
            return None

    # Step 2 — HTF bias via discount/premium of the swing range.
    side = htf_bias(df_htf, cfg.lookback)
    if side not in ("long", "short"):
        return None

    # Step 3 — Liquidity sweep on the correct side (sell-side for long, buy-side
    # for short). Captures both the swept level (for stop placement) AND the swing
    # of the swept reference window (for the OTE band — TJR Step 5).
    sweep = find_liquidity_sweep(df_5m, side, cfg.lookback, cfg.sweep_bars)
    if sweep is None:
        return None
    _sweep_kind, sweep_level, swept_lo, swept_hi = sweep

    # Step 4 — Entry zone: EITHER a direction-matched 3-candle FVG OR a
    # direction-matched Order Block may trigger (2026-07-08: FVG demoted from a
    # hard "must be present" requirement to confirm-only, mirroring the role the
    # OB always had -- an OB-only path is now valid, same as an FVG-only path).
    # When both are present the reason is annotated 'fvg+ob'.
    zone = _direction_matched_zone(df_5m, side, cfg)
    if zone is None:
        return None
    zone_lo, zone_hi, zone_kind = zone

    # Step 5 — OTE: the FVG/OB zone must sit inside the 61.8%-79% retracement of
    # the SWING THAT WAS SWEPT (the find_liquidity_sweep reference window), not
    # the full last-``lookback`` 5M range (which would include the recent sweep
    # dip and shift the band). This enforces the TJR Step 5 spec precisely.
    zone_mid = (zone_lo + zone_hi) / 2.0
    if not in_ote_zone(zone_mid, swept_lo, swept_hi, side, cfg.ote_low, cfg.ote_high):
        return None

    # Step 6 — 1M MSB confirmation: a CLOSED 1M candle beyond the last 1M swing.
    if not msb_confirmed(df_1m, side, cfg.msb_bars):
        return None

    # ── All checks passed: build the order geometry ──────────────────────────
    # Price-sanity on the exact bars that set entry (1M signal bar) and stop
    # (sweep_level, derived from the 5M ref window). A finite-but-malformed feed
    # (high < low, non-positive prices) must NOT yield a finite-but-nonsensical
    # signal; this is the strategy-side complement to risk_guard/traderpost
    # validate-before-send.
    sig_bar = df_1m.iloc[-1]
    if not _row_sane(float(sig_bar["open"]), float(sig_bar["high"]),
                     float(sig_bar["low"]), float(sig_bar["close"])):
        return None
    if not (_finite(sweep_level) and sweep_level > 0):
        return None

    entry = float(df_1m["close"].iloc[-1])   # confirmed 1M close = entry ref
    if not _finite(entry) or entry <= 0:
        return None

    tick = _TICK_SIZE.get(instrument, _DEFAULT_TICK)

    # Step 7 — Protective stop beyond the sweep (sweep ±1 tick), but CAPPED at the
    # spec's max risk per contract (max_stop_points = $300/contract = 6 pts ES) and
    # floored at stop_ticks. A sweep far from entry would otherwise risk many times
    # the win target; tightening to the cap keeps the loss BOUNDED and R:R real.
    floor = cfg.stop_ticks * tick
    cap = cfg.max_stop_points
    if side == "long":
        stop = sweep_level - tick
        stop = max(stop, entry - cap)      # never risk more than the cap
        stop = min(stop, entry - floor)    # never tighter than the floor
        if stop >= entry:
            return None                    # sweep on the wrong side -> no order
    else:
        stop = sweep_level + tick
        stop = min(stop, entry + cap)
        stop = max(stop, entry + floor)
        if stop <= entry:
            return None
    if not _finite(stop) or stop <= 0:
        return None                        # degenerate stop -> no order

    # Step 8 — R = the ACTUAL (capped) stop distance, so TP1/TP2 are a real 2R/4R
    # relative to what we actually risk (not a disconnected fixed number).
    risk = abs(entry - stop)
    if not _finite(risk) or risk <= 0:
        return None                        # degenerate R -> no order
    if side == "long":
        tp1 = entry + cfg.tp1_rr * risk
        tp2 = entry + cfg.tp2_rr * risk
    else:
        tp1 = entry - cfg.tp1_rr * risk
        tp2 = entry - cfg.tp2_rr * risk

    if not all(_finite(v) for v in (tp1, tp2)):
        return None

    # Stamp the ET timestamp. ``now_et_norm`` is guaranteed tz-aware ET here
    # (Step 0a), so this is non-None; guard anyway so a naive ts can never ship.
    ts = _iso_et(now_et_norm)
    if ts is None:
        return None

    reason = (
        f"kill_zone={','.join(cfg.kill_zones)}; htf_bias={side}; "
        f"{_sweep_kind}@{sweep_level:g}; {zone_kind}_in_ote; msb_1m_confirmed"
    )

    return {
        "side": side,
        "instrument": instrument,
        "entry": float(entry),
        "stop": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "size": int(cfg.default_contracts),
        "reason": reason,
        "ts": ts,
    }


# ── Private utilities ────────────────────────────────────────────────────────
def _finite(x: float) -> bool:
    """True if ``x`` is a finite real number (not NaN/inf/None)."""
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _row_sane(o: float, h: float, l: float, c: float) -> bool:
    """True if a single OHLC bar is finite, positive, and ordered (high>=low).

    Guards against a malformed-but-finite feed (e.g. high < low, a zero/negative
    price) producing a finite-but-nonsensical signal. Also requires open/close to
    sit within [low, high].
    """
    if not all(_finite(v) for v in (o, h, l, c)):
        return False
    if not (o > 0 and h > 0 and l > 0 and c > 0):
        return False
    if h < l:
        return False
    if not (l <= o <= h and l <= c <= h):
        return False
    return True


def _bars_sane(window: pd.DataFrame) -> bool:
    """Vectorized price-sanity over a window: all bars finite, positive, ordered."""
    try:
        o = window["open"].astype(float)
        h = window["high"].astype(float)
        l = window["low"].astype(float)
        c = window["close"].astype(float)
    except (KeyError, ValueError, TypeError):
        return False
    import numpy as np
    cols = (o, h, l, c)
    if not all(np.isfinite(s.to_numpy()).all() for s in cols):
        return False
    if not all((s.to_numpy() > 0).all() for s in cols):
        return False
    if (h.to_numpy() < l.to_numpy()).any():
        return False
    if ((o.to_numpy() < l.to_numpy()) | (o.to_numpy() > h.to_numpy())).any():
        return False
    if ((c.to_numpy() < l.to_numpy()) | (c.to_numpy() > h.to_numpy())).any():
        return False
    return True


def _get_tf(bars_by_tf: dict[str, pd.DataFrame],
            keys: tuple[str, ...]) -> Optional[pd.DataFrame]:
    """Return the first present, non-empty, well-formed DataFrame among ``keys``."""
    if not isinstance(bars_by_tf, dict):
        return None
    for k in keys:
        df = bars_by_tf.get(k)
        if isinstance(df, pd.DataFrame) and not df.empty \
                and all(col in df.columns for col in _OHLC):
            return df
    return None


def _direction_matched_zone(
    df_5m: pd.DataFrame, side: str, cfg: StrategyConfig
) -> Optional[tuple[float, float, str]]:
    """Resolve the entry zone, matched to ``side``. FVG is now confirm-only.

    2026-07-08 change: previously a direction-matched 3-candle FVG was the ONLY
    thing that could trigger a trade (mirroring ``tjr_backtest.py``'s
    ``if not fvg: continue``), and the Order Block was confirm-only. That made
    the FVG a hard blocker among 6 simultaneous required conditions, and the
    live bot fired effectively zero real signals. FVG is now OPTIONAL/CONFIRMING,
    exactly mirroring the role the OB always had: EITHER a direction-matched FVG
    OR a direction-matched Order Block may trigger the setup. All other ICT
    conditions (kill-zone timing, HTF bias, liquidity sweep, OTE band, 1M MSB)
    are unchanged hard requirements enforced elsewhere in ``generate_signal``.

    Returns ``(zone_low, zone_high, kind)`` where ``kind`` is:
      - ``'fvg+ob'``  — both a direction-matched FVG and OB are present (uses the
        FVG's price range; strongest confirmation).
      - ``'fvg'``     — only a direction-matched FVG is present.
      - ``'ob'``      — only a direction-matched Order Block is present (the new
        OB-only trigger path).
    Returns ``None`` only when NEITHER a direction-matched FVG NOR a
    direction-matched OB is present.
    """
    fvg = detect_fvg(df_5m)
    fvg_zone = None
    if fvg is not None:
        lo, hi, kind = fvg
        if (side == "long" and kind == "bullish") or (side == "short" and kind == "bearish"):
            fvg_zone = (lo, hi)            # direction-matched FVG available (may trigger)

    ob = detect_order_block(df_5m, side, cfg.ob_bars)   # already direction-matched internally

    if fvg_zone is not None and ob is not None:
        return fvg_zone[0], fvg_zone[1], "fvg+ob"
    if fvg_zone is not None:
        return fvg_zone[0], fvg_zone[1], "fvg"
    if ob is not None:
        return ob[0], ob[1], "ob"          # OB-only trigger path (new: FVG no longer required)
    return None                            # neither FVG nor OB direction-matched -> no setup


def _iso_et(now_et: datetime) -> Optional[str]:
    """ISO-8601 ET timestamp WITH offset, or ``None`` if ``now_et`` is naive.

    The LOCKED §2 schema requires "never emit a naive timestamp into the signal".
    ``generate_signal`` already fails closed (returns None) on a naive/non-ET
    instant via :func:`_as_et`, so in production this only ever receives a
    tz-aware ET datetime. We still fail closed here (return ``None``) on a naive
    value rather than stamp an offset-less timestamp, so the invariant holds even
    if this helper is called directly.
    """
    if now_et.tzinfo is None:
        return None
    return now_et.astimezone(_ET).isoformat(timespec="seconds")


# ── __main__ smoke test (synthetic bars; no I/O, no network) ─────────────────
def _synthetic_bars_by_tf() -> tuple[dict[str, pd.DataFrame], datetime]:
    """Hand-built bars that satisfy the full LONG checklist, for the smoke test.

    Construction:
      - 1h: a clear downswing so the latest close sits in *discount* (< 50% of
        the swing range) -> long bias.
      - 5m: a 20+ bar base, then a 3-bar sweep that pokes BELOW the base low and
        closes back above it, with a final 3-candle bullish FVG whose midpoint
        lands in the 61.8%-79% OTE band of the setup swing.
      - 1m: prior bars capped low, final bar closes ABOVE them (MSB up).
    """
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    now_et = datetime(2026, 6, 21, 9, 42, 0, tzinfo=et)  # inside NY-Open kill zone

    def mk(rows: list[tuple], freq: str, end: datetime) -> pd.DataFrame:
        idx = pd.date_range(end=end, periods=len(rows), freq=freq, tz=et)
        df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
        df["volume"] = 1000
        return df

    # 1h: descending closes -> latest close in discount.
    htf_rows = []
    px = 4800.0
    for _ in range(24):
        htf_rows.append((px, px + 2, px - 6, px - 5))
        px -= 5
    df_1h = mk(htf_rows, "1h", now_et)

    # 5m setup. With lookback=20, sweep_bars=3 the helper evaluates at i=n-1 with
    #   ref    = iloc[n-24 : n-4]   (the base, defines the swing low/high)
    #   recent = iloc[n-4  : n]     (the last 4 bars, must dip below the swing low)
    # We build 22 base bars then 4 final bars (a dip bar + a 3-candle bullish FVG)
    # so the dip lands strictly inside `recent` and the FVG midpoint sits inside
    # the 61.8%-79% OTE band of the base swing.
    #
    # Base swing: low=4700, high=4760  => R=60. Long OTE band of the swing =
    #   [4760 - 0.79*60, 4760 - 0.618*60] = [4712.6, 4722.92].
    base_low, base_high = 4700.0, 4760.0
    rows_5m: list[tuple] = []
    for _ in range(22):                       # reference base (defines swing lo/hi)
        rows_5m.append((4730.0, base_high, base_low, 4730.0))
    # recent window (last 4 bars): dip below 4700, then a 3-candle bullish FVG
    # (c1.high < c3.low) whose midpoint ~4718 lands inside the OTE band above.
    rows_5m.append((4716.0, 4717.0, 4694.0, 4715.0))   # dip bar: sweeps below 4700
    rows_5m.append((4715.0, 4716.0, 4714.0, 4715.5))   # c1 : high = 4716
    rows_5m.append((4716.0, 4722.0, 4716.0, 4721.0))   # c2
    rows_5m.append((4721.0, 4725.0, 4720.0, 4724.0))   # c3 : low(4720) > c1.high(4716)
    df_5m = mk(rows_5m, "5min", now_et)
    # FVG = (c1.high=4716, c3.low=4720); midpoint 4718 in [4712.6, 4722.92]. OK.

    # 1m trigger: prior bars capped at ~4705, final bar closes above -> MSB up.
    rows_1m: list[tuple] = []
    for _ in range(6):
        rows_1m.append((4704.0, 4705.0, 4703.0, 4704.5))
    rows_1m.append((4705.0, 4710.0, 4704.5, 4709.0))   # close 4709 > 4705 swing high
    df_1m = mk(rows_1m, "1min", now_et)

    return {"1h": df_1h, "5m": df_5m, "1m": df_1m}, now_et


def _smoke_test() -> int:
    """Run a deterministic smoke test on synthetic bars. Returns process exit code."""
    print("=" * 64)
    print("  strategy.py smoke test — TJR/ICT Lucid 25K")
    print("=" * 64)

    bars, now_et = _synthetic_bars_by_tf()

    # Sub-checks (helper-level), so a failure points at the right step.
    assert in_kill_zone(now_et, ("ny_open",)) is True, "kill-zone gate failed"
    assert htf_bias(bars["1h"]) == "long", "expected LONG htf bias (discount)"
    sweep = find_liquidity_sweep(bars["5m"], "long")
    assert sweep is not None and sweep[0] == "bullish_sweep", "expected bullish sweep"
    # Sweep now also returns the swept reference-window swing (low, high) so OTE is
    # measured off the swept swing (TJR Step 5), not the full last-lookback range.
    assert len(sweep) == 4, "sweep must return (kind, level, ref_low, ref_high)"
    _k, _lvl, ref_lo, ref_hi = sweep
    assert math.isclose(ref_lo, 4700.0) and math.isclose(ref_hi, 4760.0), \
        f"swept ref swing should be (4700, 4760), got ({ref_lo}, {ref_hi})"
    fvg = detect_fvg(bars["5m"])
    assert fvg is not None and fvg[2] == "bullish", "expected bullish FVG"
    assert msb_confirmed(bars["1m"], "long") is True, "expected 1M MSB up"
    print("[ok] helpers: kill-zone, htf_bias=long, bullish sweep, bullish FVG, MSB up")

    sig = generate_signal(bars, now_et, instrument="ES")
    assert sig is not None, "generate_signal returned None on a valid LONG setup"
    assert sig["side"] == "long", f"side={sig['side']}"
    assert sig["instrument"] == "ES"
    for k in ("entry", "stop", "tp1", "tp2"):
        assert _finite(sig[k]), f"{k} not finite: {sig[k]!r}"
    assert sig["stop"] != sig["entry"], "stop must differ from entry"
    assert sig["stop"] < sig["entry"], "long stop must sit below entry (beyond sweep)"
    # R is the ACTUAL (capped) stop distance: floored at stop_ticks, capped at
    # max_stop_points ($300/contract on ES). Targets are a real 2R/4R off that.
    cfg = StrategyConfig()
    r = abs(sig["entry"] - sig["stop"])
    floor = cfg.stop_ticks * _TICK_SIZE["ES"]
    assert floor - 1e-9 <= r <= cfg.max_stop_points + 1e-9, f"R {r} outside [{floor}, {cfg.max_stop_points}]"
    assert math.isclose(sig["tp1"], sig["entry"] + cfg.tp1_rr * r, rel_tol=1e-9), "tp1 != entry+2R"
    assert math.isclose(sig["tp2"], sig["entry"] + cfg.tp2_rr * r, rel_tol=1e-9), "tp2 != entry+4R"
    assert sig["size"] >= 1
    assert "T" in sig["ts"] and ("+" in sig["ts"] or "-" in sig["ts"]), \
        f"ts not ISO-8601 ET with offset: {sig['ts']}"
    print("[ok] generate_signal -> valid LONG signal")
    for k, v in sig.items():
        print(f"       {k:>11}: {v}")

    # Negative: outside the kill zone -> None.
    from zoneinfo import ZoneInfo
    off_hours = now_et.replace(hour=20, tzinfo=ZoneInfo("America/New_York"))
    assert generate_signal(bars, off_hours, instrument="ES") is None, \
        "expected None outside kill zone"
    print("[ok] outside kill zone -> None")

    # Negative: missing required timeframe -> None.
    assert generate_signal({"1h": bars["1h"], "5m": bars["5m"]}, now_et) is None, \
        "expected None when 1m timeframe is missing"
    print("[ok] missing 1m timeframe -> None")

    # Negative: a NAIVE datetime must fail closed (no naive ts may ever ship).
    naive = now_et.replace(tzinfo=None)
    assert generate_signal(bars, naive, instrument="ES") is None, \
        "expected None on a naive (offset-less) datetime"
    assert _iso_et(naive) is None, "_iso_et must return None on a naive datetime"
    assert in_kill_zone(naive, ("ny_open",)) is False, \
        "in_kill_zone must fail closed on a naive datetime"
    print("[ok] naive datetime -> None (no naive ts)")

    # Non-ET tz-aware: 09:42 UTC is 05:42 ET (outside ny_open) -> None, proving the
    # kill-zone gate evaluates the correct ET wall clock, not the raw .time().
    from datetime import timezone
    utc_0942 = now_et.astimezone(timezone.utc).replace(hour=9, minute=42)
    assert generate_signal(bars, utc_0942, instrument="ES") is None, \
        "expected None: 09:42 UTC is 05:42 ET, outside the NY-Open kill zone"
    # And the equivalent ET instant of a UTC-tagged 13:42 (== 09:42 ET) DOES fire,
    # with a correctly ET-offset ts.
    utc_for_0942_et = now_et.astimezone(timezone.utc)  # same instant as 09:42 ET
    sig_utc = generate_signal(bars, utc_for_0942_et, instrument="ES")
    assert sig_utc is not None and sig_utc["ts"].endswith("-04:00"), \
        f"expected an ET-offset ts from a UTC-tagged instant, got {sig_utc}"
    print("[ok] non-ET tz-aware converted to ET wall clock")

    # Negative: a malformed-but-finite 1M signal bar (high < low) -> None.
    import copy
    bad = {k: v.copy(deep=True) for k, v in bars.items()}
    bad["1m"].iloc[-1, bad["1m"].columns.get_loc("high")] = 4700.0  # high < low(4704.5)
    assert generate_signal(bad, now_et, instrument="ES") is None, \
        "expected None on a malformed (high<low) 1M signal bar"
    print("[ok] malformed-but-finite bar -> None")

    # Purity: inputs must be unmutated.
    bars2, now2 = _synthetic_bars_by_tf()
    snap = {k: v.copy(deep=True) for k, v in bars2.items()}
    generate_signal(bars2, now2, instrument="ES")
    for k in bars2:
        assert bars2[k].equals(snap[k]), f"input DataFrame {k} was mutated"
    print("[ok] pure: input DataFrames not mutated")

    print("-" * 64)
    print("ALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_smoke_test())
