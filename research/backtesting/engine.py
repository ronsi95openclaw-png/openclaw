"""Deterministic event-driven backtesting engine.

BacktestEngine replays a list of Candles, calls the user-supplied
strategy function at every bar, and manages position lifecycle including
fill simulation, MAE/MFE tracking, funding payments, and fee accounting.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from research.types import (
    BacktestResult,
    BacktestTrade,
    Candle,
    Signal,
)
from research.backtesting.fees import BloFinFeeModel, FeeModel
from research.backtesting.slippage import FixedBpsSlippage, SlippageModel
from research.backtesting.funding import FixedFundingModel, FundingModel
from research.backtesting.execution_model import ExecutionModel


# ── Portfolio data structures ─────────────────────────────────────────────────

@dataclass
class OpenPosition:
    """Live position tracked by the engine."""

    symbol:              str
    side:                str       # "long" | "short"
    entry_price:         float
    size:                float
    entry_bar:           int
    sl_price:            float
    tp_price:            float
    max_adverse_price:   float     # worst price seen (tracks MAE)
    max_favorable_price: float     # best price seen  (tracks MFE)
    funding_paid:        float = 0.0
    entry_slippage_usd:  float = 0.0


@dataclass
class PortfolioState:
    """Snapshot of portfolio passed to the strategy function each bar."""

    cash:     float
    equity:   float
    position: Optional[OpenPosition]     # at most one position at a time
    trades:   List[BacktestTrade] = field(default_factory=list)


# ── Engine ────────────────────────────────────────────────────────────────────

# Bars per 8 h for common timeframes (used to decide funding-interval check).
# The engine infers timeframe from the median inter-bar gap.
_8H_MS = 8 * 60 * 60 * 1_000


class BacktestEngine:
    """Deterministic event-driven backtesting engine.

    Parameters
    ----------
    initial_capital:  starting portfolio value in USD
    leverage:         multiplier applied to position notional
    commission_model: FeeModel instance; defaults to BloFinFeeModel
    slippage_model:   SlippageModel instance; defaults to FixedBpsSlippage(5)
    funding_model:    FundingModel instance; defaults to FixedFundingModel
    latency_bars:     bars of latency before fill (0=same bar, 1=next-bar open)
    risk_pct:         % of equity risked per trade (default 1.5)
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        leverage: int = 3,
        commission_model: Optional[FeeModel] = None,
        slippage_model: Optional[SlippageModel] = None,
        funding_model: Optional[FundingModel] = None,
        latency_bars: int = 1,
        risk_pct: float = 1.5,
    ) -> None:
        self.initial_capital = initial_capital
        self.leverage = leverage
        self.risk_pct = risk_pct
        self.latency_bars = max(0, latency_bars)

        self._fee_model = commission_model or BloFinFeeModel()
        self._slip_model = slippage_model or FixedBpsSlippage(5.0)
        self._fund_model = funding_model or FixedFundingModel(0.0001)

        self._exec = ExecutionModel(
            slippage=self._slip_model,
            fees=self._fee_model,
            funding=self._fund_model,
            latency_bars=self.latency_bars,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(
        self,
        candles: List[Candle],
        strategy_fn: Callable[
            [Candle, List[Candle], PortfolioState],
            Optional[Signal],
        ],
        params: Dict[str, Any],
        symbol: str = "BTC-USDT",
        strategy_name: str = "unnamed",
    ) -> BacktestResult:
        """Run strategy over candles and return a complete BacktestResult.

        Parameters
        ----------
        candles:       chronologically ordered OHLCV bars
        strategy_fn:   callable(current_candle, history, portfolio) → Signal | None
        params:        arbitrary strategy parameters (stored in result)
        symbol:        instrument symbol for metadata
        strategy_name: label stored in result
        """
        if not candles:
            return self._empty_result(strategy_name, symbol, params)

        # Detect bar interval to determine funding cadence
        bar_interval_ms = self._detect_bar_interval(candles)
        bars_per_funding = max(1, round(_8H_MS / bar_interval_ms)) if bar_interval_ms > 0 else 32

        cash: float = self.initial_capital
        position: Optional[OpenPosition] = None
        trades: List[BacktestTrade] = []
        equity_curve: List[float] = []
        timestamps: List[datetime] = []

        # Pending signal: (signal, bar_index_when_filled)
        pending_signal: Optional[Signal] = None
        pending_fill_bar: int = -1

        # Track cumulative funding paid per bar cycle
        bars_since_funding: int = 0

        n = len(candles)

        for i, candle in enumerate(candles):
            # ── Funding payment ──────────────────────────────────────────
            bars_since_funding += 1
            if position is not None and bars_since_funding >= bars_per_funding:
                bars_since_funding = 0
                fpay = self._exec.compute_funding_payment(
                    self._to_exec_pos(position), candle, self.leverage
                )
                position.funding_paid += fpay
                cash -= fpay  # positive = you pay; negative = you receive

            # ── Fill pending order ───────────────────────────────────────
            if pending_signal is not None and i >= pending_fill_bar:
                if position is None:  # only enter if flat
                    pos, entry_fee, entry_slip_usd = self._open_position(
                        pending_signal, candle, cash, i
                    )
                    if pos is not None:
                        position = pos
                        cash -= entry_fee
                pending_signal = None
                pending_fill_bar = -1

            # ── Check SL / TP on open position ───────────────────────────
            if position is not None:
                hit_sl = self._exec.should_hit_sl(self._to_exec_pos(position), candle)
                hit_tp = self._exec.should_hit_tp(self._to_exec_pos(position), candle)

                if hit_tp or hit_sl:
                    reason = "tp" if hit_tp else "sl"
                    trade, pnl = self._close_position(
                        position, candle, i, reason, cash
                    )
                    trades.append(trade)
                    cash += pnl
                    position = None

            # ── Update MAE / MFE on live position ────────────────────────
            if position is not None:
                self._update_excursions(position, candle)

            # ── Compute mark-to-market equity ────────────────────────────
            mark = self._mark_position(position, candle)
            equity = cash + mark
            equity_curve.append(equity)
            timestamps.append(
                datetime.fromtimestamp(candle.ts / 1000.0, tz=timezone.utc)
            )

            # ── Strategy call ────────────────────────────────────────────
            portfolio = PortfolioState(
                cash=cash,
                equity=equity,
                position=position,
                trades=list(trades),
            )
            history = candles[: i + 1]
            signal = strategy_fn(candle, history, portfolio)

            if signal is not None and signal.action in ("long", "short"):
                # Only take signal if flat or closing opposite
                if position is None:
                    pending_signal = signal
                    pending_fill_bar = i + self.latency_bars

        # ── Close any open position at end of data ────────────────────────
        if position is not None:
            last_candle = candles[-1]
            trade, pnl = self._close_position(
                position, last_candle, n - 1, "end", cash
            )
            trades.append(trade)
            cash += pnl
            # Rewrite last equity bar
            equity_curve[-1] = cash

        final_capital = cash
        start_time = datetime.fromtimestamp(candles[0].ts / 1000.0, tz=timezone.utc)
        end_time   = datetime.fromtimestamp(candles[-1].ts / 1000.0, tz=timezone.utc)

        return BacktestResult(
            strategy=strategy_name,
            symbol=symbol,
            params=params,
            trades=trades,
            equity_curve=equity_curve,
            timestamps=timestamps,
            initial_capital=self.initial_capital,
            final_capital=final_capital,
            start_time=start_time,
            end_time=end_time,
            metadata={
                "leverage":      self.leverage,
                "risk_pct":      self.risk_pct,
                "latency_bars":  self.latency_bars,
                "total_bars":    n,
            },
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _detect_bar_interval(self, candles: List[Candle]) -> float:
        """Return median inter-bar gap in milliseconds."""
        if len(candles) < 2:
            return 15 * 60 * 1_000  # default: 15 min
        gaps = [
            candles[i + 1].ts - candles[i].ts
            for i in range(min(10, len(candles) - 1))
        ]
        gaps.sort()
        return float(gaps[len(gaps) // 2])

    def _open_position(
        self,
        signal: Signal,
        candle: Candle,
        cash: float,
        bar_idx: int,
    ) -> tuple[Optional[OpenPosition], float, float]:
        """Create an OpenPosition from a signal at a given candle.

        Returns (OpenPosition | None, fee_paid, entry_slippage_usd).
        Returns (None, 0, 0) if sizing produces an invalid position.
        """
        from research.backtesting.fills import FillSimulator
        filler = FillSimulator()
        order_side = "buy" if signal.action == "long" else "sell"
        fill = filler.simulate_market_fill(
            order_side, 1.0, candle, self._slip_model
        )
        entry_price = fill.fill_price

        sl_pct = max(signal.sl_pct, 0.01) / 100.0
        tp_pct = max(signal.tp_pct, 0.01) / 100.0

        risk_usd = cash * (self.risk_pct / 100.0)
        sl_usd_per_unit = entry_price * sl_pct
        if sl_usd_per_unit <= 0:
            return None, 0.0, 0.0
        size = risk_usd / sl_usd_per_unit
        size = max(size, 0.0001)

        if signal.action == "long":
            sl_price = entry_price * (1.0 - sl_pct)
            tp_price = entry_price * (1.0 + tp_pct)
        else:
            sl_price = entry_price * (1.0 + sl_pct)
            tp_price = entry_price * (1.0 - tp_pct)

        notional = size * entry_price * self.leverage
        fee = self._fee_model.compute_taker(notional)
        entry_slip_usd = abs(fill.fill_price - candle.open) * size

        pos = OpenPosition(
            symbol=signal.symbol,
            side=signal.action,
            entry_price=entry_price,
            size=size,
            entry_bar=bar_idx,
            sl_price=sl_price,
            tp_price=tp_price,
            max_adverse_price=entry_price,
            max_favorable_price=entry_price,
            funding_paid=0.0,
            entry_slippage_usd=entry_slip_usd,
        )
        return pos, fee, entry_slip_usd

    def _close_position(
        self,
        pos: OpenPosition,
        candle: Candle,
        bar_idx: int,
        reason: str,
        cash: float,
    ) -> tuple[BacktestTrade, float]:
        """Close position, compute PnL, build BacktestTrade. Returns (trade, cash_delta)."""
        from research.backtesting.fills import FillSimulator
        filler = FillSimulator()

        order_side = "sell" if pos.side == "long" else "buy"

        # For SL/TP hits, use the SL/TP price directly; for signal/end use open
        if reason == "sl":
            exit_price = pos.sl_price
        elif reason == "tp":
            exit_price = pos.tp_price
        else:
            fill = filler.simulate_market_fill(
                order_side, pos.size, candle, self._slip_model
            )
            exit_price = fill.fill_price

        exit_slip_usd = abs(exit_price - candle.open) * pos.size

        if pos.side == "long":
            gross_pnl = (exit_price - pos.entry_price) * pos.size * self.leverage
        else:
            gross_pnl = (pos.entry_price - exit_price) * pos.size * self.leverage

        notional = pos.size * exit_price * self.leverage
        exit_fee = self._fee_model.compute_taker(notional)

        net_pnl = gross_pnl - exit_fee - pos.funding_paid
        notional_entry = pos.size * pos.entry_price
        net_pnl_pct = (net_pnl / notional_entry * 100.0) if notional_entry > 0 else 0.0

        # MAE / MFE in USD (leveraged)
        if pos.side == "long":
            mae = (pos.entry_price - pos.max_adverse_price) * pos.size * self.leverage
            mfe = (pos.max_favorable_price - pos.entry_price) * pos.size * self.leverage
        else:
            mae = (pos.max_adverse_price - pos.entry_price) * pos.size * self.leverage
            mfe = (pos.entry_price - pos.max_favorable_price) * pos.size * self.leverage

        mae = max(0.0, mae)
        mfe = max(0.0, mfe)

        entry_ts = datetime.fromtimestamp(
            candle.ts / 1000.0, tz=timezone.utc
        )  # approximation; accurate entry time needs original candle

        trade = BacktestTrade(
            trade_id=str(uuid.uuid4())[:8],
            symbol=pos.symbol,
            strategy="unnamed",
            side=pos.side,
            entry_time=entry_ts,  # best approximation without storing entry ts
            exit_time=datetime.fromtimestamp(candle.ts / 1000.0, tz=timezone.utc),
            entry_price=pos.entry_price,
            exit_price=exit_price,
            size=pos.size,
            gross_pnl=gross_pnl,
            fees=exit_fee,
            net_pnl=net_pnl,
            net_pnl_pct=net_pnl_pct,
            entry_slippage=pos.entry_slippage_usd,
            exit_slippage=exit_slip_usd,
            max_adverse_excursion=mae,
            max_favorable_excursion=mfe,
            holding_bars=max(1, bar_idx - pos.entry_bar),
            exit_reason=reason,
            funding_paid=pos.funding_paid,
        )
        return trade, net_pnl

    def _update_excursions(self, pos: OpenPosition, candle: Candle) -> None:
        """Update max adverse / favorable price on every bar."""
        if pos.side == "long":
            # Adverse = low (worst case for long)
            pos.max_adverse_price   = min(pos.max_adverse_price,   candle.low)
            pos.max_favorable_price = max(pos.max_favorable_price, candle.high)
        else:
            # Adverse = high (worst case for short)
            pos.max_adverse_price   = max(pos.max_adverse_price,   candle.high)
            pos.max_favorable_price = min(pos.max_favorable_price, candle.low)

    def _mark_position(self, pos: Optional[OpenPosition], candle: Candle) -> float:
        """Return unrealised PnL of an open position at close price."""
        if pos is None:
            return 0.0
        if pos.side == "long":
            return (candle.close - pos.entry_price) * pos.size * self.leverage
        else:
            return (pos.entry_price - candle.close) * pos.size * self.leverage

    def _to_exec_pos(self, pos: OpenPosition):  # type: ignore[return]
        """Convert engine OpenPosition to execution_model's _OpenPosition."""
        from research.backtesting.execution_model import _OpenPosition as _EP
        return _EP(
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            size=pos.size,
            entry_bar=pos.entry_bar,
            sl_price=pos.sl_price,
            tp_price=pos.tp_price,
            max_adverse_price=pos.max_adverse_price,
            max_favorable_price=pos.max_favorable_price,
            funding_paid=pos.funding_paid,
        )

    def _empty_result(
        self,
        strategy_name: str,
        symbol: str,
        params: Dict[str, Any],
    ) -> BacktestResult:
        now = datetime.now(timezone.utc)
        return BacktestResult(
            strategy=strategy_name,
            symbol=symbol,
            params=params,
            trades=[],
            equity_curve=[self.initial_capital],
            timestamps=[now],
            initial_capital=self.initial_capital,
            final_capital=self.initial_capital,
            start_time=now,
            end_time=now,
            metadata={},
        )
