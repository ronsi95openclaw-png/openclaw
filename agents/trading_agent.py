"""
ClawBot — Autonomous Trading Sub-Agent
=======================================
Handles DCA scheduling, futures signal scanning, backtesting,
strategy optimisation, and self-coder patch proposals.

Public API
----------
  run_trading_cycle(bot, chat_id)  -> str
  get_trading_status()             -> dict
  set_dca(coin, amount, hours)     -> str
  get_signals(coins)               -> list
  run_agent_backtest(coin, days)   -> dict
  trigger_optimize(coin)           -> dict
  get_sheets_analysis(coin)        -> str
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Project imports ──────────────────────────────────────────────────────────
from trading.exchange import (
    fetch_closes,
    fetch_ticker_price,
    get_account_balance,
)
from trading.strategy import (
    RSIMACDConfig,
    RSIMACDStrategy,
    calculate_rsi,
    calculate_macd,
    detect_macd_crossover,
)
from core.brain import ask_hybrid

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT          = Path(__file__).parent.parent
STATE_FILE     = _ROOT / "data" / "trading_agent_state.json"
STRATEGY_FILE  = _ROOT / "data" / "trading_agent_strategy.json"
LOG_FILE       = _ROOT / "data" / "logs" / "trading_agent.log"
PATCHES_DIR    = _ROOT / "data" / "strategy_patches"

# ── Safety patterns (reject if any present in a code patch) ──────────────────
_BLOCKED_PATCH_PATTERNS = [
    r"os\.system",
    r"subprocess",
    r"\beval\b",
    r"\bexec\b",
    r"__import__",
    r"open\s*\(",          # generic file open outside allowed paths
]

# ── Default scan coins ────────────────────────────────────────────────────────
_DEFAULT_COINS = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "XRP_USDT"]

# ── Optimiser grid ────────────────────────────────────────────────────────────
_OPT_RSI_PERIODS   = [10, 12, 14, 16, 18, 20]
_OPT_MACD_FAST     = [8, 10, 12, 14]
_OPT_MACD_SLOW     = [21, 24, 26, 28, 30]

logger = logging.getLogger("clawbot.agents.trading_agent")


# ── JSON-line logger ──────────────────────────────────────────────────────────

def _log(action: str, detail: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts":     datetime.now(timezone.utc).isoformat(),
        "action": action,
        "detail": detail,
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning(f"Log write failed: {exc}")


# ── State helpers ─────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"dca_schedules": [], "last_optimizer_run": 0, "last_cycle_ts": 0}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _load_strategy() -> dict:
    if STRATEGY_FILE.exists():
        try:
            return json.loads(STRATEGY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_strategy(params: dict) -> None:
    STRATEGY_FILE.parent.mkdir(parents=True, exist_ok=True)
    STRATEGY_FILE.write_text(json.dumps(params, indent=2), encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# TradingAgent
# ═════════════════════════════════════════════════════════════════════════════

class TradingAgent:
    """Autonomous crypto trading sub-agent for ClawBot."""

    STATE_FILE    = STATE_FILE
    STRATEGY_FILE = STRATEGY_FILE
    LOG_FILE      = LOG_FILE

    # ── Init ──────────────────────────────────────────────────────────────────

    def __init__(self) -> None:
        self._state    = _load_state()
        self._strategy = _load_strategy()
        _log("init", {"state_keys": list(self._state.keys())})
        try:
            self.sheets = SheetsReporter()
        except Exception as _sheets_exc:
            logger.warning(f"SheetsReporter init failed (non-fatal): {_sheets_exc}")
            self.sheets = None  # type: ignore[assignment]

    # =========================================================================
    # 1. DCA Engine
    # =========================================================================

    def run_dca(self, coin: str, usdt_amount: float) -> dict:
        """Place a market-buy DCA order for `usdt_amount` of `coin`."""
        _log("dca_run", {"coin": coin, "usdt_amount": usdt_amount})
        try:
            from trading.executor import _place_order  # type: ignore
            result = _place_order(coin, "BUY", usdt_amount)
            entry = {
                "status":      "executed",
                "coin":        coin,
                "usdt_amount": usdt_amount,
                "order":       result,
                "ts":          datetime.now(timezone.utc).isoformat(),
            }
            _log("dca_executed", entry)
            if self.sheets:
                dca_dict = {
                    "ts":          entry["ts"],
                    "coin":        coin,
                    "amount_usdt": usdt_amount,
                    "order":       result,
                }
                self.sheets.log_dca(dca_dict)
            return entry
        except ValueError as exc:
            msg = str(exc)
            if "10002" in msg or "UNAUTHORIZED" in msg.upper():
                err = {"error": "API_KEY_INVALID", "hint": "Check CRYPTOCOM_API_KEY in .env"}
                _log("dca_error", err)
                return err
            err = {"error": str(exc), "coin": coin}
            _log("dca_error", err)
            return err
        except Exception as exc:
            err = {"error": str(exc), "coin": coin}
            _log("dca_error", err)
            return err

    def get_dca_schedule(self) -> list:
        """Return the configured DCA schedule entries."""
        return self._state.get("dca_schedules", [])

    def add_dca_schedule(self, coin: str, amount: float, interval_hours: float) -> None:
        """Add (or update) a recurring DCA entry in the state file."""
        schedules: list = self._state.setdefault("dca_schedules", [])
        # Remove existing entry for same coin so we don't duplicate
        schedules[:] = [s for s in schedules if s.get("coin") != coin]
        entry = {
            "coin":           coin,
            "amount_usdt":    amount,
            "interval_hours": interval_hours,
            "next_run_ts":    time.time(),   # run on next cycle check
            "created_at":     datetime.now(timezone.utc).isoformat(),
        }
        schedules.append(entry)
        self._state["dca_schedules"] = schedules
        _save_state(self._state)
        _log("dca_schedule_added", entry)

    def _run_due_dca_schedules(self) -> list[dict]:
        """Check DCA schedules and fire any that are due. Returns results."""
        results: list[dict] = []
        now = time.time()
        schedules = self._state.get("dca_schedules", [])
        changed = False
        for s in schedules:
            if now >= s.get("next_run_ts", 0):
                result = self.run_dca(s["coin"], s["amount_usdt"])
                results.append(result)
                s["next_run_ts"] = now + s["interval_hours"] * 3600
                s["last_run_at"] = datetime.now(timezone.utc).isoformat()
                changed = True
        if changed:
            _save_state(self._state)
        return results

    # =========================================================================
    # 2. Futures / Spot Signal Scanning
    # =========================================================================

    def scan_futures_signals(self, coins: list[str]) -> list[dict]:
        """
        RSI+MACD scan for each coin. Returns a list of signal dicts
        (only BUY / SELL — HOLD signals are filtered out).
        """
        # Load per-coin strategy params if available
        saved = _load_strategy()
        config = RSIMACDConfig(
            rsi_period=saved.get("rsi_period", 14),
            macd_fast=saved.get("macd_fast", 12),
            macd_slow=saved.get("macd_slow", 26),
        )
        strategy = RSIMACDStrategy(config)
        signals: list[dict] = []

        for coin in coins:
            try:
                closes = fetch_closes(coin, timeframe="4h", count=100)
            except Exception as exc:
                _log("scan_skip", {"coin": coin, "reason": str(exc)})
                continue

            signal = strategy.evaluate(coin, closes)
            if signal.action == "HOLD":
                continue

            try:
                price = fetch_ticker_price(coin)
            except Exception:
                price = 0.0

            sig_dict = {
                "coin":       coin,
                "action":     signal.action,
                "rsi":        round(signal.rsi, 2),
                "macd":       round(signal.macd, 6),
                "macd_signal": round(signal.macd_signal_val, 6),
                "histogram":  round(signal.macd_histogram, 6),
                "confidence": signal.confidence,
                "reason":     signal.reason,
                "price":      price,
                "ts":         datetime.now(timezone.utc).isoformat(),
            }
            signals.append(sig_dict)
            _log("signal_found", sig_dict)

        return signals

    def execute_signal(self, signal: dict, confirm: bool = True) -> dict:
        """
        Execute a trade signal. If `confirm=True`, asks LLM first and only
        proceeds if the reply starts with YES.
        """
        coin   = signal.get("coin", "UNKNOWN")
        action = signal.get("action", "HOLD")
        price  = signal.get("price", 0.0)
        rsi    = signal.get("rsi", 0.0)
        macd   = signal.get("macd", 0.0)

        if action not in ("BUY", "SELL"):
            return {"status": "skipped", "reason": "HOLD signal — nothing to do"}

        # LLM gate
        if confirm:
            prompt = (
                f"Should I {action} {coin} at {price:.4f}? "
                f"RSI={rsi:.1f} MACD={macd:.6f}. "
                f"Reply YES or NO and brief reason."
            )
            try:
                llm_reply, brain = ask_hybrid(prompt, force="complex")
            except Exception as exc:
                _log("llm_confirm_error", {"coin": coin, "error": str(exc)})
                return {"status": "error", "reason": f"LLM unavailable: {exc}"}

            _log("llm_confirm", {"coin": coin, "action": action, "reply": llm_reply[:200], "brain": brain})

            if not llm_reply.strip().upper().startswith("YES"):
                return {
                    "status": "rejected_by_llm",
                    "coin":   coin,
                    "action": action,
                    "reason": llm_reply[:300],
                }

        # Fetch live portfolio size for position sizing
        try:
            balances = get_account_balance()
            usdt_bal = balances.get("USDT", {}).get("available", 0.0)
        except Exception as exc:
            msg = str(exc)
            if "10002" in msg or "UNAUTHORIZED" in msg.upper():
                return {"error": "API_KEY_INVALID", "hint": "Check CRYPTOCOM_API_KEY in .env"}
            usdt_bal = 0.0

        from trading.strategy import calculate_position_size
        sizing = calculate_position_size(usdt_bal or 1000.0, price or 1.0, risk_pct=1.5)
        usdt_amount = sizing["usd_amount"]

        try:
            from trading.executor import _place_order  # type: ignore
            order_result = _place_order(coin, action, usdt_amount)
        except ValueError as exc:
            msg = str(exc)
            if "10002" in msg or "UNAUTHORIZED" in msg.upper():
                err = {"error": "API_KEY_INVALID", "hint": "Check CRYPTOCOM_API_KEY in .env"}
                _log("execute_signal_error", err)
                return err
            err = {"status": "error", "coin": coin, "reason": msg}
            _log("execute_signal_error", err)
            return err
        except Exception as exc:
            err = {"status": "error", "coin": coin, "reason": str(exc)}
            _log("execute_signal_error", err)
            return err

        result = {
            "status":      "executed",
            "coin":        coin,
            "action":      action,
            "usdt_amount": usdt_amount,
            "price":       price,
            "order":       order_result,
            "ts":          datetime.now(timezone.utc).isoformat(),
        }
        _log("signal_executed", result)
        if self.sheets:
            trade_dict = {
                "ts":           result["ts"],
                "coin":         coin,
                "action":       action,
                "amount_usdt":  usdt_amount,
                "price":        price,
                "rsi":          rsi,
                "macd":         macd,
                "macd_signal":  signal.get("macd_signal", ""),
                "llm_confirmed": confirm,
                "result":       "executed",
                "pnl_usdt":     "",
                "pnl_pct":      "",
            }
            self.sheets.log_trade(trade_dict)
        return result

    # =========================================================================
    # 3. Backtester
    # =========================================================================

    def run_backtest(
        self,
        coin: str,
        strategy_params: Optional[dict] = None,
        days: int = 30,
    ) -> dict:
        """
        Run RSI+MACD backtest on `coin` using `strategy_params` (or defaults).
        Fetches candle data from Crypto.com public API.

        Returns: {coin, params, total_return_pct, win_rate, num_trades,
                  sharpe, max_drawdown}
        """
        params = strategy_params or {}
        rsi_period   = int(params.get("rsi_period", 14))
        macd_fast    = int(params.get("macd_fast", 12))
        macd_slow    = int(params.get("macd_slow", 26))
        rsi_oversold = float(params.get("rsi_oversold", 35.0))
        rsi_overbought = float(params.get("rsi_overbought", 65.0))

        # candle count: 4h candles → 6 per day
        candle_count = min(days * 6 + 60, 1000)

        try:
            closes = fetch_closes(coin, timeframe="4h", count=candle_count)
        except Exception as exc:
            _log("backtest_fetch_error", {"coin": coin, "error": str(exc)})
            return {"error": str(exc), "coin": coin}

        trades = self._simulate_rsi_macd(
            closes, rsi_period, macd_fast, macd_slow,
            rsi_oversold, rsi_overbought,
        )

        metrics = self._calc_metrics(trades)
        result = {
            "coin":             coin,
            "params":           params,
            "candles_used":     len(closes),
            "num_trades":       metrics["num_trades"],
            "win_rate":         metrics["win_rate"],
            "total_return_pct": metrics["total_return_pct"],
            "sharpe":           metrics["sharpe"],
            "max_drawdown":     metrics["max_drawdown"],
        }
        _log("backtest_complete", result)
        if self.sheets:
            self.sheets.update_performance(result)
        return result

    # ── Internal backtest simulation ──────────────────────────────────────────

    def _simulate_rsi_macd(
        self,
        closes: list[float],
        rsi_period: int,
        macd_fast: int,
        macd_slow: int,
        rsi_oversold: float,
        rsi_overbought: float,
    ) -> list[dict]:
        """Walk forward simulation. Returns list of {entry, exit, pnl_pct}."""
        min_idx = max(rsi_period + 1, macd_slow + 9 + 2)
        if len(closes) < min_idx + 2:
            return []

        # Pre-compute RSI series (Wilder smoothing)
        rsi_series = self._rsi_series(closes, rsi_period)

        trades: list[dict] = []
        in_trade     = False
        entry_price  = 0.0
        is_long      = True

        for i in range(min_idx, len(closes)):
            rsi  = rsi_series[i]
            sub  = closes[: i + 1]
            sub2 = closes[: i]

            try:
                m1, s1, _ = calculate_macd(sub,  macd_fast, macd_slow, 9)
                m0, s0, _ = calculate_macd(sub2, macd_fast, macd_slow, 9)
            except ValueError:
                continue

            bullish_cross = m0 <= s0 and m1 > s1
            bearish_cross = m0 >= s0 and m1 < s1
            price = closes[i]

            if not in_trade:
                if rsi < rsi_oversold and (bullish_cross or m1 > s1):
                    in_trade    = True
                    entry_price = price
                    is_long     = True
                elif rsi > rsi_overbought and (bearish_cross or m1 < s1):
                    in_trade    = True
                    entry_price = price
                    is_long     = False
            else:
                exit_now = False
                if is_long and (rsi > rsi_overbought or bearish_cross):
                    exit_now = True
                elif not is_long and (rsi < rsi_oversold or bullish_cross):
                    exit_now = True

                if exit_now:
                    pnl_pct = (price - entry_price) / entry_price * 100
                    if not is_long:
                        pnl_pct = -pnl_pct
                    trades.append({"entry": entry_price, "exit": price, "pnl_pct": pnl_pct})
                    in_trade = False

        return trades

    @staticmethod
    def _rsi_series(closes: list[float], period: int) -> list[float]:
        """Compute full RSI series (Wilder). Returns list same length as closes."""
        rsi_vals: list[float] = [50.0] * len(closes)
        if len(closes) < period + 1:
            return rsi_vals

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains  = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]

        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period

        def _rv(ag: float, al: float) -> float:
            return 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)

        rsi_vals[period] = _rv(avg_g, avg_l)
        for i in range(period, len(gains)):
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            rsi_vals[i + 1] = _rv(avg_g, avg_l)

        return rsi_vals

    @staticmethod
    def _calc_metrics(trades: list[dict]) -> dict:
        """Calculate performance metrics from a list of trade dicts."""
        if not trades:
            return {
                "num_trades": 0, "win_rate": 0.0, "total_return_pct": 0.0,
                "sharpe": 0.0, "max_drawdown": 0.0,
            }

        pnls  = [t["pnl_pct"] for t in trades]
        wins  = [p for p in pnls if p > 0]

        # Equity curve
        equity = 100.0
        peak   = equity
        max_dd = 0.0
        for p in pnls:
            equity *= (1 + p / 100)
            peak    = max(peak, equity)
            dd      = (peak - equity) / peak * 100
            max_dd  = max(max_dd, dd)

        total_return = equity - 100.0
        avg_pnl      = sum(pnls) / len(pnls)
        std          = statistics.stdev(pnls) if len(pnls) > 1 else 1.0
        sharpe       = avg_pnl / std if std > 0 else 0.0

        return {
            "num_trades":       len(trades),
            "win_rate":         round(len(wins) / len(trades) * 100, 1),
            "total_return_pct": round(total_return, 2),
            "sharpe":           round(sharpe, 4),
            "max_drawdown":     round(max_dd, 2),
        }

    # =========================================================================
    # 4. Self-Optimiser
    # =========================================================================

    def optimize_strategy(self, coin: str = "BTC_USDT") -> dict:
        """
        Grid search RSI period × MACD fast × MACD slow.
        Evaluates by Sharpe ratio. Saves best params to STRATEGY_FILE.
        Returns best params + metrics.
        """
        _log("optimize_start", {"coin": coin})

        try:
            closes = fetch_closes(coin, timeframe="4h", count=500)
        except Exception as exc:
            _log("optimize_fetch_error", {"coin": coin, "error": str(exc)})
            return {"error": str(exc), "coin": coin}

        best_sharpe  = -9999.0
        best_params  = {}
        best_metrics: dict = {}
        total_combos = len(_OPT_RSI_PERIODS) * len(_OPT_MACD_FAST) * len(_OPT_MACD_SLOW)
        tested       = 0

        for rp in _OPT_RSI_PERIODS:
            for mf in _OPT_MACD_FAST:
                for ms in _OPT_MACD_SLOW:
                    if mf >= ms:
                        continue  # fast must be < slow
                    trades = self._simulate_rsi_macd(closes, rp, mf, ms, 35.0, 65.0)
                    if not trades:
                        continue
                    m = self._calc_metrics(trades)
                    tested += 1
                    if m["sharpe"] > best_sharpe:
                        best_sharpe = m["sharpe"]
                        best_params = {
                            "rsi_period": rp,
                            "macd_fast":  mf,
                            "macd_slow":  ms,
                        }
                        best_metrics = m

        if not best_params:
            return {"error": "No valid parameter combo found", "coin": coin}

        best_params["coin"]        = coin
        best_params["optimized_at"] = datetime.now(timezone.utc).isoformat()
        best_params["combos_tested"] = tested

        _save_strategy(best_params)

        result = {**best_params, **best_metrics}
        _log("optimize_complete", result)

        # Update instance strategy cache
        self._strategy = best_params
        self._state["last_optimizer_run"] = time.time()
        _save_state(self._state)

        if self.sheets:
            summary_status = {
                "best_params":    best_params,
                "best_metrics":   best_metrics,
                "coin":           coin,
                "combos_tested":  tested,
                "optimized_at":   best_params.get("optimized_at", ""),
            }
            self.sheets.write_summary(summary_status)

        return result

    # =========================================================================
    # 5. Self-Coder
    # =========================================================================

    def propose_upgrade(self, metric_report: str) -> str:
        """Ask the LLM for Python code improvement suggestions based on metrics."""
        strategy_summary = json.dumps(self._strategy or {}, indent=2)
        prompt = (
            f"You are a trading strategy improvement agent.\n\n"
            f"Current strategy params:\n{strategy_summary}\n\n"
            f"Performance metric report:\n{metric_report}\n\n"
            f"Suggest specific Python code improvements to the RSI+MACD strategy "
            f"that could improve the Sharpe ratio or win rate. "
            f"Return only Python code snippets with brief explanations."
        )
        try:
            reply, brain = ask_hybrid(prompt, force="complex")
        except Exception as exc:
            _log("propose_upgrade_error", {"error": str(exc)})
            return f"LLM unavailable: {exc}"

        _log("propose_upgrade", {"brain": brain, "preview": reply[:200]})
        return reply

    def apply_upgrade(self, code_patch: str) -> bool:
        """
        Validate and write a strategy code patch to data/strategy_patches/.

        Safety: rejects patches containing dangerous patterns.
        Returns True if patch is safe and was written; False otherwise.
        """
        for pattern in _BLOCKED_PATCH_PATTERNS:
            if re.search(pattern, code_patch):
                _log("patch_rejected", {"pattern": pattern, "preview": code_patch[:200]})
                logger.warning(f"Patch rejected — contains blocked pattern: {pattern}")
                return False

        PATCHES_DIR.mkdir(parents=True, exist_ok=True)
        ts_str  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        outfile = PATCHES_DIR / f"patch_{ts_str}.py"
        try:
            outfile.write_text(code_patch, encoding="utf-8")
            _log("patch_written", {"file": str(outfile), "size": len(code_patch)})
            return True
        except Exception as exc:
            _log("patch_write_error", {"error": str(exc)})
            return False

    # =========================================================================
    # 6. Scheduled Cycle Runner
    # =========================================================================

    async def run_cycle(self, bot=None, chat_id: int = 0) -> str:
        """
        Full autonomous trading cycle:
          1. Fetch balances (graceful fail)
          2. Scan BTC/ETH/SOL/XRP for signals
          3. Run due DCA schedules
          4. Every 24 h: run optimiser + send Telegram summary

        Returns a status string.
        """
        lines: list[str] = []
        now = time.time()

        # 1. Balances ──────────────────────────────────────────────────────────
        try:
            balances = get_account_balance()
            usdt = balances.get("USDT", {}).get("available", 0.0)
            lines.append(f"Balance OK — USDT available: {usdt:.2f}")
            _log("cycle_balance", {"usdt_available": usdt})
        except ValueError as exc:
            msg = str(exc)
            if "10002" in msg or "UNAUTHORIZED" in msg.upper():
                lines.append("API_KEY_INVALID — skipping exchange steps")
                _log("cycle_balance_error", {"error": "API_KEY_INVALID"})
            else:
                lines.append(f"Balance fetch failed: {msg}")
                _log("cycle_balance_error", {"error": msg})
        except Exception as exc:
            lines.append(f"Balance fetch failed: {exc}")
            _log("cycle_balance_error", {"error": str(exc)})

        # 2. Signal scan ───────────────────────────────────────────────────────
        try:
            signals = self.scan_futures_signals(_DEFAULT_COINS)
            if signals:
                lines.append(f"Signals found: {len(signals)}")
                for s in signals:
                    lines.append(f"  {s['action']} {s['coin']} | RSI={s['rsi']} | Conf={s['confidence']}")
            else:
                lines.append("No actionable signals")
        except Exception as exc:
            lines.append(f"Scan error: {exc}")
            _log("cycle_scan_error", {"error": str(exc)})
            signals = []

        # 3. DCA schedules ─────────────────────────────────────────────────────
        try:
            dca_results = self._run_due_dca_schedules()
            if dca_results:
                lines.append(f"DCA executed: {len(dca_results)} orders")
        except Exception as exc:
            lines.append(f"DCA schedule error: {exc}")
            _log("cycle_dca_error", {"error": str(exc)})

        # 4. 24-hour optimiser ─────────────────────────────────────────────────
        last_opt = self._state.get("last_optimizer_run", 0)
        if now - last_opt >= 86400:
            try:
                opt_result = self.optimize_strategy("BTC_USDT")
                opt_summary = (
                    f"Optimiser complete — best Sharpe={opt_result.get('sharpe', '?')} | "
                    f"RSI={opt_result.get('rsi_period')} "
                    f"MACD {opt_result.get('macd_fast')}/{opt_result.get('macd_slow')}"
                )
                lines.append(opt_summary)

                # Generate Sheets analysis report
                analysis_text = ""
                try:
                    if self.sheets:
                        analysis_text = self.sheets.generate_analysis_report("BTC_USDT")
                        if analysis_text:
                            lines.append("Sheets analysis updated")
                except Exception as _ana_exc:
                    _log("cycle_analysis_error", {"error": str(_ana_exc)})

                if bot and chat_id:
                    sig_text = "\n".join(
                        f"  {s['action']} {s['coin']}" for s in signals
                    ) or "  None"
                    tg_msg = (
                        f"<b>ClawBot Daily Summary</b>\n"
                        f"Signals: {len(signals)}\n{sig_text}\n\n"
                        f"{opt_summary}"
                    )
                    if analysis_text:
                        tg_msg += f"\n\n<b>Performance Analysis</b>\n{analysis_text[:600]}"
                    try:
                        await bot.send_message(
                            chat_id=chat_id, text=tg_msg, parse_mode="HTML"
                        )
                    except Exception as tg_exc:
                        _log("cycle_tg_error", {"error": str(tg_exc)})
            except Exception as exc:
                lines.append(f"Optimiser error: {exc}")
                _log("cycle_optimizer_error", {"error": str(exc)})

        self._state["last_cycle_ts"] = now
        _save_state(self._state)

        status = " | ".join(lines) if lines else "Cycle complete"
        _log("cycle_complete", {"status": status})
        return status


# ═════════════════════════════════════════════════════════════════════════════
# Module-level singleton + public API
# ═════════════════════════════════════════════════════════════════════════════

_agent: Optional[TradingAgent] = None


def _get_agent() -> TradingAgent:
    global _agent
    if _agent is None:
        _agent = TradingAgent()
    return _agent


# ── Public API ────────────────────────────────────────────────────────────────

async def run_trading_cycle(bot=None, chat_id: int = 0) -> str:
    """Run full autonomous trading cycle. Called by the scheduler."""
    return await _get_agent().run_cycle(bot=bot, chat_id=chat_id)


def get_trading_status() -> dict:
    """Return current agent state + last strategy params."""
    agent = _get_agent()
    state = _load_state()
    strategy = _load_strategy()
    return {
        "dca_schedules":      state.get("dca_schedules", []),
        "last_cycle_ts":      state.get("last_cycle_ts", 0),
        "last_optimizer_run": state.get("last_optimizer_run", 0),
        "strategy_params":    strategy,
    }


def set_dca(coin: str, amount_usdt: float, interval_hours: float) -> str:
    """Add or update a DCA schedule entry. Returns a confirmation string."""
    coin = coin.upper()
    if "_USDT" not in coin:
        coin = f"{coin}_USDT"
    _get_agent().add_dca_schedule(coin, amount_usdt, interval_hours)
    return (
        f"DCA scheduled: {coin} — ${amount_usdt:.2f} every {interval_hours}h"
    )


def get_signals(coins: Optional[list[str]] = None) -> list[dict]:
    """Scan coins for RSI+MACD signals. Defaults to BTC/ETH/SOL/XRP."""
    target = coins or _DEFAULT_COINS
    target = [c.upper() if "_USDT" in c.upper() else f"{c.upper()}_USDT" for c in target]
    return _get_agent().scan_futures_signals(target)


def run_agent_backtest(coin: str = "BTC_USDT", days: int = 30) -> dict:
    """Run backtest with current strategy params. Returns metrics dict."""
    coin = coin.upper()
    if "_USDT" not in coin:
        coin = f"{coin}_USDT"
    params = _load_strategy()
    return _get_agent().run_backtest(coin, strategy_params=params, days=days)


def trigger_optimize(coin: str = "BTC_USDT") -> dict:
    """Run grid-search optimiser synchronously. Returns best params + metrics."""
    coin = coin.upper()
    if "_USDT" not in coin:
        coin = f"{coin}_USDT"
    return _get_agent().optimize_strategy(coin)


# ═════════════════════════════════════════════════════════════════════════════
# SheetsReporter
# ═════════════════════════════════════════════════════════════════════════════

class SheetsReporter:
    """
    Google Sheets trade logging and performance analysis for ClawBot.

    Required .env vars:
      GOOGLE_SERVICE_ACCOUNT_JSON = path or JSON string of service account
      TRADING_SHEET_NAME = "OpenClaw Trading"  (optional, defaults below)

    Sheet must be shared with the service account email.
    Tabs are auto-created on first run: Trades, Performance, Summary.

    If gspread is not installed or credentials are not set, all methods
    log a warning and return False/empty string — they never raise.
    """

    SHEET_NAME    = os.getenv("TRADING_SHEET_NAME", os.getenv("GOOGLE_SHEET_NAME", "OpenClaw Trading"))
    TRADES_TAB    = "Trades"
    ANALYSIS_TAB  = "Performance"
    SUMMARY_TAB   = "Summary"

    _TRADES_HEADERS = [
        "Timestamp", "Coin", "Action", "Amount_USDT", "Price",
        "RSI", "MACD_Signal", "LLM_Confirmed", "Result", "PnL_USDT", "PnL_PCT",
    ]
    _PERF_HEADERS = [
        "Coin", "Params", "Candles", "Num_Trades", "Win_Rate_%",
        "Total_Return_%", "Sharpe", "Max_Drawdown_%", "Updated",
    ]
    _SUMMARY_HEADERS = [
        "Metric", "Value", "Updated",
    ]

    # ── Internal: connect ────────────────────────────────────────────────────

    def _get_sheet(self):
        """Connect via gspread service account. Returns spreadsheet or None."""
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError:
            logger.warning("SheetsReporter: gspread / google-auth not installed. "
                           "Run: pip install gspread google-auth")
            return None

        sa_raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        if not sa_raw:
            logger.warning("SheetsReporter: GOOGLE_SERVICE_ACCOUNT_JSON not set — "
                           "Google Sheets logging disabled.")
            return None

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        try:
            # Support both file path and raw JSON string
            if sa_raw.startswith("{"):
                import io
                creds = Credentials.from_service_account_info(
                    json.loads(sa_raw), scopes=scopes
                )
            else:
                if not Path(sa_raw).exists():
                    logger.warning(f"SheetsReporter: service account file not found: {sa_raw}")
                    return None
                creds = Credentials.from_service_account_file(sa_raw, scopes=scopes)

            client = gspread.authorize(creds)
            try:
                spreadsheet = client.open(self.SHEET_NAME)
            except Exception:
                spreadsheet = client.create(self.SHEET_NAME)
                logger.info(f"SheetsReporter: created new spreadsheet '{self.SHEET_NAME}'")

            self._ensure_tabs(spreadsheet)
            return spreadsheet

        except Exception as exc:
            logger.warning(f"SheetsReporter: could not connect to Google Sheets: {exc}")
            return None

    def _ensure_tabs(self, spreadsheet) -> None:
        """Create Trades, Performance, Summary tabs if they don't exist."""
        try:
            existing = [ws.title for ws in spreadsheet.worksheets()]
            if self.TRADES_TAB not in existing:
                ws = spreadsheet.add_worksheet(self.TRADES_TAB, rows=5000, cols=len(self._TRADES_HEADERS))
                ws.append_row(self._TRADES_HEADERS, value_input_option="RAW")
                ws.format(f"A1:{chr(64 + len(self._TRADES_HEADERS))}1", {"textFormat": {"bold": True}})
                logger.info("SheetsReporter: created Trades tab")
            if self.ANALYSIS_TAB not in existing:
                ws = spreadsheet.add_worksheet(self.ANALYSIS_TAB, rows=200, cols=len(self._PERF_HEADERS))
                ws.append_row(self._PERF_HEADERS, value_input_option="RAW")
                ws.format(f"A1:{chr(64 + len(self._PERF_HEADERS))}1", {"textFormat": {"bold": True}})
                logger.info("SheetsReporter: created Performance tab")
            if self.SUMMARY_TAB not in existing:
                ws = spreadsheet.add_worksheet(self.SUMMARY_TAB, rows=100, cols=3)
                ws.append_row(self._SUMMARY_HEADERS, value_input_option="RAW")
                ws.format("A1:C1", {"textFormat": {"bold": True}})
                logger.info("SheetsReporter: created Summary tab")
        except Exception as exc:
            logger.warning(f"SheetsReporter: _ensure_tabs error: {exc}")

    # ── Public methods ────────────────────────────────────────────────────────

    def log_trade(self, trade: dict) -> bool:
        """
        Append a row to the Trades tab.

        trade dict keys:
          ts, coin, action, amount_usdt, price, rsi, macd_signal,
          llm_confirmed, result, pnl_usdt, pnl_pct
        """
        try:
            spreadsheet = self._get_sheet()
            if spreadsheet is None:
                return False
            ws = spreadsheet.worksheet(self.TRADES_TAB)
            row = [
                trade.get("ts",            datetime.now(timezone.utc).isoformat()),
                trade.get("coin",          ""),
                trade.get("action",        ""),
                trade.get("amount_usdt",   ""),
                trade.get("price",         ""),
                trade.get("rsi",           ""),
                trade.get("macd_signal",   ""),
                str(trade.get("llm_confirmed", "")),
                trade.get("result",        ""),
                trade.get("pnl_usdt",      ""),
                trade.get("pnl_pct",       ""),
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")
            logger.info(f"SheetsReporter: logged trade {trade.get('coin')} {trade.get('action')}")
            return True
        except Exception as exc:
            logger.warning(f"SheetsReporter.log_trade failed: {exc}")
            return False

    def log_dca(self, dca: dict) -> bool:
        """
        Append a DCA execution to the Trades tab with Action=DCA.

        dca dict keys: ts, coin, amount_usdt, order
        """
        try:
            spreadsheet = self._get_sheet()
            if spreadsheet is None:
                return False
            ws = spreadsheet.worksheet(self.TRADES_TAB)
            row = [
                dca.get("ts",          datetime.now(timezone.utc).isoformat()),
                dca.get("coin",        ""),
                "DCA",
                dca.get("amount_usdt", ""),
                "",   # price — not always available on DCA market orders
                "",   # rsi
                "",   # macd_signal
                "",   # llm_confirmed
                "executed",
                "",   # pnl_usdt
                "",   # pnl_pct
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")
            logger.info(f"SheetsReporter: logged DCA {dca.get('coin')}")
            return True
        except Exception as exc:
            logger.warning(f"SheetsReporter.log_dca failed: {exc}")
            return False

    def update_performance(self, backtest_result: dict) -> bool:
        """
        Write / update the Performance tab with the latest backtest metrics.

        backtest_result keys (from run_backtest):
          coin, params, candles_used, num_trades, win_rate,
          total_return_pct, sharpe, max_drawdown
        """
        try:
            spreadsheet = self._get_sheet()
            if spreadsheet is None:
                return False
            ws = spreadsheet.worksheet(self.ANALYSIS_TAB)

            coin    = backtest_result.get("coin", "")
            now_str = datetime.now(timezone.utc).isoformat()
            row = [
                coin,
                json.dumps(backtest_result.get("params", {})),
                backtest_result.get("candles_used",     ""),
                backtest_result.get("num_trades",       ""),
                backtest_result.get("win_rate",         ""),
                backtest_result.get("total_return_pct", ""),
                backtest_result.get("sharpe",           ""),
                backtest_result.get("max_drawdown",     ""),
                now_str,
            ]

            # Try to find existing row for this coin and update it, else append
            try:
                cell = ws.find(coin)
                ws.update(f"A{cell.row}:{chr(64 + len(self._PERF_HEADERS))}{cell.row}", [row])
            except Exception:
                ws.append_row(row, value_input_option="USER_ENTERED")

            logger.info(f"SheetsReporter: updated Performance for {coin}")
            return True
        except Exception as exc:
            logger.warning(f"SheetsReporter.update_performance failed: {exc}")
            return False

    def write_summary(self, status: dict) -> bool:
        """
        Write the Summary tab with strategy params, optimizer run info,
        and any LLM recommendations stored in status.

        status dict keys: best_params, best_metrics, coin, combos_tested,
                          optimized_at, recommendations (optional)
        """
        try:
            spreadsheet = self._get_sheet()
            if spreadsheet is None:
                return False
            ws = spreadsheet.worksheet(self.SUMMARY_TAB)
            ws.clear()
            ws.append_row(self._SUMMARY_HEADERS, value_input_option="RAW")
            ws.format("A1:C1", {"textFormat": {"bold": True}})

            now_str      = datetime.now(timezone.utc).isoformat()
            params       = status.get("best_params", {})
            metrics      = status.get("best_metrics", {})
            recommendations = status.get("recommendations", "")

            rows = [
                ["Coin",              status.get("coin", ""),                  now_str],
                ["RSI Period",        params.get("rsi_period", ""),            ""],
                ["MACD Fast",         params.get("macd_fast", ""),             ""],
                ["MACD Slow",         params.get("macd_slow", ""),             ""],
                ["Optimized At",      status.get("optimized_at", ""),          ""],
                ["Combos Tested",     status.get("combos_tested", ""),         ""],
                ["Best Sharpe",       metrics.get("sharpe", ""),               ""],
                ["Win Rate %",        metrics.get("win_rate", ""),             ""],
                ["Total Return %",    metrics.get("total_return_pct", ""),     ""],
                ["Max Drawdown %",    metrics.get("max_drawdown", ""),         ""],
                ["Num Trades",        metrics.get("num_trades", ""),           ""],
                ["Recommendations",   recommendations[:500] if recommendations else "", ""],
            ]
            ws.append_rows(rows, value_input_option="USER_ENTERED")
            logger.info("SheetsReporter: Summary tab updated")
            return True
        except Exception as exc:
            logger.warning(f"SheetsReporter.write_summary failed: {exc}")
            return False

    def generate_analysis_report(self, coin: str = "BTC_USDT") -> str:
        """
        Read up to 50 recent trades for `coin` from the Trades tab
        (or fall back to trading_agent.log), compute metrics, then ask
        ask_hybrid for actionable recommendations.

        Returns a formatted analysis string.
        Writes the recommendations to the Summary tab.
        """
        try:
            trades = self._read_recent_trades(coin, limit=50)
            if not trades:
                return f"No trade data found for {coin} — run trades first."

            metrics = self._compute_trade_metrics(trades, coin)

            prompt = (
                f"Given these trading metrics for {coin}:\n"
                f"  Total trades: {metrics['total_trades']}\n"
                f"  Win rate: {metrics['win_rate']:.1f}%\n"
                f"  Avg PnL per trade: {metrics['avg_pnl']:.4f}%\n"
                f"  Best trade: {metrics['best_trade']:.4f}%\n"
                f"  Worst trade: {metrics['worst_trade']:.4f}%\n"
                f"  Max consecutive losses: {metrics['max_consec_losses']}\n"
                f"  Strategy: RSI+MACD, 4H timeframe\n\n"
                f"What adjustments should I make to improve performance? "
                f"Be specific about RSI thresholds, MACD params, position sizing, and timing."
            )
            try:
                llm_reply, _brain = ask_hybrid(prompt, force="complex")
            except Exception as exc:
                llm_reply = f"LLM unavailable for analysis: {exc}"

            report = (
                f"Performance Analysis — {coin}\n"
                f"Trades: {metrics['total_trades']} | Win rate: {metrics['win_rate']:.1f}%\n"
                f"Avg PnL: {metrics['avg_pnl']:.4f}% | Best: {metrics['best_trade']:.4f}%"
                f" | Worst: {metrics['worst_trade']:.4f}%\n"
                f"Max consec losses: {metrics['max_consec_losses']}\n\n"
                f"Recommendations:\n{llm_reply}"
            )

            # Write recommendations to Summary tab
            try:
                spreadsheet = self._get_sheet()
                if spreadsheet:
                    ws = spreadsheet.worksheet(self.SUMMARY_TAB)
                    now_str = datetime.now(timezone.utc).isoformat()
                    # Update or append the Recommendations row
                    try:
                        cell = ws.find("Recommendations")
                        ws.update_cell(cell.row, 2, llm_reply[:500])
                        ws.update_cell(cell.row, 3, now_str)
                    except Exception:
                        ws.append_row(["Recommendations", llm_reply[:500], now_str],
                                      value_input_option="USER_ENTERED")
                    # Append analysis snapshot row
                    ws.append_row(
                        [f"Analysis_{coin}", report[:500], now_str],
                        value_input_option="USER_ENTERED",
                    )
            except Exception as sheet_exc:
                logger.warning(f"SheetsReporter: could not write analysis to Summary: {sheet_exc}")

            _log("sheets_analysis", {"coin": coin, "win_rate": metrics["win_rate"],
                                     "total_trades": metrics["total_trades"]})
            return report

        except Exception as exc:
            logger.warning(f"SheetsReporter.generate_analysis_report failed: {exc}")
            return f"Analysis failed: {exc}"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _read_recent_trades(self, coin: str, limit: int = 50) -> list[dict]:
        """
        Try to read recent trades for `coin` from Google Sheets Trades tab first.
        Fall back to trading_agent.log if Sheets not available.
        """
        trades: list[dict] = []

        # Attempt Sheets read
        try:
            spreadsheet = self._get_sheet()
            if spreadsheet:
                ws = spreadsheet.worksheet(self.TRADES_TAB)
                all_rows = ws.get_all_records()
                for row in all_rows:
                    if str(row.get("Coin", "")).upper() == coin.upper():
                        trades.append(row)
                if trades:
                    return trades[-limit:]
        except Exception as exc:
            logger.warning(f"SheetsReporter: Sheets read failed, using log fallback: {exc}")

        # Fallback: parse trading_agent.log
        if LOG_FILE.exists():
            try:
                for line in LOG_FILE.read_text(encoding="utf-8").strip().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    action = entry.get("action", "")
                    detail = entry.get("detail", {})
                    if action in ("signal_executed", "dca_executed"):
                        if str(detail.get("coin", "")).upper() == coin.upper():
                            trades.append({
                                "Timestamp":     entry.get("ts", ""),
                                "Coin":          detail.get("coin", coin),
                                "Action":        detail.get("action", ""),
                                "Amount_USDT":   detail.get("usdt_amount", ""),
                                "Price":         detail.get("price", ""),
                                "PnL_PCT":       "",
                            })
                return trades[-limit:]
            except Exception as exc:
                logger.warning(f"SheetsReporter: log fallback read failed: {exc}")

        return trades

    @staticmethod
    def _compute_trade_metrics(trades: list[dict], coin: str) -> dict:
        """Compute win rate, avg PnL, best/worst trade, consecutive losses."""
        pnl_values: list[float] = []
        for t in trades:
            raw = t.get("PnL_PCT") or t.get("pnl_pct") or t.get("pnl", "")
            try:
                v = float(raw)
                pnl_values.append(v)
            except (ValueError, TypeError):
                pass  # skip rows without PnL data

        if not pnl_values:
            return {
                "total_trades":     len(trades),
                "win_rate":         0.0,
                "avg_pnl":          0.0,
                "best_trade":       0.0,
                "worst_trade":      0.0,
                "max_consec_losses": 0,
            }

        wins   = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p <= 0]

        # Max consecutive losses
        max_streak = cur_streak = 0
        for p in pnl_values:
            if p <= 0:
                cur_streak += 1
                max_streak = max(max_streak, cur_streak)
            else:
                cur_streak = 0

        return {
            "total_trades":      len(trades),
            "win_rate":          len(wins) / len(pnl_values) * 100 if pnl_values else 0.0,
            "avg_pnl":           sum(pnl_values) / len(pnl_values),
            "best_trade":        max(pnl_values),
            "worst_trade":       min(pnl_values),
            "max_consec_losses": max_streak,
        }


# ── Public function: callable by /trader analysis ────────────────────────────

def get_sheets_analysis(coin: str = "BTC_USDT") -> str:
    """Called by /trader analysis command in receiver.py."""
    reporter = SheetsReporter()
    return reporter.generate_analysis_report(coin)
