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

    # In Railway/webhook mode (TELEGRAM_OUTBOX_MODE=supabase), outbound calls to
    # api.telegram.org are blocked by Telegram's IP allowlist.  Route sendMessage
    # through the Supabase outbox so the local relay daemon can send it instead.
    if method == "sendMessage" and os.getenv("TELEGRAM_OUTBOX_MODE") == "supabase":
        try:
            from infra.supabase_client import get_client
            sb = get_client()
            if sb:
                sb.table("telegram_outbox").insert({
                    "chat_id":    str(params.get("chat_id", "")),
                    "text":       str(params.get("text", "")),
                    "parse_mode": str(params.get("parse_mode", "HTML")),
                }).execute()
                return {"ok": True, "via": "outbox"}
        except Exception as exc:
            logger.warning("Telegram outbox write failed: %s", exc)
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
        "/briefing   — send morning briefing now\n"
        "/dca_status — DCA portfolio cost basis\n"
        "/livecheck  — pre-flight check for live mode\n"
        "/golive     — activate live mode (requires passphrase)\n"
        "/restart    — restart the scan loop\n"
        "/pause      — pause trade execution\n"
        "/resume     — resume trade execution\n"
        "/halt       — capital halt status &amp; release info\n"
        "/help       — this message\n"
        "──────────────────────\n"
        "Morning briefing at 08:00 UTC ☀️\n"
        "Daily report at midnight UTC 📊"
    )


def _cmd_status(chat_id, _text, bot_ref) -> None:
    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot reference not available yet.")
        return
    try:
        s = bot_ref.get_status()
        running   = "🟢 Running" if s["running"] else "🔴 Stopped"
        paused    = " ⏸ PAUSED" if s.get("execution_paused") else ""
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
            f"State:      {running}{paused}\n"
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
        today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        s       = bot_ref.get_status()
        log     = s.get("trade_log", [])
        open_p  = s.get("open_positions", [])
        balance = s.get("balance", 0.0)

        # Closed = has pnl and outcome, regardless of field name
        closed  = [t for t in log if t.get("outcome") and t.get("pnl") is not None]
        wins    = [t for t in closed if t.get("outcome") == "win"]
        losses  = [t for t in closed if t.get("outcome") == "loss"]
        day_pnl = sum(t.get("pnl", 0) for t in closed)
        wr      = (len(wins) / len(closed) * 100) if closed else 0.0

        lines = [f"📊 <b>TODAY'S TRADES — {today}</b>", "──", ""]

        idx = 1
        for t in closed:
            icon  = "✅" if t.get("outcome") == "win" else "❌"
            strat = t.get("strategy", "").replace("_", "")
            sym   = t.get("symbol", "").replace("_USDT", "")
            side  = (t.get("side") or t.get("action") or "").upper()
            entry = t.get("entry_price", 0)
            pnl   = t.get("pnl", 0)
            sign  = "+" if pnl >= 0 else ""
            lines.append(f"{idx}. {icon} <i>{strat} {sym} {side} @ ${entry:,.2f}</i>\n→ ${sign}{pnl:,.2f}")
            idx  += 1

        for p in open_p:
            strat = p.get("strategy", "").replace("_", "")
            sym   = p.get("symbol", "").replace("_USDT", "")
            side  = (p.get("side") or "").upper()
            entry = p.get("entry_price", 0)
            unr   = p.get("unrealized_pnl", 0)
            sign  = "+" if unr >= 0 else ""
            lines.append(f"{idx}. 🔵 <i>{strat} {sym} {side} @ ${entry:,.2f}</i>\n→ {sign}${unr:,.2f} (open)")
            idx  += 1

        if idx == 1:
            lines.append("—\nNo trades yet today")

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


def _cmd_pause(chat_id, _text, bot_ref) -> None:
    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot reference not available.")
        return
    try:
        if getattr(bot_ref.state, "execution_paused", False):
            _reply(chat_id, "⏸ Trade execution is already paused.\nSend /resume to re-enable.")
            return
        bot_ref.state.execution_paused = True
        mode = "📝 PAPER" if bot_ref.state.demo_mode else "💰 LIVE"
        _reply(chat_id,
            f"⏸ <b>Trade execution paused</b>\n"
            f"Mode: {mode}\n"
            f"The scan loop continues but no new positions will be opened.\n"
            f"Send /resume to re-enable."
        )
    except Exception as exc:
        _reply(chat_id, f"⚠️ Pause error: {exc}")


def _cmd_resume(chat_id, _text, bot_ref) -> None:
    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot reference not available.")
        return
    try:
        if not getattr(bot_ref.state, "execution_paused", False):
            _reply(chat_id, "▶️ Trade execution is already active.")
            return
        bot_ref.state.execution_paused = False
        _reply(chat_id, "▶️ <b>Trade execution resumed</b>\nNew positions can be opened on the next scan.")
    except Exception as exc:
        _reply(chat_id, f"⚠️ Resume error: {exc}")


def _cmd_halt(chat_id, _text, bot_ref) -> None:
    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot reference not available.")
        return
    try:
        import os
        from pathlib import Path
        s           = bot_ref.get_status()
        cap_state   = s.get("capital_state", "UNKNOWN")
        cap_icon    = {"SAFE": "🟢", "DEFENSIVE": "🟡",
                       "CRITICAL": "🔴", "EMERGENCY_HALT": "🚨"}.get(cap_state, "⚪")

        halt_marker = Path("data/BALANCE_HALT_MARKER")
        halt_file   = Path("data/HALT_MARKER")
        active_halts = []
        if halt_marker.exists():
            active_halts.append("BALANCE_HALT_MARKER")
        if halt_file.exists():
            active_halts.append("HALT_MARKER")

        if active_halts:
            halt_str = "\n".join(f"  🚨 {h}" for h in active_halts)
            _reply(chat_id,
                f"🚨 <b>EMERGENCY HALT ACTIVE</b>\n"
                f"──────────────────────\n"
                f"Capital state: {cap_icon} {cap_state}\n"
                f"Active markers:\n{halt_str}\n"
                f"──────────────────────\n"
                f"To release:\n"
                f"  POST /admin/halt/release on the dashboard API\n"
                f"  or delete the marker file(s) and /restart"
            )
        else:
            _reply(chat_id,
                f"✅ <b>No Halt Active</b>\n"
                f"──────────────────────\n"
                f"Capital state: {cap_icon} {cap_state}\n"
                f"No halt markers present.\n"
                f"System is operating normally."
            )
    except Exception as exc:
        _reply(chat_id, f"⚠️ Halt status error: {exc}")


def _cmd_restart(chat_id, _text, bot_ref) -> None:
    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot reference not available.")
        return
    try:
        _reply(chat_id, "🔄 Restarting scan loop...")
        bot_ref.stop()
        time.sleep(2)
        bot_ref.start()
        _reply(chat_id, "✅ Bot restarted — scan loop is live again.")
    except Exception as exc:
        _reply(chat_id, f"⚠️ Restart failed: {exc}")


def _cmd_livecheck(chat_id, _text, bot_ref) -> None:
    try:
        from runtime.live_mode_gate import format_eligibility_report
        _reply(chat_id, format_eligibility_report())
    except Exception as exc:
        _reply(chat_id, f"⚠️ livecheck error: {exc}")


def _cmd_golive(chat_id, text, bot_ref) -> None:
    from settings import LIVE_ACTIVATION_PASSPHRASE
    parts      = text.strip().split(maxsplit=1)
    passphrase = parts[1].strip() if len(parts) > 1 else ""

    if passphrase != LIVE_ACTIVATION_PASSPHRASE:
        _reply(chat_id,
               "🔐 Wrong passphrase.\n"
               "Run /livecheck to see requirements, then:\n"
               f"/golive {LIVE_ACTIVATION_PASSPHRASE}")
        return

    try:
        from runtime.live_mode_gate import check_live_mode_eligibility
        eligible, failures = check_live_mode_eligibility()
    except Exception as exc:
        _reply(chat_id, f"⚠️ eligibility check failed: {exc}")
        return

    if not eligible:
        _reply(chat_id,
               "🚫 <b>LIVE MODE BLOCKED</b> — requirements not met:\n"
               + "\n".join(failures))
        return

    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot not running — cannot switch mode.")
        return

    bot_ref.state.demo_mode = False
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    _reply(chat_id,
           f"🚨 <b>LIVE MODE ACTIVATED</b> at {ts}\n\n"
           "⚠️ Real money is now at risk.\n"
           "Edit .env and set DEMO_MODE=false to persist after restart.\n"
           "Send /status to confirm.")


def _cmd_dca_status(chat_id, _text, bot_ref) -> None:
    import json
    from pathlib import Path
    dca_file = Path(__file__).parent.parent / "data" / "dca_state.json"
    if not dca_file.exists():
        _reply(chat_id, "📊 DCA Portfolio\nNo DCA trades recorded yet.")
        return
    try:
        state = json.loads(dca_file.read_text())
    except Exception as exc:
        _reply(chat_id, f"⚠️ DCA state read error: {exc}")
        return

    if not state:
        _reply(chat_id, "📊 DCA Portfolio\nNo DCA trades recorded yet.")
        return

    lines = ["📊 <b>DCA Portfolio Status</b>\n──────────────────────"]
    for symbol, d in state.items():
        sym_short = symbol.replace("_USDT", "")
        units     = d.get("total_units", 0)
        avg_cost  = d.get("avg_cost", 0)
        count     = d.get("count", 0)
        total_usd = d.get("total_bought_usd", 0)
        lines.append(
            f"<b>{sym_short}</b>: {units:.6f} units\n"
            f"  Avg cost:  ${avg_cost:,.4f}\n"
            f"  Invested:  ${total_usd:,.2f}\n"
            f"  DCA count: {count}"
        )
    _reply(chat_id, "\n".join(lines))


def _cmd_briefing(chat_id, _text, bot_ref) -> None:
    if bot_ref is None:
        _reply(chat_id, "⚠️ Bot not running.")
        return
    try:
        from runtime.morning_briefing import get_morning_briefing
        daemon = get_morning_briefing(bot_ref)
        daemon.send_now()
        _reply(chat_id, "☀️ Morning briefing sent!")
    except Exception as exc:
        _reply(chat_id, f"⚠️ Briefing error: {exc}")


_COMMANDS = {
    "/help":       _cmd_help,
    "/start":      _cmd_help,
    "/status":     _cmd_status,
    "/trades":     _cmd_trades,
    "/goal":       _cmd_goal,
    "/balance":    _cmd_balance,
    "/weights":    _cmd_weights,
    "/briefing":   _cmd_briefing,
    "/dca_status": _cmd_dca_status,
    "/livecheck":  _cmd_livecheck,
    "/golive":     _cmd_golive,
    "/pause":      _cmd_pause,
    "/resume":     _cmd_resume,
    "/halt":       _cmd_halt,
    "/restart":    _cmd_restart,
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
        # In webhook mode (RAILWAY_PUBLIC_URL set), Telegram pushes updates to the
        # FastAPI /telegram/webhook endpoint.  Starting getUpdates polling while a
        # webhook is active causes 409 Conflict errors — skip polling entirely.
        if os.getenv("RAILWAY_PUBLIC_URL", ""):
            logger.info("TelegramCommandBot: webhook mode — polling disabled (RAILWAY_PUBLIC_URL is set)")
            return

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
        logger.info("TelegramCommandBot: long-poll mode started")

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

    def _skip_stale_updates(self) -> None:
        """Advance offset past any queued messages so we only respond to new ones."""
        tok = _token()
        if not tok:
            return
        url = f"https://api.telegram.org/bot{tok}/getUpdates"
        payload = json.dumps({"offset": -1, "timeout": 0}).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
            for upd in data.get("result", []):
                self._offset = upd["update_id"] + 1
            if self._offset:
                logger.info("TelegramCommandBot: skipped stale messages, offset=%d", self._offset)
        except Exception as exc:
            logger.debug("Skip stale updates error: %s", exc)

    def _run(self) -> None:
        logger.debug("TelegramCommandBot poll loop starting")
        self._skip_stale_updates()
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
