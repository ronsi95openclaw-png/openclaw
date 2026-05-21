"""BloFin Adaptive Trading Bot — multi-strategy orchestration layer.

Runs four strategies simultaneously on BTC/ETH/SOL every 30 seconds.
Combines effective confidence scores (raw × strategy weight) to gate entries.
Demo mode runs fully simulated with realistic price walks — no real orders.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("clawbot.trading.blofin_bot")

_STATE_FILE   = Path(__file__).parent.parent / "data" / "blofin_state.json"
_LOG_FILE     = Path(__file__).parent.parent / "data" / "logs" / "blofin_trades.log"
_JOURNAL_FILE = Path(__file__).parent.parent / "data" / "logs" / "signal_journal.jsonl"

SYMBOLS       = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
LEVERAGE      = 3
MAX_POSITIONS = 3     # across all symbols
CONF_THRESHOLD = 0.60  # minimum effective confidence to trade


@dataclass
class BotState:
    running:        bool  = False
    demo_mode:      bool  = True
    risk_pct:       float = 1.5
    scan_interval:  int   = 30
    balance:        float = 1000.0
    total_pnl:      float = 0.0
    trades_today:   int   = 0
    trades_date:    str   = ""   # ISO date of last reset, e.g. "2026-05-20"
    last_scan:      str   = ""
    status_msg:     str   = "Idle"
    open_positions: list  = field(default_factory=list)
    trade_log:      list  = field(default_factory=list)


class BloFinBot:
    def __init__(self) -> None:
        from trading.blofin_strategies import StrategyWeightEngine
        self.weights   = StrategyWeightEngine()
        self.state     = BotState()
        self._lock     = threading.Lock()
        self._stop     = threading.Event()
        self._thread:  Optional[threading.Thread] = None
        self._load_state()

        # Wire the runtime orchestrator — all signals pass through this
        self._orchestrator = self._init_orchestrator()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        if not _STATE_FILE.exists():
            return
        try:
            raw = json.loads(_STATE_FILE.read_text())
            s = self.state
            s.demo_mode    = raw.get("demo_mode",    True)
            s.risk_pct     = raw.get("risk_pct",     1.5)
            s.total_pnl    = raw.get("total_pnl",    0.0)
            s.trades_date  = raw.get("trades_date",  "")
            s.trades_today = raw.get("trades_today", 0)
            s.trade_log    = raw.get("trade_log",    [])
        except Exception as e:
            logger.warning(f"State load failed: {e}")

    def _save_state(self) -> None:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            raw = {
                "demo_mode":    self.state.demo_mode,
                "risk_pct":     self.state.risk_pct,
                "total_pnl":    self.state.total_pnl,
                "trades_date":  self.state.trades_date,
                "trades_today": self.state.trades_today,
                "trade_log":    self.state.trade_log[-50:],
            }
        _STATE_FILE.write_text(json.dumps(raw, indent=2))

    def _reset_daily_counter_if_needed(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.trades_date != today:
            self.state.trades_today = 0
            self.state.trades_date  = today

    def _append_log(self, record: dict) -> None:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now(timezone.utc).isoformat()
        line = f"BLOFIN | {ts} | {json.dumps(record)}\n"
        try:
            with open(_LOG_FILE, "a") as f:
                f.write(line)
        except Exception:
            pass

    def _journal(self, event: str, **fields) -> None:
        """Append one JSONL line to the signal journal — every decision, every reason."""
        _JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        try:
            with open(_JOURNAL_FILE, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass

    # ── Orchestrator wiring ───────────────────────────────────────────────────

    def _init_orchestrator(self):
        """Build RuntimeOrchestrator with all available subsystems."""
        try:
            from runtime.orchestrator import build_orchestrator
            return build_orchestrator(with_governance=False)
        except Exception as exc:
            logger.warning("RuntimeOrchestrator unavailable — signals bypass validation: %s", exc)
            return None

    # ── Control ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state.running    = True
        self.state.status_msg = "Starting…"
        self._thread = threading.Thread(target=self._loop, daemon=True, name="blofin-bot")
        self._thread.start()
        logger.info("BloFin bot started")

    def stop(self) -> None:
        self._stop.set()
        self.state.running    = False
        self.state.status_msg = "Stopped"
        logger.info("BloFin bot stopped")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def configure(self, demo_mode: bool | None = None, risk_pct: float | None = None) -> None:
        if demo_mode is not None:
            self.state.demo_mode = demo_mode
        if risk_pct is not None:
            self.state.risk_pct = max(0.5, min(4.0, risk_pct))
        self._save_state()

    # ── Main scan loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception as e:
                logger.error(f"Scan error: {e}", exc_info=True)
                self.state.status_msg = f"Error: {str(e)[:80]}"
            self._stop.wait(self.state.scan_interval)

    def _scan(self) -> None:
        from trading.blofin_strategies import (
            ema_cross_strategy, rsi_mean_revert_strategy,
            breakout_strategy,  funding_arb_strategy,
        )

        self.state.last_scan  = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        self.state.status_msg = "Scanning…"
        self._reset_daily_counter_if_needed()

        balance     = self._refresh_balance()
        fired       = 0

        for symbol in SYMBOLS:
            if self._stop.is_set():
                break
            if len(self.state.open_positions) >= MAX_POSITIONS:
                break

            # One position per symbol at a time
            if any(p["symbol"] == symbol for p in self.state.open_positions):
                continue

            candles, funding = self._fetch_market_data(symbol)
            if candles is None:
                continue

            signals = [
                ema_cross_strategy(symbol, candles),
                rsi_mean_revert_strategy(symbol, candles),
                breakout_strategy(symbol, candles),
                funding_arb_strategy(symbol, candles, funding),
            ]

            # Classify regime once per symbol (advisory — used for intent validation)
            regime_label = "UNKNOWN"
            if self._orchestrator is not None:
                try:
                    from research.types import Candle
                    c_objs = [Candle(**c) for c in candles]
                    regime_label = self._orchestrator.classify_regime(symbol, c_objs) or "UNKNOWN"
                except Exception:
                    pass

            for sig in signals:
                price    = candles[-1]["close"]
                eff_conf = self.weights.effective_confidence(sig.strategy, sig.confidence)

                if sig.action == "hold":
                    self._journal("hold", symbol=symbol, strategy=sig.strategy,
                                  reason=sig.reason, regime=regime_label)
                    continue

                if eff_conf < CONF_THRESHOLD:
                    self._journal("low_conf", symbol=symbol, strategy=sig.strategy,
                                  action=sig.action, raw_conf=round(sig.confidence, 3),
                                  eff_conf=round(eff_conf, 3), threshold=CONF_THRESHOLD,
                                  reason=sig.reason, regime=regime_label)
                    continue

                if len(self.state.open_positions) >= MAX_POSITIONS:
                    self._journal("max_positions", symbol=symbol, strategy=sig.strategy,
                                  action=sig.action, open=MAX_POSITIONS)
                    break

                size  = self._calc_size(balance, price, sig.sl_pct)
                if size <= 0:
                    continue

                # ── Intent pipeline gate (all signals must pass) ──────────────
                if self._orchestrator is not None:
                    verdict = self._orchestrator.process_signal(
                        symbol=symbol,
                        strategy=sig.strategy,
                        action=sig.action,
                        confidence=eff_conf,
                        leverage_requested=float(LEVERAGE),
                        size_pct=self.state.risk_pct,
                        sl_pct=sig.sl_pct,
                        tp_pct=sig.tp_pct,
                        regime_label=regime_label,
                        source="scan_loop",
                    )
                    if not verdict.approved:
                        logger.info(
                            "Signal BLOCKED by orchestrator [%s/%s]: %s",
                            symbol, sig.strategy, verdict.reason,
                        )
                        self._journal("blocked", symbol=symbol, strategy=sig.strategy,
                                      action=sig.action, eff_conf=round(eff_conf, 3),
                                      regime=regime_label, reason=verdict.reason,
                                      signal_reason=sig.reason)
                        continue
                    # Use risk-adjusted size from verdict.
                    # verdict.adjusted_size_pct is already a % of capital (e.g. 1.5).
                    # Compute directly: risk_usd = balance * pct/100 ÷ (sl as fraction)
                    adjusted_risk = verdict.adjusted_size_pct
                    if adjusted_risk > 0:
                        risk_usd = balance * (adjusted_risk / 100.0)
                        sl_usd   = price   * (sig.sl_pct / 100.0)
                        size     = max(0.001, round(risk_usd / sl_usd, 4)) if sl_usd > 0 else 0.0
                    else:
                        size = 0.0
                    if size <= 0:
                        continue

                self._journal("open", symbol=symbol, strategy=sig.strategy,
                              action=sig.action, price=round(price, 4),
                              eff_conf=round(eff_conf, 3), size=size,
                              sl_pct=sig.sl_pct, tp_pct=sig.tp_pct,
                              regime=regime_label, signal_reason=sig.reason)
                self._open_position(sig, price, size, regime_label=regime_label)
                fired += 1

        self._check_positions()

        open_cnt = len(self.state.open_positions)
        self.state.status_msg = (
            f"Scanned {len(SYMBOLS)} symbols — {fired} trade(s) opened. "
            f"{open_cnt} position(s) open."
        )
        self._save_state()

    def _fetch_market_data(self, symbol: str) -> tuple[list[dict] | None, float]:
        if self.state.demo_mode:
            return self._fake_candles(symbol), random.uniform(-0.0003, 0.0003)
        try:
            from trading.blofin_exchange import fetch_candles, fetch_funding_rate
            candles = fetch_candles(symbol, "15m", 100)
            funding = fetch_funding_rate(symbol)
            return candles, funding
        except Exception as e:
            logger.warning(f"Data fetch failed [{symbol}]: {e}")
            return None, 0.0

    def _refresh_balance(self) -> float:
        if self.state.demo_mode:
            return max(1.0, self.state.balance + self.state.total_pnl)
        try:
            from trading.blofin_exchange import get_balance
            bal = get_balance()
            self.state.balance = bal["usdt"]
        except Exception as e:
            logger.warning(f"Balance fetch failed: {e}")
        return self.state.balance

    # ── Position management ───────────────────────────────────────────────────

    def _calc_size(self, balance: float, price: float, sl_pct: float) -> float:
        """Kelly-inspired sizing: risk_usd / (price × sl_pct)."""
        if price <= 0 or sl_pct <= 0:
            return 0.0
        risk_usd = balance * (self.state.risk_pct / 100.0)
        sl_usd   = price   * (sl_pct / 100.0)
        size     = risk_usd / sl_usd
        return max(0.001, round(size, 4))

    def _open_position(self, sig, price: float, size: float, regime_label: str = "UNKNOWN") -> None:
        sl = price * (1 - sig.sl_pct / 100) if sig.action == "long" else price * (1 + sig.sl_pct / 100)
        tp = price * (1 + sig.tp_pct / 100) if sig.action == "long" else price * (1 - sig.tp_pct / 100)

        trade_id = f"{sig.strategy[:3]}{int(time.time())}"

        if not self.state.demo_mode:
            try:
                from trading.blofin_exchange import place_order
                place_order(sig.symbol, "buy" if sig.action == "long" else "sell",
                            size, sl, tp, LEVERAGE)
            except Exception as e:
                logger.error(f"Order failed [{sig.symbol}]: {e}")
                self.state.status_msg = f"Order failed: {str(e)[:80]}"
                return

        pos = {
            "id":              trade_id,
            "symbol":          sig.symbol,
            "strategy":        sig.strategy,
            "side":            sig.action,
            "entry_price":     price,
            "current_price":   price,
            "size":            size,
            "sl_price":        round(sl, 6),
            "tp_price":        round(tp, 6),
            "unrealized_pnl":  0.0,
            "opened_at":       datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "demo":            self.state.demo_mode,
            "confidence":      round(self.weights.effective_confidence(sig.strategy, sig.confidence) * 100),
            "reason":          sig.reason,
            "regime_label":    regime_label,
        }

        with self._lock:
            self.state.open_positions.append(pos)
            self.state.trades_today += 1

        logger.info(f"OPEN {sig.action.upper()} {sig.symbol} @ {price:.4f}  "
                    f"SL={sl:.4f}  TP={tp:.4f}  [{sig.strategy}]")
        self._append_log({"event": "open", "id": trade_id, "symbol": sig.symbol,
                          "strategy": sig.strategy, "side": sig.action,
                          "price": price, "size": size, "demo": self.state.demo_mode})

    def _check_positions(self) -> None:
        to_close: list[tuple[dict, str, float]] = []

        for pos in list(self.state.open_positions):
            price = self._current_price(pos)
            pos["current_price"] = price

            mult      = 1 if pos["side"] == "long" else -1
            pnl_pct   = mult * (price - pos["entry_price"]) / pos["entry_price"]
            pos["unrealized_pnl"] = round(pnl_pct * pos["entry_price"] * pos["size"] * LEVERAGE, 4)

            hit_sl = (price <= pos["sl_price"]) if pos["side"] == "long" else (price >= pos["sl_price"])
            hit_tp = (price >= pos["tp_price"]) if pos["side"] == "long" else (price <= pos["tp_price"])

            if hit_tp:
                to_close.append((pos, "win",  price))
            elif hit_sl:
                to_close.append((pos, "loss", price))

        for pos, outcome, exit_price in to_close:
            self._close_position(pos, outcome, exit_price)

    def _close_position(self, pos: dict, outcome: str, exit_price: float) -> None:
        mult  = 1 if pos["side"] == "long" else -1
        pnl   = mult * (exit_price - pos["entry_price"]) / pos["entry_price"] \
                * pos["entry_price"] * pos["size"] * LEVERAGE

        with self._lock:
            if pos in self.state.open_positions:
                self.state.open_positions.remove(pos)
            self.state.total_pnl += pnl
            record = {
                **pos,
                "exit_price": exit_price,
                "pnl":        round(pnl, 4),
                "outcome":    outcome,
                "closed_at":  datetime.now(timezone.utc).strftime("%H:%M:%S"),
            }
            self.state.trade_log.insert(0, record)
            self.state.trade_log = self.state.trade_log[:50]

        self.weights.record_result(pos["strategy"], outcome == "win")
        logger.info(f"CLOSE {pos['symbol']} [{outcome}]  PnL={pnl:+.4f}")
        self._append_log({"event": "close", "id": pos["id"], "outcome": outcome,
                          "pnl": round(pnl, 4), "exit_price": exit_price})
        self._journal("close", symbol=pos["symbol"], strategy=pos["strategy"],
                      side=pos["side"], outcome=outcome, pnl=round(pnl, 4),
                      entry_price=pos["entry_price"], exit_price=exit_price,
                      regime=pos.get("regime_label", "UNKNOWN"),
                      trade_id=pos["id"])

        # Feed capital preservation engine after each closed trade
        if self._orchestrator is not None:
            self._orchestrator.update_capital_state(
                equity=self.state.balance + self.state.total_pnl,
                trade_pnl=pnl,
            )
            # Record outcome in Ruflo memory for future advisory lookups (advisory only)
            self._orchestrator.record_trade_outcome(
                symbol=pos.get("symbol", "UNKNOWN"),
                strategy=pos.get("strategy", "UNKNOWN"),
                pnl=round(pnl, 4),
                regime=pos.get("regime_label", "UNKNOWN"),
                action=pos.get("side", "UNKNOWN").upper(),
                win=(outcome == "win"),
            )

    def _current_price(self, pos: dict) -> float:
        if self.state.demo_mode:
            return self._walk_price(pos)
        try:
            from trading.blofin_exchange import fetch_ticker
            return fetch_ticker(pos["symbol"])["last"]
        except Exception:
            return pos.get("current_price", pos["entry_price"])

    def _walk_price(self, pos: dict) -> float:
        current = pos.get("current_price", pos["entry_price"])
        drift   = random.gauss(0, 0.004)
        # Weak mean-reversion keeps demo prices realistic
        revert  = (pos["entry_price"] - current) / pos["entry_price"] * 0.02
        return max(current * 0.5, current * (1 + drift + revert))

    # ── Demo candle generator ─────────────────────────────────────────────────

    def _fake_candles(self, symbol: str) -> list[dict]:
        base  = {"BTC-USDT": 105000, "ETH-USDT": 3500, "SOL-USDT": 180}.get(symbol, 1000)
        price = base * random.uniform(0.93, 1.07)
        now   = int(time.time())
        result = []
        for i in range(100):
            chg    = random.gauss(0, 0.009)
            open_p = price
            close  = price * (1 + chg)
            high   = max(open_p, close) * random.uniform(1.000, 1.006)
            low    = min(open_p, close) * random.uniform(0.994, 1.000)
            vol    = random.uniform(50, 3000)
            # ts required by Candle dataclass for regime classification
            result.append({"ts": now - (100 - i) * 900,
                            "open": open_p, "high": high, "low": low,
                            "close": close, "volume": vol})
            price  = close
        return result

    # ── Public API ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        with self._lock:
            positions = list(self.state.open_positions)
            trade_log = list(self.state.trade_log[:20])

        unreal   = sum(p.get("unrealized_pnl", 0) for p in positions)
        balance  = max(1.0, self.state.balance + self.state.total_pnl) if self.state.demo_mode \
                   else self.state.balance

        return {
            "running":         self.is_running(),
            "demo_mode":       self.state.demo_mode,
            "risk_pct":        self.state.risk_pct,
            "balance":         round(balance, 2),
            "total_pnl":       round(self.state.total_pnl,  4),
            "unrealized_pnl":  round(unreal,                4),
            "trades_today":    self.state.trades_today,
            "last_scan":       self.state.last_scan,
            "status_msg":      self.state.status_msg,
            "open_positions":  positions,
            "trade_log":       trade_log,
            "strategy_weights": self.weights.summary(),
        }
