"""Two-way Telegram command bot for OpenClaw.

Polls Telegram for incoming messages and responds to slash commands.
Runs as a daemon thread — never blocks the scan loop.

Supported commands (as seen in the working bot screenshot):
    /help      — list all commands
    /status    — current bot status (running, capital, scan interval)
    /trades    — today's closed trades + open positions
    /goal      — $98 → $50K progress with milestones
    /balance   — current demo balance + PnL
    /weights   — strategy weights and win rates
    /pause     — (no-op in demo mode — informs user)
    /halt      — info on how to release an emergency halt

TOKEN and CHAT_ID are read fresh each poll so adding .env creds
restarts polling automatically within 30 seconds.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error
from typing import Any, Optional

logger = logging.getLogger("openclaw.runtime.telegram_bot")

_POLL_TIMEOUT  = 25   # long-poll timeout seconds
_RETRY_SLEEP   = 5    # seconds to wait after a network error
_HEALTH_EVERY  = 100  # send health ping every N ticks


def _token()   -> str: return os.getenv("TELEGRAM_BOT_TOKEN", "")
def _chat_id() -> str: return os.getenv("TELEGRAM_CHAT_ID",   "")


def _api(method: str, params: dict, timeout: int = 10) -> Optional[dict]:
    tok = _token()
    if not tok:
        return None
    url     = f"https://api.telegram.org/bot{tok}/{method}"
    payload = json.dumps(params).encode()
    req     = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as exc:
        logger.debug("Telegram API %s error: %s", method, exc)
        return None


def _reply(chat_id: int | str, text: str) -> None:
    _api("sendMessage", {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "HTML",
    })


# ── Command handlers ──────────────────────────────────────────────────────────

def _cmd_help(chat_id, _text, bot_ref) -> None:
    _reply(chat_id,
        "🤖 <b>OpenClaw Commands</b>\n"
        "──────────────────────\n"
        "/status   — bot status &amp; capital state\n"
        "/trades   — today's trades &amp; open positions\n"
        "/goal     — $98 → $50K progress\n"
        "/balance  — current balance &amp; PnL\n"
        "/weights  — strategy weights &amp; win rates\n"
        "/help     — this message\n"
        "──────────────────────\n"
        "Daily report at midnight UTC 📊"
    )


def _cmd_status(chat_id, _text, bot_ref) -> None:
    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot reference not available yet.")
        return
    try:
        s = bot_ref.get_status()
        running   = "🟢 Running" if s["running"] else "🔴 Stopped"
        demo      = "📝 PAPER" if s["demo_mode"] else "💰 LIVE"
        cap       = s.get("capital_state", "UNKNOWN")
        cap_icon  = {"SAFE": "🟢", "DEFENSIVE": "🟡",
                     "CRITICAL": "🔴", "EMERGENCY_HALT": "🚨"}.get(cap, "⚪")
        scan_int  = s.get("scan_interval", "?")
        last_scan = s.get("last_scan", "—")
        open_pos  = len(s.get("open_positions", []))
        _reply(chat_id,
            f"📡 <b>Bot Status</b>\n"
            f"──────────────────────\n"
            f"State:      {running}\n"
            f"Mode:       {demo}\n"
            f"Capital:    {cap_icon} {cap}\n"
            f"Scan:       every {scan_int}s\n"
            f"Last scan:  {last_scan}\n"
            f"Positions:  {open_pos} open\n"
            f"Msg:        {s.get('status_msg','')[:60]}"
        )
    except Exception as exc:
        _reply(chat_id, f"⚠️ Status error: {exc}")


def _cmd_trades(chat_id, _text, bot_ref) -> None:
    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot reference not available yet.")
        return
    try:
        from datetime import datetime, timezone
        s        = bot_ref.get_status()
        closed   = s.get("trade_log", [])
        open_p   = s.get("open_positions", [])
        balance  = s.get("balance", 0)
        today    = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Closed today
        today_closed = [t for t in closed if t.get("closed_at")]
        # Open positions count as today's trades too
        all_today = today_closed + open_p

        wins   = [t for t in today_closed if t.get("outcome") == "win"]
        losses = [t for t in today_closed if t.get("outcome") == "loss"]
        day_pnl = sum(t.get("pnl", 0) for t in today_closed)
        wr = (len(wins) / len(today_closed) * 100) if today_closed else 0.0

        lines = [f"📊 <b>TODAY'S TRADES — {today}</b>", "──"]

        if not all_today:
            lines.append("\n—\nNo trades yet today")
        else:
            lines.append("")
            # Closed trades numbered
            for i, t in enumerate(today_closed, 1):
                icon   = "✅" if t.get("outcome") == "win" else "❌"
                strat  = t.get("strategy", "").replace("_", "")
                sym    = t.get("symbol", "").replace("_USDT", "")
                side   = t.get("side", "").upper()
                entry  = t.get("entry_price", 0)
                pnl    = t.get("pnl", 0)
                sign   = "+" if pnl >= 0 else ""
                lines.append(f"{i}. {icon} <i>{strat} {sym} {side} @ ${entry:,.2f}</i>\n→ ${sign}{pnl:,.2f}")
            # Open positions
            for i, p in enumerate(open_p, len(today_closed) + 1):
                strat  = p.get("strategy", "").replace("_", "")
                sym    = p.get("symbol", "").replace("_USDT", "")
                side   = p.get("side", "").upper()
                entry  = p.get("entry_price", 0)
                unr    = p.get("unrealized_pnl", 0)
                sign   = "+" if unr >= 0 else ""
                lines.append(f"{i}. 🔵 <i>{strat} {sym} {side} @ ${entry:,.2f}</i>\n→ {sign}${unr:,.2f} (open)")

        day_sign = "+" if day_pnl >= 0 else ""
        lines += [
            "──", "",
            f"📊 Win Rate: {wr:.1f}% ({len(wins)}W/{len(losses)}L)",
            f"💰 Day P&amp;L: {day_sign}${day_pnl:,.2f}",
            f"💳 Balance: ${balance:,.2f}",
        ]
        _reply(chat_id, "\n".join(lines))
    except Exception as exc:
        _reply(chat_id, f"⚠️ Trades error: {exc}")


def _cmd_goal(chat_id, _text, bot_ref) -> None:
    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot reference not available yet.")
        return
    try:
        tracker = getattr(bot_ref, "_goal_tracker", None)
        if tracker is None:
            from runtime.goal_tracker import get_goal_tracker
            tracker = get_goal_tracker()
        balance  = bot_ref._refresh_balance()
        progress = tracker.update(balance)

        MILESTONE_EMOJIS = {200: "🥉", 500: "🥈", 1000: "🥇",
                            2500: "💎", 5000: "🔥", 10000: "⭐",
                            25000: "🚀", 50000: "🏆"}
        hit  = set(progress.milestones_hit)
        ms_lines = []
        for ms in [200, 500, 1000, 2500, 5000, 10000, 25000, 50000]:
            em   = MILESTONE_EMOJIS.get(ms, "🎯")
            done = "✅" if ms in hit else ("👉" if ms == progress.next_milestone else "  ")
            ms_lines.append(f"{done} {em} ${ms:,}")

        eta_str = f"{progress.eta_days}d" if progress.eta_days else "—"
        sign    = "+" if progress.total_gain_usd >= 0 else ""

        _reply(chat_id,
            f"🎯 <b>Goal: $98 → $50,000</b>\n"
            f"──────────────────────\n"
            f"💰 Balance:   <b>${progress.current_balance:,.2f}</b>\n"
            f"📈 Gain:      {sign}${progress.total_gain_usd:,.2f} ({sign}{progress.total_gain_pct:.1f}%)\n"
            f"✖️  Achieved:  {progress.multiplier_achieved:.2f}× / {progress.multiplier_needed:.0f}×\n"
            f"📊 Progress:  {progress.progress_pct:.3f}%\n"
            f"📅 Days:      {progress.days_running:.1f}\n"
            f"⏱ ETA:       {eta_str}\n"
            f"──────────────────────\n"
            + "\n".join(ms_lines)
        )
    except Exception as exc:
        _reply(chat_id, f"⚠️ Goal error: {exc}")


def _cmd_balance(chat_id, _text, bot_ref) -> None:
    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot reference not available yet.")
        return
    try:
        s       = bot_ref.get_status()
        balance = s.get("balance", 0)
        pnl     = s.get("total_pnl", 0)
        unreal  = s.get("unrealized_pnl", 0)
        sign    = "+" if pnl >= 0 else ""
        u_sign  = "+" if unreal >= 0 else ""
        start   = getattr(bot_ref.state, "starting_balance", 98.0)
        _reply(chat_id,
            f"💰 <b>Balance</b>\n"
            f"──────────────────────\n"
            f"Current:     <b>${balance:,.2f}</b>\n"
            f"Starting:    ${start:,.2f}\n"
            f"Total PnL:   {sign}${pnl:,.2f}\n"
            f"Unrealised:  {u_sign}${unreal:,.2f}"
        )
    except Exception as exc:
        _reply(chat_id, f"⚠️ Balance error: {exc}")


def _cmd_weights(chat_id, _text, bot_ref) -> None:
    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot reference not available yet.")
        return
    try:
        weights = bot_ref.get_status().get("strategy_weights", {})
        if not weights:
            _reply(chat_id, "No weight data available.")
            return
        lines = ["📊 <b>Strategy Weights</b>", "──────────────────────"]
        for strat, data in sorted(weights.items()):
            w    = data.get("weight", 1.0)
            wr   = data.get("win_rate", 50.0)
            t    = data.get("trades", 0)
            warn = " ⚠️" if w < 0.5 else ""
            lines.append(f"<b>{strat}</b>{warn}\n"
                         f"  Weight: {w:.2f}  WR: {wr:.0f}%  T: {t}")
        _reply(chat_id, "\n".join(lines))
    except Exception as exc:
        _reply(chat_id, f"⚠️ Weights error: {exc}")


_COMMANDS = {
    "/help":    _cmd_help,
    "/start":   _cmd_help,
    "/status":  _cmd_status,
    "/trades":  _cmd_trades,
    "/goal":    _cmd_goal,
    "/balance": _cmd_balance,
    "/weights": _cmd_weights,
}


# ── Polling daemon ────────────────────────────────────────────────────────────

class TelegramCommandBot:
    """Long-polls Telegram getUpdates and dispatches slash commands.

    Thread-safe.  Safe to start/stop multiple times.
    Never raises — all exceptions are caught internally.
    """

    def __init__(self, bot_ref: Any = None) -> None:
        self._bot_ref  = bot_ref   # CryptoComBot reference, set after init
        self._stop     = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock     = threading.Lock()
        self._offset   = 0
        self._tick     = 0

    def set_bot(self, bot_ref: Any) -> None:
        self._bot_ref = bot_ref

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="telegram-cmd-bot",
                daemon=True,
            )
            self._thread.start()
        logger.info("TelegramCommandBot started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._lock:
            t = self._thread
        if t:
            t.join(timeout=timeout)
        logger.info("TelegramCommandBot stopped")

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    # ── Internal ───────────────────────────────────────────────────────────────

    def _run(self) -> None:
        logger.debug("TelegramCommandBot poll loop starting")
        while not self._stop.is_set():
            tok = _token()
            cid = _chat_id()
            if not tok or not cid:
                # Wait for credentials to be added to .env
                self._stop.wait(30)
                continue
            try:
                self._poll_once()
            except Exception as exc:
                logger.debug("Poll error: %s", exc)
                self._stop.wait(_RETRY_SLEEP)

            # Periodic health ping every 100 ticks
            self._tick += 1
            if self._tick % _HEALTH_EVERY == 0:
                self._send_health_ping()

        logger.debug("TelegramCommandBot poll loop exiting")

    def _poll_once(self) -> None:
        tok = _token()
        url = f"https://api.telegram.org/bot{tok}/getUpdates"
        payload = json.dumps({
            "offset":  self._offset,
            "timeout": _POLL_TIMEOUT,
            "allowed_updates": ["message"],
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=_POLL_TIMEOUT + 5) as r:
                data = json.loads(r.read().decode())
        except urllib.error.URLError as exc:
            logger.debug("getUpdates network error: %s", exc)
            self._stop.wait(_RETRY_SLEEP)
            return

        if not data.get("ok"):
            logger.debug("getUpdates not ok: %s", data)
            return

        for update in data.get("result", []):
            self._offset = update["update_id"] + 1
            self._dispatch(update)

    def _dispatch(self, update: dict) -> None:
        msg = update.get("message", {})
        if not msg:
            return

        chat_id = msg.get("chat", {}).get("id")
        text    = (msg.get("text") or "").strip()

        if not chat_id or not text.startswith("/"):
            return

        # Extract command (strip @botname suffix if present)
        cmd = text.split()[0].split("@")[0].lower()
        handler = _COMMANDS.get(cmd)
        if handler:
            logger.info("TelegramCmd: %s from chat %s", cmd, chat_id)
            try:
                handler(chat_id, text, self._bot_ref)
            except Exception as exc:
                logger.debug("Command %s error: %s", cmd, exc)
                try:
                    _reply(chat_id, f"⚠️ Error handling {cmd}: {exc}")
                except Exception:
                    pass
        else:
            _reply(chat_id,
                   f"Unknown command: {cmd}\nType /help for available commands.")

    def _send_health_ping(self) -> None:
        bot = self._bot_ref
        if bot is None:
            return
        try:
            clock = getattr(bot, "_skill_clock", None)
            if clock:
                st = clock.get_status()
                regimes = st.get("last_regimes", {})
                errors  = len(st.get("last_errors", []))
                from runtime.telegram_alerts import alert_scan_health
                alert_scan_health(self._tick, regimes, errors)
        except Exception:
            pass


# ── Module singleton ──────────────────────────────────────────────────────────

_cmd_bot: Optional[TelegramCommandBot] = None
_cmd_bot_lock = threading.Lock()


def get_command_bot(bot_ref: Any = None) -> TelegramCommandBot:
    """Return the process-level TelegramCommandBot singleton."""
    global _cmd_bot
    if _cmd_bot is None:
        with _cmd_bot_lock:
            if _cmd_bot is None:
                _cmd_bot = TelegramCommandBot(bot_ref=bot_ref)
    elif bot_ref is not None:
        _cmd_bot.set_bot(bot_ref)
    return _cmd_bot
