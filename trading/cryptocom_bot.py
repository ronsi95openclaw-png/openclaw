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

# Monotonic counter for collision-free trade IDs within the same second
_trade_id_lock    = threading.Lock()
_trade_id_counter = 0


def _next_trade_id(strategy: str) -> str:
    global _trade_id_counter
    with _trade_id_lock:
        _trade_id_counter += 1
        return f"CX{strategy[:3].upper()}{int(time.time())}{_trade_id_counter:04d}"


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
        self._flush_lock  = threading.Lock()   # prevents concurrent date-boundary flushes
        self._thread: Optional[threading.Thread] = None
        self._load_state()

        self._orchestrator = self._init_orchestrator()
        self._reporter     = self._init_reporter()

        # Phase 2+3: portfolio risk engine, metrics, reconciliation
        self._portfolio_risk  = self._init_portfolio_risk()
        self._metrics         = self._init_metrics()
        self._run_startup_reconciliation()
        self._recon_scheduler = self._init_recon_scheduler()
        self._drift_detector  = self._init_drift_detector()
        self._exec_analytics  = self._init_exec_analytics()

        # Phase 4: WebSocket guardian, exchange metadata registry
        self._ws_guardian     = self._init_ws_guardian()

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
            s.scan_interval   = raw.get("scan_interval",   30)
            # Drop malformed positions that would crash the scan loop
            required = {"id", "symbol", "strategy", "side", "entry_price", "size", "sl_price", "tp_price"}
            valid = [p for p in s.open_positions if isinstance(p, dict) and required.issubset(p)]
            if len(valid) < len(s.open_positions):
                logger.warning("Dropped %d malformed position(s) on load",
                               len(s.open_positions) - len(valid))
            s.open_positions = valid
            # Backfill original_entry for positions saved before that field existed
            for pos in s.open_positions:
                if "original_entry" not in pos:
                    pos["original_entry"] = pos.get("entry_price", 0.0)
            # Migrate confidence values stored as old int 0-100 format → 0-1 float
            for rec in s.open_positions + s.trade_log:
                c = rec.get("confidence", 0)
                if isinstance(c, (int, float)) and c > 1.0:
                    rec["confidence"] = round(c / 100.0, 4)
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
                "scan_interval":   self.state.scan_interval,
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

    def _run_strategy_governance(self) -> None:
        """Run nightly attribution analysis and apply governance decisions."""
        try:
            from runtime.strategy_governance import get_governance_engine
            gov = get_governance_engine(dry_run=self.state.demo_mode)
            decisions = gov.run_governance_cycle()
            if decisions:
                applied = [(d.strategy, d.action.value) for d in decisions if d.applied]
                if applied:
                    logger.info("Strategy governance applied: %s", applied)
        except Exception as exc:
            logger.debug("Strategy governance skipped: %s", exc)

    # ── Wiring ────────────────────────────────────────────────────────────────

    def _init_orchestrator(self):
        try:
            from runtime.orchestrator import build_orchestrator
            balance = max(100.0, 1000.0 + self.state.total_pnl) if self.state.demo_mode \
                      else self.state.balance
            return build_orchestrator(with_governance=True, starting_balance=balance)
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

    def _init_portfolio_risk(self):
        try:
            from risk.portfolio_risk import PortfolioRiskEngine
            engine = PortfolioRiskEngine(leverage=LEVERAGE)
            logger.info("PortfolioRiskEngine initialised")
            return engine
        except Exception as exc:
            logger.warning("PortfolioRiskEngine unavailable: %s", exc)
            return None

    def _init_metrics(self):
        try:
            from runtime.metrics import get_registry, start_http_server
            registry = get_registry()
            start_http_server(port=9090)
            logger.info("Prometheus metrics on :9090")
            return registry
        except Exception as exc:
            logger.warning("Metrics registry unavailable: %s", exc)
            return None

    def _init_drift_detector(self):
        try:
            from runtime.drift_detector import DriftDetector
            dd = DriftDetector()
            logger.info("DriftDetector initialised")
            return dd
        except Exception as exc:
            logger.warning("DriftDetector unavailable: %s", exc)
            return None

    def _init_exec_analytics(self):
        try:
            from runtime.execution_analytics import ExecutionAnalyticsEngine
            eng = ExecutionAnalyticsEngine()
            try:
                eng.load_from_file("data/logs/trade_outcomes.jsonl")
            except Exception:
                pass
            logger.info("ExecutionAnalyticsEngine initialised")
            return eng
        except Exception as exc:
            logger.warning("ExecutionAnalyticsEngine unavailable: %s", exc)
            return None

    def _init_recon_scheduler(self):
        try:
            from runtime.reconciliation import ContinuousReconciliationScheduler
            sched = ContinuousReconciliationScheduler(
                interval_seconds=300,
                cooldown_seconds=60,
                demo_mode=self.state.demo_mode,
            )
            sched.set_state_provider(lambda: (
                list(self.state.open_positions),
                max(100.0, min(2000.0, 1000.0 + self.state.total_pnl))
                if self.state.demo_mode else self.state.balance,
            ))
            logger.info("ContinuousReconciliationScheduler initialised (5-min interval)")
            return sched
        except Exception as exc:
            logger.warning("ContinuousReconciliationScheduler unavailable: %s", exc)
            return None

    def _init_ws_guardian(self):
        try:
            from runtime.ws_guardian import get_guardian
            guardian = get_guardian()
            logger.info("WSGuardian initialised")
            return guardian
        except Exception as exc:
            logger.warning("WSGuardian unavailable: %s", exc)
            return None

    def _run_startup_reconciliation(self) -> None:
        try:
            from runtime.reconciliation import reconcile_on_startup
            balance = (
                max(100.0, min(2000.0, 1000.0 + self.state.total_pnl))
                if self.state.demo_mode
                else self.state.balance
            )
            report = reconcile_on_startup(
                local_positions=list(self.state.open_positions),
                local_balance=balance,
                demo_mode=self.state.demo_mode,
            )
            if report.halt_required:
                logger.critical(
                    "Startup reconciliation HALT required: %s", report.summary()
                )
                self.state.status_msg = "HALTED — reconciliation failure"
            elif not report.passed:
                logger.warning("Startup reconciliation warnings: %s", report.summary())
            else:
                logger.info("Startup reconciliation passed (%dms)", report.duration_ms)
        except Exception as exc:
            logger.warning("Startup reconciliation error (non-fatal): %s", exc)

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
        if self._recon_scheduler:
            self._recon_scheduler.start()
        logger.info("CryptoComBot started (demo=%s)", self.state.demo_mode)

    def stop(self) -> None:
        self._stop.set()
        self.state.running    = False
        self.state.status_msg = "Stopped"
        if self._orchestrator:
            self._orchestrator.stop()
        if self._recon_scheduler:
            self._recon_scheduler.stop()
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
                # Strategy governance: run attribution and apply decay/quarantine decisions
                try:
                    self._run_strategy_governance()
                except Exception as _e:
                    logger.debug("Strategy governance error (non-fatal): %s", _e)

            self._stop.wait(self.state.scan_interval)

    def _scan(self) -> None:
        from trading.strategies import (
            ema_cross_strategy, rsi_mean_revert_strategy,
            breakout_strategy, bollinger_band_strategy, trend_follow_strategy,
            _rsi, _atr,
        )

        _scan_start = time.monotonic()
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
            if not self._validate_candles(symbol, candles):
                continue

            closes = [c["close"] for c in candles]
            price  = closes[-1]

            # Feed live price into drift detector
            if self._drift_detector:
                try:
                    self._drift_detector.update_price(
                        symbol, price, int(time.time() * 1000)
                    )
                except Exception:
                    pass

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
                bollinger_band_strategy(symbol, candles),
                trend_follow_strategy(symbol, candles),
            ]

            for sig in signals:
                eff_conf = self.weights.effective_confidence(sig.strategy, sig.confidence)

                if sig.action == "hold":
                    continue

                # Max 1 open position per strategy — prevents one strategy
                # from filling all slots and forces portfolio diversification
                if any(p["strategy"] == sig.strategy for p in self.state.open_positions):
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
                    logger.warning(
                        "Signal SKIPPED [%s/%s]: size=0 (sl_pct=%.4f, price=%.4f, balance=%.2f)",
                        symbol, sig.strategy, sig.sl_pct, price, balance,
                    )
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
                        logger.warning(
                            "Signal SKIPPED [%s/%s]: adjusted size=0 "
                            "(sl_usd=%.6f, risk_usd=%.6f, adjusted_risk=%.4f)",
                            symbol, sig.strategy, sl_usd, risk_usd, adjusted_risk,
                        )
                        continue

                # Correlated exposure gate — block only when 2+ existing positions are
                # already in the same direction (adding a 3rd would be all-in one way)
                same_dir = sum(1 for p in self.state.open_positions
                               if p.get("side") == sig.action)
                if same_dir >= 2:
                    logger.info(
                        "Signal BLOCKED [%s/%s]: %d same-direction positions already open",
                        symbol, sig.strategy, same_dir,
                    )
                    continue

                # ATR volatility scalar: reduce size when short-term vol > baseline
                # Protects against over-sizing into sudden volatility spikes
                try:
                    atr_short = _atr(candles[-10:], 5)
                    atr_base  = _atr(candles, 14)
                    if atr_base > 0:
                        vol_ratio = atr_short / atr_base
                        if vol_ratio > 1.3:
                            size = round(size * max(0.6, 1.0 / vol_ratio), 6)
                except Exception:
                    pass

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

        # Phase 2 metrics
        _scan_ms = (time.monotonic() - _scan_start) * 1000
        if self._metrics:
            try:
                self._metrics.record_scan_duration(_scan_ms / 1000.0)
                self._metrics.update_positions(open_cnt)
                self._metrics.update_pnl(self.state.total_pnl)
            except Exception:
                pass

    # ── Market data ───────────────────────────────────────────────────────────

    def _fetch_market_data(self, symbol: str) -> tuple[list[dict] | None, float]:
        # Always try real market data — demo_mode only blocks order execution, not data
        try:
            from trading.cryptocom_mcp_bridge import get_bridge
            bridge  = get_bridge()
            candles = bridge.fetch_candles(symbol, "15m", 100)
            if candles:
                try:
                    from trading.exchange import fetch_funding_rate, to_perp_instrument
                    funding = fetch_funding_rate(to_perp_instrument(symbol))
                except Exception:
                    funding = 0.0
                return candles, funding
        except Exception as e:
            logger.warning("Market data fetch failed [%s]: %s — using simulation", symbol, e)
        # Simulation fallback: only reached if REST and cache both unavailable
        return self._fake_candles(symbol), random.uniform(-0.0003, 0.0003)

    def _validate_candles(self, symbol: str, candles: list) -> bool:
        """Reject candle sets with bad data before any strategy runs on them."""
        import math
        if len(candles) < 30:
            logger.debug("Candle validation: %s — too few candles (%d)", symbol, len(candles))
            return False
        for c in candles:
            for field in ("open", "high", "low", "close", "volume"):
                v = c.get(field, 0)
                if not v or math.isnan(float(v)) or math.isinf(float(v)) or float(v) <= 0:
                    logger.warning(
                        "Candle validation: %s — bad %s value %r — skipping symbol",
                        symbol, field, v,
                    )
                    return False
            if c["high"] < c["low"] or c["high"] < c["close"] or c["low"] > c["close"]:
                logger.warning("Candle validation: %s — OHLC integrity fail — skipping", symbol)
                return False
        return True

    def _refresh_balance(self) -> float:
        if self.state.demo_mode:
            # Cap at 2× starting capital so sizing stays realistic after winning sessions
            return max(100.0, min(2000.0, 1000.0 + self.state.total_pnl))
        try:
            from trading.exchange import get_derivatives_balance
            bal = get_derivatives_balance()
            if bal:
                with self._lock:
                    self.state.balance = bal.get("available", self.state.balance)
                return self.state.balance
        except Exception:
            pass
        try:
            from trading.exchange import get_account_balance
            balances = get_account_balance()
            usdt     = balances.get("USDT", {}).get("available", 0.0)
            if usdt > 0:
                with self._lock:
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
        # WebSocket guardian gate: block entries if WS health is critically degraded
        if self._ws_guardian and self._ws_guardian.should_halt_entries():
            logger.warning(
                "WSGuardian HALT: blocking new position [%s] — WebSocket health degraded",
                sig.symbol,
            )
            return

        # Drift detection gate: block entries if exchange data is critically stale
        if self._drift_detector and self._drift_detector.should_halt_entries():
            logger.warning(
                "DriftDetector HALT: blocking new position [%s] — critical exchange drift",
                sig.symbol,
            )
            return

        # Continuous reconciliation gate: block entries if unresolved CRITICAL mismatch
        if self._recon_scheduler and self._recon_scheduler.should_halt_entries():
            logger.warning(
                "Reconciliation HALT: blocking new position [%s] — unresolved exchange mismatch",
                sig.symbol,
            )
            return

        # Portfolio risk gate: block new positions when exposure limits breached
        if self._portfolio_risk and balance > 0:
            try:
                prices = {p["symbol"]: p.get("entry_price", price)
                          for p in self.state.open_positions}
                prices[sig.symbol] = price
                self._portfolio_risk.update_positions(self.state.open_positions, prices)
                if self._portfolio_risk.should_reduce_positions(balance, regime_label):
                    logger.warning(
                        "PortfolioRisk BLOCKED new position [%s/%s]: exposure limit exceeded",
                        sig.symbol, regime_label,
                    )
                    return
            except Exception as _pre:
                logger.debug("Portfolio risk check error (non-fatal): %s", _pre)

        sl = price * (1 - sig.sl_pct / 100) if sig.action == "long" else price * (1 + sig.sl_pct / 100)
        tp = price * (1 + sig.tp_pct / 100) if sig.action == "long" else price * (1 - sig.tp_pct / 100)
        trade_id = _next_trade_id(sig.strategy)

        # DCA split-entry: open 60% now, reserve 40% for a DCA add
        # DCA triggers at 50% of SL distance from entry
        dca_size     = round(size * 0.4, 6)
        initial_size = round(size * 0.6, 6)
        dca_dist_pct = sig.sl_pct * 0.5
        dca_trigger  = (price * (1 - dca_dist_pct / 100) if sig.action == "long"
                        else price * (1 + dca_dist_pct / 100))

        # Partial TP: close 50% of position at halfway to full TP, move SL to breakeven
        partial_tp_dist  = sig.tp_pct * 0.5
        partial_tp_price = (price * (1 + partial_tp_dist / 100) if sig.action == "long"
                            else price * (1 - partial_tp_dist / 100))

        if not self.state.demo_mode:
            try:
                from trading.executor import open_position
                result = open_position(
                    symbol=sig.symbol,
                    side=sig.action.upper(),
                    sl_price=round(sl, 6),
                    tp_price=round(tp, 6),
                    qty=initial_size,   # pass exact base-currency qty from risk model
                    leverage=LEVERAGE,
                )
                if not result.get("sl_tp_ok"):
                    # Position opened on exchange but missing SL/TP — do NOT track in state.
                    # Operator must manually close this on the exchange.
                    logger.critical(
                        "UNHEDGED position on exchange for %s — SL/TP placement failed. "
                        "NOT tracking in bot state. Manual close required on exchange.",
                        sig.symbol,
                    )
                    self.state.status_msg = f"UNHEDGED {sig.symbol} — manual close needed"
                    return
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
                                  sig.strategy, sig.confidence), 4),
            "reason":         sig.reason,
            "regime_label":   regime_label,
            # DCA fields
            "dca_size":          dca_size,
            "dca_trigger":       round(dca_trigger, 6),
            "dca_count":         0,
            "original_entry":    price,
            "partial_tp_price":  round(partial_tp_price, 6),
            "partial_tp_taken":  False,
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

        # Emit POSITION_OPENED event to EventStore
        try:
            from runtime.event_store import EventStore, EventType
            _es = EventStore()
            _es.append(
                event_type=EventType.POSITION_OPENED,
                trace_id=trade_id,
                payload={
                    "symbol": sig.symbol, "strategy": sig.strategy,
                    "side": sig.action, "entry_price": price, "size": initial_size,
                    "sl_price": round(sl, 6), "tp_price": round(tp, 6),
                    "regime": regime_label, "demo": self.state.demo_mode,
                },
                symbol=sig.symbol,
                strategy=sig.strategy,
            )
        except Exception:
            pass

        self._save_state()

    def _check_positions(self) -> None:
        # If capital engine says flatten-all (EMERGENCY_HALT), close every open position
        if self._orchestrator and self._orchestrator._capital:
            try:
                if self._orchestrator._capital.should_flatten_all():
                    logger.critical(
                        "EMERGENCY_HALT: flattening %d open position(s)",
                        len(self.state.open_positions),
                    )
                    for pos in list(self.state.open_positions):
                        price = self._current_price(pos)
                        self._close_position(pos, "loss", price)
                    return
            except Exception as _e:
                logger.warning("Flatten-all check failed (non-fatal): %s", _e)

        to_close = []
        for pos in list(self.state.open_positions):
            price = self._current_price(pos)
            pos["current_price"] = price

            # DCA add-on: if price hit the DCA trigger and we haven't added yet
            if pos.get("dca_count", 0) == 0 and pos.get("dca_size", 0) > 0:
                dca_trig = pos["dca_trigger"]
                hit_dca  = (price <= dca_trig) if pos["side"] == "long" else (price >= dca_trig)
                if hit_dca:
                    size1     = pos["size"]
                    size2     = pos["dca_size"]
                    entry1    = pos["entry_price"]
                    avg_entry = (entry1 * size1 + price * size2) / (size1 + size2)

                    dca_ok = True
                    if not self.state.demo_mode:
                        try:
                            from trading.exchange import (
                                cancel_all_orders, place_perp_order, to_perp_instrument,
                            )
                            instr     = to_perp_instrument(pos["symbol"])
                            exit_side = "SELL" if pos["side"] == "long" else "BUY"
                            total_qty = round(size1 + size2, 6)
                            cancel_all_orders(instr)
                            place_perp_order(instr,
                                             "BUY" if pos["side"] == "long" else "SELL",
                                             "MARKET", size2)
                            place_perp_order(instr, exit_side, "STOP_LOSS",  total_qty,
                                             ref_price=pos["sl_price"])
                            place_perp_order(instr, exit_side, "TAKE_PROFIT", total_qty,
                                             ref_price=pos["tp_price"])
                        except Exception as exc:
                            logger.warning("DCA live order failed [%s]: %s — skipping state update",
                                           pos["symbol"], exc)
                            dca_ok = False

                    # Only update bot state if orders succeeded (or in demo mode)
                    if dca_ok:
                        with self._lock:
                            pos["entry_price"] = round(avg_entry, 6)
                            pos["size"]        = round(size1 + size2, 6)
                            pos["dca_count"]   = 1
                            pos["dca_size"]    = 0.0   # clear inside lock, only on success
                    logger.info("DCA ADD %s %s dca@%.4f  avg_entry=%.4f  size=%.6f",
                                pos["side"].upper(), pos["symbol"],
                                price, avg_entry, pos["size"])
                    self._append_log({"event": "dca_add", "id": pos["id"],
                                      "symbol": pos["symbol"], "dca_price": round(price, 4),
                                      "avg_entry": round(avg_entry, 6), "new_size": pos["size"]})

            # Partial TP: close 50% and move SL to breakeven at halfway to full TP
            if not pos.get("partial_tp_taken") and pos.get("partial_tp_price"):
                mult_p     = 1 if pos["side"] == "long" else -1
                hit_partial = ((price >= pos["partial_tp_price"]) if pos["side"] == "long"
                               else (price <= pos["partial_tp_price"]))
                if hit_partial:
                    half_size = round(pos["size"] * 0.5, 6)
                    with self._lock:
                        # Read entry_price inside lock — DCA may have updated it concurrently
                        entry_price = pos["entry_price"]
                        partial_pnl = mult_p * (price - entry_price) * half_size * LEVERAGE
                        pos["size"]             = half_size
                        pos["sl_price"]         = entry_price  # free ride: SL → breakeven
                        pos["partial_tp_taken"] = True
                        self.state.total_pnl = round(self.state.total_pnl + partial_pnl, 4)
                    logger.info("PARTIAL TP %s %s @%.4f  pnl=%+.4f  SL→BE  remaining=%.6f",
                                pos["side"].upper(), pos["symbol"], price, partial_pnl, half_size)
                    self._append_log({"event": "partial_tp", "id": pos["id"],
                                      "price": round(price, 4), "pnl": round(partial_pnl, 4),
                                      "remaining_size": half_size})

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
                "confidence":   pos.get("confidence", 0.0),
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

            # Obsidian knowledge vault — trade journal entry
            try:
                import sys
                sys.path.insert(0, str(Path.home() / "ai-system"))
                from obsidian.trade_journal_writer import write_trade
                write_trade(outcome_record)
            except Exception:
                pass
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
                confidence=pos.get("confidence", 0.0),
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

        # Emit POSITION_CLOSED event to EventStore
        try:
            from runtime.event_store import EventStore, EventType
            _es = EventStore()
            _es.append(
                event_type=EventType.POSITION_CLOSED,
                trace_id=pos.get("id", ""),
                payload={
                    "symbol": pos["symbol"], "strategy": pos["strategy"],
                    "side": pos["side"], "entry_price": pos["entry_price"],
                    "exit_price": exit_price, "pnl": round(pnl, 4),
                    "outcome": outcome, "demo": self.state.demo_mode,
                },
                symbol=pos["symbol"],
                strategy=pos["strategy"],
            )
        except Exception:
            pass

        self._save_state()

    def _current_price(self, pos: dict) -> float:
        # Always try real price — demo_mode only blocks order execution
        try:
            from trading.cryptocom_mcp_bridge import get_bridge
            ticker = get_bridge().fetch_ticker(pos["symbol"])
            price  = ticker.get("last", 0.0)
            if price > 0:
                return price
        except Exception:
            pass
        # Fallback: random walk from last known price (simulation only)
        return self._walk_price(pos)

    def _walk_price(self, pos: dict) -> float:
        current = pos.get("current_price", pos["entry_price"])
        # Reduced noise (0.15% per step vs 0.4%) + stronger mean reversion (8% vs 2%)
        # so positions last long enough to test SL/TP logic meaningfully
        drift  = random.gauss(0, 0.0015)
        revert = (pos["entry_price"] - current) / pos["entry_price"] * 0.08
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
        """Write daily summary row to Sheets and run Claude Opus analysis.

        Protected by _flush_lock to prevent concurrent flushes (startup catch-up
        thread vs end-of-day scan loop boundary race).
        """
        if not self._flush_lock.acquire(blocking=False):
            logger.info("flush_daily_summary: another flush already running, skipping")
            return
        try:
            self._flush_daily_summary_inner(notes=notes, run_analysis=run_analysis)
        finally:
            self._flush_lock.release()

    def _flush_daily_summary_inner(self, notes: str = "", run_analysis: bool = True) -> None:
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

        # Obsidian knowledge vault — daily note + strategy performance snapshot
        try:
            import sys
            sys.path.insert(0, str(Path.home() / "ai-system"))
            from obsidian.vault_manager import write_daily_note
            from obsidian.optimization_writer import write_strategy_performance
            write_daily_note(report_date, s["total_pnl"], s["trades_today"],
                             wins, losses, notes)
            write_strategy_performance(s["strategy_weights"])
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
