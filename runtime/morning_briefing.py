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


# ── Module-level singleton ────────────────────────────────────────────────────

_daemon: Optional[MorningBriefingDaemon] = None


def get_morning_briefing(bot: "CryptoComBot") -> MorningBriefingDaemon:
    global _daemon
    if _daemon is None:
        _daemon = MorningBriefingDaemon(bot)
    return _daemon
