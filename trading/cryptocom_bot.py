"""Crypto.com Trading Bot — primary live trading engine for OpenClaw.

Runtime architecture (data flows top → bottom):
    Claude Opus Analyst   (runtime/claude_analyst.py)  — daily pattern analysis
        ↓ recommendations
    Ruflo Multi-Agent     (runtime/ruflo_agent.py)      — HNSW memory + swarm
        ↓ advisory signals
    Trade Intent Layer    (runtime/intent_pipeline.py)  — schema + regime gate
        ↓ approved intents
    Python Risk Kernel    (risk/capital_preservation.py)— drawdown state machine
        ↓ sized positions
    Execution Engine      (trading/executor.py)         — order placement
        ↓
    Crypto.com Exchange   (trading/exchange.py REST + MCP tools for market data)

Live trading uses:
  - Market data   : Crypto.com MCP tools (mcp__f177133f__*) when in Claude session,
                    falling back to trading/exchange.py REST API for autonomous runs
  - Order execution: Crypto.com wallet/exchange REST API (CRYPTOCOM_API_KEY required)

Demo mode: fully simulated — no real orders, no API keys needed.

Requires .env:
    CRYPTOCOM_API_KEY=
    CRYPTOCOM_SECRET=
    ANTHROPIC_API_KEY=          (for Claude Opus daily analysis)
    GOOGLE_SHEETS_CREDENTIALS_FILE=/path/to/credentials.json  (optional)
    GOOGLE_SHEET_ID=<your-sheet-id>  (optional)
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

logger = logging.getLogger("openclaw.trading.cryptocom_bot")

_STATE_FILE    = Path(__file__).parent.parent / "data" / "cryptocom_state.json"
_LOG_FILE      = Path(__file__).parent.parent / "data" / "logs" / "cryptocom_trades.log"
_OUTCOMES_FILE = Path(__file__).parent.parent / "data" / "logs" / "trade_outcomes.jsonl"

# Symbols (internal names — mapped to BTCUSD-PERP etc. at execution time)
SYMBOLS        = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
MAX_POSITIONS  = 3
CONF_THRESHOLD = 0.60
LEVERAGE       = 3   # perpetual futures leverage


@dataclass
class BotState:
    running:         bool  = False
    demo_mode:       bool  = True
    risk_pct:        float = 1.5
    scan_interval:   int   = 30
    balance:         float = 1000.0
    total_pnl:       float = 0.0
    trades_today:    int   = 0
    trades_date:     str   = ""
    last_scan:       str   = ""
    last_flush_date: str   = ""
    status_msg:      str   = "Idle"
    open_positions:  list  = field(default_factory=list)
    trade_log:       list  = field(default_factory=list)


class CryptoComBot:
    """Drop-in replacement for BloFinBot, using Crypto.com exchange."""

    def __init__(self) -> None:
        from trading.strategies import StrategyWeightEngine
        self.weights      = StrategyWeightEngine()
        self.state        = BotState()
        self._lock        = threading.Lock()
        self._stop        = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._load_state()

        self._orchestrator = self._init_orchestrator()
        self._reporter     = self._init_reporter()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        if not _STATE_FILE.exists():
            return
        try:
            raw = json.loads(_STATE_FILE.read_text())
            s = self.state
            s.demo_mode      = raw.get("demo_mode",      True)
            s.risk_pct       = raw.get("risk_pct",       1.5)
            s.total_pnl      = raw.get("total_pnl",      0.0)
            s.trades_date    = raw.get("trades_date",    "")
            s.trades_today   = raw.get("trades_today",   0)
            s.trade_log      = raw.get("trade_log",      [])
            s.open_positions  = raw.get("open_positions",  [])
            s.last_flush_date = raw.get("last_flush_date", "")
            # Reset daily counter if the persisted date is stale
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if s.trades_date and s.trades_date != today:
                s.trades_today = 0
                s.trades_date  = today
        except Exception as e:
            logger.warning("State load failed: %s", e)

    def _save_state(self) -> None:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            raw = {
                "demo_mode":      self.state.demo_mode,
                "risk_pct":       self.state.risk_pct,
                "total_pnl":      self.state.total_pnl,
                "trades_date":    self.state.trades_date,
                "trades_today":   self.state.trades_today,
                "trade_log":      self.state.trade_log[-50:],
                "open_positions":  self.state.open_positions,
                "last_flush_date": self.state.last_flush_date,
            }
        _STATE_FILE.write_text(json.dumps(raw, indent=2))

    def _append_log(self, record: dict) -> None:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now(timezone.utc).isoformat()
        line = f"CRYPTOCOM | {ts} | {json.dumps(record)}\n"
        try:
            with open(_LOG_FILE, "a") as f:
                f.write(line)
        except Exception:
            pass

    def _reset_daily_counter_if_needed(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.trades_date != today:
            self.state.trades_today = 0
            self.state.trades_date  = today

    def _auto_disable_weak_strategies(self) -> None:
        """Suspend strategies with weight < 0.3 and 20+ trades (backlog #5).
        Sets weight to 0.0 to prevent any signals — logs once per disable."""
        for name, stats in self.weights.stats.items():
            if stats.trades >= 20 and 0 < stats.weight < 0.3:
                stats.weight = 0.0
                self.weights.save()
                logger.warning(
                    "AUTO-DISABLED %s: weight=%.2f with %d trades — "
                    "set to 0.0 until manual reset",
                    name, stats.weight, stats.trades,
                )

    def _adjust_scan_interval(self) -> None:
        """Dynamic scan interval — fast in trending, slow in ranging (backlog #3).
        Trending regimes: 15s. Ranging/unknown: 60s. Default: 30s."""
        if not self.state.open_positions and not self._orchestrator:
            return
        regimes = {p.get("regime_label", "UNKNOWN") for p in self.state.open_positions}
        trending = {"TRENDING_BULL", "TRENDING_BEAR", "MOMENTUM_BULL", "MOMENTUM_BEAR",
                    "VOL_EXPANSION", "NEWS_SPIKE"}
        slow     = {"RANGING", "MEAN_REVERTING", "VOL_COMPRESSION", "UNKNOWN"}
        if regimes & trending:
            self.state.scan_interval = 15
        elif regimes & slow:
            self.state.scan_interval = 60
        else:
            self.state.scan_interval = 30

    def _auto_apply_opus_weights(self) -> None:
        """Apply weight_adjustments from the latest Claude Opus analysis JSON (backlog #2).
        Reads data/optimization/analysis_*.json and merges recommended adjustments."""
        import glob
        analysis_dir = Path(__file__).parent.parent / "data" / "optimization"
        files = sorted(analysis_dir.glob("analysis_*.json"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            return
        try:
            report = json.loads(files[0].read_text())
            adjustments = report.get("weight_adjustments", {})
            if not adjustments:
                return
            applied = {}
            for strategy, factor in adjustments.items():
                if strategy in self.weights.stats:
                    old = self.weights.stats[strategy].weight
                    # Clamp: Opus can only nudge ±50% per cycle, never outside 0.1–2.0
                    new = max(0.1, min(2.0, old * float(factor)))
                    self.weights.stats[strategy].weight = round(new, 3)
                    applied[strategy] = f"{old:.2f}→{new:.2f}"
            if applied:
                self.weights.save()
                logger.info("Opus weight auto-apply from %s: %s", files[0].name, applied)
        except Exception as exc:
            logger.debug("Opus weight auto-apply skipped: %s", exc)

    # ── Wiring ────────────────────────────────────────────────────────────────

    def _init_orchestrator(self):
        try:
            from runtime.orchestrator import build_orchestrator
            return build_orchestrator(with_governance=True)
        except Exception as exc:
            logger.warning("RuntimeOrchestrator unavailable: %s", exc)
            return None

    def _init_reporter(self):
        try:
            from reporting.google_sheets import get_reporter
            reporter = get_reporter()
            logger.info("Google Sheets reporter initialised")
            return reporter
        except Exception as exc:
            logger.warning("SheetReporter unavailable: %s", exc)
            return None

    # ── Control ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state.running    = True
        self.state.status_msg = "Starting…"
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="cryptocom-bot"
        )
        self._thread.start()
        logger.info("CryptoComBot started (demo=%s)", self.state.demo_mode)

    def stop(self) -> None:
        self._stop.set()
        self.state.running    = False
        self.state.status_msg = "Stopped"
        if self._orchestrator:
            self._orchestrator.stop()
        logger.info("CryptoComBot stopped")

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
        from datetime import timedelta
        # Missed-flush recovery: if yesterday's summary was never flushed (e.g. bot
        # was offline at midnight), run it now in a background thread so the scan
        # loop isn't delayed.
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        if self.state.last_flush_date and self.state.last_flush_date != yesterday:
            logger.info(
                "Missed daily flush detected (last=%s, expected=%s) — running catch-up",
                self.state.last_flush_date, yesterday,
            )
            threading.Thread(
                target=self.flush_daily_summary,
                kwargs={"notes": "catch_up"},
                daemon=True,
                name="flush-catchup",
            ).start()

        _last_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception as e:
                logger.error("Scan error: %s", e, exc_info=True)
                self.state.status_msg = f"Error: {str(e)[:80]}"

            # End-of-day: flush summary + trigger Claude Opus analysis
            _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if _today != _last_day:
                _last_day = _today
                try:
                    self.flush_daily_summary()
                except Exception as _e:
                    logger.warning("End-of-day flush failed (non-fatal): %s", _e)
                # Auto-apply Claude Opus weight recommendations (backlog #2)
                try:
                    self._auto_apply_opus_weights()
                except Exception as _e:
                    logger.debug("Opus weight auto-apply error (non-fatal): %s", _e)

            self._stop.wait(self.state.scan_interval)

    def _scan(self) -> None:
        from trading.strategies import (
            ema_cross_strategy, rsi_mean_revert_strategy,
            breakout_strategy, trend_follow_strategy,
            _rsi,
        )

        self.state.last_scan  = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        self.state.status_msg = "Scanning…"
        self._reset_daily_counter_if_needed()

        balance = self._refresh_balance()
        mode    = "DEMO" if self.state.demo_mode else "LIVE"
        fired   = 0

        for symbol in SYMBOLS:
            if self._stop.is_set():
                break
            if len(self.state.open_positions) >= MAX_POSITIONS:
                break
            if any(p["symbol"] == symbol for p in self.state.open_positions):
                continue

            candles, funding = self._fetch_market_data(symbol)
            if candles is None:
                continue

            closes = [c["close"] for c in candles]
            price  = closes[-1]

            # RSI — used in Sheets logging for analysis
            try:
                current_rsi = _rsi(closes, 14)
            except Exception:
                current_rsi = 50.0

            # Regime classification (advisory)
            regime_label = "UNKNOWN"
            if self._orchestrator is not None:
                try:
                    from research.types import Candle
                    c_objs = [Candle(**c) for c in candles]
                    regime_label = self._orchestrator.classify_regime(
                        symbol, c_objs
                    ) or "UNKNOWN"
                    if self._reporter:
                        self._reporter.log_regime(symbol, regime_label,
                                                  rsi=current_rsi)
                except Exception:
                    pass

            signals = [
                ema_cross_strategy(symbol, candles),
                rsi_mean_revert_strategy(symbol, candles),
                breakout_strategy(symbol, candles),
                trend_follow_strategy(symbol, candles),
            ]

            for sig in signals:
                eff_conf = self.weights.effective_confidence(sig.strategy, sig.confidence)

                if sig.action == "hold":
                    continue

                # Log every non-hold signal (including blocked) to Sheets Signals tab
                if self._reporter:
                    self._reporter.log_signal(
                        symbol=symbol, price=price, rsi=current_rsi,
                        signal_found=sig.reason[:60], action=sig.action,
                        strategy=sig.strategy, confidence=sig.confidence,
                        effective_conf=eff_conf, regime=regime_label,
                        blocked=eff_conf < CONF_THRESHOLD,
                        block_reason=f"eff_conf {eff_conf:.2f} < {CONF_THRESHOLD}"
                                     if eff_conf < CONF_THRESHOLD else "",
                    )

                if eff_conf < CONF_THRESHOLD:
                    continue
                if len(self.state.open_positions) >= MAX_POSITIONS:
                    break

                size = self._calc_size(balance, price, sig.sl_pct)
                if size <= 0:
                    continue

                # Intent pipeline gate
                if self._orchestrator is not None:
                    verdict = self._orchestrator.process_signal(
                        symbol=symbol, strategy=sig.strategy,
                        action=sig.action, confidence=eff_conf,
                        leverage_requested=float(LEVERAGE),
                        size_pct=self.state.risk_pct,
                        sl_pct=sig.sl_pct, tp_pct=sig.tp_pct,
                        regime_label=regime_label, source="scan_loop",
                    )
                    if not verdict.approved:
                        logger.info("Signal BLOCKED [%s/%s]: %s",
                                    symbol, sig.strategy, verdict.reason)
                        if self._reporter:
                            self._reporter.log_signal(
                                symbol=symbol, price=price, rsi=current_rsi,
                                signal_found=sig.reason[:60], action="BLOCKED",
                                strategy=sig.strategy, confidence=sig.confidence,
                                effective_conf=eff_conf, regime=regime_label,
                                blocked=True, block_reason=verdict.reason,
                            )
                        continue
                    adjusted_risk = verdict.adjusted_size_pct
                    if adjusted_risk > 0:
                        risk_usd = balance * (adjusted_risk / 100.0)
                        sl_usd   = price   * (sig.sl_pct / 100.0)
                        size     = max(0.001, round(risk_usd / sl_usd, 6)) if sl_usd > 0 else 0.0
                    else:
                        size = 0.0
                    if size <= 0:
                        continue

                self._open_position(sig, price, size, regime_label=regime_label,
                                   rsi=current_rsi, balance=balance, mode=mode)
                fired += 1

        self._check_positions()      # close SL/TP hits AFTER opening new positions
        # Auto-disable strategies with weight < 0.3 and 20+ trades (backlog #5)
        self._auto_disable_weak_strategies()
        open_cnt = len(self.state.open_positions)
        self.state.status_msg = (
            f"Scanned {len(SYMBOLS)} symbols — {fired} trade(s) opened. "
            f"{open_cnt} position(s) open."
        )
        self._adjust_scan_interval()
        self._save_state()

    # ── Market data ───────────────────────────────────────────────────────────

    def _fetch_market_data(self, symbol: str) -> tuple[list[dict] | None, float]:
        if self.state.demo_mode:
            return self._fake_candles(symbol), random.uniform(-0.0003, 0.0003)
        try:
            # Use MCP bridge — pulls from injected MCP data if available in session,
            # otherwise falls back to exchange.py REST API automatically
            from trading.cryptocom_mcp_bridge import get_bridge
            bridge  = get_bridge()
            candles = bridge.fetch_candles(symbol, "15m", 100)
            try:
                from trading.exchange import fetch_funding_rate
                funding = fetch_funding_rate(symbol)
            except Exception:
                funding = 0.0
            return (candles if candles else None), funding
        except Exception as e:
            logger.warning("Market data fetch failed [%s]: %s", symbol, e)
            return None, 0.0

    def _refresh_balance(self) -> float:
        if self.state.demo_mode:
            return max(100.0, 1000.0 + self.state.total_pnl)
        try:
            from trading.exchange import get_derivatives_balance
            bal = get_derivatives_balance()
            if bal:
                self.state.balance = bal.get("available", self.state.balance)
                return self.state.balance
        except Exception:
            pass
        try:
            from trading.exchange import get_account_balance
            balances = get_account_balance()
            usdt     = balances.get("USDT", {}).get("available", 0.0)
            if usdt > 0:
                self.state.balance = usdt
                return usdt
        except Exception as e:
            logger.warning("Balance fetch failed: %s", e)
        return self.state.balance

    # ── Position management ───────────────────────────────────────────────────

    def _calc_size(self, balance: float, price: float, sl_pct: float) -> float:
        if price <= 0 or sl_pct <= 0:
            return 0.0
        risk_usd = balance * (self.state.risk_pct / 100.0)
        sl_usd   = price   * (sl_pct / 100.0)
        return max(0.001, round(risk_usd / sl_usd, 6))

    def _open_position(self, sig, price: float, size: float,
                       regime_label: str = "UNKNOWN",
                       rsi: float = 50.0, balance: float = 0.0,
                       mode: str = "DEMO") -> None:
        sl = price * (1 - sig.sl_pct / 100) if sig.action == "long" else price * (1 + sig.sl_pct / 100)
        tp = price * (1 + sig.tp_pct / 100) if sig.action == "long" else price * (1 - sig.tp_pct / 100)
        trade_id = f"CX{sig.strategy[:3]}{int(time.time())}"

        # DCA split-entry: open 60% now, reserve 40% for a DCA add
        # DCA triggers at 50% of SL distance from entry
        dca_size     = round(size * 0.4, 6)
        initial_size = round(size * 0.6, 6)
        dca_dist_pct = sig.sl_pct * 0.5
        dca_trigger  = (price * (1 - dca_dist_pct / 100) if sig.action == "long"
                        else price * (1 + dca_dist_pct / 100))

        if not self.state.demo_mode:
            try:
                from trading.executor import open_position
                notional_60pct = balance * (self.state.risk_pct / 100.0) * 0.6
                open_position(
                    symbol=sig.symbol,
                    side=sig.action.upper(),
                    sl_price=round(sl, 6),
                    tp_price=round(tp, 6),
                    notional_usd=notional_60pct,
                    leverage=LEVERAGE,
                )
            except Exception as e:
                logger.error("Order failed [%s]: %s", sig.symbol, e)
                self.state.status_msg = f"Order failed: {str(e)[:80]}"
                return

        pos = {
            "id":             trade_id,
            "symbol":         sig.symbol,
            "strategy":       sig.strategy,
            "side":           sig.action,
            "entry_price":    price,
            "current_price":  price,
            "size":           initial_size,
            "sl_price":       round(sl, 6),
            "tp_price":       round(tp, 6),
            "unrealized_pnl": 0.0,
            "opened_at":      datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "demo":           self.state.demo_mode,
            "confidence":     round(self.weights.effective_confidence(
                                  sig.strategy, sig.confidence) * 100),
            "reason":         sig.reason,
            "regime_label":   regime_label,
            # DCA fields
            "dca_size":       dca_size,
            "dca_trigger":    round(dca_trigger, 6),
            "dca_count":      0,
            "original_entry": price,
        }

        with self._lock:
            self.state.open_positions.append(pos)
            self.state.trades_today += 1

        logger.info("OPEN %s %s @ %.4f  SL=%.4f  TP=%.4f  [%s]  DCA@%.4f",
                    sig.action.upper(), sig.symbol, price, sl, tp, sig.strategy, dca_trigger)
        self._append_log({"event": "open", "id": trade_id, "symbol": sig.symbol,
                          "strategy": sig.strategy, "side": sig.action,
                          "price": price, "size": initial_size,
                          "dca_size": dca_size, "regime": regime_label})

        try:
            from runtime.telegram_alerts import alert_trade_opened
            alert_trade_opened(
                symbol=sig.symbol, side=sig.action, strategy=sig.strategy,
                entry=price, sl=sl, tp=tp, size=initial_size,
                confidence=self.weights.effective_confidence(sig.strategy, sig.confidence),
                regime=regime_label, demo=self.state.demo_mode,
            )
        except Exception:
            pass

        if self._reporter:
            self._reporter.log_trade_open(
                symbol=sig.symbol, strategy=sig.strategy, side=sig.action,
                entry_price=price, size=initial_size,
                balance=balance or self.state.balance,
                regime=regime_label,
                confidence=self.weights.effective_confidence(sig.strategy, sig.confidence),
                rsi=rsi, mode=mode,
            )

    def _check_positions(self) -> None:
        to_close = []
        for pos in list(self.state.open_positions):
            price = self._current_price(pos)
            pos["current_price"] = price

            # DCA add-on: if price hit the DCA trigger and we haven't added yet
            if pos.get("dca_count", 1) == 0 and pos.get("dca_size", 0) > 0:
                dca_trig = pos["dca_trigger"]
                hit_dca  = (price <= dca_trig) if pos["side"] == "long" else (price >= dca_trig)
                if hit_dca:
                    size1     = pos["size"]
                    size2     = pos["dca_size"]
                    entry1    = pos["entry_price"]
                    avg_entry = (entry1 * size1 + price * size2) / (size1 + size2)

                    if not self.state.demo_mode:
                        try:
                            # Cancel old SL/TP (sized for initial 60%), place fresh ones
                            # for the full combined size at the same absolute price levels
                            from trading.exchange import (
                                cancel_all_orders, place_perp_order, to_perp_instrument,
                            )
                            instr = to_perp_instrument(pos["symbol"])
                            cancel_all_orders(instr)
                            exit_side = "SELL" if pos["side"] == "long" else "BUY"
                            total_qty = round(size1 + size2, 6)
                            # Add DCA market order
                            place_perp_order(instr,
                                             "BUY" if pos["side"] == "long" else "SELL",
                                             "MARKET", size2)
                            # Re-place SL/TP for full size
                            place_perp_order(instr, exit_side, "STOP_LOSS",  total_qty,
                                             ref_price=pos["sl_price"])
                            place_perp_order(instr, exit_side, "TAKE_PROFIT", total_qty,
                                             ref_price=pos["tp_price"])
                        except Exception as exc:
                            logger.warning("DCA live order failed [%s]: %s", pos["symbol"], exc)

                    pos["entry_price"] = round(avg_entry, 6)
                    pos["size"]        = round(size1 + size2, 6)
                    pos["dca_count"]   = 1
                    pos["dca_size"]    = 0.0
                    logger.info("DCA ADD %s %s dca@%.4f  avg_entry=%.4f  size=%.6f",
                                pos["side"].upper(), pos["symbol"],
                                price, avg_entry, pos["size"])
                    self._append_log({"event": "dca_add", "id": pos["id"],
                                      "symbol": pos["symbol"], "dca_price": round(price, 4),
                                      "avg_entry": round(avg_entry, 6), "new_size": pos["size"]})

            mult    = 1 if pos["side"] == "long" else -1
            pnl_pct = mult * (price - pos["entry_price"]) / pos["entry_price"]
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
        mult = 1 if pos["side"] == "long" else -1
        pnl  = mult * (exit_price - pos["entry_price"]) * pos["size"] * LEVERAGE

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

        # Append to outcomes JSONL — feeds Claude Analyst and Ruflo memory
        try:
            _OUTCOMES_FILE.parent.mkdir(parents=True, exist_ok=True)
            outcome_record = {
                "ts":           datetime.now(timezone.utc).isoformat(),
                "id":           pos.get("id", ""),
                "symbol":       pos["symbol"],
                "strategy":     pos["strategy"],
                "side":         pos["side"],
                "outcome":      outcome,
                "pnl":          round(pnl, 4),
                "entry_price":  pos["entry_price"],
                "exit_price":   exit_price,
                "size":         pos["size"],
                "regime":       pos.get("regime_label", "UNKNOWN"),
                "confidence":   pos.get("confidence", 0) / 100.0,
                "signal_reason": pos.get("reason", ""),
                "narrative":    pos.get("narrative", ""),
                "dca_count":    pos.get("dca_count", 0),
                "demo":         self.state.demo_mode,
            }
            # Qwen compression — adds 2-sentence lesson before Claude Opus reads it
            try:
                from runtime.qwen_compressor import compress_trade
                outcome_record["qwen_lesson"] = compress_trade(outcome_record)
            except Exception:
                outcome_record["qwen_lesson"] = ""

            with open(_OUTCOMES_FILE, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(outcome_record) + "\n")
        except Exception as _e:
            logger.debug("Outcome JSONL write failed (non-fatal): %s", _e)

        if not self.state.demo_mode:
            try:
                from trading.executor import close_position
                close_position(pos["symbol"], pos["side"].upper(), pos["size"])
            except Exception as e:
                # Position may already be closed by exchange SL/TP — non-fatal
                logger.warning("Exchange close order skipped (may already be closed): %s", e)

        self.weights.record_result(pos["strategy"], outcome == "win")
        logger.info("CLOSE %s [%s]  PnL=%+.4f", pos["symbol"], outcome, pnl)
        self._append_log({"event": "close", "id": pos["id"], "outcome": outcome,
                          "pnl": round(pnl, 4), "exit_price": exit_price})

        try:
            from runtime.telegram_alerts import alert_trade_closed
            alert_trade_closed(
                symbol=pos["symbol"], outcome=outcome, pnl=pnl,
                total_pnl=self.state.total_pnl, strategy=pos["strategy"],
                demo=self.state.demo_mode,
            )
        except Exception:
            pass

        # Sheets: log close — include balance, mode, win rate note
        if self._reporter:
            win_rate    = self.weights.stats[pos["strategy"]].win_rate
            bal_after   = max(100.0, 1000.0 + self.state.total_pnl) \
                          if self.state.demo_mode else self.state.balance
            self._reporter.log_trade_close(
                symbol=pos["symbol"], strategy=pos["strategy"],
                side=pos["side"], entry_price=pos["entry_price"],
                exit_price=exit_price, size=pos["size"],
                pnl=round(pnl, 4), outcome=outcome,
                balance=round(bal_after, 2),
                regime=pos.get("regime_label", "UNKNOWN"),
                confidence=pos.get("confidence", 0) / 100.0,
                mode="DEMO" if self.state.demo_mode else "LIVE",
                notes=f"WR={win_rate:.0%}",
            )

        # Capital engine update
        if self._orchestrator is not None:
            self._orchestrator.update_capital_state(
                equity=max(100.0, 1000.0 + self.state.total_pnl),
                trade_pnl=pnl,
            )
            self._orchestrator.record_trade_outcome(
                symbol=pos["symbol"], strategy=pos["strategy"],
                pnl=round(pnl, 4), regime=pos.get("regime_label", "UNKNOWN"),
                action=pos["side"].upper(), win=(outcome == "win"),
            )

    def _current_price(self, pos: dict) -> float:
        if self.state.demo_mode:
            return self._walk_price(pos)
        try:
            from trading.exchange import fetch_ticker
            return fetch_ticker(pos["symbol"])["last"]
        except Exception:
            return pos.get("current_price", pos["entry_price"])

    def _walk_price(self, pos: dict) -> float:
        current = pos.get("current_price", pos["entry_price"])
        drift   = random.gauss(0, 0.004)
        revert  = (pos["entry_price"] - current) / pos["entry_price"] * 0.02
        return max(current * 0.5, current * (1 + drift + revert))

    # ── Demo candle generator ─────────────────────────────────────────────────

    def _fake_candles(self, symbol: str) -> list[dict]:
        base  = {"BTC_USDT": 105000, "ETH_USDT": 3500, "SOL_USDT": 180}.get(symbol, 1000)
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
            result.append({"ts": now - (100 - i) * 900,
                            "open": open_p, "high": high, "low": low,
                            "close": close, "volume": vol})
            price = close
        return result

    # ── Public API ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        with self._lock:
            positions = list(self.state.open_positions)
            trade_log = list(self.state.trade_log[:20])

        unreal  = sum((p.get("unrealized_pnl", 0.0) for p in positions), 0.0)
        balance = max(100.0, 1000.0 + self.state.total_pnl) if self.state.demo_mode \
                  else self.state.balance

        capital_state = "UNKNOWN"
        if self._orchestrator is not None:
            try:
                capital_state = self._orchestrator._capital.get_state().name
            except Exception:
                pass

        return {
            "running":          self.is_running(),
            "demo_mode":        self.state.demo_mode,
            "risk_pct":         self.state.risk_pct,
            "balance":          round(balance, 2),
            "total_pnl":        round(self.state.total_pnl, 4),
            "unrealized_pnl":   round(unreal, 4),
            "trades_today":     self.state.trades_today,
            "last_scan":        self.state.last_scan,
            "scan_interval":    self.state.scan_interval,
            "capital_state":    capital_state,
            "status_msg":       self.state.status_msg,
            "open_positions":   positions,
            "trade_log":        trade_log,
            "strategy_weights": self.weights.summary(),
            "sheets_connected": bool(self._reporter and self._reporter.is_connected()),
            "telegram_ok":      __import__("runtime.telegram_alerts",
                                           fromlist=["is_configured"]).is_configured(),
        }

    def flush_daily_summary(self, notes: str = "", run_analysis: bool = True) -> None:
        """Write daily summary row to Sheets and run Claude Opus analysis."""
        from datetime import timedelta
        # If called as a catch-up flush (bot restarted after midnight), report yesterday's date
        flush_date = self.state.last_flush_date
        yesterday  = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        today      = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Use yesterday if this is a missed-flush catch-up, else today
        report_date = yesterday if (notes == "catch_up" and flush_date != yesterday) else today
        s     = self.get_status()

        # Aggregate per-strategy PnL from trade log
        strategy_stats: dict = {}
        for t in self.state.trade_log:
            name = t.get("strategy", "UNKNOWN")
            if name not in strategy_stats:
                strategy_stats[name] = {"pnl": 0.0, "trades": 0}
            strategy_stats[name]["pnl"]    += t.get("pnl", 0)
            strategy_stats[name]["trades"] += 1

        log    = self.state.trade_log
        wins   = sum(1 for t in log if t.get("outcome") == "win")
        losses = sum(1 for t in log if t.get("outcome") == "loss")
        regimes = list({p.get("regime_label", "UNKNOWN")
                        for p in self.state.open_positions})

        if self._reporter:
            self._reporter.log_daily_summary(
                date=report_date,
                balance=s["balance"],
                day_pnl=s["total_pnl"],
                start_balance=1000.0,
                trades=s["trades_today"],
                wins=wins, losses=losses,
                strategy_stats=strategy_stats,
                regimes_seen=regimes,
                notes=notes,
            )

        try:
            from runtime.telegram_alerts import alert_daily_summary
            alert_daily_summary(
                date=report_date, total_pnl=s["total_pnl"],
                trades=s["trades_today"], wins=wins, losses=losses,
                demo=self.state.demo_mode,
            )
        except Exception:
            pass

        # Claude Opus analysis — runs asynchronously so it doesn't block the bot
        if run_analysis and (wins + losses) >= 5:
            import threading
            reporter_ref = self._reporter
            orchestrator_ref = self._orchestrator

            def _analyse():
                try:
                    from runtime.claude_analyst import run_analysis as _run
                    from pathlib import Path

                    # Count how many outcomes have a Qwen lesson (for Sheets logging)
                    outcomes_path = _OUTCOMES_FILE
                    qwen_count = 0
                    if outcomes_path.exists():
                        import json as _json
                        for ln in outcomes_path.read_text().splitlines():
                            try:
                                if _json.loads(ln).get("qwen_lesson"):
                                    qwen_count += 1
                            except Exception:
                                pass

                    report = _run(silent=True)
                    logger.info(
                        "Daily Claude analysis: %s  WR=%.0f%%  actions=%d  qwen_lessons=%d",
                        report.overall_health, report.win_rate_pct,
                        len(report.immediate_actions), qwen_count,
                    )

                    # Log analysis report to Google Sheets "Claude Analysis" tab
                    if reporter_ref:
                        try:
                            reporter_ref.log_analysis_report(
                                date=today,
                                overall_health=report.overall_health,
                                win_rate_pct=report.win_rate_pct,
                                expectancy_usd=report.expectancy_usd,
                                top_failure=report.top_failure_patterns[0] if report.top_failure_patterns else "",
                                top_win=report.top_win_patterns[0] if report.top_win_patterns else "",
                                immediate_action=report.immediate_actions[0] if report.immediate_actions else "",
                                weight_adjustments=report.weight_adjustments,
                                ruflo_directive=report.ruflo_learning_directive,
                                qwen_lessons_used=qwen_count,
                            )
                        except Exception:
                            pass

                    # Feed Ruflo learning directive into the journal
                    if report.ruflo_learning_directive and orchestrator_ref:
                        try:
                            orchestrator_ref.record_trade_outcome(
                                symbol="ALL", strategy="ANALYST",
                                pnl=0.0, regime="DAILY_REVIEW", action="REVIEW",
                                win=report.win_rate_pct >= 55,
                                metadata={"ruflo_directive": report.ruflo_learning_directive,
                                          "immediate_actions": report.immediate_actions},
                            )
                        except Exception:
                            pass
                except Exception as exc:
                    logger.warning("Claude daily analysis failed (non-fatal): %s", exc)
            threading.Thread(target=_analyse, daemon=True, name="claude-analyst").start()

        # Stamp the flush date so startup recovery knows this day was covered
        self.state.last_flush_date = report_date
        self._save_state()
