"""Daily 8am UTC morning briefing — sends balance/P&L/strategy summary to Telegram."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from trading.cryptocom_bot import CryptoComBot

logger = logging.getLogger("openclaw.runtime.morning_briefing")


class MorningBriefingDaemon:
    """Fires a Telegram briefing at 08:00 UTC every day.

    Injected with a bot reference so it can read live state without
    creating circular imports. Start/stop lifecycle mirrors WeightApplicationDaemon.
    """

    TARGET_HOUR = 8  # UTC

    def __init__(self, bot: "CryptoComBot") -> None:
        self._bot        = bot
        self._stop_event = threading.Event()
        self._lock       = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._last_sent_date: Optional[str] = None  # YYYY-MM-DD, prevents double-fire

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="MorningBriefingDaemon", daemon=True
            )
            self._thread.start()
        logger.info("MorningBriefingDaemon started")

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_s)
        with self._lock:
            self._thread = None
        logger.info("MorningBriefingDaemon stopped")

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def send_now(self) -> None:
        """Send briefing immediately (for testing or manual trigger via /briefing)."""
        try:
            self._send_briefing()
        except Exception as exc:
            logger.warning("MorningBriefingDaemon.send_now failed: %s", exc)

    # ── Daemon loop ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        logger.debug("MorningBriefingDaemon thread started")
        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")

            if now.hour == self.TARGET_HOUR and self._last_sent_date != today:
                try:
                    self._send_briefing()
                    self._last_sent_date = today
                except Exception as exc:
                    logger.warning("Morning briefing send error: %s", exc)

            # Sleep 30s between checks — precise enough for an hourly trigger
            self._stop_event.wait(timeout=30)

        logger.debug("MorningBriefingDaemon thread exiting")

    def _send_briefing(self) -> None:
        from runtime.telegram_alerts import _send

        bot   = self._bot
        state = bot.state
        now   = datetime.now(timezone.utc)
        date  = now.strftime("%Y-%m-%d")
        mode  = "📝 PAPER" if state.demo_mode else "💰 LIVE"

        balance   = round(state.balance, 2)
        total_pnl = round(state.total_pnl, 2)
        pnl_sign  = "+" if total_pnl >= 0 else ""

        # Win rate from closed trades
        trades = state.total_trades
        wins   = state.winning_trades
        losses = state.losing_trades
        wr     = round(wins / trades * 100, 1) if trades else 0.0

        # Capital state
        cap_state = "UNKNOWN"
        try:
            cap_state = bot._capital.get_state().state_name
        except Exception:
            pass

        cap_icons = {"SAFE": "🟢", "DEFENSIVE": "🟡", "CRITICAL": "🔴",
                     "EMERGENCY_HALT": "🚨"}
        cap_icon  = cap_icons.get(cap_state, "⚪")

        # Goal progress
        goal_line = ""
        try:
            gp         = bot._goal_tracker.get_progress()
            next_ms    = gp.get("next_milestone", 0)
            eta_days   = gp.get("eta_days")
            goal_pct   = round(balance / 50_000 * 100, 2)
            eta_str    = f"{eta_days:.0f}d" if eta_days else "—"
            goal_line  = (
                f"\n──────────────────────\n"
                f"🎯 Goal:     ${balance:,.2f} / $50,000 ({goal_pct}%)\n"
                f"📍 Next:     ${next_ms:,.0f}  ETA: {eta_str}"
            )
        except Exception:
            pass

        # Top 3 strategies by weight
        strat_lines = ""
        try:
            weights = bot.weights.summary()
            top3    = sorted(weights.items(), key=lambda x: x[1]["weight"], reverse=True)[:3]
            strat_lines = "\n──────────────────────\n"
            for name, s in top3:
                strat_lines += (
                    f"📊 {name}: {s['weight']:.1f}×  "
                    f"{s['win_rate']:.0f}% WR  ({s['trades']}T)\n"
                )
        except Exception:
            pass

        # Open positions summary
        pos_line = ""
        try:
            n_open = len(state.open_positions)
            if n_open:
                pos_line = f"\n📂 Open:     {n_open} position(s)"
        except Exception:
            pass

        _send(
            f"☀️ <b>MORNING BRIEFING {date}</b>  {mode}\n"
            f"──────────────────────\n"
            f"💰 Balance:  <b>${balance:,.2f}</b>\n"
            f"📈 Total P&L: {pnl_sign}${total_pnl:,.2f}\n"
            f"🎯 Win Rate: {wr}%  ({wins}W / {losses}L / {trades}T)\n"
            f"{cap_icon} Capital:   {cap_state}"
            f"{pos_line}"
            f"{strat_lines}"
            f"{goal_line}"
        )
        logger.info("Morning briefing sent for %s", date)


# ── Midnight daily report ─────────────────────────────────────────────────────

class MidnightReportDaemon:
    """Sends a daily summary at 00:00 UTC — trades/PnL/top strategy."""

    TARGET_HOUR = 0

    def __init__(self, bot: "CryptoComBot") -> None:
        self._bot             = bot
        self._stop_event      = threading.Event()
        self._lock            = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._last_sent_date: Optional[str] = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="MidnightReportDaemon", daemon=True
            )
            self._thread.start()
        logger.info("MidnightReportDaemon started")

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop_event.set()
        with self._lock:
            t = self._thread
        if t:
            t.join(timeout=timeout_s)
        with self._lock:
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            now   = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")
            if now.hour == self.TARGET_HOUR and self._last_sent_date != today:
                try:
                    self._send_report()
                    self._last_sent_date = today
                except Exception as exc:
                    logger.warning("Midnight report error: %s", exc)
            self._stop_event.wait(timeout=30)

    def _send_report(self) -> None:
        from runtime.telegram_alerts import _send
        bot   = self._bot
        state = bot.state
        now   = datetime.now(timezone.utc)
        balance   = round(state.starting_balance + state.total_pnl, 2)
        total_pnl = round(state.total_pnl, 2)
        sign      = "+" if total_pnl >= 0 else ""

        trades = getattr(state, "total_trades", 0)
        wins   = getattr(state, "winning_trades", 0)
        losses = getattr(state, "losing_trades", 0)
        wr     = round(wins / trades * 100, 1) if trades else 0.0

        # today's closed trades
        today_str = now.strftime("%Y-%m-%d")
        log       = list(state.trade_log)
        day_trades = [t for t in log if (t.get("closed_at") or "").startswith(today_str)]
        day_pnl    = sum(t.get("pnl", 0) for t in day_trades)
        day_sign   = "+" if day_pnl >= 0 else ""

        cap_state = "UNKNOWN"
        try:
            cap_state = bot._capital.get_state().state_name
        except Exception:
            pass
        cap_icon = {"SAFE": "🟢", "DEFENSIVE": "🟡",
                    "CRITICAL": "🔴", "EMERGENCY_HALT": "🚨"}.get(cap_state, "⚪")

        goal_line = ""
        try:
            gp      = bot._goal_tracker.get_progress()
            next_ms = gp.get("next_milestone", 0)
            goal_pct = round(balance / 50_000 * 100, 2)
            goal_line = f"\n🎯 Progress: ${balance:,.2f} / $50K ({goal_pct}%)  Next: ${next_ms:,.0f}"
        except Exception:
            pass

        _send(
            f"📊 <b>DAILY REPORT — {today_str}</b>\n"
            f"──────────────────────\n"
            f"💰 Balance:    <b>${balance:,.2f}</b>\n"
            f"📈 Total PnL:  {sign}${total_pnl:,.2f}\n"
            f"📅 Today PnL:  {day_sign}${day_pnl:,.2f}  ({len(day_trades)} trades)\n"
            f"🎯 Win Rate:   {wr}%  ({wins}W / {losses}L)\n"
            f"{cap_icon} Capital:    {cap_state}"
            f"{goal_line}"
        )
        logger.info("Midnight report sent")


# ── 4-hour heartbeat ──────────────────────────────────────────────────────────

class HeartbeatDaemon:
    """Sends a brief health ping every 4 hours."""

    INTERVAL_H = 4

    def __init__(self, bot: "CryptoComBot") -> None:
        self._bot        = bot
        self._stop_event = threading.Event()
        self._lock       = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="HeartbeatDaemon", daemon=True
            )
            self._thread.start()
        logger.info("HeartbeatDaemon started (every %dh)", self.INTERVAL_H)

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop_event.set()
        with self._lock:
            t = self._thread
        if t:
            t.join(timeout=timeout_s)

    def _run(self) -> None:
        # Stagger first ping by interval so it doesn't fire right at startup
        self._stop_event.wait(timeout=self.INTERVAL_H * 3600)
        while not self._stop_event.is_set():
            try:
                self._send_heartbeat()
            except Exception as exc:
                logger.debug("Heartbeat error: %s", exc)
            self._stop_event.wait(timeout=self.INTERVAL_H * 3600)

    def _send_heartbeat(self) -> None:
        from runtime.telegram_alerts import _send
        bot     = self._bot
        state   = bot.state
        balance = round(state.starting_balance + state.total_pnl, 2)
        n_open  = len(getattr(state, "open_positions", []))
        running = "🟢" if bot.is_running() else "🔴"
        mode    = "PAPER" if state.demo_mode else "LIVE"
        cap     = "UNKNOWN"
        try:
            cap = bot._capital.get_state().state_name
        except Exception:
            pass
        cap_icon = {"SAFE": "🟢", "DEFENSIVE": "🟡",
                    "CRITICAL": "🔴", "EMERGENCY_HALT": "🚨"}.get(cap, "⚪")
        now_str  = datetime.now(timezone.utc).strftime("%H:%M UTC")
        _send(
            f"{running} <b>Heartbeat</b> {now_str}  [{mode}]\n"
            f"Balance: ${balance:,.2f}  |  {cap_icon} {cap}  |  {n_open} open"
        )
        logger.debug("Heartbeat sent at %s", now_str)


# ── Module-level singletons ───────────────────────────────────────────────────

_daemon:     Optional[MorningBriefingDaemon] = None
_midnight:   Optional[MidnightReportDaemon]  = None
_heartbeat:  Optional[HeartbeatDaemon]       = None


def get_morning_briefing(bot: "CryptoComBot") -> MorningBriefingDaemon:
    global _daemon
    if _daemon is None:
        _daemon = MorningBriefingDaemon(bot)
    return _daemon


def get_midnight_report(bot: "CryptoComBot") -> MidnightReportDaemon:
    global _midnight
    if _midnight is None:
        _midnight = MidnightReportDaemon(bot)
    return _midnight


def get_heartbeat(bot: "CryptoComBot") -> HeartbeatDaemon:
    global _heartbeat
    if _heartbeat is None:
        _heartbeat = HeartbeatDaemon(bot)
    return _heartbeat
