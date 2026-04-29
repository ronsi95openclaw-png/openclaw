"""LLM-augmented trading signal analysis.

Sits between strategy.py (RSI+MACD signals) and executor.py (order placement).
Passes HIGH confidence signals through brain(purpose="high_stakes") for a
second opinion before execution, returning enriched signal dicts with an
LLM verdict and adjusted confidence.

Usage:
    from trading.analyzer import enrich_signals

    signals = strategy.scan_all(candle_data)
    enriched = enrich_signals(signals)          # adds llm_verdict field
    high = [s for s in enriched if s["execute"]]
"""
from __future__ import annotations

import json
import logging
from typing import Any

from lib.brain import brain

logger = logging.getLogger("clawbot.trading.analyzer")

_ANALYSIS_PROMPT = """\
You are a strict crypto risk officer reviewing trade signals before execution.

Signals submitted for review:
{signals_json}

For each signal return a JSON array with this exact structure:
[
  {{
    "coin": "<coin>",
    "action": "<BUY|SELL>",
    "execute": <true|false>,
    "adjusted_confidence": "<HIGH|MEDIUM|LOW>",
    "verdict": "<one sentence reason>"
  }}
]

Rules:
- execute=true only when adjusted_confidence is HIGH
- Downgrade confidence if: RSI is only marginally oversold/overbought (within 3 of threshold),
  histogram is weak (absolute value < 0.0001), or macro risk is elevated
- Upgrade confidence only when all indicators align strongly
- Reply with ONLY the JSON array, no other text"""


def enrich_signals(signals: list, portfolio_usd: float = 0.0) -> list[dict]:
    """Run HIGH-confidence signals through LLM review.

    Args:
        signals:       List of Signal objects from RSIMACDStrategy.scan_all()
        portfolio_usd: Current portfolio value (added as context if > 0)

    Returns:
        List of dicts with original signal data plus:
            execute (bool), adjusted_confidence (str), verdict (str), llm_reviewed (bool)
    """
    if not signals:
        return []

    # Separate HIGH from non-HIGH — only HIGH ones need LLM review
    high = [s for s in signals if s.confidence == "HIGH"]
    others = [s for s in signals if s.confidence != "HIGH"]

    # Pass-through for non-HIGH signals (skip LLM, never execute)
    results: list[dict] = []
    for sig in others:
        results.append(_to_dict(sig, execute=False, adjusted_confidence=sig.confidence,
                                verdict="Skipped — not HIGH confidence", llm_reviewed=False))

    if not high:
        return results

    # Build prompt payload
    signal_list = [
        {
            "coin":            s.coin,
            "action":          s.action,
            "rsi":             round(s.rsi, 2),
            "macd_histogram":  round(s.macd_histogram, 6),
            "confidence":      s.confidence,
            "reason":          s.reason,
        }
        for s in high
    ]

    prompt_text = _ANALYSIS_PROMPT.format(signals_json=json.dumps(signal_list, indent=2))
    if portfolio_usd > 0:
        prompt_text += f"\n\nPortfolio value: ${portfolio_usd:,.0f}"

    try:
        response = brain(prompt=prompt_text, purpose="high_stakes")
        verdicts = _parse_response(response["text"])
    except Exception as exc:
        logger.error(f"LLM signal review failed: {exc} — falling back to original confidence")
        verdicts = {}

    # Merge LLM verdicts with original signal data
    for sig in high:
        v = verdicts.get(sig.coin, {})
        execute = v.get("execute", False)
        adj_conf = v.get("adjusted_confidence", sig.confidence)
        verdict = v.get("verdict", "LLM review unavailable")
        results.append(_to_dict(sig, execute=execute,
                                adjusted_confidence=adj_conf,
                                verdict=verdict, llm_reviewed=True))
        logger.info(
            f"[{sig.coin}] {sig.action} | LLM: execute={execute} "
            f"adj_conf={adj_conf} | {verdict}"
        )

    return results


def _parse_response(text: str) -> dict[str, dict]:
    """Parse LLM JSON response into a coin-keyed dict."""
    text = text.strip()
    # Find JSON array in response even if there's surrounding text
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return {}
    try:
        items = json.loads(text[start:end + 1])
        return {item["coin"]: item for item in items if "coin" in item}
    except Exception as exc:
        logger.warning(f"Failed to parse LLM signal review: {exc}")
        return {}


def _to_dict(sig: Any, execute: bool, adjusted_confidence: str,
             verdict: str, llm_reviewed: bool) -> dict:
    return {
        "coin":                sig.coin,
        "action":              sig.action,
        "rsi":                 sig.rsi,
        "macd":                sig.macd,
        "macd_histogram":      sig.macd_histogram,
        "confidence":          sig.confidence,
        "adjusted_confidence": adjusted_confidence,
        "execute":             execute,
        "verdict":             verdict,
        "llm_reviewed":        llm_reviewed,
        "reason":              sig.reason,
    }
