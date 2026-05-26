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
        # Compute current balance: explicit field wins, else starting_balance + total_pnl.
        # Never let a missing 'balance' key silently write the starting value.
        _starting = float(raw.get("starting_balance", 98.0))
        _pnl      = float(raw.get("total_pnl", 0.0))
        _balance  = float(raw["balance"]) if "balance" in raw else _starting + _pnl
        sb.table("bot_state").upsert({
            "id":               _SINGLETON_ID,
            "demo_mode":        bool(raw.get("demo_mode", True)),
            "running":          bool(raw.get("running", False)),
            "balance":          _balance,
            "starting_balance": _starting,
            "total_pnl":        _pnl,
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
    """Load bot state — pick whichever source has higher total_pnl (most up-to-date).

    When local PnL > Supabase PnL we also sync local → Supabase so Railway
    starts with the correct balance on the next boot.
    """
    supabase_state: Optional[dict] = None
    local_state:    Optional[dict] = None

    sb = _sb()
    if sb is not None:
        try:
            res = sb.table("bot_state").select("*").eq("id", _SINGLETON_ID).single().execute()
            if res.data:
                r = res.data
                supabase_state = {
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
                logger.info(
                    "bot_state Supabase: balance=%.2f  pnl=%.2f",
                    supabase_state["balance"], supabase_state["total_pnl"],
                )
        except Exception as exc:
            logger.debug("bot_state supabase read failed: %s", exc)

    if _STATE_FILE.exists():
        try:
            local_state = json.loads(_STATE_FILE.read_text())
            # Derive balance if not explicitly stored (older state files omit it)
            if "balance" not in local_state:
                local_state["balance"] = (
                    float(local_state.get("starting_balance", 98.0))
                    + float(local_state.get("total_pnl", 0.0))
                )
            logger.info(
                "bot_state local: balance=%.2f  pnl=%.2f",
                float(local_state.get("balance") or 0),
                float(local_state.get("total_pnl") or 0),
            )
        except Exception:
            pass

    if supabase_state and local_state:
        sb_pnl    = float(supabase_state.get("total_pnl", 0))
        local_pnl = float(local_state.get("total_pnl", 0))
        if local_pnl > sb_pnl:
            logger.info(
                "bot_state: local pnl=%.2f > supabase pnl=%.2f — using local, syncing Supabase",
                local_pnl, sb_pnl,
            )
            save_bot_state(local_state)  # push correct state to cloud
            return local_state
        logger.info("bot_state: using Supabase (pnl=%.2f)", sb_pnl)
        return supabase_state
    elif supabase_state:
        return supabase_state
    elif local_state:
        return local_state
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


def load_trade_outcomes() -> list:
    """Load all trade outcomes — local JSONL file (Supabase not used to avoid large reads)."""
    trades = []
    if _OUTCOMES_FILE.exists():
        try:
            with open(_OUTCOMES_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            trades.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except Exception as exc:
            logger.debug("load_trade_outcomes file read failed: %s", exc)
    return trades


def startup_integrity_check() -> dict:
    """Compare Supabase trade count vs local JSONL on startup.

    Returns {"ok": bool, "issues": list, "supabase_count": int, "local_count": int}.
    Called from main.py before the trading loop starts.
    """
    issues = []

    # Load from local JSONL
    local_trades = load_trade_outcomes()
    local_ids    = {t.get("id") for t in local_trades if t.get("id")}
    local_count  = len(local_ids)

    # Load from Supabase
    supabase_ids   = set()
    supabase_count = 0
    sb = _sb()
    if sb is None:
        issues.append("Supabase unreachable — cannot compare trade counts")
    else:
        try:
            res = sb.table("trade_outcomes").select("id").execute()
            if res.data:
                supabase_ids   = {r["id"] for r in res.data if r.get("id")}
                supabase_count = len(supabase_ids)
        except Exception as exc:
            issues.append(f"Supabase trade_outcomes read failed: {exc}")

    missing_from_supabase = local_ids - supabase_ids
    missing_from_local    = supabase_ids - local_ids

    supabase_reachable = sb is not None and not any("unreachable" in i for i in issues)

    # Only flag local→Supabase drift when Supabase was actually reachable.
    # If Supabase is down we can't tell whether it's real drift or just offline.
    if missing_from_supabase and supabase_reachable:
        issues.append(
            f"STATE DRIFT: {len(missing_from_supabase)} trade(s) in local JSONL "
            f"missing from Supabase: {list(missing_from_supabase)[:5]}"
        )
    # Only flag Supabase→local drift when the local file actually exists.
    # Ephemeral envs (Railway) have no local JSONL by design — that is NOT drift.
    if missing_from_local and supabase_count > 0 and _OUTCOMES_FILE.exists():
        issues.append(
            f"STATE DRIFT: {len(missing_from_local)} trade(s) in Supabase "
            f"missing from local JSONL: {list(missing_from_local)[:5]}"
        )

    ok = len([i for i in issues if "STATE DRIFT" in i]) == 0
    logger.info(
        "startup_integrity_check: ok=%s  local=%d  supabase=%d  issues=%d",
        ok, local_count, supabase_count, len(issues),
    )
    return {
        "ok":             ok,
        "issues":         issues,
        "supabase_count": supabase_count,
        "local_count":    local_count,
    }


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
