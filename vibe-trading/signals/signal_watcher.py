"""
mnq_signal_watcher_v2.py
========================
Autonomous MNQ signal watcher for Lucid 25K prop eval (TJR/ICT kill zone strategy).

Version : 2.0.0
Strategy: ICT Kill Zone — Liquidity Sweep + FVG/OB confluence
Instrument: MNQ (Micro Nasdaq-100) | $2/pt

Kill Zones  : London 02:00-05:00 ET | NY 08:30-11:00 ET
Daily limits: 3 trades max | -$60 daily loss stop
EOD flatten : 4:00 PM ET | Pre-EOD warning 3:45 PM ET

Alerts: Telegram (HTML) + Windows toast + log file
Kill switch: create KILL_SWITCH file (one directory above) to halt immediately

HARD RULES:
  - NEVER calls any trading API or auto-executes any order
  - NEVER auto-trades — "AWAITING RONNIE EXECUTION" on every signal
  - Ronnie executes manually in NinjaTrader / Tradovate
  - All signals gated by Lucid evaluation prop rules

Confidence scoring (3 factors):
  Sweep (mandatory) + FVG + OB → HIGH   (⭐⭐)
  Sweep + FVG                  → MEDIUM (⭐)
  Sweep + OB                   → MEDIUM (⭐)
  Sweep only                   → SKIP   (insufficient confluence)

Result feedback loop:
  Drop result_<DIRECTION>_<+/-AMOUNT>.txt in incoming/ after each trade.
  Example: result_LONG_+40.txt | result_SHORT_-20.txt
  Watcher updates day_pnl + peak_equity, confirms via Telegram, moves to processed/.

Usage:
    python mnq_signal_watcher_v2.py
    (missing deps installed automatically on first run)
"""

__version__ = "2.0.0"

# ── stdlib ────────────────────────────────────────────────────────────────────
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

# ── auto-install missing third-party deps ─────────────────────────────────────
_REQUIRED_PKGS = ["yfinance", "pandas", "numpy", "pytz", "schedule", "requests", "plyer"]


def _ensure_deps() -> None:
    """Install any missing pip packages silently before import."""
    missing = []
    for pkg in _REQUIRED_PKGS:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[STARTUP] Auto-installing missing packages: {missing}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", *missing]
        )
        print("[STARTUP] Done. Continuing...\n")


_ensure_deps()

import numpy as np          # noqa: E402
import pandas as pd         # noqa: E402
import pytz                 # noqa: E402
import requests             # noqa: E402
import schedule             # noqa: E402
import yfinance as yf       # noqa: E402

try:
    from plyer import notification as _plyer_notify
    _PLYER_OK = True
except Exception:
    _plyer_notify = None
    _PLYER_OK = False

# ── paths ─────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
STATE_FILE    = BASE_DIR / "mnq_state.json"
LOG_FILE      = BASE_DIR / "mnq_signals.log"
KILL_SWITCH   = BASE_DIR.parent / "KILL_SWITCH"
INCOMING_DIR  = BASE_DIR / "incoming"
PROCESSED_DIR = BASE_DIR / "processed"

INCOMING_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# ── logging (set up early so everything below can use log) ───────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("mnq_watcher")

# ── .env loader ───────────────────────────────────────────────────────────────

def _load_env() -> None:
    """
    Locate a .env file and populate os.environ.

    Search order:
      1. Walk parent directories from BASE_DIR, up to 5 levels.
      2. %LOCALAPPDATA%\\hermes\\.env on Windows.

    Reads KEY=VALUE lines; skips blank lines and # comments.
    Uses os.environ.setdefault so existing env vars take precedence.
    """
    candidates: list[Path] = []

    cur = BASE_DIR
    for _ in range(5):
        candidates.append(cur / ".env")
        if cur == cur.parent:       # filesystem root guard
            break
        cur = cur.parent

    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        candidates.append(Path(local_app) / "hermes" / ".env")

    for env_file in candidates:
        if env_file.exists():
            log.info(f".env loaded from: {env_file}")
            try:
                with open(env_file, "r", encoding="utf-8") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, _, val = line.partition("=")
                        os.environ.setdefault(
                            key.strip(),
                            val.strip().strip('"').strip("'"),
                        )
            except Exception as exc:
                log.warning(f".env read error ({exc}) — continuing without it.")
            break  # first hit wins


_load_env()

TELEGRAM_TOKEN = (os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("HERMES_TELEGRAM_BOT_TOKEN") or os.environ.get("HERMES_BOT_TOKEN") or "")
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("HERMES_TELEGRAM_CHAT_ID") or os.environ.get("ALLOWED_CHAT_ID") or "")

if TELEGRAM_TOKEN:
    log.info("Telegram: bot token found.")
else:
    log.warning("Telegram: TELEGRAM_TOKEN not set — desktop-only alerts.")

# ── instrument configuration (keep exactly as specified) ──────────────────────
TICKER     = "NQ=F"
INSTRUMENT = "MNQ"
MULT       = 2.0       # $2 per point (Micro NQ)
RISK       = 20.0      # $ risk per trade
STOP_PTS   = 10        # stop loss in points
TP1_PTS    = 20        # first target in points  (2R)
TP2_PTS    = 40        # second target in points (4R)
PIVOT_N    = 3         # pivot lookback bars for swing detection

LUCID = {
    "account_size":    25_000,
    "max_trailing_dd": 1_500,
    "daily_loss_limit":   60,
    "max_trades_day":      3,
    "eod_hour":           16,   # 4:00 PM ET
    "consistency_cap":  0.45,   # no single day > 45 % of total running profit
}

KILL_ZONES = [
    ((2,  0), (5,  0)),   # London open
    ((8, 30), (11, 0)),   # New York open
]

# ── timezones ─────────────────────────────────────────────────────────────────
ET  = pytz.timezone("America/New_York")
UTC = pytz.utc


# ══════════════════════════════════════════════════════════════════════════════
#  Telegram & desktop alerts
# ══════════════════════════════════════════════════════════════════════════════

def send_telegram(msg: str, parse_mode: str = "HTML") -> bool:
    """
    Send an HTML-formatted message via Telegram Bot API.

    Returns True on success, False on failure or missing credentials.
    Logs but never raises on network errors.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.debug("Telegram credentials not configured — skipping.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       msg,
        "parse_mode": parse_mode,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.exceptions.HTTPError as exc:
        log.error(f"Telegram HTTP error: {exc} — {resp.text[:120]}")
    except Exception as exc:
        log.error(f"Telegram send failed: {exc}")
    return False


def send_toast(title: str, message: str) -> None:
    """
    Show a Windows desktop toast notification.
    Silent fail if plyer is unavailable or on non-Windows platforms.
    """
    if not _PLYER_OK or _plyer_notify is None:
        return
    try:
        _plyer_notify.notify(
            title=title,
            message=message,
            app_name="MNQ Signal Watcher",
            timeout=10,
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  State persistence
# ══════════════════════════════════════════════════════════════════════════════

def _default_state() -> dict:
    """Return a fresh daily-state dict."""
    return {
        "trade_date":    "",
        "trade_count":   0,
        "signals_sent":  [],          # list of dedup keys "ts_DIRECTION"
        "day_pnl":       0.0,
        "peak_equity":   float(LUCID["account_size"]),
        "pre_eod_sent":  False,
        "eod_sent":      False,
        "startup_sent":  "",          # date string; throttle once per day
    }


def load_state() -> dict:
    """
    Load state from STATE_FILE.

    Automatically resets daily counters (trade_count, signals_sent, day_pnl,
    pre_eod_sent, eod_sent) when the calendar date changes in ET.
    peak_equity carries forward across days (trailing drawdown is cumulative).
    """
    today_et = _et_now().strftime("%Y-%m-%d")

    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except Exception as exc:
            log.warning(f"State read error ({exc}) — starting fresh.")
            state = _default_state()
    else:
        state = _default_state()

    if state.get("trade_date") != today_et:
        log.info(f"New trading day: {today_et}. Resetting daily counters.")
        peak = state.get("peak_equity", float(LUCID["account_size"]))
        state = _default_state()
        state["trade_date"] = today_et
        state["peak_equity"] = peak      # carry forward across days
        save_state(state)

    return state


def save_state(state: dict) -> None:
    """Atomically persist state to STATE_FILE."""
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        tmp.replace(STATE_FILE)
    except Exception as exc:
        log.error(f"State save failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
#  Market-hours helpers
# ══════════════════════════════════════════════════════════════════════════════

def _et_now() -> datetime:
    """Current datetime in US/Eastern timezone."""
    return datetime.now(ET)


def _is_market_hours() -> bool:
    """
    Return True if the current ET time is within Mon-Fri 01:00-17:30.

    MNQ trades nearly 24 hours on CME Globex, but we only need data during
    kill zones (London 02-05 ET, NY 08:30-11 ET). Skipping weekends and
    overnight hours avoids spurious yfinance errors and unnecessary API calls.
    """
    now = _et_now()
    if now.weekday() >= 5:                   # 5=Saturday, 6=Sunday
        return False
    t = (now.hour, now.minute)
    return (1, 0) <= t <= (17, 30)


def _in_kill_zone() -> tuple[bool, str]:
    """
    Check whether the current ET time is inside any configured kill zone.

    Returns (is_in_zone: bool, zone_label: str).
    """
    now = _et_now()
    t = (now.hour, now.minute)
    labels = ["London Session", "NY Session"]
    for (sh, sm), (eh, em), label in zip(
        [kz[0] for kz in KILL_ZONES],
        [kz[1] for kz in KILL_ZONES],
        labels,
    ):
        if (sh, sm) <= t < (eh, em):
            return True, label
    return False, ""


# ══════════════════════════════════════════════════════════════════════════════
#  Price data fetch
# ══════════════════════════════════════════════════════════════════════════════

def fetch_ohlcv(lookback_bars: int = 60) -> pd.DataFrame | None:
    """
    Download recent 5-minute bars for NQ=F via yfinance.

    Fetches the last 3 calendar days to ensure enough bars survive market gaps.
    Returns a DataFrame with lowercase columns [open, high, low, close, volume]
    indexed by UTC-aware datetime, limited to the last `lookback_bars` rows.
    Returns None on any fetch or parsing error.
    """
    try:
        ticker = yf.Ticker(TICKER)
        df = ticker.history(period="3d", interval="5m", auto_adjust=True)
        if df.empty:
            log.warning("yfinance returned an empty DataFrame.")
            return None
        # Normalise MultiIndex columns (yfinance sometimes returns them)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[cols].dropna()
        if df.empty:
            log.warning("All bars dropped after dropna().")
            return None
        log.debug(f"Fetched {len(df)} bars; latest: {df.index[-1]}")
        return df.tail(lookback_bars)
    except Exception as exc:
        log.error(f"Data fetch error: {exc}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  ICT pattern detectors — swing pivots, sweep, FVG, order block
# ══════════════════════════════════════════════════════════════════════════════

def _find_swing_pivots(df: pd.DataFrame, n: int = PIVOT_N) -> tuple[list[int], list[int]]:
    """
    Identify swing high and swing low bar indices using an n-bar pivot rule.

    A swing high at integer position i means df['high'].iloc[i] is strictly
    the maximum of the n bars before and after it.  Similarly for swing lows.

    Returns:
        swing_high_positions : list of int positions in df
        swing_low_positions  : list of int positions in df
    """
    hi = df["high"].values
    lo = df["low"].values
    swing_highs: list[int] = []
    swing_lows:  list[int] = []

    for i in range(n, len(df) - n):
        window_h = hi[i - n : i + n + 1]
        window_l = lo[i - n : i + n + 1]
        if hi[i] == window_h.max():
            swing_highs.append(i)
        if lo[i] == window_l.min():
            swing_lows.append(i)

    return swing_highs, swing_lows


def _detect_sweep(
    df: pd.DataFrame,
) -> tuple[str | None, int | None, float | None]:
    """
    Detect a liquidity sweep on the most recent confirmed bar (index -2).

    ICT liquidity sweep:
      LONG  — price wicks below a prior swing low then closes back above it
              (equal lows / buy-side liquidity taken, bullish reversal expected)
      SHORT — price wicks above a prior swing high then closes back below it
              (sell-side liquidity taken, bearish reversal expected)

    The bar at index -1 is treated as the live/forming bar and is excluded.

    Returns:
        (direction, swept_bar_idx, swept_price_level)
        direction is "LONG", "SHORT", or None if no sweep detected.
    """
    if len(df) < PIVOT_N * 2 + 3:
        return None, None, None

    swing_highs, swing_lows = _find_swing_pivots(df)
    confirmed_i = len(df) - 2           # second-to-last bar
    bar = df.iloc[confirmed_i]

    # --- LONG: sweep of a prior swing low ---
    for sl_i in reversed(swing_lows):
        if sl_i >= confirmed_i:
            continue
        level = float(df["low"].iloc[sl_i])
        if bar["low"] < level and bar["close"] > level:
            return "LONG", sl_i, level

    # --- SHORT: sweep of a prior swing high ---
    for sh_i in reversed(swing_highs):
        if sh_i >= confirmed_i:
            continue
        level = float(df["high"].iloc[sh_i])
        if bar["high"] > level and bar["close"] < level:
            return "SHORT", sh_i, level

    return None, None, None


def _detect_fvg(
    df: pd.DataFrame,
    direction: str,
    from_bar_idx: int,
) -> tuple[float, float] | None:
    """
    Detect the nearest Fair Value Gap (FVG / imbalance) after from_bar_idx.

    ICT FVG — a 3-candle imbalance where price leaves a gap between
    candle[i-2] and candle[i]:
      Bullish FVG (LONG context): candle[i].low  > candle[i-2].high
      Bearish FVG (SHORT context): candle[i].high < candle[i-2].low

    Searches bars starting two positions after the swept level up to (not
    including) the current live bar.

    Returns (gap_low, gap_high) of the FVG, or None.
    """
    search_start = max(from_bar_idx + 2, 2)
    search_end   = len(df) - 1      # exclude potentially incomplete live bar

    for i in range(search_start, search_end):
        lo_i   = df["low"].iloc[i]
        hi_i   = df["high"].iloc[i]
        hi_im2 = df["high"].iloc[i - 2]
        lo_im2 = df["low"].iloc[i - 2]

        if direction == "LONG" and lo_i > hi_im2:
            return (float(hi_im2), float(lo_i))

        if direction == "SHORT" and hi_i < lo_im2:
            return (float(hi_i), float(lo_im2))

    return None


def _detect_order_block(
    df: pd.DataFrame,
    direction: str,
    swept_bar_idx: int,
) -> tuple[float, float] | None:
    """
    Detect an ICT Order Block (OB) before the swept price level.

    After a Break of Structure (BOS) via liquidity sweep, the OB is defined as
    the last opposing candle immediately before the swept level — the final
    institutional order that caused the move being retraced.

      LONG  OB: last bearish candle (close < open) before the swept swing low
      SHORT OB: last bullish candle (close > open) before the swept swing high

    Searches up to 20 bars back from swept_bar_idx.

    Returns (ob_low, ob_high) of the OB candle, or None.
    """
    search_end   = swept_bar_idx - 1
    search_start = max(0, swept_bar_idx - 20)

    for i in range(search_end, search_start - 1, -1):
        o = float(df["open"].iloc[i])
        c = float(df["close"].iloc[i])

        if direction == "LONG"  and c < o:      # bearish candle → OB for long
            return (float(df["low"].iloc[i]), float(df["high"].iloc[i]))

        if direction == "SHORT" and c > o:      # bullish candle → OB for short
            return (float(df["low"].iloc[i]), float(df["high"].iloc[i]))

    return None


# ══════════════════════════════════════════════════════════════════════════════
#  Lucid prop-rule guard
# ══════════════════════════════════════════════════════════════════════════════

def _check_lucid_rules(state: dict) -> tuple[bool, str]:
    """
    Evaluate all Lucid 25K prop-eval constraints before issuing a signal.

    Returns:
        (allowed: bool, reason: str)  — reason is empty when allowed.
    """
    # 1. Daily trade cap
    if state["trade_count"] >= LUCID["max_trades_day"]:
        return False, f"Daily trade cap reached ({state['trade_count']}/{LUCID['max_trades_day']})"

    # 2. Daily loss limit
    if state["day_pnl"] <= -LUCID["daily_loss_limit"]:
        return False, f"Daily loss limit hit (day P&L ${state['day_pnl']:+.2f})"

    # 3. Trailing drawdown from peak equity
    current_account = LUCID["account_size"] + state["day_pnl"]
    drawdown = state["peak_equity"] - current_account
    if drawdown >= LUCID["max_trailing_dd"]:
        return False, (
            f"Trailing DD limit hit — ${drawdown:.2f} from peak "
            f"${state['peak_equity']:,.2f}"
        )

    # 4. Consistency cap — no single day > 45 % of cumulative profit
    total_profit = state["peak_equity"] - LUCID["account_size"]
    if total_profit > 0:
        cap = LUCID["consistency_cap"] * total_profit
        if state["day_pnl"] >= cap:
            return False, (
                f"Consistency cap — today ${state['day_pnl']:+.2f} "
                f">= 45 % of cumulative profit ${total_profit:.2f}"
            )

    return True, ""


# ══════════════════════════════════════════════════════════════════════════════
#  Signal message builder
# ══════════════════════════════════════════════════════════════════════════════

def _build_signal_message(
    direction:     str,
    entry_price:   float,
    confidence:    str,
    factors:       list[str],
    fvg:           tuple[float, float] | None,
    ob:            tuple[float, float] | None,
    swept_level:   float,
    zone_label:    str,
    state:         dict,
) -> str:
    """
    Construct an HTML-formatted Telegram signal message.

    Parameters
    ----------
    direction   : "LONG" or "SHORT"
    entry_price : approximate entry (last confirmed close)
    confidence  : "HIGH" (all 3 factors) or "MEDIUM" (2 of 3)
    factors     : e.g. ["Sweep", "FVG", "OB"]
    fvg         : (low, high) of detected FVG, or None
    ob          : (low, high) of detected OB, or None
    swept_level : price level of the swept swing high/low
    zone_label  : e.g. "London Session"
    state       : current daily state dict
    """
    arrow      = "🟢 LONG"  if direction == "LONG"  else "🔴 SHORT"
    conf_stars = "⭐⭐ HIGH" if confidence == "HIGH" else "⭐ MEDIUM"
    factor_str = " + ".join(factors)
    n_factors  = len(factors)

    if direction == "LONG":
        stop = entry_price - STOP_PTS
        tp1  = entry_price + TP1_PTS
        tp2  = entry_price + TP2_PTS
    else:
        stop = entry_price + STOP_PTS
        tp1  = entry_price - TP1_PTS
        tp2  = entry_price - TP2_PTS

    contracts  = max(1, int(RISK / (STOP_PTS * MULT)))
    risk_total = contracts * STOP_PTS * MULT

    trade_num   = state["trade_count"] + 1      # will be incremented after send
    day_pnl_str = f"${state['day_pnl']:+.2f}"
    acct_val    = LUCID["account_size"] + state["day_pnl"]
    now_str     = _et_now().strftime("%H:%M ET")

    fvg_str = f"{fvg[0]:.2f} – {fvg[1]:.2f}" if fvg else "—"
    ob_str  = f"{ob[0]:.2f} – {ob[1]:.2f}"   if ob  else "—"

    return (
        f"<b>🎯 {INSTRUMENT} SIGNAL — {arrow}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Kill Zone:</b>   {zone_label}\n"
        f"<b>Confidence:</b>  {conf_stars}\n"
        f"<b>Factors ({n_factors}/3):</b> {factor_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Entry ≈</b>  {entry_price:.2f}\n"
        f"<b>Stop:</b>    {stop:.2f}  ({STOP_PTS} pts)\n"
        f"<b>TP1:</b>     {tp1:.2f}  ({TP1_PTS} pts / +${RISK * 2:.0f}) → move SL to BE\n"
        f"<b>TP2:</b>     {tp2:.2f}  ({TP2_PTS} pts / +${RISK * 4:.0f})\n"
        f"<b>Size:</b>    {contracts} micro contract(s)\n"
        f"<b>Risk:</b>    ${risk_total:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Swept level:</b>  {swept_level:.2f}\n"
        f"<b>FVG range:</b>    {fvg_str}\n"
        f"<b>OB range:</b>     {ob_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Trade:</b>    {trade_num}/{LUCID['max_trades_day']} today\n"
        f"<b>Day P&amp;L:</b>  {day_pnl_str}  |  Acct: ${acct_val:,.2f}\n"
        f"<b>Time:</b>     {now_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <b>AWAITING RONNIE EXECUTION</b>\n"
        f"Execute manually in NinjaTrader / Tradovate"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Result-file feedback loop
# ══════════════════════════════════════════════════════════════════════════════

_RESULT_PATTERN = re.compile(
    r"^result_(LONG|SHORT)_([+\-]\d+(?:\.\d+)?)\.txt$",
    re.IGNORECASE,
)


def process_result_files(state: dict) -> bool:
    """
    Scan INCOMING_DIR for result files dropped by Ronnie after manual execution.

    File naming convention:
        result_<DIRECTION>_<+/-AMOUNT>.txt
        e.g.  result_LONG_+40.txt   (closed +$40)
              result_SHORT_-20.txt  (closed -$20)

    For each valid file:
      - Adds the P&L to state["day_pnl"]
      - Updates state["peak_equity"] if account equity hit a new high
      - Sends a Telegram confirmation
      - Moves the file to PROCESSED_DIR with a timestamp prefix

    Returns True if at least one result file was processed.
    """
    processed_any = False

    for fpath in sorted(INCOMING_DIR.glob("result_*.txt")):
        match = _RESULT_PATTERN.match(fpath.name)
        if not match:
            log.warning(f"Unrecognised result filename — skipped: {fpath.name}")
            continue

        direction = match.group(1).upper()
        pnl       = float(match.group(2))

        # Update state
        state["day_pnl"] += pnl
        new_account = LUCID["account_size"] + state["day_pnl"]

        # FIX (bug #2): Update peak_equity when account equity hits a new high
        if new_account > state["peak_equity"]:
            state["peak_equity"] = new_account
            log.info(f"New peak equity: ${state['peak_equity']:,.2f}")

        save_state(state)

        # Move to processed/ with timestamp prefix to avoid name collisions
        ts_prefix = _et_now().strftime("%Y%m%d_%H%M%S_")
        dest = PROCESSED_DIR / (ts_prefix + fpath.name)
        try:
            shutil.move(str(fpath), str(dest))
        except Exception as exc:
            log.error(f"Could not move result file: {exc}")

        log.info(
            f"Result processed: {fpath.name} → {direction} ${pnl:+.2f} | "
            f"Day P&L: ${state['day_pnl']:+.2f} | Peak: ${state['peak_equity']:,.2f}"
        )

        # Telegram confirmation
        outcome_emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"{outcome_emoji} <b>Trade Result Logged — {INSTRUMENT}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Direction:</b>    {direction}\n"
            f"<b>Result:</b>       ${pnl:+.2f}\n"
            f"<b>Day P&amp;L:</b>  ${state['day_pnl']:+.2f}\n"
            f"<b>Account:</b>      ${new_account:,.2f}\n"
            f"<b>Peak Equity:</b>  ${state['peak_equity']:,.2f}\n"
            f"<b>Time:</b>         {_et_now().strftime('%H:%M ET')}\n"
            f"Trades today: {state['trade_count']}/{LUCID['max_trades_day']}"
        )
        send_telegram(msg)
        processed_any = True

    return processed_any


# ══════════════════════════════════════════════════════════════════════════════
#  Startup summary
# ══════════════════════════════════════════════════════════════════════════════

def send_startup_summary(state: dict) -> None:
    """
    Send a morning Telegram summary when the watcher starts or on the first
    poll of a new trading day. Throttled to once per calendar day via
    state["startup_sent"].
    """
    today = _et_now().strftime("%Y-%m-%d")
    if state.get("startup_sent") == today:
        return

    msg = (
        f"📊 <b>MNQ Watcher v{__version__} online</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Day:</b>         {state['trade_count']}/{LUCID['max_trades_day']} trades\n"
        f"<b>P&amp;L:</b>     ${state['day_pnl']:+.2f}\n"
        f"<b>Peak Equity:</b> ${state['peak_equity']:,.2f}\n"
        f"<b>Account:</b>     ${LUCID['account_size'] + state['day_pnl']:,.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Kill zones (ET):</b>\n"
        f"  🇬🇧 London: 02:00 – 05:00\n"
        f"  🇺🇸 NY:     08:30 – 11:00\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Strategy: Sweep → FVG / OB confluence\n"
        f"Confidence: HIGH (3/3) or MEDIUM (2/3)\n"
        f"⚠️ All trades executed manually by Ronnie"
    )
    if send_telegram(msg):
        state["startup_sent"] = today
        save_state(state)
        log.info("Startup summary sent via Telegram.")
    else:
        log.info("Startup summary logged (Telegram not available).")
        # Still mark as sent to avoid spam-retrying each poll
        state["startup_sent"] = today
        save_state(state)


# ══════════════════════════════════════════════════════════════════════════════
#  EOD and pre-EOD handlers
# ══════════════════════════════════════════════════════════════════════════════

def check_eod_warnings(state: dict) -> bool:
    """
    Check and fire pre-EOD (3:45 PM ET) and EOD (4:00 PM ET) alerts.

    Pre-EOD fires once per day when hour==15 and minute>=45.
    EOD fires once per day when hour>=16.

    Returns True if it is now past EOD (caller should skip signal logic).
    """
    now_et = _et_now()

    # ── Pre-EOD warning at 3:45 PM ─────────────────────────────────────────
    if (
        now_et.hour == 15
        and now_et.minute >= 45
        and not state.get("pre_eod_sent")
    ):
        msg = (
            f"⚠️ <b>{INSTRUMENT} — 15-Minute Pre-EOD Warning</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Time: {now_et.strftime('%H:%M ET')}\n"
            f"Trades today: {state['trade_count']}/{LUCID['max_trades_day']}\n"
            f"Day P&amp;L: ${state['day_pnl']:+.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ <b>Flatten all positions by 4:00 PM ET!</b>\n"
            f"Lucid rule: no overnight holds."
        )
        send_telegram(msg)
        send_toast("MNQ — Pre-EOD Warning", "15 min to close. Check open positions!")
        state["pre_eod_sent"] = True
        save_state(state)
        log.info("Pre-EOD warning sent (3:45 PM).")

    # ── EOD at 4:00 PM ─────────────────────────────────────────────────────
    if now_et.hour >= LUCID["eod_hour"]:
        if not state.get("eod_sent"):
            acct_val = LUCID["account_size"] + state["day_pnl"]
            msg = (
                f"🔔 <b>{INSTRUMENT} EOD Summary</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Trades today:</b>  {state['trade_count']}/{LUCID['max_trades_day']}\n"
                f"<b>Day P&amp;L:</b>   ${state['day_pnl']:+.2f}\n"
                f"<b>Account:</b>       ${acct_val:,.2f}\n"
                f"<b>Peak Equity:</b>   ${state['peak_equity']:,.2f}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Session closed. No new signals until tomorrow."
            )
            send_telegram(msg)
            send_toast("MNQ EOD", f"Session closed. Day P&L ${state['day_pnl']:+.2f}")
            state["eod_sent"] = True
            save_state(state)
            log.info("EOD summary sent.")
        return True   # signal caller to skip further logic

    return False


# ══════════════════════════════════════════════════════════════════════════════
#  Core poll — runs every 5 minutes
# ══════════════════════════════════════════════════════════════════════════════

def check_signals() -> None:
    """
    Main polling function called every 5 minutes by the scheduler.

    Execution flow:
      1.  Kill-switch file check → halt watcher
      2.  Process incoming result files (feedback loop)
      3.  Market-hours gate (Mon-Fri 01:00-17:30 ET)
      4.  Startup summary (once per day)
      5.  EOD / pre-EOD alerts (returns early if past EOD)
      6.  Kill-zone gate (London 02-05 / NY 08:30-11)
      7.  Lucid prop-rule guard
      8.  Fetch 5-minute OHLCV data
      9.  Detect liquidity sweep
      10. Detect FVG and Order Block
      11. Confidence scoring (need sweep + at least 1 of FVG/OB)
      12. Dedup check (key = "<bar_timestamp>_<direction>")
      13. Build + send signal; update trade_count and peak_equity
    """

    # ── 1. Kill switch ─────────────────────────────────────────────────────
    if KILL_SWITCH.exists():
        log.warning("KILL_SWITCH file detected — watcher halted. Remove file to resume.")
        return

    state = load_state()

    # ── 2. Result files (feedback loop) ───────────────────────────────────
    process_result_files(state)

    # ── 3. Market-hours gate ───────────────────────────────────────────────
    if not _is_market_hours():
        now_et = _et_now()
        log.debug(
            f"Outside market hours ({now_et.strftime('%a %H:%M ET')}) — skipping poll."
        )
        return

    # ── 4. Startup summary (once per day) ─────────────────────────────────
    send_startup_summary(state)

    # ── 5. EOD / pre-EOD ──────────────────────────────────────────────────
    if check_eod_warnings(state):
        return     # past 4:00 PM — no new signals

    # ── 6. Kill-zone gate ─────────────────────────────────────────────────
    in_kz, zone_label = _in_kill_zone()
    if not in_kz:
        now_et = _et_now()
        log.debug(f"Not in kill zone at {now_et.strftime('%H:%M ET')} — skipping.")
        return

    # ── 7. Lucid prop-rule guard ──────────────────────────────────────────
    allowed, reason = _check_lucid_rules(state)
    if not allowed:
        log.info(f"Lucid rules block new signal: {reason}")
        return

    # ── 8. Fetch OHLCV ────────────────────────────────────────────────────
    df = fetch_ohlcv(lookback_bars=60)
    if df is None or len(df) < PIVOT_N * 2 + 4:
        log.warning("Insufficient data for analysis — skipping poll.")
        return

    # ── 9. Detect sweep ───────────────────────────────────────────────────
    direction, swept_idx, swept_level = _detect_sweep(df)
    if direction is None:
        log.debug("No liquidity sweep detected on confirmed bar.")
        return

    log.info(f"Sweep detected: {direction} @ {swept_level:.2f} (bar position {swept_idx})")

    # ── 10. Detect FVG and Order Block ────────────────────────────────────
    fvg = _detect_fvg(df, direction, swept_idx)
    ob  = _detect_order_block(df, direction, swept_idx)

    log.info(
        f"FVG: {f'{fvg[0]:.2f}-{fvg[1]:.2f}' if fvg else 'None'}  |  "
        f"OB: {f'{ob[0]:.2f}-{ob[1]:.2f}' if ob else 'None'}"
    )

    # ── 11. Confidence scoring ────────────────────────────────────────────
    #
    # Sweep is mandatory (already confirmed above).
    # Require at least 1 additional factor (FVG or OB) for a signal.
    #
    #   Sweep + FVG + OB → HIGH   (3/3)
    #   Sweep + FVG      → MEDIUM (2/3)
    #   Sweep + OB       → MEDIUM (2/3)
    #   Sweep only       → SKIP   — too many false signals without confluence
    #
    factors = ["Sweep"]
    if fvg:
        factors.append("FVG")
    if ob:
        factors.append("OB")

    if len(factors) < 2:
        log.info("Sweep only — no FVG or OB confluence. Skipping (high false-signal risk).")
        return

    confidence = "HIGH" if len(factors) == 3 else "MEDIUM"

    # ── 12. Dedup check ───────────────────────────────────────────────────
    #
    # FIX (bug #3): Key includes direction so a LONG and SHORT on the same bar
    # timestamp are treated as distinct signals (they shouldn't both fire, but
    # the old key "confirmed_bar_ts" alone blocked any second signal from the
    # same bar even across separate poll cycles with different direction.
    #
    confirmed_bar_ts = str(df.index[-2])
    dedup_key        = f"{confirmed_bar_ts}_{direction}"

    if dedup_key in state["signals_sent"]:
        log.debug(f"Duplicate signal suppressed: {dedup_key}")
        return

    # ── 13. Build and send signal ─────────────────────────────────────────
    entry_price = float(df["close"].iloc[-2])   # confirmed bar close

    msg = _build_signal_message(
        direction=direction,
        entry_price=entry_price,
        confidence=confidence,
        factors=factors,
        fvg=fvg,
        ob=ob,
        swept_level=swept_level,
        zone_label=zone_label,
        state=state,
    )

    log.info(
        f"SIGNAL [{confidence}] {direction} @ {entry_price:.2f} | "
        f"Factors: {'+'.join(factors)} | Zone: {zone_label}"
    )

    telegram_ok = send_telegram(msg)
    send_toast(
        title=f"MNQ {direction} [{confidence}]",
        message=f"Entry ≈ {entry_price:.2f} | {' + '.join(factors)} | {zone_label}",
    )

    if telegram_ok or True:    # always update state — signal was computed & logged
        # FIX (bug #1): Increment trade_count (was never incremented in v1)
        state["trade_count"] += 1
        state["signals_sent"].append(dedup_key)

        # FIX (bug #2): Update peak_equity at signal time.
        # Actual result-driven updates happen via result file processing.
        # Here we ensure peak reflects current account value (pre-trade).
        current_account = LUCID["account_size"] + state["day_pnl"]
        if current_account > state["peak_equity"]:
            state["peak_equity"] = current_account

        save_state(state)
        log.info(
            f"State updated — trade_count: {state['trade_count']} | "
            f"peak_equity: ${state['peak_equity']:,.2f}"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

_BANNER = f"""
╔═══════════════════════════════════════════════════════╗
║   MNQ Signal Watcher  v{__version__}                          ║
║   Lucid 25K Prop Eval — TJR / ICT Kill Zone Strategy  ║
║   Sweep + FVG / OB confluence | 3-factor scoring      ║
║                                                       ║
║   !! READ-ONLY — NO TRADES AUTO-EXECUTED !!           ║
║   Ronnie executes manually in NinjaTrader/Tradovate   ║
╚═══════════════════════════════════════════════════════╝
  Log file   : {LOG_FILE}
  State file : {STATE_FILE}
  Kill switch: {KILL_SWITCH}
  Incoming   : {INCOMING_DIR}
  Processed  : {PROCESSED_DIR}
"""


def main() -> None:
    """Start the watcher: run once immediately, then poll every 5 minutes."""
    print(_BANNER)
    log.info(f"MNQ Signal Watcher v{__version__} started.")
    log.info(
        f"Config — Ticker: {TICKER} | Risk: ${RISK}/trade | "
        f"Stop: {STOP_PTS}pts | TP1: {TP1_PTS}pts | TP2: {TP2_PTS}pts"
    )
    log.info(
        f"Lucid limits — Max trades: {LUCID['max_trades_day']}/day | "
        f"Daily loss: ${LUCID['daily_loss_limit']} | "
        f"Trailing DD: ${LUCID['max_trailing_dd']}"
    )

    # Run immediately on startup, then every 5 minutes
    check_signals()
    schedule.every(5).minutes.do(check_signals)

    log.info("Scheduler active — polling every 5 minutes. Ctrl+C or KILL_SWITCH to stop.")

    while True:
        if KILL_SWITCH.exists():
            log.warning("KILL_SWITCH detected in main loop — exiting.")
            break
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Watcher stopped by user (KeyboardInterrupt).")
    except SystemExit:
        pass
