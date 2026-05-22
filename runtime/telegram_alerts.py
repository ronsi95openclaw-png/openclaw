"""Telegram trade alert notifier for OpenClaw.

Sends one-way notifications to a Telegram chat on:
  - Trade opened (symbol, side, strategy, confidence, SL/TP)
  - Trade closed (symbol, outcome, PnL, running total)
  - Capital state change (SAFE → DEFENSIVE → CRITICAL → HALT)
  - Daily summary (EOD PnL, win rate, top strategy)
  - Emergency halt triggered

Configure in .env:
    TELEGRAM_BOT_TOKEN=<bot_token_from_@BotFather>
    TELEGRAM_CHAT_ID=<your_chat_id>   # get via @userinfobot or getUpdates

All calls are fire-and-forget in a daemon thread — never blocks the scan loop.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger("openclaw.runtime.telegram_alerts")

_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID",   "")


def _send(text: str) -> None:
    """Fire-and-forget Telegram message in background thread."""
    if not _TOKEN or not _CHAT_ID:
        logger.debug("Telegram not configured — skipping alert")
        return

    def _post():
        try:
            import urllib.request, urllib.parse, json
            url     = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
            payload = json.dumps({"chat_id": _CHAT_ID, "text": text,
                                  "parse_mode": "HTML"}).encode()
            req = urllib.request.Request(url, data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status != 200:
                    logger.warning("Telegram send failed: HTTP %s", r.status)
        except Exception as exc:
            logger.debug("Telegram send error: %s", exc)

    threading.Thread(target=_post, daemon=True).start()


def is_configured() -> bool:
    return bool(_TOKEN and _CHAT_ID)


def alert_trade_opened(symbol: str, side: str, strategy: str,
                       entry: float, sl: float, tp: float,
                       size: float, confidence: float,
                       regime: str, demo: bool = True) -> None:
    mode   = "📝 PAPER" if demo else "💰 LIVE"
    arrow  = "🟢 LONG" if side == "long" else "🔴 SHORT"
    msg = (
        f"{mode} | {arrow} <b>{symbol}</b>\n"
        f"Strategy:   {strategy}  ({confidence:.0%} conf)\n"
        f"Entry:      ${entry:,.4f}\n"
        f"Size:       {size:.4f}\n"
        f"SL:         ${sl:,.4f}  |  TP: ${tp:,.4f}\n"
        f"Regime:     {regime}"
    )
    _send(msg)


def alert_trade_closed(symbol: str, outcome: str, pnl: float,
                       total_pnl: float, strategy: str,
                       demo: bool = True) -> None:
    mode   = "📝 PAPER" if demo else "💰 LIVE"
    icon   = "✅ WIN" if outcome == "win" else "❌ LOSS"
    sign   = "+" if pnl >= 0 else ""
    t_sign = "+" if total_pnl >= 0 else ""
    msg = (
        f"{mode} | {icon} <b>{symbol}</b> [{strategy}]\n"
        f"PnL:        {sign}${pnl:.2f}\n"
        f"Total PnL:  {t_sign}${total_pnl:.2f}"
    )
    _send(msg)


def alert_capital_state(old_state: str, new_state: str,
                        equity: float, daily_dd: float) -> None:
    severity = {
        "SAFE": "🟢", "DEFENSIVE": "🟡",
        "CRITICAL": "🔴", "EMERGENCY_HALT": "🚨"
    }
    icon = severity.get(new_state, "⚠️")
    msg = (
        f"{icon} <b>Capital State: {old_state} → {new_state}</b>\n"
        f"Equity:    ${equity:,.2f}\n"
        f"Daily DD:  {daily_dd:.1%}"
    )
    _send(msg)


def alert_daily_summary(date: str, total_pnl: float, trades: int,
                        wins: int, losses: int, demo: bool = True) -> None:
    mode   = "📝 PAPER" if demo else "💰 LIVE"
    wr     = round(wins / trades * 100) if trades else 0
    sign   = "+" if total_pnl >= 0 else ""
    msg = (
        f"{mode} | 📊 Daily Summary {date}\n"
        f"PnL:   {sign}${total_pnl:.2f}\n"
        f"Trades: {trades}  ({wins}W / {losses}L — {wr}% WR)"
    )
    _send(msg)


def alert_emergency_halt(reason: str, equity: float) -> None:
    msg = (
        f"🚨🚨 <b>EMERGENCY HALT</b> 🚨🚨\n"
        f"Reason:  {reason}\n"
        f"Equity:  ${equity:,.2f}\n"
        f"All positions will be flattened. Manual reset required."
    )
    _send(msg)
