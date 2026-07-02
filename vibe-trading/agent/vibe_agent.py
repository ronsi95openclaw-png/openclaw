#!/usr/bin/env python3
"""
Vibe-Trading bridge (custom)
============================
Paper-only trading-analysis agent.

- Brain:      Ollama (qwen2.5:14b on localhost:11434)
- Mandate:    enforces lucid_mandate.json (Lucid 25K eval rules)
- Kill switch: refuses to act if a KILL_SWITCH file exists
- Mode:       ALWAYS "paper" unless LIVE_MODE=true in env (and even then this
              bridge only RECOMMENDS — it never places orders or touches keys)

This module is analysis-only. It does not import any exchange client, does not
read trading keys, and cannot execute a trade. It returns a recommendation dict.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime

import urllib.request

BASE = Path(__file__).resolve().parent.parent          # vibe-trading/
MANDATE_FILE = BASE / "lucid_mandate.json"
KILL_SWITCH = BASE / "KILL_SWITCH"                      # presence => halt
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")


def load_mandate() -> dict:
    with open(MANDATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def kill_switch_engaged() -> bool:
    return KILL_SWITCH.exists()


def is_live() -> bool:
    return os.environ.get("LIVE_MODE", "").lower() == "true"


def _ask_ollama(prompt: str, timeout: int = 60) -> str:
    payload = json.dumps({"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data.get("response", "").strip()


def _build_prompt(context: dict, mandate: dict) -> str:
    rules = mandate["rules"]
    return (
        "You are a disciplined prop-firm trading analyst operating under a STRICT mandate.\n"
        f"Mandate (Lucid 25K eval): {json.dumps(rules)}\n"
        f"Allowed instruments ONLY: {rules['instruments_allowed']}\n"
        f"Max position size: {rules['max_position_size']} contracts. "
        f"Daily trade cap: {rules['daily_trade_cap']}. No overnight holds.\n\n"
        f"Market context:\n{json.dumps(context, indent=2)}\n\n"
        "Respond with a single recommendation: action (long/short/flat), instrument, "
        "size (<= max), rationale (<=2 sentences), and a confidence 0-1. "
        "If nothing meets the mandate, recommend 'flat'."
    )


def _enforce_mandate(rec: dict, mandate: dict) -> dict:
    """Clamp any recommendation to the mandate's hard limits. Defensive layer."""
    rules = mandate["rules"]
    allowed = set(rules["instruments_allowed"])
    if rec.get("instrument") not in allowed:
        rec["instrument"] = None
        rec["action"] = "flat"
        rec["mandate_note"] = "instrument not in allowed list -> forced flat"
    try:
        size = int(rec.get("size", 0))
    except (TypeError, ValueError):
        size = 0
    rec["size"] = max(0, min(size, rules["max_position_size"]))
    return rec


def analyze(context: dict) -> dict:
    """Return a paper-only trade recommendation for the given market context."""
    result = {
        "timestamp": datetime.now().isoformat(),
        "mode": "live" if is_live() else "paper",
        "executed": False,            # this bridge NEVER executes
        "recommendation": None,
    }

    if kill_switch_engaged():
        result["halted"] = True
        result["reason"] = f"KILL_SWITCH present at {KILL_SWITCH} — refusing to act"
        return result

    mandate = load_mandate()
    # Hard safety: this bridge only recommends. Even LIVE_MODE does not enable order placement here.
    if is_live():
        result["warning"] = "LIVE_MODE set, but vibe_agent is analysis-only and will not place orders."

    try:
        raw = _ask_ollama(_build_prompt(context, mandate))
    except Exception as e:
        result["error"] = f"ollama unavailable: {e}"
        return result

    rec = {"action": "flat", "instrument": None, "size": 0, "rationale": raw[:400], "confidence": None}
    rec = _enforce_mandate(rec, mandate)
    result["recommendation"] = rec
    result["raw_model_output"] = raw[:1000]
    return result


if __name__ == "__main__":
    demo_context = {
        "symbol_focus": "ES",
        "session": "RTH",
        "note": "smoke test — no real market data wired",
    }
    print(json.dumps(analyze(demo_context), indent=2))
