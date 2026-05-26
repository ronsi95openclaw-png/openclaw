"""Unified state persistence layer for OpenClaw.

Writes to Supabase (cloud) primary + local files (fallback).
Non-blocking — all exceptions are swallowed so storage never crashes the bot.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("openclaw.infra.state_store")

_write_lock = threading.Lock()

# ── File paths ────────────────────────────────────────────────────────────────

_ROOT          = Path(__file__).parent.parent
_STATE_FILE    = _ROOT / "data" / "cryptocom_state.json"
_CAPITAL_FILE  = _ROOT / "data" / "capital_state.json"
_WEIGHTS_FILE  = _ROOT / "data" / "strategy_weights.json"
_GOAL_FILE     = _ROOT / "data" / "goal_tracker.json"
_OUTCOMES_FILE = _ROOT / "data" / "logs" / "trade_outcomes.jsonl"
_QUIN_FILE     = _ROOT / "data" / "quin_decisions.jsonl"

_SINGLETON_ID  = "singleton"


def _sb():
    try:
        from infra.supabase_client import get_client
        return get_client()
    except Exception:
        return None


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Bot state ─────────────────────────────────────────────────────────────────

def save_bot_state(raw: dict) -> None:
    """Persist bot state dict to Supabase + local file."""
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(raw, indent=2))
    except Exception as exc:
        logger.debug("bot_state file write failed: %s", exc)

    sb = _sb()
    if sb is None:
        return
    try:
        sb.table("bot_state").upsert({
            "id":               _SINGLETON_ID,
            "demo_mode":        bool(raw.get("demo_mode", True)),
            "running":          bool(raw.get("running", False)),
            "balance":          float(raw.get("balance", raw.get("starting_balance", 98.0))),
            "starting_balance": float(raw.get("starting_balance", 98.0)),
            "total_pnl":        float(raw.get("total_pnl", 0.0)),
            "trades_today":     int(raw.get("trades_today", 0)),
            "trades_date":      str(raw.get("trades_date", "")),
            "scan_interval":    int(raw.get("scan_interval", 30)),
            "last_flush_date":  str(raw.get("last_flush_date", "")),
            "open_positions":   raw.get("open_positions", []),
            "trade_log":        raw.get("trade_log", []),
            "execution_paused": bool(raw.get("execution_paused", False)),
            "updated_at":       _ts(),
        }, on_conflict="id").execute()
    except Exception as exc:
        logger.debug("bot_state supabase write failed: %s", exc)


def load_bot_state() -> Optional[dict]:
    """Load bot state — Supabase first, local file fallback."""
    sb = _sb()
    if sb is not None:
        try:
            res = sb.table("bot_state").select("*").eq("id", _SINGLETON_ID).single().execute()
            if res.data:
                r = res.data
                logger.info("bot_state loaded from Supabase")
                return {
                    "demo_mode":        r.get("demo_mode", True),
                    "running":          r.get("running", False),
                    "balance":          float(r.get("balance") or 98.0),
                    "starting_balance": float(r.get("starting_balance") or 98.0),
                    "total_pnl":        float(r.get("total_pnl") or 0.0),
                    "trades_today":     int(r.get("trades_today") or 0),
                    "trades_date":      r.get("trades_date", ""),
                    "scan_interval":    int(r.get("scan_interval") or 30),
                    "last_flush_date":  r.get("last_flush_date", ""),
                    "open_positions":   r.get("open_positions") or [],
                    "trade_log":        r.get("trade_log") or [],
                    "execution_paused": r.get("execution_paused", False),
                }
        except Exception as exc:
            logger.debug("bot_state supabase read failed: %s", exc)

    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except Exception:
            pass
    return None


# ── Capital state ─────────────────────────────────────────────────────────────

def save_capital_state(raw: dict) -> None:
    """raw: {state, alltime_peak, loss_streak, ts, ...}"""
    try:
        _CAPITAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CAPITAL_FILE.write_text(json.dumps(raw, indent=2))
    except Exception as exc:
        logger.debug("capital_state file write failed: %s", exc)

    sb = _sb()
    if sb is None:
        return
    try:
        sb.table("capital_state").upsert({
            "id":                  _SINGLETON_ID,
            "state":               str(raw.get("state", "SAFE")),
            "alltime_peak":        float(raw.get("alltime_peak", 0.0)),
            "consecutive_losses":  int(raw.get("loss_streak", 0)),
            "updated_at":          _ts(),
        }, on_conflict="id").execute()
    except Exception as exc:
        logger.debug("capital_state supabase write failed: %s", exc)


def load_capital_state() -> Optional[dict]:
    sb = _sb()
    if sb is not None:
        try:
            res = sb.table("capital_state").select("*").eq("id", _SINGLETON_ID).single().execute()
            if res.data:
                r = res.data
                return {
                    "state":        r.get("state", "SAFE"),
                    "alltime_peak": float(r.get("alltime_peak") or 0.0),
                    "loss_streak":  int(r.get("consecutive_losses") or 0),
                }
        except Exception as exc:
            logger.debug("capital_state supabase read failed: %s", exc)

    if _CAPITAL_FILE.exists():
        try:
            return json.loads(_CAPITAL_FILE.read_text())
        except Exception:
            pass
    return None


# ── Strategy weights ──────────────────────────────────────────────────────────

def save_strategy_weights(weights: dict) -> None:
    """Sync simple {strategy: weight} dict to Supabase only.
    The local data/strategy_weights.json is owned by StrategyWeightEngine
    and stores the full format — do NOT overwrite it here.
    """
    sb = _sb()
    if sb is None:
        return
    try:
        rows = [
            {"strategy": k, "weight": float(v), "updated_at": _ts()}
            for k, v in weights.items()
            if isinstance(k, str) and isinstance(v, (int, float))
        ]
        if rows:
            sb.table("strategy_weights").upsert(rows, on_conflict="strategy").execute()
    except Exception as exc:
        logger.debug("strategy_weights supabase write failed: %s", exc)


def load_strategy_weights() -> Optional[dict]:
    sb = _sb()
    if sb is not None:
        try:
            res = sb.table("strategy_weights").select("strategy,weight").execute()
            if res.data:
                return {r["strategy"]: float(r["weight"]) for r in res.data}
        except Exception as exc:
            logger.debug("strategy_weights supabase read failed: %s", exc)

    if _WEIGHTS_FILE.exists():
        try:
            return json.loads(_WEIGHTS_FILE.read_text())
        except Exception:
            pass
    return None


# ── Goal tracker ──────────────────────────────────────────────────────────────

def save_goal_state(raw: dict) -> None:
    try:
        _GOAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _GOAL_FILE.write_text(json.dumps(raw, indent=2))
    except Exception as exc:
        logger.debug("goal_tracker file write failed: %s", exc)

    sb = _sb()
    if sb is None:
        return
    try:
        sb.table("goal_tracker").upsert({
            "id":               _SINGLETON_ID,
            "starting_balance": float(raw.get("starting_balance", 98.0)),
            "target":           float(raw.get("target", 50000.0)),
            "milestones_hit":   raw.get("milestones_hit", []),
            "start_date":       str(raw.get("start_date", "")),
            "updated_at":       _ts(),
        }, on_conflict="id").execute()
    except Exception as exc:
        logger.debug("goal_tracker supabase write failed: %s", exc)


def load_goal_state() -> Optional[dict]:
    sb = _sb()
    if sb is not None:
        try:
            res = sb.table("goal_tracker").select("*").eq("id", _SINGLETON_ID).single().execute()
            if res.data:
                r = res.data
                return {
                    "starting_balance": float(r.get("starting_balance") or 98.0),
                    "target":           float(r.get("target") or 50000.0),
                    "milestones_hit":   r.get("milestones_hit") or [],
                    "start_date":       r.get("start_date", ""),
                }
        except Exception as exc:
            logger.debug("goal_tracker supabase read failed: %s", exc)

    if _GOAL_FILE.exists():
        try:
            return json.loads(_GOAL_FILE.read_text())
        except Exception:
            pass
    return None


# ── Trade outcomes ────────────────────────────────────────────────────────────

def append_trade_outcome(record: dict) -> None:
    try:
        _OUTCOMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with open(_OUTCOMES_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.debug("trade_outcomes file write failed: %s", exc)

    sb = _sb()
    if sb is None:
        return
    try:
        sb.table("trade_outcomes").insert({
            "id":           record.get("id", ""),
            "symbol":       record.get("symbol", ""),
            "strategy":     record.get("strategy", ""),
            "side":         record.get("side", ""),
            "entry_price":  float(record.get("entry_price", 0)),
            "exit_price":   float(record.get("exit_price", 0)),
            "pnl":          float(record.get("pnl", 0)),
            "outcome":      record.get("outcome", ""),
            "regime_label": record.get("regime_label", record.get("regime", "")),
            "lesson":       record.get("qwen_lesson", ""),
            "demo_mode":    bool(record.get("demo", True)),
            "closed_at":    record.get("closed_at", record.get("ts", _ts())),
            "raw":          record,
        }).execute()
    except Exception as exc:
        logger.debug("trade_outcomes supabase write failed: %s", exc)


# ── QUIN decisions ────────────────────────────────────────────────────────────

def append_quin_decision(record: dict) -> None:
    try:
        _QUIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_QUIN_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.debug("quin_decisions file write failed: %s", exc)

    sb = _sb()
    if sb is None:
        return
    try:
        sb.table("quin_decisions").insert({
            "decision_id": record.get("decision_id", ""),
            "ts":          record.get("ts", _ts()),
            "action":      record.get("action", ""),
            "confidence":  float(record.get("confidence", 0)),
            "reasoning":   record.get("reasoning", ""),
            "source":      record.get("source", ""),
            "tick_id":     record.get("tick_id", ""),
            "signal":      record.get("tool_calls", [{}])[0].get("params") if record.get("tool_calls") else None,
        }).execute()
    except Exception as exc:
        logger.debug("quin_decisions supabase write failed: %s", exc)


# ── Analysis reports ──────────────────────────────────────────────────────────

def save_analysis_report(report: dict, ts: Optional[str] = None) -> None:
    sb = _sb()
    if sb is None:
        return
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        health = report.get("health_summary", {})
        # Compute overall win rate across all strategies
        all_trades = sum(v.get("trades", 0) for v in health.values() if isinstance(v, dict))
        all_wins   = sum(int(v.get("win_rate", 0) * v.get("trades", 0))
                        for v in health.values() if isinstance(v, dict))
        win_rate   = round(all_wins / all_trades * 100, 1) if all_trades else 0.0

        sb.table("analysis_reports").upsert({
            "report_date":        today,
            "weight_adjustments": report.get("weight_adjustments", {}),
            "immediate_actions":  report.get("immediate_actions", []),
            "strategy_insights":  report.get("health_summary", {}),
            "win_rate_pct":       win_rate,
            "full_report":        report,
            "created_at":         _ts(),
        }, on_conflict="report_date").execute()
    except Exception as exc:
        logger.debug("analysis_reports supabase write failed: %s", exc)


# ── Telegram webhook audit ────────────────────────────────────────────────────

def store_telegram_update(update: dict) -> None:
    sb = _sb()
    if sb is None:
        return
    try:
        sb.table("telegram_updates").insert({
            "update_id":   update.get("update_id", 0),
            "raw":         update,
            "received_at": _ts(),
        }).execute()
    except Exception as exc:
        logger.debug("telegram_updates supabase write failed: %s", exc)
