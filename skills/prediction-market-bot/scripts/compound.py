"""Log trades, classify failures, and generate lessons for compound learning."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

LOG_FILE = Path(__file__).parent / "failure_log.md"


@dataclass
class TradeLesson:
    timestamp: str
    market_id: str
    outcome: str
    profit_loss: float
    classification: str
    summary: str
    actions: List[str]


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def classify_failure(outcome: str, reason: str) -> str:
    """Classify the cause of a losing trade."""
    reason_lower = reason.lower()
    if "execution" in reason_lower:
        return "execution"
    if "prediction" in reason_lower or "model" in reason_lower:
        return "prediction"
    if "timing" in reason_lower or "news" in reason_lower:
        return "information"
    return "unknown"


def log_trade_lesson(lesson: TradeLesson) -> None:
    entry = [
        f"## {lesson.timestamp} | {lesson.market_id} | {lesson.outcome}",
        f"- Profit/loss: {lesson.profit_loss:.2f}",
        f"- Classification: {lesson.classification}",
        f"- Summary: {lesson.summary}",
        f"- Actions: {', '.join(lesson.actions)}",
        "",
    ]
    LOG_FILE.write_text(LOG_FILE.read_text(encoding="utf-8") + "\n".join(entry), encoding="utf-8")


def build_lesson(
    market_id: str,
    outcome: str,
    profit_loss: float,
    reason: str,
    actions: List[str],
) -> TradeLesson:
    classification = classify_failure(outcome, reason)
    return TradeLesson(
        timestamp=_timestamp(),
        market_id=market_id,
        outcome=outcome,
        profit_loss=profit_loss,
        classification=classification,
        summary=reason,
        actions=actions,
    )


if __name__ == "__main__":
    print("Prediction market compound module loaded.")
