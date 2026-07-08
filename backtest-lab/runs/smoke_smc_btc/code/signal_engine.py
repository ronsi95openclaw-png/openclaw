"""Smart Money Concepts (ICT) signal engine — runner-compliant.

Strategy logic is the SignalEngine class verbatim from
src/skills/smc/example_signal_engine.py. Only the top-level platform guard and
the __main__ demo block were removed (the runner's AST validator forbids
top-level executable statements). UTF-8 for the smartmoneyconcepts emoji import
is handled via PYTHONUTF8=1 in the runner's environment instead.

Signal logic: ChoCH sets direction, BOS confirms, FVG filters. 1=long, -1=short, 0=flat.
"""

from typing import Dict

import pandas as pd
from smartmoneyconcepts import smc


class SignalEngine:
    """Smart Money Concepts signal engine (BOS/ChoCH structure + FVG filter)."""

    def __init__(self, swing_length: int = 10, close_break: bool = True):
        self.swing_length = swing_length
        self.close_break = close_break

    def generate(self, data_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
        result = {}
        for code, df in data_map.items():
            signal = pd.Series(0, index=df.index)

            ohlc = df[["open", "high", "low", "close", "volume"]].copy()
            ohlc.columns = ["open", "high", "low", "close", "volume"]

            min_bars = self.swing_length * 2
            if len(ohlc) < min_bars:
                print(f"  {code} insufficient bars (<{min_bars}), skipping")
                result[code] = signal
                continue

            try:
                signal = self._compute_signal(ohlc, df.index)
            except Exception as e:
                print(f"  {code} SMC compute error: {e}")

            result[code] = signal
        return result

    def _compute_signal(
        self, ohlc: pd.DataFrame, original_index: pd.Index
    ) -> pd.Series:
        signal = pd.Series(0, index=original_index)

        # 1) Swing highs/lows
        swing_hl = smc.swing_highs_lows(ohlc, swing_length=self.swing_length)

        # 2) BOS / ChoCH structure breaks
        bos_choch = smc.bos_choch(
            ohlc, swing_highs_lows=swing_hl, close_break=self.close_break
        )

        # 3) FVG (fair value gap)
        fvg = smc.fvg(ohlc)

        bos_val = bos_choch["BOS"].fillna(0).astype(int)
        choch_val = bos_choch["CHOCH"].fillna(0).astype(int)
        fvg_val = fvg["FVG"].fillna(0).astype(int)

        # Structure signal: ChoCH first, BOS as fill-in
        structure = choch_val.where(choch_val != 0, bos_val)

        # FVG filter: only signal when FVG is same-direction or neutral
        buy = (structure == 1) & (fvg_val >= 0)
        sell = (structure == -1) & (fvg_val <= 0)

        raw_signal = buy.astype(int) - sell.astype(int)
        signal[:] = raw_signal.values

        return signal
