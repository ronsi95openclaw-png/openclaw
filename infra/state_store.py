"""Unified state persistence layer for OpenClaw.

Writes to Supabase (cloud) and local files (fallback).
All public functions are non-blocking and swallow exceptions so they
never crash the bot on a storage failure.
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

# ── File paths (unchanged from original) ─────────────────────────────────────

_ROOT          = Path(__file__).parent.parent
_STATE_FILE    = _ROOT / "data" / "cryptocom_state.json"
_CAPITAL_FILE  = _ROOT / "data" / "capital_state.json"
_WEIGHTS_FILE  = _ROOT / "data" / "strategy_weights.json"
_GOAL_FILE     = _ROOT / "data" / "goal_tracker.json"
_OUTCOMES_FILE = _ROOT / "data" / "logs" / "trade_outcomes.jsonl"
_QUIN_FILE     = _ROOT / "data" / "quin_decisions.jsonl"


def _sb():
    """Return Supabase client or None."""
    try:
        from infra.supabase_client import get_client
        return get_client()
    except Exception:
        return None


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Bot state ─────────────────────────────────────────────────────────────────

def save_bot_state(state: dict) -> None:
    """Persist bot state to Supabase + local file."""
    # Local file (always)
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as exc:
        logger.debug("bot_state file write failed: %s", exc)

    # Supabase upsert
    sb = _sb()
    if sb is None:
        return
    try:
        sb.table("bot_state").upsert({
            "id": 1,
            "state": state,
            "updated_at": _ts(),
        }, on_conflict="id").execute()
    except Exception as exc:
        logger.debug("bot_state supabase write failed: %s", exc)


def load_bot_state() -> Optional[dict]:
    """Load bot state — Supabase first, local file fallback."""
    sb = _sb()
    if sb is not None:
        try:
            res = sb.table("bot_state").select("state").eq("id", 1).single().execute()
            if res.data and res.data.get("state"):
                logger.info("bot_state loaded from Supabase")
                return res.data["state"]
        except Exception as exc:
            logger.debug("bot_state supabase read failed: %s", exc)

    # File fallback
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except Exception:
            pass
    return None


# ── Capital state ─────────────────────────────────────────────────────────────

def save_capital_state(state: dict) -> None:
    try:
        _CAPITAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CAPITAL_FILE.write_text(json.dumps(state, indent=2))
    except Exception as exc:
        logger.debug("capital_state file write failed: %s", exc)

    sb = _sb()
    if sb is None:
        return
    try:
        sb.table("capital_state").upsert({
            "id": 1,
            "state": state,
            "updated_at": _ts(),
        }, on_conflict="id").execute()
    except Exception as exc:
        logger.debug("capital_state supabase write failed: %s", exc)


def load_capital_state() -> Optional[dict]:
    sb = _sb()
    if sb is not None:
        try:
            res = sb.table("capital_state").select("state").eq("id", 1).single().execute()
            if res.data and res.data.get("state"):
                return res.data["state"]
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
    """weights: {STRATEGY_NAME: weight_float, ...}"""
    try:
        _WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _WEIGHTS_FILE.write_text(json.dumps(weights, indent=2))
    except Exception as exc:
        logger.debug("strategy_weights file write failed: %s", exc)

    sb = _sb()
    if sb is None:
        return
    try:
        rows = [
            {"strategy": k, "weight": v, "updated_at": _ts()}
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
                return {r["strategy"]: r["weight"] for r in res.data}
        except Exception as exc:
            logger.debug("strategy_weights supabase read failed: %s", exc)

    if _WEIGHTS_FILE.exists():
        try:
            return json.loads(_WEIGHTS_FILE.read_text())
        except Exception:
            pass
    return None


# ── Goal tracker ──────────────────────────────────────────────────────────────

def save_goal_state(state: dict) -> None:
    try:
        _GOAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _GOAL_FILE.write_text(json.dumps(state, indent=2))
    except Exception as exc:
        logger.debug("goal_tracker file write failed: %s", exc)

    sb = _sb()
    if sb is None:
        return
    try:
        sb.table("goal_tracker").upsert({
            "id": 1,
            "state": state,
            "updated_at": _ts(),
        }, on_conflict="id").execute()
    except Exception as exc:
        logger.debug("goal_tracker supabase write failed: %s", exc)


def load_goal_state() -> Optional[dict]:
    sb = _sb()
    if sb is not None:
        try:
            res = sb.table("goal_tracker").select("state").eq("id", 1).single().execute()
            if res.data and res.data.get("state"):
                return res.data["state"]
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
    """Append a closed trade record to JSONL file + Supabase."""
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
        row = {
            "trade_id":   record.get("id", ""),
            "strategy":   record.get("strategy", ""),
            "symbol":     record.get("symbol", ""),
            "side":       record.get("side", ""),
            "outcome":    record.get("outcome", ""),
            "pnl":        float(record.get("pnl", 0)),
            "confidence": float(record.get("confidence", 0)),
            "regime":     record.get("regime_label", ""),
            "closed_at":  record.get("closed_at", _ts()),
            "raw":        record,
        }
        sb.table("trade_outcomes").insert(row).execute()
    except Exception as exc:
        logger.debug("trade_outcomes supabase write failed: %s", exc)


# ── QUIN decisions ────────────────────────────────────────────────────────────

def append_quin_decision(record: dict) -> None:
    """Append a QUIN decision record."""
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
        row = {
            "ts":         record.get("ts", _ts()),
            "action":     record.get("action", ""),
            "confidence": float(record.get("confidence", 0)),
            "source":     record.get("source", ""),
            "symbol":     record.get("symbol", ""),
            "strategy":   record.get("strategy", ""),
            "raw":        record,
        }
        sb.table("quin_decisions").insert(row).execute()
    except Exception as exc:
        logger.debug("quin_decisions supabase write failed: %s", exc)


# ── Analysis reports ──────────────────────────────────────────────────────────

def save_analysis_report(report: dict, ts: Optional[str] = None) -> None:
    """Save a Claude Opus analysis report to Supabase."""
    if ts is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    sb = _sb()
    if sb is None:
        return
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        sb.table("analysis_reports").upsert({
            "report_date": today,
            "report": report,
            "created_at": _ts(),
        }, on_conflict="report_date").execute()
    except Exception as exc:
        logger.debug("analysis_reports supabase write failed: %s", exc)


# ── Telegram webhook store ────────────────────────────────────────────────────

def store_telegram_update(update: dict) -> None:
    """Persist a raw Telegram update (for audit + replay)."""
    sb = _sb()
    if sb is None:
        return
    try:
        sb.table("telegram_updates").insert({
            "update_id":  update.get("update_id", 0),
            "raw":        update,
            "received_at": _ts(),
        }).execute()
    except Exception as exc:
        logger.debug("telegram_updates supabase write failed: %s", exc)
