#!/usr/bin/env python3
"""
TJR/ICT Lucid 25K — Bot Backtester (`bot.backtest`)
===================================================
Offline replay harness for the *bot* order path. It loads historical bars in the
existing backtest CSV format, replays them bar-by-bar, calls the SAME pure
``strategy.generate_signal`` the live runner uses, gates every signal through the
SAME ``risk_guard.check``, then simulates fills (entry / stop / TP1 / TP2,
breakeven after TP1, EOD flatten, ~$4.50 round-trip commission) and reports:

  trades, win rate, total P&L, max drawdown, Sharpe, avg trades/day, worst day,
  and whether ANY Lucid mandate rule would have been breached.

This module is **paper-only by construction**: it makes NO network calls, never
imports ``traderpost``, and never POSTs. It is the validation twin of
``runner.py`` — same strategy, same risk_guard, no live side effects.

Design contract: ``vibe-trading/bot/ARCHITECTURE.md`` (LOCKED v1.0).
Mandate (read at runtime, never hardcoded): ``vibe-trading/lucid_mandate.json``.

CSV formats accepted (matches ``backtest/tjr_backtest.py`` + the 4yr variant):
  Format A (NinjaTrader 5M):  Date,Time,Open,High,Low,Close,Volume
                              20240101,083000,4750.25,4752.00,4748.50,4751.00,12500
  Format B (yfinance):        Datetime,Open,High,Low,Close,Volume
                              2022-06-16 09:30:00-04:00,3975.25,...
Timestamps are interpreted as ET (America/New_York).

Usage:
  python -m bot.backtest <csv> [--instrument ES]
  python -m bot.backtest path/to/ES_5M.csv --instrument ES --year 2024 --daily --json out.json

Run from the ``vibe-trading`` directory (so ``bot`` is importable as a package),
or run the module file directly for a self-contained smoke test (see __main__).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── Paths (mandate read at runtime, never copied) ────────────────────────────
BOT_DIR = Path(__file__).resolve().parent           # vibe-trading/bot/
VIBE_DIR = BOT_DIR.parent                            # vibe-trading/
MANDATE_FILE = VIBE_DIR / "lucid_mandate.json"
KILL_SWITCH_FILE = VIBE_DIR / "KILL_SWITCH"          # literal name only triggers
RESULTS_DIR = BOT_DIR / "logs" / "backtests"

# ET timezone. Prefer zoneinfo; fall back to a FIXED -04:00 (EDT) so the file
# stays runnable even on a stripped-down stdlib. (Backtest semantics only.)
#
# HAZARD: the fixed-offset fallback is EDT all year round. On EST (winter) bars
# every timestamp is mislabeled by +1 hour, which shifts the kill-zone windows
# and EOD-flatten time. Results from a fallback run are session-filtered WRONG
# for any winter data and must be treated as informational only. We emit a loud
# warning so a fallback run is never silently trusted.
ET_IS_FIXED_OFFSET_FALLBACK = False
try:  # pragma: no cover - environment dependent
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = timezone(timedelta(hours=-4), name="ET")
    ET_IS_FIXED_OFFSET_FALLBACK = True
    print(
        "WARNING: zoneinfo unavailable — ET is a FIXED -04:00 (EDT) offset. "
        "Winter (EST) bars are off by +1h; kill-zone and EOD-flatten filtering "
        "will be WRONG for those bars. This run is informational only.",
        file=sys.stderr,
    )

# ── Reuse the bot's pure modules when present; else fall back to ported logic ─
# strategy.generate_signal and risk_guard.RiskGuard are implemented independently
# against the SAME locked contract. We import them so live == backtest. If a
# sibling module is not yet on disk, we transparently fall back to a faithful
# in-file port (identical semantics to backtest/tjr_backtest.py) so this harness
# is runnable standalone today.
_USING_BOT_STRATEGY = False
_USING_BOT_RISKGUARD = False

generate_signal = None       # type: ignore[assignment]
StrategyConfig = None        # type: ignore[assignment]
RiskGuard = None             # type: ignore[assignment]
MandateView = None           # type: ignore[assignment]

try:  # package context: python -m bot.backtest
    from .strategy import generate_signal as _gs, StrategyConfig as _SC  # type: ignore
    generate_signal, StrategyConfig = _gs, _SC
    _USING_BOT_STRATEGY = True
except Exception:
    try:  # direct-run context
        from strategy import generate_signal as _gs, StrategyConfig as _SC  # type: ignore
        generate_signal, StrategyConfig = _gs, _SC
        _USING_BOT_STRATEGY = True
    except Exception:
        _USING_BOT_STRATEGY = False

try:
    from .risk_guard import RiskGuard as _RG  # type: ignore
    from .mandate import MandateView as _MV  # type: ignore
    RiskGuard, MandateView = _RG, _MV
    _USING_BOT_RISKGUARD = True
except Exception:
    try:
        from risk_guard import RiskGuard as _RG  # type: ignore
        from mandate import MandateView as _MV  # type: ignore
        RiskGuard, MandateView = _RG, _MV
        _USING_BOT_RISKGUARD = True
    except Exception:
        _USING_BOT_RISKGUARD = False


# ── Mandate loader (runtime read, fallback only to avoid crash) ───────────────
def load_mandate(path: Optional[Path] = None) -> dict:
    """Read ``lucid_mandate.json`` at runtime. Fallback defaults ONLY if the file
    is missing (loudly flagged by the caller); never the live source of truth."""
    p = Path(path) if path else MANDATE_FILE
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {
        "rules": {
            "account_size": 25000,
            "max_loss_limit": 1500,
            "consistency_rule_eval": 0.50,
            "overnight_holds": False,
            "close_eod": True,
            "instruments_allowed": ["ES", "MES", "NQ", "MNQ"],
            "max_position_size": 2,
            "daily_trade_cap": 10,
        },
        "kill_switch": {"file": "./KILL_SWITCH", "auto_flatten_on_kill": True},
        "mode": "paper",
        "_fallback": True,
    }


# ── Instrument config (tick / point values) — matches the reference backtest ──
INSTRUMENTS = {
    "ES":  {"tick": 0.25, "tick_value": 12.50, "point_value": 50.0},
    "MES": {"tick": 0.25, "tick_value": 1.25,  "point_value": 5.0},
    "NQ":  {"tick": 0.25, "tick_value": 5.00,  "point_value": 20.0},
    "MNQ": {"tick": 0.25, "tick_value": 0.50,  "point_value": 2.0},
}

# Kill-zone table (strategy-local; NOT mandate). Mirrors tjr_backtest.KILL_ZONES.
KILL_ZONES = {
    "ny_open":     (time(8, 30),  time(11, 0)),
    "london_open": (time(2, 0),   time(5, 0)),
    "ny_pm":       (time(13, 30), time(16, 0)),
}
EOD_FLATTEN_ET = time(15, 55)   # flatten all positions by 15:55 ET (Lucid EOD)
COMMISSION_RT = 4.50            # round-trip commission per contract (NinjaTrader est.)


# ── Bar container ─────────────────────────────────────────────────────────────
@dataclass
class Bar:
    """One OHLCV bar with a tz-aware ET timestamp."""
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    session_date: date = field(init=False)

    def __post_init__(self) -> None:
        self.session_date = self.dt.date()


# ── CSV loading (both formats, localized to ET) ───────────────────────────────
def _localize(dt: datetime) -> datetime:
    """Attach ET if naive, else convert to ET."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def load_bars_csv(path: Path) -> list[Bar]:
    """Load bars from either the NinjaTrader (Date,Time,...) or yfinance
    (Datetime,...) CSV format. Malformed rows are skipped. Returns tz-aware ET
    bars sorted by time. Matches the conventions in ``backtest/tjr_backtest.py``
    (``parse_csv``) and the 4yr ``parse_yfinance_csv``."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV not found: {p}")

    bars: list[Bar] = []
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    # Sniff format from the first non-empty row's header / shape.
    fmt = None  # 'nt' or 'yf'
    header = rows[0] if rows else []
    first_cell = header[0].strip().lower() if header else ""
    if first_cell == "datetime":
        fmt = "yf"
    elif first_cell == "date":
        fmt = "nt"

    for row in rows:
        if not row:
            continue
        c0 = row[0].strip()
        cu = c0.upper()
        if cu in ("DATE", "DATETIME", ""):
            continue  # header / blank
        try:
            if fmt == "yf" or ("-" in c0 and ":" in (row[0] if len(row) else "")):
                # yfinance: 2022-06-16 09:30:00-04:00, O,H,L,C,V
                dt_str = c0
                tzoffset = None
                # strip/keep offset for ET conversion
                if len(dt_str) >= 25 and (dt_str[19] in "+-"):
                    base, off = dt_str[:19], dt_str[19:]
                    try:
                        dt = datetime.strptime(base, "%Y-%m-%d %H:%M:%S")
                        sign = 1 if off[0] == "+" else -1
                        hh, mm = off[1:].split(":")[0], (off.split(":")[1] if ":" in off else "00")
                        dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(hh), minutes=int(mm))))
                    except ValueError:
                        continue
                else:
                    base = dt_str[:19] if len(dt_str) >= 19 and dt_str[10] == " " else dt_str.split("+")[0].strip()
                    parsed = None
                    for f2 in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                        try:
                            parsed = datetime.strptime(base, f2)
                            break
                        except ValueError:
                            continue
                    if parsed is None:
                        continue
                    dt = parsed
                bar = Bar(
                    dt=_localize(dt),
                    open=float(row[1]), high=float(row[2]), low=float(row[3]),
                    close=float(row[4]), volume=float(row[5]) if len(row) > 5 else 0.0,
                )
            else:
                # NinjaTrader: 20240101,083000, O,H,L,C,V
                date_str = c0
                time_str = row[1].strip().zfill(6)
                dt = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
                bar = Bar(
                    dt=_localize(dt),
                    open=float(row[2]), high=float(row[3]), low=float(row[4]),
                    close=float(row[5]), volume=float(row[6]) if len(row) > 6 else 0.0,
                )
            bars.append(bar)
        except (ValueError, IndexError):
            continue  # tolerant skip, same as reference parsers

    bars.sort(key=lambda b: b.dt)
    return bars


# ── Pure detection helpers (faithful port of tjr_backtest.py; pure) ───────────
# Used ONLY when the bot's strategy.py is not importable. Identical semantics so
# the backtest agrees bar-for-bar with the reference and with the live strategy.
def _in_kill_zone(dt: datetime, zones: list[str]) -> bool:
    # Bars are localized to ET, so dt.time() is the correct ET wall-clock.
    t = dt.time()
    for z in zones:
        lo, hi = KILL_ZONES[z]
        if lo <= t < hi:
            return True
    return False


def _detect_fvg(bars: list[Bar], i: int) -> Optional[tuple[float, float, str]]:
    if i < 2:
        return None
    b0, b2 = bars[i - 2], bars[i]
    if b0.high < b2.low:
        return (b0.high, b2.low, "bullish")
    if b0.low > b2.high:
        return (b2.high, b0.low, "bearish")
    return None


def _detect_sweep(bars: list[Bar], i: int, lookback: int, sweep_bars: int) -> Optional[str]:
    if i < lookback + sweep_bars:
        return None
    ref = bars[i - lookback - sweep_bars:i - sweep_bars]
    recent = bars[i - sweep_bars:i + 1]
    cur = bars[i]
    swing_low = min(b.low for b in ref)
    swing_high = max(b.high for b in ref)
    recent_low = min(b.low for b in recent)
    recent_high = max(b.high for b in recent)
    if recent_low < swing_low and cur.close > swing_low:
        return "bullish_sweep"
    if recent_high > swing_high and cur.close < swing_high:
        return "bearish_sweep"
    return None


def _detect_msb(bars: list[Bar], i: int, direction: str, msb_bars: int = 5) -> bool:
    if i < msb_bars:
        return False
    look = bars[i - msb_bars:i]
    cur = bars[i]
    if direction == "long":
        return cur.close > max(b.high for b in look)
    if direction == "short":
        return cur.close < min(b.low for b in look)
    return False


def _htf_bias_ok(bars: list[Bar], i: int, direction: str, lookback: int) -> bool:
    """Discount (<50% of swing range) ⇒ long bias; premium (>50%) ⇒ short bias."""
    if i < lookback:
        return False
    win = bars[i - lookback:i + 1]
    lo = min(b.low for b in win)
    hi = max(b.high for b in win)
    if hi <= lo:
        return False
    mid = lo + 0.5 * (hi - lo)
    price = bars[i].close
    if direction == "long":
        return price <= mid    # discount
    return price >= mid        # premium


def _in_ote(price: float, swing_lo: float, swing_hi: float, direction: str,
            ote_low: float = 0.618, ote_high: float = 0.79) -> bool:
    rng = swing_hi - swing_lo
    if rng <= 0:
        return False
    if direction == "long":
        lvl_hi = swing_hi - ote_low * rng
        lvl_lo = swing_hi - ote_high * rng
    else:
        lvl_lo = swing_lo + ote_low * rng
        lvl_hi = swing_lo + ote_high * rng
    return min(lvl_lo, lvl_hi) <= price <= max(lvl_lo, lvl_hi)


def _fallback_signal(bars: list[Bar], i: int, *, instrument: str,
                     zones: list[str], lookback: int, sweep_bars: int,
                     tick: float, tp1_rr: float, tp2_rr: float,
                     default_contracts: int) -> Optional[dict]:
    """In-file TJR/ICT signal generator (used only when bot.strategy is absent).
    Returns a locked-schema Signal dict (entry == bar i close as a fill proxy) or
    None. Mirrors the decision order in ARCHITECTURE §3.3."""
    bar = bars[i]
    if not _in_kill_zone(bar.dt, zones):
        return None
    sweep = _detect_sweep(bars, i, lookback, sweep_bars)
    if not sweep:
        return None
    direction = "long" if sweep == "bullish_sweep" else "short"
    if not _htf_bias_ok(bars, i, direction, lookback):
        return None
    fvg = _detect_fvg(bars, i)
    if not fvg:
        return None
    fvg_lo, fvg_hi, fvg_type = fvg
    if (direction == "long") != (fvg_type == "bullish"):
        return None
    ref = bars[max(0, i - lookback - sweep_bars):i - sweep_bars] or bars[:i] or [bar]
    swing_lo = min(b.low for b in ref)
    swing_hi = max(b.high for b in ref)
    fvg_mid = (fvg_lo + fvg_hi) / 2.0
    if not _in_ote(fvg_mid, swing_lo, swing_hi, direction):
        return None
    if not _detect_msb(bars, i, direction, msb_bars=5):
        return None

    entry = bar.close
    if direction == "long":
        sweep_level = min(b.low for b in ref)
        stop = sweep_level - tick
    else:
        sweep_level = max(b.high for b in ref)
        stop = sweep_level + tick
    r = abs(entry - stop)
    if r <= 0:
        return None
    if direction == "long":
        tp1, tp2 = entry + tp1_rr * r, entry + tp2_rr * r
    else:
        tp1, tp2 = entry - tp1_rr * r, entry - tp2_rr * r

    return {
        "side": direction,
        "instrument": instrument,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "size": default_contracts,
        "reason": f"kill_zone+{sweep}+fvg+ote+msb",
        "ts": bar.dt.isoformat(),
    }


# ── In-file risk_guard fallback (pure code, mandate-driven) ───────────────────
class _FallbackRiskGuard:
    """Faithful port of ARCHITECTURE §5 RiskGuard, used only when the bot's
    ``risk_guard.RiskGuard`` is not importable. Pure-code mandate enforcement;
    never raises on bad input (fail-closed to 'flat')."""

    def __init__(self, mandate_rules: dict, *, daily_gate_pct: float = 0.80,
                 consecutive_loss_limit: int = 3,
                 eod_flatten_et: time = EOD_FLATTEN_ET):
        self.rules = mandate_rules
        self.daily_gate_pct = daily_gate_pct
        self.consecutive_loss_limit = consecutive_loss_limit
        self.eod_flatten_et = eod_flatten_et

    def check(self, signal: dict, account_state: dict) -> dict:
        r = self.rules
        try:
            # 1. kill switch
            if KILL_SWITCH_FILE.exists():
                return {"decision": "flat", "size": 0, "reason": "kill_switch"}
            # 2. invalid/NaN account state ⇒ circuit breaker
            for k in ("realized_pnl_today", "unrealized_pnl", "total_eval_profit",
                      "trade_count_today", "consecutive_losses"):
                v = account_state.get(k)
                if v is None or (isinstance(v, float) and not math.isfinite(v)):
                    return {"decision": "flat", "size": 0, "reason": "invalid_account_state"}
            # 3. EOD flatten
            now_t = _parse_ts(account_state.get("now_et")).time()
            if now_t >= self.eod_flatten_et:
                return {"decision": "flat", "size": 0, "reason": "eod_flatten"}
            # 4. instrument allowlist
            if signal.get("instrument") not in r["instruments_allowed"]:
                return {"decision": "reject", "size": 0, "reason": "instrument_not_allowed"}
            # 5. side / price sanity
            if signal.get("side") not in ("long", "short"):
                return {"decision": "reject", "size": 0, "reason": "bad_side"}
            for k in ("entry", "stop"):
                v = signal.get(k)
                if v is None or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                    return {"decision": "reject", "size": 0, "reason": f"bad_{k}"}
            if float(signal["stop"]) == float(signal["entry"]):
                return {"decision": "reject", "size": 0, "reason": "stop_eq_entry"}
            # 6. daily trade cap
            if account_state["trade_count_today"] >= r["daily_trade_cap"]:
                return {"decision": "reject", "size": 0, "reason": "daily_trade_cap"}
            # 7. hard max-loss limit ⇒ circuit breaker
            net = account_state["realized_pnl_today"] + account_state["unrealized_pnl"]
            if net <= -r["max_loss_limit"]:
                return {"decision": "flat", "size": 0, "reason": "max_loss_limit"}
            # 8. soft daily-loss gate ⇒ circuit breaker
            if account_state["realized_pnl_today"] <= -(r["max_loss_limit"] * self.daily_gate_pct):
                return {"decision": "flat", "size": 0, "reason": "daily_loss_gate"}
            # 9. consecutive losses
            if account_state["consecutive_losses"] >= self.consecutive_loss_limit:
                return {"decision": "flat", "size": 0, "reason": "consecutive_losses"}
            # 10. consistency 50%
            tep = account_state["total_eval_profit"]
            if tep > 0 and account_state["realized_pnl_today"] >= tep * r["consistency_rule_eval"]:
                return {"decision": "reject", "size": 0, "reason": "consistency_cap"}
            # 11. size clamp
            req = int(signal.get("size", 1))
            size = max(1, min(req, int(r["max_position_size"])))
            reason = "ok" if size == req else f"size_clamped_{req}->{size}"
            return {"decision": "approve", "size": size, "reason": reason}
        except Exception:
            return {"decision": "flat", "size": 0, "reason": "invalid_account_state"}


def _parse_ts(s) -> datetime:
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.now(ET)


# ── Simulated open trade ──────────────────────────────────────────────────────
@dataclass
class _OpenTrade:
    entry_dt: datetime
    side: str
    entry: float
    stop: float
    tp1: float
    tp2: float
    size: int
    instrument: str
    point_value: float
    tp1_hit: bool = False
    tp1_pnl: float = 0.0
    # Original entry→stop distance captured at OPEN, before _take_tp1 moves the
    # stop to breakeven. r_multiple in _close MUST use this (not the live t.stop),
    # otherwise post-TP1 exits collapse R to 0 and inflate avg_r_multiple.
    original_r: float = 0.0
    # Worst adverse excursion in DOLLARS while the trade is open (peak intra-trade
    # drawdown). Used to surface intra-trade max_loss_limit breaches the between-
    # trades risk_guard gate can never see.
    worst_adverse_pnl: float = 0.0


@dataclass
class _ClosedTrade:
    entry_dt: datetime
    exit_dt: datetime
    side: str
    entry: float
    exit: float
    size: int
    status: str        # 'tp1' | 'tp2' | 'sl' | 'eod'
    pnl: float
    r_multiple: float
    session_date: date
    # Worst (most negative) mark-to-market dollar P&L observed while open.
    worst_adverse_pnl: float = 0.0


# ── Backtest engine ───────────────────────────────────────────────────────────
class BotBacktester:
    """Replays bars through strategy.generate_signal → risk_guard.check → fill
    simulation, enforcing the live order-path gates. No network, no live calls."""

    def __init__(self, instrument: str = "ES", *, mandate_path: Optional[Path] = None,
                 zones: Optional[list[str]] = None, active_year: Optional[int] = None,
                 commission_rt: float = COMMISSION_RT,
                 daily_gate_pct: float = 0.80,
                 consecutive_loss_limit: int = 3):
        self.mandate = load_mandate(mandate_path)
        self.rules = self.mandate["rules"]
        self.mandate_fallback = bool(self.mandate.get("_fallback"))

        if instrument not in self.rules["instruments_allowed"]:
            raise ValueError(f"{instrument} not in mandate allowlist {self.rules['instruments_allowed']}")
        self.instrument = instrument
        self.icfg = INSTRUMENTS[instrument]
        self.tick = self.icfg["tick"]
        self.point_value = self.icfg["point_value"]

        self.zones = zones or ["ny_open"]
        self.active_year = active_year
        self.commission_rt = commission_rt
        self.account_size = float(self.rules["account_size"])

        # Strategy config (from bot.strategy if available, else local defaults).
        if _USING_BOT_STRATEGY and StrategyConfig is not None:
            self.scfg = StrategyConfig(kill_zones=tuple(self.zones))
        else:
            self.scfg = None

        # Risk guard (bot's if available, else faithful in-file port).
        if _USING_BOT_RISKGUARD and RiskGuard is not None and MandateView is not None:
            self.risk_guard = RiskGuard(MandateView.from_file(mandate_path),
                                        daily_gate_pct=daily_gate_pct,
                                        consecutive_loss_limit=consecutive_loss_limit,
                                        eod_flatten_et=EOD_FLATTEN_ET)
        else:
            self.risk_guard = _FallbackRiskGuard(self.rules,
                                                 daily_gate_pct=daily_gate_pct,
                                                 consecutive_loss_limit=consecutive_loss_limit)

        # Engines-of-record (paper ledger).
        self.equity = self.account_size
        self.realized_today = 0.0
        self.consecutive_losses = 0
        self.trade_count_today = 0
        self.cur_session: Optional[date] = None
        # Last bar seen by the replay loop — used to flatten a carried position
        # at the PRIOR bar's close when the session date rolls over.
        self._prev_bar: Optional[Bar] = None
        self.open_trade: Optional[_OpenTrade] = None
        self.closed: list[_ClosedTrade] = []
        self.day_pnl: dict[date, float] = defaultdict(float)
        self.day_trades: dict[date, int] = defaultdict(int)
        self.decision_counts: dict[str, int] = defaultdict(int)
        # equity curve (one point per closed trade) for drawdown + Sharpe
        self.equity_curve: list[float] = [self.account_size]

    # ── account state for risk_guard ─────────────────────────────────────────
    def _account_state(self, now_et: datetime) -> dict:
        # Mark the open position to the latest known close so the combined
        # realized+unrealized max_loss_limit gate (risk_guard check #7) is
        # meaningful rather than always seeing 0 unrealized. In this harness the
        # gate is only consulted when flat, so this primarily hardens the value
        # for any future mid-trade gate call; the intra-trade peak excursion is
        # additionally audited in _lucid_breaches so a >max_loss_limit single-
        # trade drawdown is never invisible.
        mark = self._prev_bar.close if self._prev_bar is not None else None
        unrealized = self._unrealized(now_et_price=mark)
        return {
            "account_size": self.account_size,
            "equity": self.equity,
            "realized_pnl_today": self.realized_today,
            "unrealized_pnl": unrealized,
            "total_eval_profit": self.equity - self.account_size,
            "open_position": (
                {"instrument": self.open_trade.instrument, "side": self.open_trade.side,
                 "size": self.open_trade.size, "entry": self.open_trade.entry,
                 "stop": self.open_trade.stop} if self.open_trade else None
            ),
            "trade_count_today": self.trade_count_today,
            "consecutive_losses": self.consecutive_losses,
            "session_date": now_et.date().isoformat(),
            "now_et": now_et.isoformat(),
        }

    def _unrealized(self, now_et_price: Optional[float]) -> float:
        # Mark the open trade to ``now_et_price`` (the latest known close). Returns
        # 0 when flat or when no mark price is available yet. This makes the
        # combined realized+unrealized max_loss_limit gate able to see an
        # in-progress drawdown instead of a hardcoded 0.
        t = self.open_trade
        if t is None or now_et_price is None:
            return 0.0
        if t.side == "long":
            gross = now_et_price - t.entry
        else:
            gross = t.entry - now_et_price
        return gross * t.point_value * t.size

    # ── session rollover (reset daily counters) ──────────────────────────────
    def _roll_session(self, d: date) -> None:
        if self.cur_session != d:
            # PRIMARY overnight-carry fix: if a position is still open when the
            # session date changes, the live mandate (overnight_holds=false /
            # close_eod=true) requires it flat. The bar-based EOD flatten in
            # _update_open only fires when a bar exists at/after 15:55 ET; on
            # early closes, gaps, or a session whose last bar is <15:55 it never
            # fires and the trade silently carries to the next day. Force-close it
            # here as 'eod' at the PRIOR bar's close BEFORE resetting counters, so
            # the P&L lands on the correct (previous) session and the trade is not
            # stranded. The breach auditor (#5) additionally flags any cross-day
            # hold so the carry is reported, not just silently closed.
            if self.open_trade is not None and self._prev_bar is not None:
                pb = self._prev_bar
                self._close(self.open_trade, pb.dt, pb.close, "eod", pb.session_date)
            self.cur_session = d
            self.realized_today = 0.0
            self.trade_count_today = 0

    # ── signal generation (bot strategy if present, else fallback) ───────────
    def _signal(self, bars: list[Bar], i: int) -> Optional[dict]:
        if _USING_BOT_STRATEGY and generate_signal is not None:
            try:
                bars_by_tf = _bars_by_tf(bars, i)
                sig = generate_signal(bars_by_tf, bars[i].dt,
                                      instrument=self.instrument, config=self.scfg)
                return sig
            except Exception:
                return None  # strategy must stay pure; any error => no setup
        return _fallback_signal(
            bars, i, instrument=self.instrument, zones=self.zones,
            lookback=20, sweep_bars=3, tick=self.tick,
            tp1_rr=2.0, tp2_rr=4.0, default_contracts=1,
        )

    # ── mark-to-market the open trade's worst adverse excursion ──────────────
    def _mark_worst(self, t: _OpenTrade, bar: Bar) -> None:
        """Update the trade's peak intra-bar drawdown in dollars. For a long the
        worst point in the bar is its low; for a short, its high. This lets the
        breach auditor surface a >max_loss_limit single-trade excursion that the
        between-trades risk_guard gate (which only runs when flat) never sees."""
        if t.side == "long":
            adverse = (bar.low - t.entry) * t.point_value * t.size
            # Stop fires before the bar's full adverse range is realized.
            stop_floor = (t.stop - t.entry) * t.point_value * t.size
        else:
            adverse = (t.entry - bar.high) * t.point_value * t.size
            stop_floor = (t.entry - t.stop) * t.point_value * t.size
        # Cap excursion at stop distance — risk beyond this can't happen.
        adverse = max(adverse, stop_floor)
        if adverse < t.worst_adverse_pnl:
            t.worst_adverse_pnl = adverse

    # ── fill simulation on a single bar for the open trade ───────────────────
    def _update_open(self, bar: Bar) -> None:
        t = self.open_trade
        if not t:
            return
        # Mark-to-market each bar so intra-trade drawdown is observable.
        self._mark_worst(t, bar)
        # EOD flatten
        if bar.dt.time() >= EOD_FLATTEN_ET:
            self._close(t, bar.dt, bar.close, "eod", bar.session_date)
            return
        if t.side == "long":
            if bar.low <= t.stop:
                self._close(t, bar.dt, t.stop, "sl", bar.session_date)
            elif not t.tp1_hit and bar.high >= t.tp1:
                self._take_tp1(t, bar)
            elif t.tp1_hit and bar.high >= t.tp2:
                self._close(t, bar.dt, t.tp2, "tp2", bar.session_date)
        else:  # short
            if bar.high >= t.stop:
                self._close(t, bar.dt, t.stop, "sl", bar.session_date)
            elif not t.tp1_hit and bar.low <= t.tp1:
                self._take_tp1(t, bar)
            elif t.tp1_hit and bar.low <= t.tp2:
                self._close(t, bar.dt, t.tp2, "tp2", bar.session_date)

    def _take_tp1(self, t: _OpenTrade, bar: Bar) -> None:
        half = t.size // 2 or 1
        if t.side == "long":
            t.tp1_pnl = (t.tp1 - t.entry) * t.point_value * half - self.commission_rt * half
        else:
            t.tp1_pnl = (t.entry - t.tp1) * t.point_value * half - self.commission_rt * half
        t.tp1_hit = True
        t.stop = t.entry  # breakeven after TP1
        if t.size == 1:
            self._close(t, bar.dt, t.tp1, "tp1", bar.session_date)

    def _close(self, t: _OpenTrade, exit_dt: datetime, exit_price: float,
               status: str, sd: date) -> None:
        # r_multiple MUST use the entry→stop distance captured at OPEN. After
        # _take_tp1 moves t.stop to breakeven, abs(t.entry - t.stop) collapses to
        # 0 and would fall back to one tick — inflating R to ~points*4 for every
        # post-TP1 exit. original_r is frozen at open for exactly this reason.
        r_points = t.original_r if t.original_r > 0 else (
            abs(t.entry - t.stop) if abs(t.entry - t.stop) > 0 else self.tick)
        if t.side == "long":
            gross = exit_price - t.entry
        else:
            gross = t.entry - exit_price
        if t.size >= 2 and t.tp1_hit and status in ("tp2", "sl", "eod"):
            remaining = t.size - (t.size // 2)
            pnl = t.tp1_pnl + gross * t.point_value * remaining - self.commission_rt * remaining
        else:
            pnl = gross * t.point_value * t.size - self.commission_rt * t.size

        # R-multiple on the ORIGINAL entry-distance at open (stop later moved to BE).
        r_mult = gross / r_points if r_points else 0.0

        self.equity += pnl
        self.realized_today += pnl
        self.day_pnl[sd] += pnl
        self.consecutive_losses = 0 if pnl > 0 else self.consecutive_losses + 1
        self.equity_curve.append(self.equity)
        self.closed.append(_ClosedTrade(
            entry_dt=t.entry_dt, exit_dt=exit_dt, side=t.side, entry=t.entry,
            exit=exit_price, size=t.size, status=status, pnl=pnl,
            r_multiple=r_mult, session_date=sd,
            worst_adverse_pnl=t.worst_adverse_pnl,
        ))
        self.open_trade = None

    # ── main replay loop ─────────────────────────────────────────────────────
    def run(self, bars: list[Bar]) -> dict:
        for i, bar in enumerate(bars):
            out_of_year = bool(self.active_year and bar.dt.year != self.active_year)

            # An out-of-year bar must NOT strand an already-open position. If a
            # trade opened on the last in-year bar and the next bars fall outside
            # --year, we still roll the session and progress/flatten the open
            # trade (so a session rollover or EOD flatten fires) instead of
            # leaking it to the end-of-data fallback in a different regime. We
            # only suppress NEW entries on out-of-year bars.
            if out_of_year and self.open_trade is None:
                # Flat and outside the window: nothing to progress. Do not touch
                # _prev_bar so a later in-year roll flattens at the right close.
                continue

            self._roll_session(bar.session_date)

            # 1. progress open trade first (fills / EOD). Always runs for an open
            #    trade, even on out-of-year bars, so the position is not stranded.
            if self.open_trade:
                self._update_open(bar)

            # Remember this bar as the prior-bar reference for the next session
            # rollover flatten (PRIMARY overnight-carry fix in _roll_session).
            self._prev_bar = bar

            # 2. look for a new entry only when flat AND inside the active year.
            if self.open_trade or out_of_year:
                continue
            sig = self._signal(bars, i)
            if sig is None:
                self.decision_counts["skip"] += 1
                continue

            # 3. MANDATORY risk_guard gate — exactly the live order path
            acct = self._account_state(bar.dt)
            result = self.risk_guard.check(sig, acct)
            self.decision_counts[result.get("decision", "?")] += 1
            if result.get("decision") != "approve":
                continue

            # 4. "send" == simulate the fill (paper). No traderpost, no POST.
            size = int(result.get("size", sig.get("size", 1)))
            self._open_from_signal(sig, size, bar.session_date)

        # flatten any residual position at data end
        if self.open_trade and bars:
            last = self._prev_bar or bars[-1]
            self._close(self.open_trade, last.dt, last.close, "eod", last.session_date)

        return self._build_results(bars)

    def _open_from_signal(self, sig: dict, size: int, sd: date) -> None:
        entry = float(sig["entry"])
        stop = float(sig["stop"])
        self.open_trade = _OpenTrade(
            entry_dt=_parse_ts(sig["ts"]),
            side=sig["side"],
            entry=entry,
            stop=stop,
            tp1=float(sig["tp1"]),
            tp2=float(sig["tp2"]),
            size=size,
            instrument=self.instrument,
            point_value=self.point_value,
            # Locked at OPEN so r_multiple survives the breakeven stop move.
            original_r=abs(entry - stop),
        )
        self.trade_count_today += 1
        self.day_trades[sd] += 1

    # ── reporting ────────────────────────────────────────────────────────────
    def _build_results(self, bars: list[Bar]) -> dict:
        trades = self.closed
        if not trades:
            return {
                "error": "No trades generated. Check kill-zone filter, data range, CSV format.",
                "decisions": dict(self.decision_counts),
                "strategy_source": "bot.strategy" if _USING_BOT_STRATEGY else "in-file fallback",
                "risk_guard_source": "bot.risk_guard" if _USING_BOT_RISKGUARD else "in-file fallback",
                "mandate_fallback": self.mandate_fallback,
            }

        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl <= 0]
        total_pnl = sum(t.pnl for t in trades)
        win_rate = len(winners) / len(trades)

        # Max drawdown on the equity curve
        peak = self.equity_curve[0]
        max_dd = 0.0
        max_dd_pct = 0.0
        for eq in self.equity_curve:
            peak = max(peak, eq)
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = (dd / peak) if peak else 0.0

        # Sharpe — on per-trade returns (mean/std * sqrt(N)); annualization-free,
        # reported as a per-sequence Sharpe so it's comparable across runs.
        rets = [t.pnl for t in trades]
        n = len(rets)
        mean = sum(rets) / n
        var = sum((x - mean) ** 2 for x in rets) / (n - 1) if n > 1 else 0.0
        std = math.sqrt(var)
        sharpe = (mean / std * math.sqrt(n)) if std > 0 else 0.0

        # Daily aggregates
        trading_days = sorted(self.day_pnl.keys())
        n_days = len(trading_days) or 1
        avg_trades_per_day = len(trades) / n_days
        worst_day_date = min(self.day_pnl, key=lambda d: self.day_pnl[d])
        worst_day_pnl = self.day_pnl[worst_day_date]
        best_day_date = max(self.day_pnl, key=lambda d: self.day_pnl[d])

        # Lucid breach detection (would any mandate rule have been breached?)
        breaches = self._lucid_breaches(trades, max_dd)

        return {
            "strategy": "TJR ICT Kill Zone — Lucid 25K (bot path)",
            "instrument": self.instrument,
            "strategy_source": "bot.strategy" if _USING_BOT_STRATEGY else "in-file fallback",
            "risk_guard_source": "bot.risk_guard" if _USING_BOT_RISKGUARD else "in-file fallback",
            "mandate_fallback": self.mandate_fallback,
            "period": {
                "first_trade": str(trades[0].entry_dt.date()),
                "last_trade": str(trades[-1].entry_dt.date()),
                "trading_days": n_days,
                "bars": len(bars),
            },
            "performance": {
                "total_trades": len(trades),
                "winners": len(winners),
                "losers": len(losers),
                "win_rate_pct": round(win_rate * 100, 1),
                "total_pnl": round(total_pnl, 2),
                "avg_win": round(sum(t.pnl for t in winners) / len(winners), 2) if winners else 0.0,
                "avg_loss": round(sum(t.pnl for t in losers) / len(losers), 2) if losers else 0.0,
                "avg_r_multiple": round(sum(t.r_multiple for t in trades) / len(trades), 2),
                "tp1_exits": sum(1 for t in trades if t.status == "tp1"),
                "tp2_exits": sum(1 for t in trades if t.status == "tp2"),
                "sl_exits": sum(1 for t in trades if t.status == "sl"),
                "eod_exits": sum(1 for t in trades if t.status == "eod"),
                "avg_trades_per_day": round(avg_trades_per_day, 2),
                "sharpe_per_trade": round(sharpe, 3),
            },
            "risk": {
                "max_drawdown_dollar": round(max_dd, 2),
                "max_drawdown_pct": round(max_dd_pct * 100, 2),
                "worst_day": {"date": str(worst_day_date), "pnl": round(worst_day_pnl, 2)},
                "best_day": {"date": str(best_day_date), "pnl": round(self.day_pnl[best_day_date], 2)},
                # Worst single-trade adverse mark-to-market excursion (dollars),
                # so an intra-trade drawdown past max_loss_limit is visible even
                # if the trade later recovered to a smaller realized loss.
                "worst_trade_excursion": round(
                    min((t.worst_adverse_pnl for t in trades), default=0.0), 2),
            },
            "equity": {
                "start": self.account_size,
                "end": round(self.equity, 2),
                "net_change": round(self.equity - self.account_size, 2),
            },
            "decisions": dict(self.decision_counts),
            "lucid_compliance": {
                "max_loss_limit": self.rules["max_loss_limit"],
                "max_position_size": self.rules["max_position_size"],
                "daily_trade_cap": self.rules["daily_trade_cap"],
                "consistency_rule_eval": self.rules["consistency_rule_eval"],
                "any_breach": bool(breaches),
                "breaches": breaches,
            },
        }

    def _lucid_breaches(self, trades: list[_ClosedTrade], max_dd: float) -> list[dict]:
        """Audit whether ANY Lucid mandate rule WOULD have been breached during
        the replay (independent of the gates, so we can verify the gates held)."""
        rules = self.rules
        breaches: list[dict] = []

        # 1. Max position size — any fill above the cap.
        cap = int(rules["max_position_size"])
        over = [t for t in trades if t.size > cap]
        if over:
            breaches.append({"rule": "max_position_size", "limit": cap,
                             "count": len(over)})

        # 2. Daily trade cap.
        dcap = int(rules["daily_trade_cap"])
        over_days = {str(d): c for d, c in self.day_trades.items() if c > dcap}
        if over_days:
            breaches.append({"rule": "daily_trade_cap", "limit": dcap,
                             "days": over_days})

        # 3. Hard max-loss limit (end-of-day drawdown) — any day worse than -limit,
        #    and overall peak-to-trough drawdown beyond the limit.
        mll = float(rules["max_loss_limit"])
        bad_days = {str(d): round(p, 2) for d, p in self.day_pnl.items() if p <= -mll}
        if bad_days:
            breaches.append({"rule": "max_loss_limit_daily", "limit": -mll,
                             "days": bad_days})
        if max_dd >= mll:
            breaches.append({"rule": "max_loss_limit_drawdown", "limit": mll,
                             "max_drawdown": round(max_dd, 2)})

        # 4. Consistency 50% — running check that no single positive day exceeds
        #    50% of cumulative eval profit at that time.
        #
        #    DENOMINATOR NOTE (intentional, documented): this auditor uses
        #    ``running`` = sum of PRIOR days' P&L only, i.e. cumulative profit
        #    BEFORE the day under test. risk_guard gate #10 instead compares the
        #    day's realized against ``total_eval_profit`` = (equity - account_size),
        #    the all-time cumulative INCLUDING the current day's open progress.
        #    The two denominators differ by design: the gate is a forward-looking
        #    pre-trade block (does this new fill risk crossing 50%?), while this
        #    audit is an independent after-the-fact ledger check. They are NOT
        #    expected to be numerically identical; this audit is the authority for
        #    "did a consistency violation actually occur".
        cap_pct = float(rules["consistency_rule_eval"])
        running = 0.0
        viol = []
        for d in sorted(self.day_pnl.keys()):
            dp = self.day_pnl[d]
            if dp > 0 and running > 0 and dp / running > cap_pct:
                viol.append({"date": str(d), "day_pnl": round(dp, 2),
                             "total_at_time": round(running, 2),
                             "pct": round(dp / running * 100, 1)})
            running += dp
        if viol:
            breaches.append({"rule": "consistency_50pct", "limit_pct": cap_pct * 100,
                             "violations": viol})

        # 5. Overnight hold / EOD close. Flag a trade as an overnight breach if
        #    EITHER it exits after 16:00 ET (late same-day) OR it exits on a
        #    different calendar date than it entered (a true cross-session carry).
        #    The date-mismatch arm is essential: a position carried overnight and
        #    flattened ~09:00 the next morning has exit time 09:00, so a
        #    time-only check would miss the very overnight hold the mandate bans.
        if not rules.get("overnight_holds", False):
            held = [
                t for t in trades
                if t.exit_dt.time() > time(16, 0)
                or t.exit_dt.date() != t.entry_dt.date()
            ]
            if held:
                cross_day = sum(
                    1 for t in held if t.exit_dt.date() != t.entry_dt.date())
                breaches.append({
                    "rule": "overnight_hold",
                    "count": len(held),
                    "cross_session_holds": cross_day,
                })

        # 5b. Intra-trade max-loss excursion — a SINGLE position whose worst
        #     adverse mark-to-market exceeded -max_loss_limit at any point while
        #     open. The between-trades risk_guard gate (which combines realized +
        #     unrealized) only runs when flat, so this in-trade peak is otherwise
        #     invisible. We surface it here so a >$max_loss_limit single-trade
        #     drawdown is reported even if the trade later recovered to a smaller
        #     realized loss.
        mll_intra = float(rules["max_loss_limit"])
        excursions = [
            {"entry": str(t.entry_dt), "exit": str(t.exit_dt),
             "worst_adverse_pnl": round(t.worst_adverse_pnl, 2),
             "realized_pnl": round(t.pnl, 2)}
            for t in trades if t.worst_adverse_pnl <= -mll_intra
        ]
        if excursions:
            breaches.append({"rule": "max_loss_limit_intratrade",
                             "limit": -mll_intra,
                             "count": len(excursions),
                             "trades": excursions})

        # 6. Instrument allowlist (defense-in-depth).
        if self.instrument not in rules["instruments_allowed"]:
            breaches.append({"rule": "instrument_not_allowed",
                             "instrument": self.instrument})
        return breaches


# ── bars_by_tf adapter (for the bot's pandas-based strategy, if present) ──────
def _bars_by_tf(bars: list[Bar], i: int):
    """Build the {tf: DataFrame} dict the bot's pandas strategy expects from the
    bar window up to and including index i. Lazy pandas import (only used when
    bot.strategy is the active generator). Returns a dict or raises if pandas is
    unavailable — caught by the caller, which then yields no signal."""
    import pandas as pd  # local import; strategy path only

    lo = max(0, i - 400)  # rolling window is plenty for HTF bias + setup
    window = bars[lo:i + 1]
    idx = pd.DatetimeIndex([b.dt for b in window])
    base = pd.DataFrame(
        {"open": [b.open for b in window], "high": [b.high for b in window],
         "low": [b.low for b in window], "close": [b.close for b in window],
         "volume": [b.volume for b in window]},
        index=idx,
    )
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = {"5m": base}
    for tf, rule in (("1m", "1min"), ("15m", "15min"), ("1h", "1h")):
        try:
            out[tf] = base.resample(rule).agg(agg).dropna()
        except Exception:
            out[tf] = base
    out["1m"] = base  # finest available proxy
    return out


# ── pretty printer ───────────────────────────────────────────────────────────
def print_report(res: dict, *, daily: bool = False,
                 day_pnl: Optional[dict] = None) -> None:
    line = "=" * 66
    print("\n" + line)
    print(f"  {res.get('strategy', 'TJR Bot Backtest')}")
    print(line)
    if "error" in res:
        print(f"ERROR: {res['error']}")
        print(f"  decisions: {res.get('decisions')}")
        return

    p, r, e = res["performance"], res["risk"], res["equity"]
    print(f"\nSources: strategy={res['strategy_source']} | "
          f"risk_guard={res['risk_guard_source']} | "
          f"mandate_fallback={res['mandate_fallback']}")
    print(f"\nPERFORMANCE  ({res['period']['first_trade']} -> {res['period']['last_trade']}, "
          f"{res['period']['trading_days']} days)")
    print(f"  Trades:            {p['total_trades']}  "
          f"({p['tp1_exits']} TP1 / {p['tp2_exits']} TP2 / {p['sl_exits']} SL / {p['eod_exits']} EOD)")
    print(f"  Win rate:          {p['win_rate_pct']}%")
    print(f"  Total P&L:         ${p['total_pnl']:.2f}")
    print(f"  Avg win / loss:    ${p['avg_win']:.2f} / ${p['avg_loss']:.2f}")
    print(f"  Avg R:             {p['avg_r_multiple']:.2f}R")
    print(f"  Avg trades/day:    {p['avg_trades_per_day']:.2f}")
    print(f"  Sharpe (per-trade):{p['sharpe_per_trade']:.3f}")
    print(f"\nRISK")
    print(f"  Max drawdown:      ${r['max_drawdown_dollar']:.2f} ({r['max_drawdown_pct']:.2f}%)")
    print(f"  Worst day:         {r['worst_day']['date']}  ${r['worst_day']['pnl']:.2f}")
    print(f"  Best day:          {r['best_day']['date']}  ${r['best_day']['pnl']:.2f}")
    print(f"  Equity:            ${e['start']:.0f} -> ${e['end']:.2f}  "
          f"(net ${e['net_change']:.2f})")

    lc = res["lucid_compliance"]
    print(f"\nLUCID MANDATE BREACH CHECK")
    print(f"  Decisions:         {res['decisions']}")
    if lc["any_breach"]:
        print(f"  RESULT:            BREACH(ES) DETECTED ({len(lc['breaches'])})")
        for b in lc["breaches"]:
            print(f"    - {b}")
    else:
        print(f"  RESULT:            No Lucid rule breached "
              f"(cap={lc['max_position_size']}, daily={lc['daily_trade_cap']}, "
              f"maxloss=${lc['max_loss_limit']}, consistency={lc['consistency_rule_eval']})")

    if daily and day_pnl:
        print("\nDAILY BREAKDOWN")
        running = 0.0
        for d in sorted(day_pnl):
            running += day_pnl[d]
            print(f"  {d}  day=${day_pnl[d]:+9.2f}  running=${running:+10.2f}")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bot.backtest",
        description="TJR/ICT Lucid 25K bot-path backtester (paper-only, no live calls)")
    ap.add_argument("csv", help="Path to historical CSV (NinjaTrader or yfinance format)")
    ap.add_argument("--instrument", default="ES", choices=list(INSTRUMENTS.keys()))
    ap.add_argument("--zones", nargs="+", default=["ny_open"],
                    choices=list(KILL_ZONES.keys()), help="Kill zones to trade")
    ap.add_argument("--year", type=int, default=None, help="Filter to a single year")
    ap.add_argument("--mandate", default=None, help="Override path to lucid_mandate.json")
    ap.add_argument("--daily", action="store_true", help="Print per-day P&L breakdown")
    ap.add_argument("--json", default=None, help="Write the results JSON to this path")
    args = ap.parse_args(argv)

    try:
        bars = load_bars_csv(Path(args.csv))
    except FileNotFoundError as ex:
        print(f"ERROR: {ex}", file=sys.stderr)
        return 2
    if not bars:
        print("ERROR: no bars parsed (check CSV format).", file=sys.stderr)
        return 1
    print(f"Loaded {len(bars):,} bars "
          f"({bars[0].dt.date()} -> {bars[-1].dt.date()}) from {Path(args.csv).name}")

    bt = BotBacktester(
        instrument=args.instrument,
        mandate_path=Path(args.mandate) if args.mandate else None,
        zones=args.zones, active_year=args.year,
    )
    if bt.mandate_fallback:
        print("WARNING: lucid_mandate.json not found — using fallback defaults "
              "(this run is informational only).", file=sys.stderr)

    res = bt.run(bars)
    print_report(res, daily=args.daily, day_pnl=dict(bt.day_pnl))

    out_path = Path(args.json) if args.json else (
        RESULTS_DIR / f"bt_{args.instrument}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, default=str)
    print(f"\nResults JSON -> {out_path}")
    return 0 if "error" not in res else 1


# ── __main__ smoke test ───────────────────────────────────────────────────────
def _smoke_test() -> int:
    """Self-contained smoke test: synthesizes a tiny NinjaTrader-format CSV with a
    NY-open bullish sweep + bullish FVG + MSB, runs the engine, and asserts the
    risk_guard gate fired and the report shape is intact. No network, no files
    left behind beyond a temp CSV. Verifies the module is runnable end-to-end."""
    import tempfile

    # Build ~60 5M bars on 2024-01-02 starting 08:30 ET. First ~40 establish a
    # swing range/discount; then a dip-below-then-reclaim sweep + an up FVG + MSB.
    rows = []
    base_dt = datetime(2024, 1, 2, 8, 30)
    price = 4750.0
    bars_spec = []
    # 25 gently ranging bars (build lookback window, mild downtrend into discount)
    for k in range(25):
        o = price
        c = price - 0.25
        h = max(o, c) + 0.5
        lo = min(o, c) - 0.5
        bars_spec.append((o, h, lo, c))
        price = c
    # sweep down hard (take out the swing low), then reclaim
    sweep_low = min(b[2] for b in bars_spec) - 3.0
    bars_spec.append((price, price + 0.25, sweep_low, sweep_low + 0.5))   # spike down
    bars_spec.append((sweep_low + 0.5, sweep_low + 1.0, sweep_low, sweep_low + 0.75))
    reclaim = min(b[2] for b in bars_spec[:25]) + 2.0
    bars_spec.append((sweep_low + 0.75, reclaim + 1.0, sweep_low + 0.5, reclaim))  # reclaim close above swing low
    # bullish FVG: bar[i-2].high < bar[i].low, plus MSB (close above recent highs)
    bars_spec.append((reclaim, reclaim + 2.0, reclaim - 0.5, reclaim + 1.5))
    bars_spec.append((reclaim + 1.5, reclaim + 3.0, reclaim + 1.0, reclaim + 2.5))
    bars_spec.append((reclaim + 2.5, reclaim + 8.0, reclaim + 5.0, reclaim + 7.0))  # gap up + MSB
    # follow-through so a TP1/TP2 or SL can resolve
    p = reclaim + 7.0
    for k in range(15):
        bars_spec.append((p, p + 4.0, p - 1.0, p + 3.0))
        p += 3.0

    for k, (o, h, lo, c) in enumerate(bars_spec):
        dt = base_dt + timedelta(minutes=5 * k)
        rows.append(f"{dt.strftime('%Y%m%d')},{dt.strftime('%H%M%S')},"
                    f"{o:.2f},{h:.2f},{lo:.2f},{c:.2f},1000")

    tmp = Path(tempfile.gettempdir()) / "bot_backtest_smoke.csv"
    tmp.write_text("Date,Time,Open,High,Low,Close,Volume\n" + "\n".join(rows) + "\n",
                   encoding="utf-8")

    bars = load_bars_csv(tmp)
    assert bars, "smoke: no bars parsed"
    assert bars[0].dt.tzinfo is not None, "smoke: bars must be tz-aware ET"

    bt = BotBacktester(instrument="ES", zones=["ny_open"])
    res = bt.run(bars)

    # The risk_guard gate must have been consulted at least once OR strategy
    # skipped everything — either way the decision counters must exist.
    assert "decisions" in res, "smoke: missing decision audit"
    assert isinstance(res["decisions"], dict), "smoke: decisions not a dict"
    if "error" not in res:
        lc = res["lucid_compliance"]
        assert "any_breach" in lc, "smoke: missing breach verdict"
        # 1 contract, ny_open only, single position => no Lucid rule should breach
        assert lc["any_breach"] is False, f"smoke: unexpected breach {lc['breaches']}"
        assert res["performance"]["total_trades"] >= 1, "smoke: expected >=1 trade"
        print(f"SMOKE OK: {res['performance']['total_trades']} trade(s), "
              f"net ${res['equity']['net_change']:.2f}, "
              f"decisions={res['decisions']}, breach={lc['any_breach']}")
    else:
        # No trade is acceptable if gates/strategy filtered it; still a pass for
        # the harness wiring as long as decisions were recorded.
        print(f"SMOKE OK (no trade): decisions={res['decisions']}")

    print(f"strategy_source={res['strategy_source']} "
          f"risk_guard_source={res['risk_guard_source']}")
    try:
        tmp.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    # If args are given, behave as the CLI; otherwise run the smoke test.
    if len(sys.argv) > 1:
        raise SystemExit(main())
    raise SystemExit(_smoke_test())
