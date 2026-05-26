"""Telegram alert notifier for OpenClaw — outbound alerts only.

Sends fire-and-forget notifications on every key bot event.
TOKEN and CHAT_ID are read fresh on each call so hot-reloading .env works.

Configure in .env:
    TELEGRAM_BOT_TOKEN=<from @BotFather>
    TELEGRAM_CHAT_ID=<your personal or group chat ID>
"""
from __future__ import annotations

import logging
import os
import threading
import json
import urllib.request
from typing import Optional

logger = logging.getLogger("openclaw.runtime.telegram_alerts")


def _token()   -> str: return os.getenv("TELEGRAM_BOT_TOKEN", "")
def _chat_id() -> str: return os.getenv("TELEGRAM_CHAT_ID",   "")


def _send(text: str, parse_mode: str = "HTML") -> None:
    """Fire-and-forget Telegram message. Never raises."""
    tok = _token()
    cid = _chat_id()
    if not tok or not cid:
        logger.debug("Telegram not configured — skipping alert")
        return

    def _post() -> None:
        try:
            url     = f"https://api.telegram.org/bot{tok}/sendMessage"
            payload = json.dumps({
                "chat_id":    cid,
                "text":       text,
                "parse_mode": parse_mode,
            }).encode()
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status != 200:
                    logger.warning("Telegram send failed: HTTP %s", r.status)
        except Exception as exc:
            logger.debug("Telegram send error (non-fatal): %s", exc)

    threading.Thread(target=_post, daemon=True, name="tg-alert").start()


def is_configured() -> bool:
    return bool(_token() and _chat_id())


# ── Outbound alerts ───────────────────────────────────────────────────────────

def _pbar(current: float, total: float, width: int = 8) -> str:
    """ASCII progress bar: [████░░░░] 60%"""
    ratio  = min(current / total, 1.0) if total > 0 else 0.0
    filled = int(width * ratio)
    pct    = int(ratio * 100)
    done   = "✅" if ratio >= 1.0 else f"{pct}%"
    return f"[{'█' * filled}{'░' * (width - filled)}] {done}"


def alert_bot_started(demo: bool, balance: float) -> None:
    mode = "📝 PAPER TRADING" if demo else "💰 LIVE TRADING"
    # Live gate progress
    paper_count = 0
    win_rate    = 0.0
    try:
        from runtime.live_mode_gate import _load_paper_trades
        trades      = _load_paper_trades()
        paper_count = len(trades)
        wins        = sum(1 for t in trades if t.get("outcome") == "win")
        win_rate    = wins / paper_count if paper_count else 0.0
    except Exception:
        pass
    _send(
        f"🚀 <b>OpenClaw Bot Started</b>\n"
        f"──────────────────────\n"
        f"Mode:      {mode}\n"
        f"Balance:   <b>${balance:,.2f}</b>\n"
        f"Goal:      $98 → $50,000\n"
        f"──────────────────────\n"
        f"📊 Live Gate Progress\n"
        f"Trades: {_pbar(paper_count, 30)} {paper_count}/30\n"
        f"WR:     {_pbar(win_rate * 100, 54)} {win_rate:.0%}/54%\n"
        f"──────────────────────\n"
        f"Midnight report UTC 📊 | /help for commands"
    )


def alert_execution_resumed(balance: float = 0.0) -> None:
    bal = f"  Balance: ${balance:,.2f}" if balance else ""
    _send(f"▶️ <b>Trade execution resumed</b>{bal}")


def alert_capital_recovered(old_state: str, balance: float) -> None:
    _send(
        f"🟢 <b>Capital state → SAFE</b>\n"
        f"Recovered from: {old_state}\n"
        f"Balance: ${balance:,.2f}"
    )


def alert_trade_opened(symbol: str, side: str, strategy: str,
                       entry: float, sl: float, tp: float,
                       size: float, confidence: float,
                       regime: str, demo: bool = True,
                       balance: float = 0.0,
                       quin_source: str = "") -> None:
    mode  = "📝 PAPER" if demo else "💰 LIVE"
    arrow = "🟢 LONG" if side == "long" else "🔴 SHORT"
    sl_pct = abs(sl - entry) / entry * 100
    tp_pct = abs(tp - entry) / entry * 100
    quin_line = f"\nQUIN:       {quin_source}" if quin_source else ""
    bal_line  = f"\nBalance:    ${balance:,.2f}" if balance else ""
    _send(
        f"{mode} | {arrow} <b>{symbol}</b>\n"
        f"Strategy:   <b>{strategy}</b>  ({confidence:.0%})\n"
        f"Entry:      ${entry:,.4f}\n"
        f"SL:         ${sl:,.4f}  (-{sl_pct:.1f}%)\n"
        f"TP:         ${tp:,.4f}  (+{tp_pct:.1f}%)\n"
        f"Size:       {size:.4f}\n"
        f"Regime:     {regime}"
        f"{quin_line}"
        f"{bal_line}"
    )


def alert_trade_closed(symbol: str, outcome: str, pnl: float,
                       total_pnl: float, strategy: str,
                       balance: float = 0.0,
                       demo: bool = True) -> None:
    mode   = "📝 PAPER" if demo else "💰 LIVE"
    icon   = "✅ WIN" if outcome == "win" else "❌ LOSS"
    sign   = "+" if pnl >= 0 else ""
    t_sign = "+" if total_pnl >= 0 else ""
    bal_line = f"\n💰 Balance:  ${balance:,.2f}" if balance else ""
    _send(
        f"{mode} | {icon} <b>{symbol}</b> [{strategy}]\n"
        f"PnL:        <b>{sign}${pnl:,.2f}</b>\n"
        f"Total PnL:  {t_sign}${total_pnl:,.2f}"
        f"{bal_line}"
    )


def alert_capital_state(old_state: str, new_state: str,
                        equity: float, daily_dd: float) -> None:
    icons = {"SAFE": "🟢", "DEFENSIVE": "🟡",
             "CRITICAL": "🔴", "EMERGENCY_HALT": "🚨"}
    icon = icons.get(new_state, "⚠️")
    _send(
        f"{icon} <b>Capital: {old_state} → {new_state}</b>\n"
        f"Equity:    ${equity:,.2f}\n"
        f"Daily DD:  {daily_dd:.2%}"
    )


def alert_daily_summary(date: str, total_pnl: float, trades: int,
                        wins: int, losses: int, demo: bool = True,
                        balance: float = 0.0,
                        best_strategy: str = "",
                        goal_balance: float = 0.0,
                        goal_target: float = 50_000.0) -> None:
    mode = "📝 PAPER" if demo else "💰 LIVE"
    wr   = round(wins / trades * 100, 1) if trades else 0.0
    sign = "+" if total_pnl >= 0 else ""

    if not trades:
        _send(f"📊 Daily Report — No trades today")
        return

    best_line  = f"\n🏆 Best:      {best_strategy}" if best_strategy else ""
    goal_line  = ""
    if goal_balance and goal_target:
        goal_line = f"\n──────────────────────\n🎯 Progress: ${goal_balance:,.2f} / ${goal_target:,.0f}"

    _send(
        f"📊 <b>DAILY REPORT {date}</b>\n"
        f"──────────────────────\n"
        f"💰 Balance:  ${balance:,.2f}\n"
        f"📈 P&L:      {sign}${total_pnl:,.2f}\n"
        f"🎯 Win Rate: {wr}%\n"
        f"📊 Trades:   {trades}  ({wins}W / {losses}L)"
        f"{best_line}"
        f"{goal_line}"
    )


def alert_emergency_halt(reason: str, equity: float) -> None:
    _send(
        f"🚨🚨 <b>EMERGENCY HALT</b> 🚨🚨\n"
        f"Reason:  {reason}\n"
        f"Equity:  ${equity:,.2f}\n"
        f"All positions flattened. Manual reset required."
    )


def alert_milestone_hit(milestone: float, balance: float,
                        days: float, demo: bool = True) -> None:
    """Fired when the goal tracker crosses a milestone."""
    mode = "📝 PAPER" if demo else "💰 LIVE"
    _send(
        f"🏆🏆 <b>MILESTONE HIT!</b> 🏆🏆\n"
        f"{mode}\n"
        f"Target:   <b>${milestone:,.0f}</b>\n"
        f"Balance:  ${balance:,.2f}\n"
        f"Days:     {days:.1f}\n"
        f"Next milestone on the road to $50,000 🚀"
    )


def alert_quin_blocked(symbol: str, strategy: str,
                       reason: str) -> None:
    """Fired when QUIN vetoes a signal the intent pipeline approved."""
    _send(
        f"🤖 <b>QUIN Block</b> [{symbol}/{strategy}]\n"
        f"Reason: {reason[:120]}"
    )


def alert_scan_health(tick: int, regimes: dict, errors: int) -> None:
    """Optional periodic health ping (sent every 100 ticks ~ 100 min)."""
    regime_str = "  ".join(f"{s.replace('_USDT','')}: {r}"
                           for s, r in regimes.items())
    err_str    = f"  ⚠️ {errors} error(s)" if errors else "  ✅ clean"
    _send(
        f"📡 <b>Scan Health</b>  tick #{tick}\n"
        f"Regimes:  {regime_str}\n"
        f"Status:  {err_str}"
    )
