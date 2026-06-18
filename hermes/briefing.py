"""Hermes — morning briefing composer.

Pure functions that turn health.py output into a concise plain-text briefing:
one status line per bot plus any actionable alerts ("ClawBot idle >6h",
"N HaulYeah leads uncontacted", "TJR setup sent"). Plain text (no HTML) so it
renders cleanly in Telegram and on a terminal. Easily unit-testable — no I/O.
"""
from __future__ import annotations

from datetime import datetime, timezone

# Surface an idle alert once a bot has been quiet longer than this.
_IDLE_ALERT_S = 6 * 60 * 60  # 6h


def _status_emoji(bot: dict) -> str:
    if bot.get("running"):
        return "🟢"
    if bot.get("status") == "idle":
        return "🟡"
    return "⚪"


def _clawbot_line(bot: dict) -> str:
    emoji = _status_emoji(bot)
    parts = [f"{emoji} ClawBot: {bot.get('status', 'unknown')} ({bot.get('last_seen', 'never')})"]
    ollama = bot.get("ollama") or {}
    if ollama:
        parts.append("ollama up" if ollama.get("online") else "ollama down")
    trades = bot.get("recent_trades") or []
    if trades:
        parts.append(f"{len(trades)} recent trade(s)")
    return " · ".join(parts)


def _haulyeah_line(bot: dict) -> str:
    emoji = _status_emoji(bot)
    parts = [f"{emoji} HaulYeah: {bot.get('status', 'unknown')} ({bot.get('last_seen', 'never')})"]
    pending = bot.get("pending_outreach", 0)
    leads   = bot.get("leads", 0)
    if leads:
        parts.append(f"{leads} lead(s)")
    parts.append(f"{pending} pending outreach")
    return " · ".join(parts)


def _alerts(health: dict) -> list:
    """Derive actionable alert strings from the aggregate health dict."""
    alerts = []

    claw = health.get("clawbot", {})
    age = claw.get("age_seconds")
    if age is not None and age >= _IDLE_ALERT_S:
        alerts.append("⚠️ ClawBot idle >6h — check the bot process")
    elif claw.get("status") == "unknown":
        alerts.append("⚠️ ClawBot status unknown — no runtime artifacts found")
    if claw.get("tjr_setups"):
        alerts.append(f"📐 TJR setup sent ({len(claw['tjr_setups'])} recent)")

    haul = health.get("haulyeah", {})
    pending = haul.get("pending_outreach", 0)
    if pending:
        alerts.append(f"📨 {pending} new HaulYeah lead(s) uncontacted")
    h_age = haul.get("age_seconds")
    if h_age is not None and h_age >= _IDLE_ALERT_S:
        alerts.append("⚠️ HaulYeah idle >6h — check the bot process")

    return alerts


def compose_briefing(health: dict, now: str | None = None) -> str:
    """Compose the plain-text morning briefing from aggregate health output.

    Args:
        health: dict as returned by hermes.health.get_all_health().
        now: optional pre-formatted timestamp string (for deterministic tests).

    Returns:
        A multi-line plain-text briefing string.
    """
    if now is None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [f"🪽 Hermes Briefing — {now}", ""]

    claw = health.get("clawbot")
    if claw:
        lines.append(_clawbot_line(claw))
    haul = health.get("haulyeah")
    if haul:
        lines.append(_haulyeah_line(haul))

    alerts = _alerts(health)
    lines.append("")
    if alerts:
        lines.append("Alerts:")
        for a in alerts:
            lines.append(f"  {a}")
    else:
        lines.append("No alerts — all systems nominal.")

    return "\n".join(lines)
