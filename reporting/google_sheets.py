"""Google Sheets trade reporter.

Logs every trade, signal, regime change, and daily summary to a shared
Google Sheet so performance can be reviewed and the bot adjusted daily.

Sheet structure (one tab each):
  Trades       — every open and close with full context
  Signals      — every signal generated (including blocked ones)
  Daily Summary— one aggregated row per calendar day
  Regime Log   — regime classification changes per symbol

Setup:
  1. Create a Google Cloud service account and download credentials JSON
  2. Share your Google Sheet with the service account email (Editor)
  3. Add to .env:
       GOOGLE_SHEETS_CREDENTIALS_FILE=/path/to/credentials.json
       GOOGLE_SHEET_ID=<your-sheet-id>

All writes are non-blocking (queued background thread). If Sheets is
unreachable the bot continues — errors are logged, never raised.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("openclaw.reporting.google_sheets")

# ── Column headers ────────────────────────────────────────────────────────────

_TRADES_HEADERS = [
    "Timestamp", "Event", "Symbol", "Strategy", "Side",
    "Entry Price", "Exit Price", "Size", "PnL", "Outcome",
    "Regime", "Confidence %", "Leverage", "Notes",
]

_SIGNALS_HEADERS = [
    "Timestamp", "Symbol", "Strategy", "Signal", "Confidence %",
    "Effective Conf %", "Regime", "Blocked", "Block Reason",
]

_DAILY_HEADERS = [
    "Date", "Balance $", "Day PnL $", "Total PnL $",
    "Trades", "Wins", "Losses", "Win Rate %",
    "Best Strategy", "Best PnL $", "Worst Strategy", "Worst PnL $",
    "Regimes Seen", "Notes",
]

_REGIME_HEADERS = [
    "Timestamp", "Symbol", "Regime", "ADX", "RSI", "ATR Ratio",
    "Confidence",
]


class SheetReporter:
    """Async Google Sheets reporter. All public methods are fire-and-forget.

    Batches rows in a queue and flushes on a background thread to avoid
    blocking the scan loop. Sheet tabs and headers are created automatically
    on first write.
    """

    def __init__(
        self,
        credentials_file: str = None,
        sheet_id: str = None,
    ):
        self._creds_file = (
            credentials_file
            or os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE", "")
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        )
        self._sheet_id = sheet_id or os.getenv("GOOGLE_SHEET_ID", "")
        self._gc = None
        self._sheet = None
        self._worksheets: Dict[str, Any] = {}
        self._connected = False

        self._queue: queue.Queue = queue.Queue(maxsize=500)
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="sheets-reporter"
        )
        self._thread.start()
        logger.info("SheetReporter initialised (background thread started)")

    # ── Public API ────────────────────────────────────────────────────────────

    def log_trade_open(
        self,
        symbol: str,
        strategy: str,
        side: str,
        entry_price: float,
        size: float,
        regime: str,
        confidence: float,
        leverage: int = 1,
    ) -> None:
        self._enqueue("Trades", [
            _now(), "OPEN", symbol, strategy, side.upper(),
            round(entry_price, 6), "", round(size, 6), "", "",
            regime, round(confidence * 100, 1), leverage, "",
        ])

    def log_trade_close(
        self,
        symbol: str,
        strategy: str,
        side: str,
        entry_price: float,
        exit_price: float,
        size: float,
        pnl: float,
        outcome: str,           # "win" | "loss"
        regime: str,
        notes: str = "",
    ) -> None:
        self._enqueue("Trades", [
            _now(), "CLOSE", symbol, strategy, side.upper(),
            round(entry_price, 6), round(exit_price, 6),
            round(size, 6), round(pnl, 4), outcome.upper(),
            regime, "", "", notes,
        ])

    def log_signal(
        self,
        symbol: str,
        strategy: str,
        action: str,
        confidence: float,
        effective_conf: float,
        regime: str,
        blocked: bool,
        block_reason: str = "",
    ) -> None:
        self._enqueue("Signals", [
            _now(), symbol, strategy, action.upper(),
            round(confidence * 100, 1),
            round(effective_conf * 100, 1),
            regime,
            "YES" if blocked else "NO",
            block_reason,
        ])

    def log_regime(
        self,
        symbol: str,
        label: str,
        adx: float = 0.0,
        rsi: float = 0.0,
        atr_ratio: float = 0.0,
        confidence: float = 0.0,
    ) -> None:
        self._enqueue("Regime Log", [
            _now(), symbol, label,
            round(adx, 2), round(rsi, 2),
            round(atr_ratio, 4), round(confidence, 3),
        ])

    def log_daily_summary(
        self,
        date: str,
        balance: float,
        day_pnl: float,
        total_pnl: float,
        trades: int,
        wins: int,
        losses: int,
        strategy_stats: Dict[str, Dict],
        regimes_seen: List[str] = None,
        notes: str = "",
    ) -> None:
        win_rate = round(wins / trades * 100, 1) if trades else 0.0
        # Best / worst strategy by total pnl this day
        best_s = worst_s = best_pnl = worst_pnl = ""
        if strategy_stats:
            by_pnl = sorted(
                strategy_stats.items(),
                key=lambda x: x[1].get("pnl", 0),
                reverse=True,
            )
            best_s, best_data   = by_pnl[0]
            worst_s, worst_data = by_pnl[-1]
            best_pnl  = round(best_data.get("pnl", 0), 4)
            worst_pnl = round(worst_data.get("pnl", 0), 4)

        regimes_str = ", ".join(sorted(set(regimes_seen or []))) or "UNKNOWN"

        self._enqueue("Daily Summary", [
            date,
            round(balance, 2), round(day_pnl, 4), round(total_pnl, 4),
            trades, wins, losses, win_rate,
            best_s, best_pnl, worst_s, worst_pnl,
            regimes_str, notes,
        ])

    def is_connected(self) -> bool:
        return self._connected

    def get_status(self) -> Dict[str, Any]:
        return {
            "connected":    self._connected,
            "queue_depth":  self._queue.qsize(),
            "sheet_id":     self._sheet_id[:12] + "…" if self._sheet_id else "",
            "creds_file":   os.path.basename(self._creds_file) if self._creds_file else "",
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _enqueue(self, tab: str, row: list) -> None:
        try:
            self._queue.put_nowait((tab, row))
        except queue.Full:
            logger.warning("SheetReporter queue full — dropping row for %s", tab)

    def _worker(self) -> None:
        """Background thread: drains queue and writes to Sheets."""
        while True:
            try:
                tab, row = self._queue.get(timeout=5)
                self._write_row(tab, row)
                self._queue.task_done()
            except queue.Empty:
                continue
            except Exception as exc:
                logger.error("SheetReporter worker error: %s", exc)
                time.sleep(10)

    def _connect(self) -> bool:
        if self._connected:
            return True
        if not self._creds_file or not self._sheet_id:
            logger.warning(
                "SheetReporter: GOOGLE_SHEETS_CREDENTIALS_FILE or "
                "GOOGLE_SHEET_ID not set — Sheets logging disabled"
            )
            return False
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            creds      = Credentials.from_service_account_file(self._creds_file, scopes=scopes)
            self._gc   = gspread.authorize(creds)
            self._sheet = self._gc.open_by_key(self._sheet_id)
            self._connected = True
            logger.info("SheetReporter: connected to '%s'", self._sheet.title)
            return True
        except FileNotFoundError:
            logger.error("SheetReporter: credentials file not found: %s", self._creds_file)
        except Exception as exc:
            logger.error("SheetReporter: connection failed: %s", exc)
        return False

    def _get_worksheet(self, tab: str) -> Optional[Any]:
        if tab in self._worksheets:
            return self._worksheets[tab]
        if not self._connect():
            return None
        headers_map = {
            "Trades":        _TRADES_HEADERS,
            "Signals":       _SIGNALS_HEADERS,
            "Daily Summary": _DAILY_HEADERS,
            "Regime Log":    _REGIME_HEADERS,
        }
        try:
            try:
                ws = self._sheet.worksheet(tab)
            except Exception:
                ws = self._sheet.add_worksheet(title=tab, rows=5000, cols=20)
                ws.append_row(headers_map.get(tab, []), value_input_option="USER_ENTERED")
                # Freeze header row and bold it
                ws.format("1:1", {"textFormat": {"bold": True}})
                logger.info("SheetReporter: created tab '%s'", tab)

            self._worksheets[tab] = ws
            return ws
        except Exception as exc:
            logger.error("SheetReporter: could not get/create worksheet '%s': %s", tab, exc)
            return None

    def _write_row(self, tab: str, row: list) -> None:
        ws = self._get_worksheet(tab)
        if ws is None:
            return
        try:
            ws.append_row(row, value_input_option="USER_ENTERED")
        except Exception as exc:
            logger.warning("SheetReporter: append_row failed (%s): %s", tab, exc)
            # Reset connection so it will reconnect on next write
            self._connected = False
            self._worksheets.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ── Singleton ─────────────────────────────────────────────────────────────────

_reporter: Optional[SheetReporter] = None
_reporter_lock = threading.Lock()


def get_reporter() -> SheetReporter:
    """Return (or create) the shared SheetReporter singleton."""
    global _reporter
    with _reporter_lock:
        if _reporter is None:
            _reporter = SheetReporter()
        return _reporter
