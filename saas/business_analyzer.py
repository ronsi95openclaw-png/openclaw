"""Generic business analysis module — reusable template for any SaaS domain.

Every analysis call routes through brain() with purpose="analysis" or
purpose="high_stakes", so it automatically benefits from the middleware
pipeline (intent classification, output compression, memory routing).

Extend this for any business context: inventory, bar stock, client pipelines,
ad spend, revenue analysis, etc.

Usage:
    from saas.business_analyzer import analyze, optimize_inventory, summarize_metrics

    report = analyze("Should I raise prices by 15%?", context={"revenue": 8400})
    inv    = optimize_inventory({"beer": 12, "gin": 2, "rum": 45})
    kpis   = summarize_metrics({"revenue": 8400, "costs": 5100, "customers": 34})
"""
from __future__ import annotations

import json
from typing import Any

from lib.brain import brain


def analyze(question: str, context: dict | None = None, high_stakes: bool = False) -> str:
    """General-purpose business question routed through brain().

    Args:
        question:    The business question to answer.
        context:     Optional dict of supporting data injected into the prompt.
        high_stakes: True forces Claude Haiku (T3); False routes automatically.

    Returns:
        Plain text analysis response.
    """
    prompt = question
    if context:
        prompt = f"{question}\n\nContext data:\n{json.dumps(context, default=str, indent=2)}"

    result = brain(
        prompt=prompt,
        purpose="high_stakes" if high_stakes else "analysis",
    )
    return result["text"]


def optimize_inventory(inventory: dict[str, Any]) -> str:
    """Analyse an inventory dict and return reorder recommendations.

    Args:
        inventory: {item_name: quantity} or richer dicts with cost/threshold.

    Returns:
        Bullet-point recommendations.
    """
    prompt = (
        "Analyse this inventory and return:\n"
        "- Low stock items (flag anything below safe threshold)\n"
        "- Overstock items (flag anything 3x above normal usage)\n"
        "- Prioritised reorder suggestions\n\n"
        f"Inventory:\n{json.dumps(inventory, indent=2)}"
    )
    return brain(prompt=prompt, purpose="analysis")["text"]


def summarize_metrics(metrics: dict[str, Any]) -> str:
    """Summarise KPIs and suggest one concrete action.

    Args:
        metrics: Dict of KPI names → values (revenue, costs, customers, etc.)

    Returns:
        3-5 bullet summary with one actionable recommendation.
    """
    prompt = (
        "Summarise these business metrics in 3-5 bullets.\n"
        "End with ONE concrete action to improve the weakest metric.\n\n"
        f"Metrics:\n{json.dumps(metrics, indent=2, default=str)}"
    )
    return brain(prompt=prompt, purpose="analysis")["text"]


def validate_trade_decision(trade: dict) -> tuple[bool, str]:
    """Execution guard: validate a trade dict before it reaches the exchange.

    Checks structural completeness and confidence threshold.
    Does not make network calls — deterministic logic only.

    Args:
        trade: Dict with keys: entry, stop_loss, take_profit, confidence (0-1),
               direction (long/short/neutral).

    Returns:
        (is_valid, reason_string)
    """
    required = ("entry", "stop_loss", "direction", "confidence")
    missing  = [k for k in required if k not in trade]
    if missing:
        return False, f"Missing fields: {missing}"

    if trade["direction"] == "neutral":
        return False, "Direction is neutral — no trade"

    try:
        conf = float(trade["confidence"])
    except (TypeError, ValueError):
        return False, "confidence must be a number 0-1"

    if conf < 0.6:
        return False, f"Confidence {conf:.2f} below 0.60 threshold"

    entry = float(trade["entry"])
    stop  = float(trade["stop_loss"])
    if trade["direction"] == "long" and stop >= entry:
        return False, "stop_loss must be below entry for a long"
    if trade["direction"] == "short" and stop <= entry:
        return False, "stop_loss must be above entry for a short"

    return True, "ok"
