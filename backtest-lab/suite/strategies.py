"""Five crypto strategy signal engines for the vibe-trading backtest suite.

Each engine exposes `generate(data_map) -> {code: pd.Series}` where the Series is
the per-bar TARGET POSITION (1=long, -1=short, 0=flat), forward-filled so trades
span multiple bars (the CryptoEngine treats a change in the series as a trade).

- SMC: verbatim logic from the shipped smc skill (BOS/ChoCH + FVG).
- EMA Trend Retest / Range Breakout / Liquidity Sweep: hand-built from the
  documented logic of the technical-basic / smc skills.
- Funding Rate MR: hand-built; reads a 'funding' column attached to the perp df.
"""
from typing import Dict

import numpy as np
import pandas as pd
from smartmoneyconcepts import smc


def _state_machine(enter_long, enter_short, exit_long, exit_short, close, stop_pct=0.12):
    """Build a forward-filled position series from entry/exit boolean Series.

    A position is held until its exit condition fires (or opposite entry flips,
    or a per-trade stop-loss of ``stop_pct`` from entry is breached). The stop
    keeps shorts from losing >100% (which would drive equity negative and break
    the engine's annualisation) and reflects how these strategies trade in
    practice (tight invalidation).
    """
    el = enter_long.values
    es = enter_short.values
    xl = exit_long.values
    xs = exit_short.values
    cl = close.values
    n = len(close)
    pos = np.zeros(n, dtype=int)
    state = 0
    entry = 0.0
    for i in range(n):
        if state == 1:
            stopped = stop_pct is not None and entry > 0 and (cl[i] - entry) / entry <= -stop_pct
            if stopped or xl[i] or es[i]:
                state = 0
        elif state == -1:
            stopped = stop_pct is not None and entry > 0 and (cl[i] - entry) / entry >= stop_pct
            if stopped or xs[i] or el[i]:
                state = 0
        if state == 0:
            if el[i]:
                state = 1
                entry = cl[i]
            elif es[i]:
                state = -1
                entry = cl[i]
        pos[i] = state
    return pd.Series(pos, index=close.index)


class SMCEngine:
    """Smart Money Concepts (ICT): ChoCH direction + BOS confirm + FVG filter."""

    def __init__(self, swing_length: int = 10, close_break: bool = True):
        self.swing_length = swing_length
        self.close_break = close_break

    def generate(self, data_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
        out = {}
        for code, df in data_map.items():
            sig = pd.Series(0, index=df.index)
            ohlc = df[["open", "high", "low", "close", "volume"]].copy()
            if len(ohlc) >= self.swing_length * 2:
                try:
                    swing = smc.swing_highs_lows(ohlc, swing_length=self.swing_length)
                    bc = smc.bos_choch(ohlc, swing_highs_lows=swing, close_break=self.close_break)
                    fvg = smc.fvg(ohlc)
                    bos = bc["BOS"].fillna(0).astype(int)
                    choch = bc["CHOCH"].fillna(0).astype(int)
                    fv = fvg["FVG"].fillna(0).astype(int)
                    structure = choch.where(choch != 0, bos)
                    buy = (structure == 1) & (fv >= 0)
                    sell = (structure == -1) & (fv <= 0)
                    sig[:] = (buy.astype(int) - sell.astype(int)).values
                except Exception as e:
                    print(f"  {code} SMC error: {e}")
            out[code] = sig
        return out


class EMATrendRetestEngine:
    """Trend = EMA(fast) vs EMA(slow); enter on a pullback that retests EMA(fast)
    and resumes in the trend direction; hold until the trend flips."""

    def __init__(self, fast: int = 20, slow: int = 50):
        self.fast, self.slow = fast, slow

    def generate(self, data_map):
        out = {}
        for code, df in data_map.items():
            c, h, l = df["close"], df["high"], df["low"]
            ef = c.ewm(span=self.fast, adjust=False).mean()
            es = c.ewm(span=self.slow, adjust=False).mean()
            up, down = ef > es, ef < es
            enter_long = up & (l <= ef) & (c > ef)      # pullback into EMA, closes above
            enter_short = down & (h >= ef) & (c < ef)
            out[code] = _state_machine(enter_long, enter_short, down, up, c)
        return out


class RangeBreakoutEngine:
    """Donchian breakout: long on close above prior N-bar high, short on close
    below prior N-bar low; hold until the opposite channel breaks."""

    def __init__(self, lookback: int = 20):
        self.n = lookback

    def generate(self, data_map):
        out = {}
        for code, df in data_map.items():
            c = df["close"]
            upper = df["high"].rolling(self.n).max().shift(1)
            lower = df["low"].rolling(self.n).min().shift(1)
            enter_long = c > upper
            enter_short = c < lower
            out[code] = _state_machine(enter_long, enter_short, enter_short, enter_long, c)
        return out


class LiquiditySweepEngine:
    """Stop-hunt reversal: price wicks below a prior N-bar low then reclaims it
    (bullish sweep -> long); wicks above prior N-bar high then loses it
    (bearish sweep -> short). Exit on the opposite sweep."""

    def __init__(self, lookback: int = 20):
        self.n = lookback

    def generate(self, data_map):
        out = {}
        for code, df in data_map.items():
            swing_low = df["low"].rolling(self.n).min().shift(1)
            swing_high = df["high"].rolling(self.n).max().shift(1)
            bull = (df["low"] < swing_low) & (df["close"] > swing_low)
            bear = (df["high"] > swing_high) & (df["close"] < swing_high)
            out[code] = _state_machine(bull, bear, bear, bull, df["close"])
        return out


class FundingRateMREngine:
    """Funding-rate mean reversion (perp). Reads a 'funding' column.
    Crowded longs (funding z-score high) -> short; crowded shorts (z low) -> long.
    Flat when funding normalises (|z| < exit)."""

    def __init__(self, window: int = 30, z_entry: float = 1.5, z_exit: float = 0.5):
        self.w, self.ze, self.zx = window, z_entry, z_exit

    def generate(self, data_map):
        out = {}
        for code, df in data_map.items():
            if "funding" not in df.columns:
                out[code] = pd.Series(0, index=df.index)
                continue
            f = df["funding"].astype(float)
            mu = f.rolling(self.w).mean()
            sd = f.rolling(self.w).std().replace(0, np.nan)
            z = (f - mu) / sd
            enter_long = z < -self.ze       # very negative funding -> long
            enter_short = z > self.ze       # very positive funding -> short
            normal = z.abs() < self.zx
            out[code] = _state_machine(enter_long, enter_short, normal, normal, df["close"])
        return out


ENGINES = {
    "SMC": SMCEngine,
    "EMA_Trend_Retest": EMATrendRetestEngine,
    "Range_Breakout": RangeBreakoutEngine,
    "Liquidity_Sweep": LiquiditySweepEngine,
    "Funding_Rate_MR": FundingRateMREngine,
}
