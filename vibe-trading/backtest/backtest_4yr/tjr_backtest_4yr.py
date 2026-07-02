#!/usr/bin/env python3
"""
tjr_backtest_4yr.py -- TJR/ICT Strategy on 4-Year yfinance Data
===============================================================
Adapts the TJR ICT Kill Zone strategy to work on:
  * Daily (1D) bars  -- uses swing sweeps, no time filter
  * 1-Hour (1H) bars -- uses NY Open + London Open kill zones

Strategy logic (same core as tjr_backtest.py):
  1. Kill zone filter (1H only; daily: any session)
  2. Liquidity sweep detection (prior swing high/low violated -> close-back)
  3. Fair Value Gap (3-candle imbalance) in same direction as sweep
  4. Market Structure Break confirmation
  5. Entry on next bar open, SL below/above sweep level
  6. TP1 at 2R (move stop to BE), TP2 at 4R (full exit for all contract counts)
  7. Enforce Lucid 25K mandate rules

v2 changes:
  - MNQ_1H stop_ticks: 150 -> 80 (20pt stop, $40 risk)
  - TP1 no longer closes single-contract trades; moves stop to BE, holds for TP2
  - Consistency cap enforced LIVE at 45% (was 50% post-hoc only)
  - daily_loss_limit kwarg added (hard per-day stop in dollars)

Data format (yfinance CSV):
  Datetime,Open,High,Low,Close,Volume
  2022-06-16 09:30:00-04:00,3975.25,...
"""
from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

try:
    import pandas as pd
except ImportError:
    pd = None   # Pandas optional; we fall back to CSV reader

# -- Lucid 25K Mandate --------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent   # vibe-trading/
MANDATE_FILE = BASE_DIR / "lucid_mandate.json"

LUCID_DEFAULTS = {
    "account_size": 25_000,
    "max_loss_limit": 1_500,
    "profit_target": 1_500,
    "consistency_rule_eval": 0.50,
    "overnight_holds_allowed": False,
    "instruments_allowed": ["ES", "MES", "NQ", "MNQ"],
    "max_position_size": 2,
    "daily_trade_cap": 10,
}

def load_mandate() -> dict:
    if MANDATE_FILE.exists():
        with open(MANDATE_FILE) as f:
            data = json.load(f)
        return data.get("rules", data)
    return LUCID_DEFAULTS

# -- Instrument Config ---------------------------------------------------------

INSTRUMENTS = {
    "ES":  {"tick": 0.25, "tick_value": 12.50, "point_value": 50.0},
    "MES": {"tick": 0.25, "tick_value": 1.25,  "point_value": 5.0},
    "NQ":  {"tick": 0.25, "tick_value": 5.00,  "point_value": 20.0},
    "MNQ": {"tick": 0.25, "tick_value": 0.50,  "point_value": 2.0},
}

# -- Kill Zones (ET) ----------------------------------------------------------

KILL_ZONES = {
    "ny_open":     (time(8, 30),  time(11, 0)),
    "london_open": (time(2, 0),   time(5, 0)),
    "ny_pm":       (time(13, 30), time(16, 0)),
}

EOD_CLOSE_TIME_1H = time(15, 0)   # Close 1H trades by 3 PM ET
MAX_HOLD_DAYS_1D  = 5             # Daily: close trade after 5 bars max

# -- Data Structures ----------------------------------------------------------

@dataclass
class Bar:
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    session_date: date = field(init=False)

    def __post_init__(self):
        self.session_date = self.dt.date()

@dataclass
class Trade:
    bar_idx: int
    entry_dt: datetime
    direction: str
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    contracts: int
    instrument: str
    tick_value: float
    point_value: float
    timeframe: str
    exit_dt: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    pnl: float = 0.0
    r_multiple: float = 0.0
    status: str = "open"
    tp1_hit: bool = False
    tp1_pnl: float = 0.0
    bars_held: int = 0

    @property
    def risk_per_contract(self) -> float:
        return abs(self.entry_price - self.stop_price) * self.point_value

@dataclass
class DayStats:
    session_date: date
    trades: list = field(default_factory=list)
    pnl: float = 0.0
    trade_count: int = 0

# -- CSV Parser ---------------------------------------------------------------

def parse_yfinance_csv(filepath: Path) -> list:
    """
    Parse yfinance-format CSV.
    Header: Datetime,Open,High,Low,Close,Volume
    The Datetime column may include timezone offset: 2022-06-16 09:30:00-04:00
    """
    bars = []
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = None
        for row in reader:
            if not row:
                continue
            # Header row
            if header is None:
                header = [c.strip().lower() for c in row]
                if "datetime" not in header and "date" not in header:
                    header = ["datetime", "open", "high", "low", "close", "volume"]
                continue

            try:
                dt_raw = row[0].strip()
                dt_str = dt_raw.strip()
                if len(dt_str) >= 19 and dt_str[10] == " ":
                    dt_str = dt_str[:19]
                elif "+" in dt_str:
                    dt_str = dt_str.split("+")[0].strip()

                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(dt_str, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    continue

                bar = Bar(
                    dt=dt,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]) if len(row) > 5 else 0.0,
                )
                bars.append(bar)
            except (ValueError, IndexError):
                continue

    bars.sort(key=lambda b: b.dt)
    if bars:
        print(f"  Loaded {len(bars):,} bars  [{bars[0].dt.date()} -> {bars[-1].dt.date()}]")
    return bars

# -- Strategy Helpers ---------------------------------------------------------

def is_in_kill_zone(dt: datetime, zones: list) -> bool:
    """Check if datetime falls within any kill zone (ET assumed)."""
    t = dt.time()
    for zone in zones:
        lo, hi = KILL_ZONES[zone]
        if lo <= t < hi:
            return True
    return False

def detect_fvg(bars: list, i: int) -> Optional[tuple]:
    """3-candle Fair Value Gap at position i."""
    if i < 2:
        return None
    b0, b2 = bars[i - 2], bars[i]
    if b0.high < b2.low:
        return (b0.high, b2.low, "bullish")
    if b0.low > b2.high:
        return (b2.high, b0.low, "bearish")
    return None

def detect_sweep(bars: list, i: int, lookback: int, sweep_bars: int) -> Optional[str]:
    """
    Detect liquidity sweep + close-back reversal.
    Returns "bullish_sweep", "bearish_sweep", or None.
    """
    if i < lookback + sweep_bars:
        return None
    ref    = bars[i - lookback - sweep_bars: i - sweep_bars]
    recent = bars[i - sweep_bars: i + 1]
    cur    = bars[i]

    swing_low   = min(b.low  for b in ref)
    swing_high  = max(b.high for b in ref)
    recent_low  = min(b.low  for b in recent)
    recent_high = max(b.high for b in recent)

    if recent_low < swing_low and cur.close > swing_low:
        return "bullish_sweep"
    if recent_high > swing_high and cur.close < swing_high:
        return "bearish_sweep"
    return None

def detect_msb(bars: list, i: int, direction: str, msb_bars: int = 5) -> bool:
    """Market Structure Break on local swing."""
    if i < msb_bars:
        return False
    look = bars[i - msb_bars: i]
    cur  = bars[i]
    if direction == "long":
        return cur.close > max(b.high for b in look)
    if direction == "short":
        return cur.close < min(b.low for b in look)
    return False

# -- Core Engine --------------------------------------------------------------

class TJRBacktester4yr:
    """
    TJR/ICT strategy backtester for 4-year yfinance data.
    Supports both daily (1D) and hourly (1H) timeframes.
    """

    def __init__(
        self,
        instrument: str = "ES",
        timeframe: str = "1d",          # "1d" or "1h"
        contracts: int = 1,
        tp1_rr: float = 2.0,
        tp2_rr: float = 4.0,
        commission: float = 4.50,       # per contract round-trip
        kill_zones: list = None,        # 1H only; daily ignores
        lookback: int = None,           # auto-set by timeframe
        sweep_bars: int = None,         # auto-set by timeframe
        msb_bars: int = None,           # auto-set by timeframe
        stop_ticks: int = None,         # override stop in ticks
        daily_loss_limit: Optional[float] = None,  # hard per-day loss cap (e.g. 200)  [v2]
    ):
        rules = load_mandate()

        self.instrument = instrument.upper()
        self.timeframe  = timeframe.lower()
        assert self.instrument in INSTRUMENTS, f"Unknown instrument: {instrument}"

        cfg = INSTRUMENTS[self.instrument]
        self.tick_size   = cfg["tick"]
        self.tick_value  = cfg["tick_value"]
        self.point_value = cfg["point_value"]

        self.contracts  = contracts
        self.tp1_rr     = tp1_rr
        self.tp2_rr     = tp2_rr
        self.commission = commission

        # Timeframe-specific defaults
        if self.timeframe == "1d":
            self.lookback    = lookback   or 10
            self.sweep_bars  = sweep_bars or 3
            self.msb_bars    = msb_bars   or 3
            self.kill_zones  = []          # daily: no time filter
            default_stop_pts = 20.0 if self.instrument in ("ES", "MES") else 80.0
            self.stop_points = (stop_ticks * self.tick_size) if stop_ticks else default_stop_pts
        else:
            # 1H
            self.lookback   = lookback   or 16
            self.sweep_bars = sweep_bars or 3
            self.msb_bars   = msb_bars   or 5
            self.kill_zones = kill_zones or ["ny_open"]
            default_stop_pts = 2.0 if self.instrument in ("ES", "MES") else 8.0
            self.stop_points = (stop_ticks * self.tick_size) if stop_ticks else default_stop_pts

        self.tp1_points = self.stop_points * self.tp1_rr
        self.tp2_points = self.stop_points * self.tp2_rr

        # Mandate
        self.account_size    = rules.get("account_size",          25_000)
        self.max_loss_limit  = rules.get("max_loss_limit",         1_500)
        self.profit_target   = rules.get("profit_target",          1_500)
        self.consistency_cap = rules.get("consistency_rule_eval",   0.50)
        self.daily_trade_cap = rules.get("daily_trade_cap",            10)
        self.daily_gate_pct  = 0.80   # internal: stop at 80% of hard limit
        self.daily_loss_limit = daily_loss_limit  # None = no per-day cap  [v2]

        # State
        self.trades: list = []
        self.open_trade: Optional[Trade] = None
        self.day_stats: dict = {}
        self.equity      = float(self.account_size)
        self.peak_equity = float(self.account_size)

    # -- Rule Checks -----------------------------------------------------------

    def _ensure_day(self, d: date) -> DayStats:
        if d not in self.day_stats:
            self.day_stats[d] = DayStats(d)
        return self.day_stats[d]

    def _can_trade(self, d: date, dt: datetime) -> tuple:
        day = self.day_stats.get(d, DayStats(d))

        # EOD for 1H
        if self.timeframe == "1h" and dt.time() >= EOD_CLOSE_TIME_1H:
            return False, "eod"

        if day.trade_count >= self.daily_trade_cap:
            return False, "daily_trade_cap"

        # Hard per-day loss limit (e.g. $200 for MNQ_1H)  [v2: new]
        if self.daily_loss_limit is not None and day.pnl <= -self.daily_loss_limit:
            return False, "daily_loss_limit"

        gate = self.max_loss_limit * self.daily_gate_pct
        if day.pnl <= -gate:
            return False, "daily_loss_gate"

        # Consistency cap enforced LIVE at 45% (scoring rule is 50%)  [v2: was self.consistency_cap=0.50]
        running_profit = self.equity - self.account_size
        if running_profit > 0 and day.pnl >= running_profit * 0.45:
            return False, "consistency_cap"

        return True, ""

    # -- Trade Lifecycle -------------------------------------------------------

    def _open_trade(self, bar: Bar, bar_idx: int, direction: str,
                    sweep_level: float) -> Optional[Trade]:
        if self.open_trade:
            return None
        can, _ = self._can_trade(bar.session_date, bar.dt)
        if not can:
            return None

        entry = bar.open
        if direction == "long":
            stop = sweep_level - self.tick_size
            tp1  = entry + self.tp1_points
            tp2  = entry + self.tp2_points
        else:
            stop = sweep_level + self.tick_size
            tp1  = entry - self.tp1_points
            tp2  = entry - self.tp2_points

        t = Trade(
            bar_idx=bar_idx, entry_dt=bar.dt, direction=direction,
            entry_price=entry, stop_price=stop, tp1_price=tp1, tp2_price=tp2,
            contracts=self.contracts, instrument=self.instrument,
            tick_value=self.tick_value, point_value=self.point_value,
            timeframe=self.timeframe,
        )
        self.open_trade = t
        day = self._ensure_day(bar.session_date)
        day.trade_count += 1
        return t

    def _update_trade(self, bar: Bar, bar_idx: int) -> Optional[Trade]:
        t = self.open_trade
        if not t:
            return None

        t.bars_held += 1
        d = bar.session_date
        closed = None

        # EOD close (1H only)
        if self.timeframe == "1h" and bar.dt.time() >= EOD_CLOSE_TIME_1H:
            t.exit_dt, t.exit_price, t.exit_reason, t.status = bar.dt, bar.close, "eod_close", "eod"
            self._settle(t, d)
            self.open_trade = None
            return t

        # Max hold (daily)
        if self.timeframe == "1d" and t.bars_held >= MAX_HOLD_DAYS_1D:
            t.exit_dt, t.exit_price, t.exit_reason, t.status = bar.dt, bar.close, "max_hold", "eod"
            self._settle(t, d)
            self.open_trade = None
            return t

        if t.direction == "long":
            if bar.low <= t.stop_price:
                t.exit_dt, t.exit_price, t.exit_reason, t.status = bar.dt, t.stop_price, "stop_loss", "sl"
                self._settle(t, d); self.open_trade = None; return t
            if not t.tp1_hit and bar.high >= t.tp1_price:
                # [v2] TP1 = move stop to BE; all contracts (incl. 1) hold on for TP2
                t.tp1_hit = True
                t.stop_price = t.entry_price   # move to BE
                if t.contracts >= 2:
                    half = t.contracts // 2
                    t.tp1_pnl = (t.tp1_price - t.entry_price) * t.point_value * half - self.commission * half
            # [v2] Use 'if' not 'elif' so TP2 can fire on same bar TP1 is hit
            if t.tp1_hit and bar.high >= t.tp2_price:
                t.exit_dt, t.exit_price, t.exit_reason, t.status = bar.dt, t.tp2_price, "tp2", "tp2"
                self._settle(t, d); self.open_trade = None; return t
        else:
            if bar.high >= t.stop_price:
                t.exit_dt, t.exit_price, t.exit_reason, t.status = bar.dt, t.stop_price, "stop_loss", "sl"
                self._settle(t, d); self.open_trade = None; return t
            if not t.tp1_hit and bar.low <= t.tp1_price:
                # [v2] TP1 = move stop to BE; all contracts (incl. 1) hold on for TP2
                t.tp1_hit = True
                t.stop_price = t.entry_price
                if t.contracts >= 2:
                    half = t.contracts // 2
                    t.tp1_pnl = (t.entry_price - t.tp1_price) * t.point_value * half - self.commission * half
            # [v2] Use 'if' not 'elif' so TP2 can fire on same bar TP1 is hit
            if t.tp1_hit and bar.low <= t.tp2_price:
                t.exit_dt, t.exit_price, t.exit_reason, t.status = bar.dt, t.tp2_price, "tp2", "tp2"
                self._settle(t, d); self.open_trade = None; return t

        return None

    def _settle(self, t: Trade, d: date):
        if t.direction == "long":
            gross_pts = t.exit_price - t.entry_price
        else:
            gross_pts = t.entry_price - t.exit_price

        if t.contracts >= 2 and t.tp1_hit and t.status in ("tp2", "sl"):
            rem = t.contracts - (t.contracts // 2)
            t.pnl = t.tp1_pnl + gross_pts * t.point_value * rem - self.commission * rem
        else:
            t.pnl = gross_pts * t.point_value * t.contracts - self.commission * t.contracts

        t.r_multiple = gross_pts / self.stop_points if self.stop_points else 0

        self.equity += t.pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        self.trades.append(t)

        day = self._ensure_day(d)
        day.pnl += t.pnl
        day.trades.append(t)

    # -- Main Run --------------------------------------------------------------

    def run(self, bars: list) -> dict:
        pending_signal = {}   # bar_idx -> (dir, sweep_lvl, fvg)

        for i, bar in enumerate(bars):
            d = bar.session_date
            self._ensure_day(d)

            # Update open trade first
            if self.open_trade:
                self._update_trade(bar, i)

            # Check for new signal
            if not self.open_trade:
                # Kill zone filter (1H only)
                if self.kill_zones and not is_in_kill_zone(bar.dt, self.kill_zones):
                    pass
                else:
                    can, _ = self._can_trade(d, bar.dt)
                    if can:
                        sweep = detect_sweep(bars, i, self.lookback, self.sweep_bars)
                        if sweep:
                            direction = "long" if sweep == "bullish_sweep" else "short"
                            fvg = detect_fvg(bars, i)
                            if fvg:
                                fvg_low, fvg_high, fvg_type = fvg
                                dir_ok = (direction == "long" and fvg_type == "bullish") or \
                                         (direction == "short" and fvg_type == "bearish")
                                if dir_ok and detect_msb(bars, i, direction, self.msb_bars):
                                    ref_slice = bars[max(0, i - self.lookback - self.sweep_bars): i - self.sweep_bars]
                                    if direction == "long":
                                        sweep_lvl = min(b.low for b in ref_slice) if ref_slice else bar.low
                                    else:
                                        sweep_lvl = max(b.high for b in ref_slice) if ref_slice else bar.high
                                    pending_signal[i] = (direction, sweep_lvl, fvg)

            # Execute pending signal on next bar
            if (i - 1) in pending_signal and not self.open_trade:
                direction, sweep_lvl, fvg = pending_signal.pop(i - 1)
                can, _ = self._can_trade(d, bar.dt)
                if can:
                    self._open_trade(bar, i, direction, sweep_lvl)

        # Force-close at end of data
        if self.open_trade:
            last = bars[-1]
            t = self.open_trade
            t.exit_dt, t.exit_price, t.exit_reason, t.status = last.dt, last.close, "data_end", "eod"
            self._settle(t, last.session_date)
            self.open_trade = None

        return self._build_results()

    # -- Results ---------------------------------------------------------------

    def _build_results(self) -> dict:
        if not self.trades:
            return {
                "error": "No trades generated",
                "instrument": self.instrument,
                "timeframe": self.timeframe,
                "hint": "Check kill zone filter, lookback, and data coverage.",
            }

        winners = [t for t in self.trades if t.pnl > 0]
        losers  = [t for t in self.trades if t.pnl <= 0]
        tp1s    = [t for t in self.trades if t.status in ("tp1", "tp1_full")]
        tp2s    = [t for t in self.trades if t.status == "tp2"]
        sls     = [t for t in self.trades if t.status == "sl"]
        eods    = [t for t in self.trades if t.status == "eod"]

        total_pnl    = sum(t.pnl for t in self.trades)
        win_rate     = len(winners) / len(self.trades)
        avg_win      = sum(t.pnl for t in winners) / len(winners) if winners else 0.0
        avg_loss     = sum(t.pnl for t in losers)  / len(losers)  if losers  else 0.0
        gross_profit = sum(t.pnl for t in winners)
        gross_loss   = abs(sum(t.pnl for t in losers))
        pf           = gross_profit / gross_loss if gross_loss else float("inf")
        avg_r        = sum(t.r_multiple for t in self.trades) / len(self.trades)

        # Max drawdown
        eq, peak, max_dd = float(self.account_size), float(self.account_size), 0.0
        for t in self.trades:
            eq   += t.pnl
            peak  = max(peak, eq)
            max_dd = max(max_dd, peak - eq)
        max_dd_pct = max_dd / self.account_size * 100

        # Consistency violations (post-hoc, still uses 50% scoring rule)
        cv_list = []
        running = 0.0
        for d in sorted(self.day_stats):
            dp = self.day_stats[d].pnl
            if dp > 0 and running > 0:
                if dp / running > self.consistency_cap:
                    cv_list.append({"date": str(d), "day_pnl": round(dp, 2),
                                    "total_at_time": round(running, 2),
                                    "pct": round(dp / running * 100, 1)})
            running += dp

        lucid_pass = (
            total_pnl >= self.profit_target
            and max_dd < self.max_loss_limit
            and len(cv_list) == 0
        )

        return {
            "strategy": f"TJR ICT -- {self.instrument} {self.timeframe.upper()} -- 4yr v2",
            "instrument": self.instrument,
            "timeframe": self.timeframe,
            "contracts": self.contracts,
            "period": {
                "first_bar":    str(self.trades[0].entry_dt.date()),
                "last_bar":     str(self.trades[-1].entry_dt.date()),
                "trading_days": len(self.day_stats),
            },
            "performance": {
                "total_trades": len(self.trades),
                "winners": len(winners), "losers": len(losers),
                "win_rate_pct": round(win_rate * 100, 1),
                "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
                "profit_factor": round(pf, 2),
                "avg_r_multiple": round(avg_r, 2),
                "total_pnl": round(total_pnl, 2),
                "tp1_exits": len(tp1s), "tp2_exits": len(tp2s),
                "sl_exits": len(sls), "eod_exits": len(eods),
            },
            "risk": {
                "max_drawdown_dollar": round(max_dd, 2),
                "max_drawdown_pct": round(max_dd_pct, 2),
                "stop_points": self.stop_points,
                "risk_per_trade": round(self.stop_points * self.point_value * self.contracts, 2),
                "daily_loss_limit": self.daily_loss_limit,
            },
            "lucid_compliance": {
                "max_loss_limit": self.max_loss_limit,
                "profit_target": self.profit_target,
                "profit_target_met": total_pnl >= self.profit_target,
                "hit_max_loss": max_dd >= self.max_loss_limit,
                "consistency_violations": len(cv_list),
                "cv_details": cv_list[:5],
                "estimated_pass": lucid_pass,
            },
            "equity": {
                "start": self.account_size,
                "end": round(self.equity, 2),
                "net_change": round(self.equity - self.account_size, 2),
            },
        }


# -- Public API ---------------------------------------------------------------

def run_backtest(
    csv_path: Path,
    instrument: str = "ES",
    timeframe: str = "1d",
    contracts: int = 1,
    **kwargs,
) -> dict:
    """
    Load yfinance CSV, run the TJR backtest, return results dict.
    Used by run_all.py.
    """
    bars = parse_yfinance_csv(csv_path)
    if not bars:
        return {"error": f"No bars parsed from {csv_path}", "instrument": instrument, "timeframe": timeframe}

    bt = TJRBacktester4yr(
        instrument=instrument,
        timeframe=timeframe,
        contracts=contracts,
        **kwargs,
    )
    return bt.run(bars)


# -- CLI ----------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="TJR 4yr Backtester")
    parser.add_argument("--csv",        required=True)
    parser.add_argument("--instrument", default="ES", choices=list(INSTRUMENTS))
    parser.add_argument("--timeframe",  default="1d", choices=["1d", "1h"])
    parser.add_argument("--contracts",  type=int, default=1)
    args = parser.parse_args()

    results = run_backtest(Path(args.csv), args.instrument, args.timeframe, args.contracts)

    if "error" in results:
        print(f"ERROR: {results['error']}")
        sys.exit(1)

    p  = results["performance"]
    r  = results["risk"]
    lc = results["lucid_compliance"]
    e  = results["equity"]

    print(f"\n{'='*65}")
    print(f"  {results['strategy']}")
    print(f"{'='*65}")
    print(f"  Trades:        {p['total_trades']}  (TP1:{p['tp1_exits']} TP2:{p['tp2_exits']} SL:{p['sl_exits']} EOD:{p['eod_exits']})")
    print(f"  Win rate:      {p['win_rate_pct']}%")
    print(f"  Profit factor: {p['profit_factor']:.2f}")
    print(f"  Avg R:         {p['avg_r_multiple']:.2f}R")
    print(f"  Net P&L:       ${e['net_change']:.2f}")
    print(f"  Max drawdown:  ${r['max_drawdown_dollar']:.2f}  ({r['max_drawdown_pct']:.1f}%)")
    passed = "PASS" if lc["estimated_pass"] else "FAIL"
    print(f"  Lucid 25K:     {passed}")


if __name__ == "__main__":
    main()
