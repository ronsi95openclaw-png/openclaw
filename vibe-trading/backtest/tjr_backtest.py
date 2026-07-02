#!/usr/bin/env python3
"""
TJR Lucid 25K Backtester
========================
Applies the TJR ICT Kill Zone strategy against NinjaTrader-exported 5M OHLC CSV.

Strategy logic:
  1. Filter to kill zone windows only (default: NY Open 08:30–11:00 ET)
  2. Detect liquidity sweeps (prior swing high/low violation + close-back)
  3. Identify Fair Value Gaps (3-candle imbalance) after the sweep
  4. Simulate entry at FVG midpoint on the next bar open
  5. Exit: TP1 at 2R (50% of position), TP2 at 4R, or SL below sweep low
  6. Enforce ALL Lucid 25K mandate rules

Usage:
  python tjr_backtest.py --csv vibe-trading/backtest/data/ES_5M.csv
  python tjr_backtest.py --csv data/ES_5M.csv --instrument ES --year 2024

NinjaTrader CSV format (no header, or header row starting with "Date"):
  Date,Time,Open,High,Low,Close,Volume
  20240101,083000,4750.25,4752.00,4748.50,4751.00,12500
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

# ── Lucid 25K Mandate (mirrors lucid_mandate.json) ───────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent   # vibe-trading/
MANDATE_FILE = BASE_DIR / "lucid_mandate.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

def load_mandate() -> dict:
    if MANDATE_FILE.exists():
        with open(MANDATE_FILE) as f:
            return json.load(f)
    # Fallback defaults (matches known Lucid 25K eval rules)
    return {
        "rules": {
            "account_size": 25000,
            "max_loss_limit": 1500,
            "consistency_rule_eval": 0.50,
            "overnight_holds": False,
            "instruments_allowed": ["ES", "MES", "NQ", "MNQ"],
            "max_position_size": 2,
            "daily_trade_cap": 10,
        },
        "mode": "paper"
    }

# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class Bar:
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    session_date: date = field(init=False)

    def __post_init__(self):
        # Trading day: bars before ~18:00 belong to this calendar date
        self.session_date = self.dt.date()

@dataclass
class Trade:
    bar_idx: int
    entry_dt: datetime
    direction: str          # "long" or "short"
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    contracts: int
    instrument: str
    tick_value: float
    point_value: float
    # Filled in on exit
    exit_dt: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    pnl: float = 0.0
    r_multiple: float = 0.0
    status: str = "open"    # "open", "tp1", "tp2", "sl", "eod"
    tp1_hit: bool = False
    tp1_pnl: float = 0.0

    @property
    def risk_per_contract(self) -> float:
        return abs(self.entry_price - self.stop_price) * self.point_value

    @property
    def risk_total(self) -> float:
        return self.risk_per_contract * self.contracts

@dataclass
class DayStats:
    session_date: date
    trades: list = field(default_factory=list)
    pnl: float = 0.0
    trade_count: int = 0
    stopped_by_daily_gate: bool = False
    stopped_by_consistency: bool = False
    stopped_by_trade_cap: bool = False

# ── Instrument Config ──────────────────────────────────────────────────────────

INSTRUMENTS = {
    "ES":  {"tick": 0.25, "tick_value": 12.50, "point_value": 50.0},
    "MES": {"tick": 0.25, "tick_value": 1.25,  "point_value": 5.0},
    "NQ":  {"tick": 0.25, "tick_value": 5.00,  "point_value": 20.0},
    "MNQ": {"tick": 0.25, "tick_value": 0.50,  "point_value": 2.0},
}

# ── Kill Zone Config ───────────────────────────────────────────────────────────

KILL_ZONES = {
    "ny_open":     (time(8, 30),  time(11, 0)),
    "london_open": (time(2, 0),   time(5, 0)),
    "ny_pm":       (time(13, 30), time(16, 0)),
}

EOD_CLOSE_TIME = time(15, 55)   # Flatten all positions by this time

# ── CSV Parser ────────────────────────────────────────────────────────────────

_ET_ZONE = None
try:
    from zoneinfo import ZoneInfo as _ZI
    _ET_ZONE = _ZI("America/New_York")
except Exception:
    pass


def parse_csv(filepath: str) -> list[Bar]:
    """
    Parse 5M OHLCV CSV. Accepts three formats:

    Format A — NinjaTrader headerless:
      20240101,083000,4750.25,4752.00,4748.50,4751.00,12500

    Format B — NinjaTrader with header:
      Date,Time,Open,High,Low,Close,Volume

    Format C — yfinance (header required):
      Datetime,open,high,low,close,volume
      2026-06-21 18:00:00-04:00,7543.25,...
    """
    bars = []
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {filepath}")

    # Detect format from header row
    is_yfinance = False
    with open(path, newline="", encoding="utf-8") as f:
        first = f.readline().strip().lower()
        if first.startswith("datetime"):
            is_yfinance = True

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            h = row[0].strip().lower()
            if h in ("date", "datetime", ""):
                continue  # skip header

            try:
                if is_yfinance:
                    # Datetime,open,high,low,close,volume  (tz-aware ISO)
                    from datetime import timezone
                    dt_raw = row[0].strip()
                    # fromisoformat handles "2026-06-21 18:00:00-04:00"
                    dt = datetime.fromisoformat(dt_raw)
                    if dt.tzinfo is not None and _ET_ZONE is not None:
                        dt = dt.astimezone(_ET_ZONE).replace(tzinfo=None)
                    elif dt.tzinfo is not None:
                        # fallback: strip tz
                        dt = dt.replace(tzinfo=None)
                    bar = Bar(
                        dt=dt,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=int(float(row[5])) if len(row) > 5 else 0,
                    )
                else:
                    # NinjaTrader: Date,Time,Open,High,Low,Close,Volume
                    date_str = row[0].strip()
                    time_str = row[1].strip().zfill(6)
                    dt = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
                    bar = Bar(
                        dt=dt,
                        open=float(row[2]),
                        high=float(row[3]),
                        low=float(row[4]),
                        close=float(row[5]),
                        volume=int(float(row[6])) if len(row) > 6 else 0,
                    )
                bars.append(bar)
            except (ValueError, IndexError):
                continue   # skip malformed rows

    bars.sort(key=lambda b: b.dt)
    print(f"Loaded {len(bars):,} bars from {path.name} "
          f"({bars[0].dt.date() if bars else '?'} -> {bars[-1].dt.date() if bars else '?'})")
    return bars

# ── Strategy Logic ────────────────────────────────────────────────────────────

def is_in_kill_zone(dt: datetime, zones: list[str] = ["ny_open"]) -> bool:
    t = dt.time()
    for zone in zones:
        lo, hi = KILL_ZONES[zone]
        if lo <= t < hi:
            return True
    return False

def detect_fvg(bars: list[Bar], i: int) -> Optional[tuple[float, float, str]]:
    """
    Detect a Fair Value Gap ending at bar i (3-candle pattern: i-2, i-1, i).
    Returns (fvg_low, fvg_high, "bullish"/"bearish") or None.

    Bullish FVG: bar[i-2].high < bar[i].low  → gap between high of 2 bars ago and low of current
    Bearish FVG: bar[i-2].low > bar[i].high  → gap between low of 2 bars ago and high of current
    """
    if i < 2:
        return None
    b0, b2 = bars[i - 2], bars[i]

    # Bullish FVG (price ran up leaving an imbalance below)
    if b0.high < b2.low:
        return (b0.high, b2.low, "bullish")

    # Bearish FVG (price ran down leaving an imbalance above)
    if b0.low > b2.high:
        return (b2.high, b0.low, "bearish")

    return None

def detect_liquidity_sweep(bars: list[Bar], i: int,
                            lookback: int = 20, sweep_bars: int = 3) -> Optional[str]:
    """
    Detect if price has recently swept a swing high/low and is reversing.
    Returns "bullish_sweep" (swept lows, now reversing up),
            "bearish_sweep" (swept highs, now reversing down), or None.

    Bullish sweep: recent LOW dipped below swing_low (lookback), but CURRENT CLOSE is above swing_low.
    Bearish sweep: recent HIGH exceeded swing_high (lookback), but CURRENT CLOSE is below swing_high.
    """
    if i < lookback + sweep_bars:
        return None

    ref_bars = bars[i - lookback - sweep_bars: i - sweep_bars]
    recent_bars = bars[i - sweep_bars: i + 1]
    current = bars[i]

    swing_low = min(b.low for b in ref_bars)
    swing_high = max(b.high for b in ref_bars)

    recent_low = min(b.low for b in recent_bars)
    recent_high = max(b.high for b in recent_bars)

    # Bullish sweep: dipped below swing_low, now closed back above it
    if recent_low < swing_low and current.close > swing_low:
        return "bullish_sweep"

    # Bearish sweep: spiked above swing_high, now closed back below it
    if recent_high > swing_high and current.close < swing_high:
        return "bearish_sweep"

    return None

def detect_msb(bars: list[Bar], i: int, direction: str, msb_bars: int = 5) -> bool:
    """
    Detect Market Structure Break (MSB) on LTF.
    Long MSB: current close is above the last swing high in the past msb_bars bars.
    Short MSB: current close is below the last swing low in the past msb_bars bars.
    """
    if i < msb_bars:
        return False

    look = bars[i - msb_bars: i]
    current = bars[i]

    if direction == "long":
        recent_swing_high = max(b.high for b in look)
        return current.close > recent_swing_high

    if direction == "short":
        recent_swing_low = min(b.low for b in look)
        return current.close < recent_swing_low

    return False

# ── Core Engine ────────────────────────────────────────────────────────────────

class TJRBacktester:
    def __init__(
        self,
        instrument: str = "ES",
        contracts: int = 1,
        stop_ticks: int = 8,         # 8 ticks = 2 points on ES
        tp1_rr: float = 2.0,
        tp2_rr: float = 4.0,
        kill_zones: list[str] = None,
        commission: float = 4.50,    # per contract round-trip
        lookback: int = 20,
        sweep_bars: int = 3,
        active_year: Optional[int] = None,
    ):
        self.mandate = load_mandate()
        rules = self.mandate["rules"]

        assert instrument in rules["instruments_allowed"], \
            f"{instrument} not in allowed instruments: {rules['instruments_allowed']}"
        assert contracts <= rules["max_position_size"], \
            f"Contracts {contracts} exceeds mandate max {rules['max_position_size']}"

        self.instrument = instrument
        self.contracts = contracts
        self.inst_cfg = INSTRUMENTS[instrument]
        self.tick_size = self.inst_cfg["tick"]
        self.tick_value = self.inst_cfg["tick_value"]
        self.point_value = self.inst_cfg["point_value"]

        self.stop_ticks = stop_ticks
        self.stop_points = stop_ticks * self.tick_size
        self.tp1_rr = tp1_rr
        self.tp2_rr = tp2_rr
        self.tp1_points = self.stop_points * tp1_rr
        self.tp2_points = self.stop_points * tp2_rr
        self.commission = commission

        self.kill_zones = kill_zones or ["ny_open"]
        self.lookback = lookback
        self.sweep_bars = sweep_bars
        self.active_year = active_year

        # Mandate limits
        self.account_size = rules["account_size"]
        self.max_loss_limit = rules["max_loss_limit"]
        self.consistency_cap = rules["consistency_rule_eval"]
        self.daily_trade_cap = rules["daily_trade_cap"]
        self.daily_gate_pct = 0.80   # self-imposed: stop at 80% of max loss

        # State
        self.trades: list[Trade] = []
        self.open_trade: Optional[Trade] = None
        self.day_stats: dict[date, DayStats] = {}
        self.equity = float(self.account_size)
        self.eval_peak_equity = float(self.account_size)

    # ── Lucid Rule Checks ─────────────────────────────────────────────────────

    def _daily_pnl(self, session_date: date) -> float:
        return self.day_stats.get(session_date, DayStats(session_date)).pnl

    def _total_eval_profit(self) -> float:
        return self.equity - self.account_size

    def _can_trade_today(self, session_date: date, current_dt: datetime) -> tuple[bool, str]:
        """Returns (can_trade, reason_if_not)"""
        day = self.day_stats.get(session_date, DayStats(session_date))

        # EOD check
        if current_dt.time() >= EOD_CLOSE_TIME:
            return False, "eod"

        # Daily trade cap
        if day.trade_count >= self.daily_trade_cap:
            return False, f"daily_trade_cap ({self.daily_trade_cap})"

        # Daily loss gate (80% of Lucid's $1,500 = $1,200)
        daily_loss_gate = self.max_loss_limit * self.daily_gate_pct
        if day.pnl <= -daily_loss_gate:
            return False, f"daily_loss_gate (-${daily_loss_gate:.0f})"

        # Consistency rule — if today's profit already at cap, stop adding
        total_profit = self._total_eval_profit()
        if total_profit > 0 and day.pnl >= total_profit * self.consistency_cap:
            return False, f"consistency_cap (50% of ${total_profit:.0f})"

        # Absolute max loss limit (Lucid's hard EOD drawdown check)
        unrealized = self.open_trade.pnl if self.open_trade else 0
        if (self.equity - self.account_size + unrealized) <= -self.max_loss_limit:
            return False, f"max_loss_limit (-${self.max_loss_limit})"

        return True, ""

    def _ensure_day(self, session_date: date) -> DayStats:
        if session_date not in self.day_stats:
            self.day_stats[session_date] = DayStats(session_date)
        return self.day_stats[session_date]

    # ── Trade Lifecycle ────────────────────────────────────────────────────────

    def _open_trade(self, bar: Bar, bar_idx: int, direction: str,
                    sweep_level: float) -> Optional[Trade]:
        """Attempt to open a new trade."""
        if self.open_trade:
            return None  # Already in a trade (max 1 at a time in this sim)

        session_date = bar.session_date
        can, reason = self._can_trade_today(session_date, bar.dt)
        if not can:
            return None

        entry = bar.open   # enter on next bar's open (after signal bar)

        if direction == "long":
            stop = sweep_level - self.tick_size  # 1 tick below sweep low
            tp1  = entry + self.tp1_points
            tp2  = entry + self.tp2_points
        else:  # short
            stop = sweep_level + self.tick_size  # 1 tick above sweep high
            tp1  = entry - self.tp1_points
            tp2  = entry - self.tp2_points

        trade = Trade(
            bar_idx=bar_idx,
            entry_dt=bar.dt,
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            tp1_price=tp1,
            tp2_price=tp2,
            contracts=self.contracts,
            instrument=self.instrument,
            tick_value=self.tick_value,
            point_value=self.point_value,
        )

        self.open_trade = trade
        day = self._ensure_day(session_date)
        day.trade_count += 1
        return trade

    def _update_open_trade(self, bar: Bar) -> Optional[Trade]:
        """Check if open trade hits TP1, TP2, SL, or needs EOD close."""
        t = self.open_trade
        if not t:
            return None

        closed_trade = None
        session_date = bar.session_date

        # EOD close check
        if bar.dt.time() >= EOD_CLOSE_TIME:
            t.exit_dt = bar.dt
            t.exit_price = bar.close
            t.exit_reason = "eod_close"
            t.status = "eod"
            self._settle_trade(t, session_date)
            closed_trade = t
            self.open_trade = None
            return closed_trade

        if t.direction == "long":
            # SL hit
            if bar.low <= t.stop_price:
                t.exit_dt = bar.dt
                t.exit_price = t.stop_price
                t.exit_reason = "stop_loss"
                t.status = "sl"
                self._settle_trade(t, session_date)
                closed_trade = t
                self.open_trade = None
            # TP1 hit (first)
            elif not t.tp1_hit and bar.high >= t.tp1_price:
                t.tp1_hit = True
                half = t.contracts // 2 or 1
                t.tp1_pnl = (t.tp1_price - t.entry_price) * t.point_value * half \
                             - self.commission * half
                # Move stop to breakeven
                t.stop_price = t.entry_price
                # If only 1 contract, full close at TP1
                if t.contracts == 1:
                    t.exit_dt = bar.dt
                    t.exit_price = t.tp1_price
                    t.exit_reason = "tp1_full"
                    t.status = "tp1"
                    self._settle_trade(t, session_date)
                    closed_trade = t
                    self.open_trade = None
            # TP2 hit (after TP1)
            elif t.tp1_hit and bar.high >= t.tp2_price:
                t.exit_dt = bar.dt
                t.exit_price = t.tp2_price
                t.exit_reason = "tp2"
                t.status = "tp2"
                self._settle_trade(t, session_date)
                closed_trade = t
                self.open_trade = None

        else:  # short
            if bar.high >= t.stop_price:
                t.exit_dt = bar.dt
                t.exit_price = t.stop_price
                t.exit_reason = "stop_loss"
                t.status = "sl"
                self._settle_trade(t, session_date)
                closed_trade = t
                self.open_trade = None
            elif not t.tp1_hit and bar.low <= t.tp1_price:
                t.tp1_hit = True
                half = t.contracts // 2 or 1
                t.tp1_pnl = (t.entry_price - t.tp1_price) * t.point_value * half \
                             - self.commission * half
                t.stop_price = t.entry_price
                if t.contracts == 1:
                    t.exit_dt = bar.dt
                    t.exit_price = t.tp1_price
                    t.exit_reason = "tp1_full"
                    t.status = "tp1"
                    self._settle_trade(t, session_date)
                    closed_trade = t
                    self.open_trade = None
            elif t.tp1_hit and bar.low <= t.tp2_price:
                t.exit_dt = bar.dt
                t.exit_price = t.tp2_price
                t.exit_reason = "tp2"
                t.status = "tp2"
                self._settle_trade(t, session_date)
                closed_trade = t
                self.open_trade = None

        return closed_trade

    def _settle_trade(self, t: Trade, session_date: date):
        """Compute final PnL and update equity/day stats."""
        if t.direction == "long":
            gross_points = t.exit_price - t.entry_price
        else:
            gross_points = t.entry_price - t.exit_price

        # For 2-contract TP2 trades: TP1 pnl already counted for half;
        # remaining half exits at TP2
        if t.contracts >= 2 and t.tp1_hit and t.status == "tp2":
            remaining = t.contracts - (t.contracts // 2)
            t.pnl = t.tp1_pnl + gross_points * t.point_value * remaining \
                    - self.commission * remaining
        elif t.contracts >= 2 and t.tp1_hit and t.status == "sl":
            remaining = t.contracts - (t.contracts // 2)
            t.pnl = t.tp1_pnl + gross_points * t.point_value * remaining \
                    - self.commission * remaining
        else:
            t.pnl = gross_points * t.point_value * t.contracts \
                    - self.commission * t.contracts

        t.r_multiple = gross_points / self.stop_points if self.stop_points > 0 else 0

        self.equity += t.pnl
        self.eval_peak_equity = max(self.eval_peak_equity, self.equity)
        self.trades.append(t)

        day = self._ensure_day(session_date)
        day.pnl += t.pnl
        day.trades.append(t)

    # ── Main Run ──────────────────────────────────────────────────────────────

    def run(self, bars: list[Bar]) -> dict:
        """Run the backtest over the bar list. Returns summary stats."""
        signal_bar_info = {}   # bar_idx → (direction, sweep_level, fvg_info)

        for i, bar in enumerate(bars):
            # Year filter
            if self.active_year and bar.dt.year != self.active_year:
                continue

            session_date = bar.session_date
            self._ensure_day(session_date)

            # ── Update open trade first ───────────────────────────────────────
            if self.open_trade:
                self._update_open_trade(bar)

            # ── Entry signal detection ────────────────────────────────────────
            if not self.open_trade:
                # Kill zone filter
                if not is_in_kill_zone(bar.dt, self.kill_zones):
                    continue

                # Can we trade?
                can, _ = self._can_trade_today(session_date, bar.dt)
                if not can:
                    continue

                # Detect liquidity sweep
                sweep = detect_liquidity_sweep(bars, i, self.lookback, self.sweep_bars)
                if not sweep:
                    continue

                direction = "long" if sweep == "bullish_sweep" else "short"

                # Detect FVG (look at the last 3 bars)
                fvg = detect_fvg(bars, i)
                if not fvg:
                    continue

                fvg_low, fvg_high, fvg_type = fvg

                # FVG direction must match sweep direction
                if direction == "long" and fvg_type != "bullish":
                    continue
                if direction == "short" and fvg_type != "bearish":
                    continue

                # MSB confirmation on LTF (approximate with 5M bars)
                if not detect_msb(bars, i, direction, msb_bars=5):
                    continue

                # Determine sweep level for stop placement
                ref_bars_window = bars[max(0, i - self.lookback - self.sweep_bars):i - self.sweep_bars]
                if direction == "long":
                    sweep_level = min(b.low for b in ref_bars_window) if ref_bars_window else bar.low
                else:
                    sweep_level = max(b.high for b in ref_bars_window) if ref_bars_window else bar.high

                # Schedule entry on NEXT bar (enter on next open)
                signal_bar_info[i] = (direction, sweep_level, fvg)

            # ── Execute pending signal on this bar (the bar after signal) ─────
            if (i - 1) in signal_bar_info and not self.open_trade:
                direction, sweep_level, fvg = signal_bar_info.pop(i - 1)
                can, _ = self._can_trade_today(session_date, bar.dt)
                if can:
                    self._open_trade(bar, i, direction, sweep_level)

        # Close any open trade at end of data
        if self.open_trade:
            last = bars[-1]
            self.open_trade.exit_dt = last.dt
            self.open_trade.exit_price = last.close
            self.open_trade.exit_reason = "data_end"
            self.open_trade.status = "eod"
            self._settle_trade(self.open_trade, last.session_date)
            self.open_trade = None

        return self._build_results()

    # ── Results ───────────────────────────────────────────────────────────────

    def _build_results(self) -> dict:
        if not self.trades:
            return {"error": "No trades generated. Check kill zone filter, data range, and CSV format."}

        winners = [t for t in self.trades if t.pnl > 0]
        losers  = [t for t in self.trades if t.pnl <= 0]
        tp1s    = [t for t in self.trades if t.status in ("tp1", "tp1_full")]
        tp2s    = [t for t in self.trades if t.status == "tp2"]
        sls     = [t for t in self.trades if t.status == "sl"]
        eods    = [t for t in self.trades if t.status == "eod"]

        total_pnl = sum(t.pnl for t in self.trades)
        win_rate = len(winners) / len(self.trades) if self.trades else 0
        avg_win  = sum(t.pnl for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t.pnl for t in losers) / len(losers) if losers else 0
        profit_factor = abs(sum(t.pnl for t in winners) / sum(t.pnl for t in losers)) \
                        if losers and sum(t.pnl for t in losers) != 0 else float("inf")
        avg_r = sum(t.r_multiple for t in self.trades) / len(self.trades)

        # Max drawdown (equity curve)
        eq = float(self.account_size)
        peak = eq
        max_dd = 0.0
        max_dd_pct = 0.0
        for t in self.trades:
            eq += t.pnl
            peak = max(peak, eq)
            dd = peak - eq
            dd_pct = dd / peak
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = dd_pct

        # Consistency rule check — flag days that exceeded 50% of total profits
        consistency_violations = []
        total_profit_running = 0.0
        sorted_days = sorted(self.day_stats.keys())
        for d in sorted_days:
            dp = self.day_stats[d].pnl
            if dp > 0 and total_profit_running > 0:
                if dp / total_profit_running > self.consistency_cap:
                    consistency_violations.append({
                        "date": str(d),
                        "day_pnl": round(dp, 2),
                        "total_at_time": round(total_profit_running, 2),
                        "pct": round(dp / total_profit_running * 100, 1)
                    })
            total_profit_running += dp

        # Lucid 25K eval pass/fail assessment
        lucid_pass = (
            total_pnl >= 1500                    # need $1,500 profit target (6%)
            and max_dd < self.max_loss_limit     # never exceeded drawdown limit
            and len(consistency_violations) == 0  # no consistency violations
        )

        results = {
            "strategy": "TJR ICT Kill Zone — Lucid 25K Edition",
            "instrument": self.instrument,
            "contracts": self.contracts,
            "period": {
                "first_trade": str(self.trades[0].entry_dt.date()) if self.trades else None,
                "last_trade":  str(self.trades[-1].entry_dt.date()) if self.trades else None,
                "trading_days": len(self.day_stats),
            },
            "performance": {
                "total_trades": len(self.trades),
                "winners": len(winners),
                "losers": len(losers),
                "win_rate_pct": round(win_rate * 100, 1),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "profit_factor": round(profit_factor, 2),
                "avg_r_multiple": round(avg_r, 2),
                "total_pnl": round(total_pnl, 2),
                "tp1_exits": len(tp1s),
                "tp2_exits": len(tp2s),
                "sl_exits": len(sls),
                "eod_exits": len(eods),
            },
            "risk": {
                "max_drawdown_dollar": round(max_dd, 2),
                "max_drawdown_pct": round(max_dd_pct * 100, 2),
                "stop_ticks": self.stop_ticks,
                "stop_points": self.stop_points,
                "risk_per_trade": round(self.stop_points * self.point_value * self.contracts, 2),
            },
            "lucid_compliance": {
                "max_loss_limit": self.max_loss_limit,
                "hit_max_loss": max_dd >= self.max_loss_limit,
                "consistency_violations": consistency_violations,
                "estimated_pass": lucid_pass,
                "profit_target_met": total_pnl >= 1500,
            },
            "equity": {
                "start": self.account_size,
                "end": round(self.equity, 2),
                "net_change": round(self.equity - self.account_size, 2),
            }
        }

        return results

# ── Daily Breakdown Report ────────────────────────────────────────────────────

def print_daily_breakdown(bt: TJRBacktester):
    print("\n── Daily Breakdown ──────────────────────────────────────────────────")
    print(f"{'Date':<12} {'Trades':>6} {'P&L':>10} {'Running':>10}  Notes")
    print("-" * 65)
    running = 0.0
    for d in sorted(bt.day_stats.keys()):
        day = bt.day_stats[d]
        running += day.pnl
        notes = []
        if day.stopped_by_daily_gate:
            notes.append("DAILY GATE HIT")
        if day.stopped_by_consistency:
            notes.append("CONSISTENCY CAP")
        pnl_str = f"+${day.pnl:.2f}" if day.pnl >= 0 else f"-${abs(day.pnl):.2f}"
        run_str = f"+${running:.2f}" if running >= 0 else f"-${abs(running):.2f}"
        print(f"{str(d):<12} {day.trade_count:>6} {pnl_str:>10} {run_str:>10}  {', '.join(notes)}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TJR Lucid 25K Backtester")
    parser.add_argument("--csv", required=True, help="Path to NinjaTrader 5M CSV")
    parser.add_argument("--instrument", default="ES", choices=list(INSTRUMENTS.keys()))
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--stop-ticks", type=int, default=8,
                        help="Stop loss in ticks (default 8 = 2 points on ES)")
    parser.add_argument("--tp1-rr", type=float, default=2.0, help="TP1 R:R ratio")
    parser.add_argument("--tp2-rr", type=float, default=4.0, help="TP2 R:R ratio")
    parser.add_argument("--kill-zones", nargs="+", default=["ny_open"],
                        choices=list(KILL_ZONES.keys()),
                        help="Kill zones to trade (default: ny_open)")
    parser.add_argument("--year", type=int, default=None,
                        help="Filter to a specific year only")
    parser.add_argument("--daily", action="store_true",
                        help="Print daily P&L breakdown")
    args = parser.parse_args()

    # Load data
    bars = parse_csv(args.csv)
    if not bars:
        print("ERROR: No bars parsed. Check CSV format.")
        sys.exit(1)

    # Run backtest
    bt = TJRBacktester(
        instrument=args.instrument,
        contracts=args.contracts,
        stop_ticks=args.stop_ticks,
        tp1_rr=args.tp1_rr,
        tp2_rr=args.tp2_rr,
        kill_zones=args.kill_zones,
        active_year=args.year,
    )
    results = bt.run(bars)

    # Print results
    print("\n" + "=" * 65)
    print(f"  {results.get('strategy', 'TJR Backtest')}")
    print("=" * 65)

    if "error" in results:
        print(f"ERROR: {results['error']}")
        sys.exit(1)

    p = results["performance"]
    r = results["risk"]
    l = results["lucid_compliance"]
    e = results["equity"]

    print(f"\n📊 PERFORMANCE ({results['period']['first_trade']} → {results['period']['last_trade']})")
    print(f"  Total trades:    {p['total_trades']}  ({p['tp1_exits']} TP1 · {p['tp2_exits']} TP2 · {p['sl_exits']} SL · {p['eod_exits']} EOD)")
    print(f"  Win rate:        {p['win_rate_pct']}%")
    print(f"  Avg win:         ${p['avg_win']:.2f}")
    print(f"  Avg loss:        ${p['avg_loss']:.2f}")
    print(f"  Profit factor:   {p['profit_factor']:.2f}")
    print(f"  Avg R:           {p['avg_r_multiple']:.2f}R")
    print(f"  Net P&L:         ${e['net_change']:.2f}")

    print(f"\n📉 RISK")
    print(f"  Risk per trade:  ${r['risk_per_trade']:.2f} ({r['stop_ticks']} ticks · {r['stop_points']} pts)")
    print(f"  Max drawdown:    ${r['max_drawdown_dollar']:.2f} ({r['max_drawdown_pct']:.1f}%)")

    print(f"\n🏦 LUCID 25K COMPLIANCE")
    print(f"  Max loss limit:  ${l['max_loss_limit']}")
    print(f"  Hit max loss?    {'⛔ YES' if l['hit_max_loss'] else '✅ No'}")
    net_chg = e["net_change"]
    profit_str = "✅ Met ($1,500)" if l["profit_target_met"] else f"❌ Not met (${net_chg:.0f})"
    print(f"  Profit target:   {profit_str}")
    if l["consistency_violations"]:
        print(f"  Consistency:     ⚠️  {len(l['consistency_violations'])} violation(s)")
        for v in l["consistency_violations"][:5]:
            print(f"                   {v['date']}: ${v['day_pnl']:.0f} = {v['pct']}% of ${v['total_at_time']:.0f}")
    else:
        print(f"  Consistency:     ✅ No violations")
    status = "✅ ESTIMATED PASS" if l["estimated_pass"] else "❌ ESTIMATED FAIL"
    print(f"\n  Eval result:     {status}")

    # Daily breakdown
    if args.daily:
        print_daily_breakdown(bt)

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = RESULTS_DIR / f"tjr_{args.instrument}_{ts}.json"
    with open(outfile, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Results saved -> {outfile}")


if __name__ == "__main__":
    main()
