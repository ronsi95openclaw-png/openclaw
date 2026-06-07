"""
ClawBot — Liquidity Sweep Strategy
==================================
Smart-money / ICT-style mean reversion: price often runs slightly past a prior
swing high/low to trigger resting stop orders, then snaps back. We enter on
the reversion.

LIMITATION (documented for orchestrator + reviewers):
  This pipeline only has CLOSE prices — no OHLC wicks. A real liquidity-grab
  detector wants the WICK that pokes through the prior swing and the body
  that closes back inside. We approximate it with "intra-window excursion
  using closes": some intermediate close pierced the swing level, but the
  MOST RECENT close has reverted back inside the range. This is necessarily
  less sensitive than a wick-based detector — and will miss sweeps that
  happened intrabar without leaving a footprint on the close series.

Algorithm (BUY / bullish sweep):
  1. reference_window = closes[-(swing_lookback + sweep_within) : -sweep_within]
     swing_low = min(reference_window)
  2. recent_window   = closes[-sweep_within:]
  3. If min(recent_window) <= swing_low * (1 - min_breach_pct/100) AND
        closes[-1]        >= swing_low * (1 - max_close_offset_pct/100):
        bias = BUY
  4. HIGH conf  : sweep + bullish RSI divergence
                  (RSI at the new-low candle > RSI at the reference-low candle)
  5. MEDIUM conf: sweep without divergence
  6. HOLD       : no sweep detected
  Mirror logic for SELL (sweep above swing_high + bearish divergence).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from trading.strategy import Signal, calculate_rsi


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class LiquiditySweepConfig:
    swing_lookback: int = 20
    sweep_within: int = 5
    min_breach_pct: float = 0.1        # % beyond the swing required to count as a sweep
    max_close_offset_pct: float = 0.3  # % the current close may sit outside the swing
    rsi_period: int = 14

    coins: list = field(default_factory=lambda: [
        "BTC_USDT", "SOL_USDT", "XRP_USDT", "ETH_USDT"
    ])


# ── Strategy ──────────────────────────────────────────────────────────────────

class LiquiditySweepStrategy:
    """Detect a liquidity sweep (close-based proxy) and trade the reversion.

    Pure: no I/O, no network. Reuses the canonical `Signal` dataclass and
    `calculate_rsi` helper from `trading.strategy`.
    """

    def __init__(self, config: Optional[LiquiditySweepConfig] = None):
        self.config = config or LiquiditySweepConfig()
        # Need enough history for: reference window + recent window + an RSI
        # computation at the candle that made the reference window's extreme.
        # +1 because calculate_rsi requires period+1 closes.
        self.warmup = (
            self.config.swing_lookback
            + self.config.sweep_within
            + self.config.rsi_period
            + 1
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_rsi(closes: List[float], period: int) -> Optional[float]:
        """Return RSI or None if there aren't enough closes."""
        if len(closes) < period + 1:
            return None
        try:
            return calculate_rsi(closes, period)
        except ValueError:
            return None

    @staticmethod
    def _argmin(seq: List[float]) -> int:
        # Tie-breaks to the EARLIEST occurrence (matches "the candle that
        # originally made the low"). seq is small (swing_lookback ~ 20).
        best_i = 0
        best_v = seq[0]
        for i, v in enumerate(seq):
            if v < best_v:
                best_v = v
                best_i = i
        return best_i

    @staticmethod
    def _argmax(seq: List[float]) -> int:
        best_i = 0
        best_v = seq[0]
        for i, v in enumerate(seq):
            if v > best_v:
                best_v = v
                best_i = i
        return best_i

    # ── core ──────────────────────────────────────────────────────────────────

    def evaluate(self, coin: str, closes: List[float]) -> Signal:
        """Evaluate one coin's close series and return a Signal."""
        cfg = self.config
        n = len(closes)

        # Hard guard: need enough closes to define both windows.
        # (Warmup includes RSI requirement too, but divergence is optional
        # and we still want to be able to call MEDIUM conf without RSI when
        # somehow the RSI window is short. We still require both price
        # windows to exist.)
        min_price_len = cfg.swing_lookback + cfg.sweep_within
        if n < min_price_len:
            return Signal(
                coin=coin, action="HOLD",
                rsi=0.0, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason="Insufficient candle data for liquidity sweep.",
                confidence="LOW",
            )

        reference_window = closes[-(cfg.swing_lookback + cfg.sweep_within) : -cfg.sweep_within]
        recent_window = closes[-cfg.sweep_within:]
        last_close = closes[-1]

        swing_low = min(reference_window)
        swing_high = max(reference_window)

        # Sweep thresholds.
        low_breach_level = swing_low * (1.0 - cfg.min_breach_pct / 100.0)
        low_offset_level = swing_low * (1.0 - cfg.max_close_offset_pct / 100.0)
        high_breach_level = swing_high * (1.0 + cfg.min_breach_pct / 100.0)
        high_offset_level = swing_high * (1.0 + cfg.max_close_offset_pct / 100.0)

        current_rsi = self._safe_rsi(closes, cfg.rsi_period) or 0.0

        # ── Bullish sweep (sweep below swing_low, snap back up) ──────────────
        recent_min = min(recent_window)
        if recent_min <= low_breach_level and last_close >= low_offset_level:
            # Index (within closes) of the candle that printed the new low and
            # the candle that originally defined the reference swing low.
            recent_min_offset = self._argmin(recent_window)               # 0..sweep_within-1
            new_low_idx = n - cfg.sweep_within + recent_min_offset
            ref_low_offset = self._argmin(reference_window)               # 0..swing_lookback-1
            ref_low_idx = n - (cfg.swing_lookback + cfg.sweep_within) + ref_low_offset

            # Divergence check: RSI at the new low should be HIGHER than RSI
            # at the original swing low (price made a lower low, momentum
            # didn't — classic bullish divergence).
            rsi_at_new_low = self._safe_rsi(closes[: new_low_idx + 1], cfg.rsi_period)
            rsi_at_ref_low = self._safe_rsi(closes[: ref_low_idx + 1], cfg.rsi_period)
            has_divergence = (
                rsi_at_new_low is not None
                and rsi_at_ref_low is not None
                and rsi_at_new_low > rsi_at_ref_low
            )

            if has_divergence:
                return Signal(
                    coin=coin, action="BUY",
                    rsi=current_rsi, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                    reason=(
                        f"Liquidity sweep below swing low {swing_low:.6f}; "
                        f"close back at {last_close:.6f}. Bullish RSI divergence "
                        f"({rsi_at_new_low:.1f} > {rsi_at_ref_low:.1f})."
                    ),
                    confidence="HIGH",
                )
            return Signal(
                coin=coin, action="BUY",
                rsi=current_rsi, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason=(
                    f"Liquidity sweep below swing low {swing_low:.6f}; "
                    f"close back at {last_close:.6f}. No RSI divergence."
                ),
                confidence="MEDIUM",
            )

        # ── Bearish sweep (sweep above swing_high, snap back down) ───────────
        recent_max = max(recent_window)
        if recent_max >= high_breach_level and last_close <= high_offset_level:
            recent_max_offset = self._argmax(recent_window)
            new_high_idx = n - cfg.sweep_within + recent_max_offset
            ref_high_offset = self._argmax(reference_window)
            ref_high_idx = n - (cfg.swing_lookback + cfg.sweep_within) + ref_high_offset

            rsi_at_new_high = self._safe_rsi(closes[: new_high_idx + 1], cfg.rsi_period)
            rsi_at_ref_high = self._safe_rsi(closes[: ref_high_idx + 1], cfg.rsi_period)
            has_divergence = (
                rsi_at_new_high is not None
                and rsi_at_ref_high is not None
                and rsi_at_new_high < rsi_at_ref_high
            )

            if has_divergence:
                return Signal(
                    coin=coin, action="SELL",
                    rsi=current_rsi, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                    reason=(
                        f"Liquidity sweep above swing high {swing_high:.6f}; "
                        f"close back at {last_close:.6f}. Bearish RSI divergence "
                        f"({rsi_at_new_high:.1f} < {rsi_at_ref_high:.1f})."
                    ),
                    confidence="HIGH",
                )
            return Signal(
                coin=coin, action="SELL",
                rsi=current_rsi, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
                reason=(
                    f"Liquidity sweep above swing high {swing_high:.6f}; "
                    f"close back at {last_close:.6f}. No RSI divergence."
                ),
                confidence="MEDIUM",
            )

        return Signal(
            coin=coin, action="HOLD",
            rsi=current_rsi, macd=0.0, macd_signal_val=0.0, macd_histogram=0.0,
            reason=(
                f"No liquidity sweep. swing_low={swing_low:.6f}, "
                f"swing_high={swing_high:.6f}, close={last_close:.6f}."
            ),
            confidence="LOW",
        )
