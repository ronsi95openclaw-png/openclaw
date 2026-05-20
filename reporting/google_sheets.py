"""Google Sheets trade reporter — wired to the live cryptobot sheet.

Sheet ID: 1hH7HTk-klm1828Jzalob5yrdsXXWGajGb0cPCofYkUs
Tabs (match existing structure exactly):
  Signals  — every 30s scan result: pair, price, RSI, signal, action
  Trades   — every executed trade: entry, PnL, balance, indicators
  Daily    — EOD summary per day

Auth: GOOGLE_CREDS_JSON env var (full service account JSON string)
      Falls back to GOOGLE_SHEETS_CREDENTIALS_FILE (path) if JSON not set.

All writes are non-blocking (background thread queue). If Sheets is
unreachable, the bot continues — errors are logged, never raised.
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

# ── Sheet ID (fixed — already shared with service account) ────────────────────
_DEFAULT_SHEET_ID = "1hH7HTk-klm1828Jzalob5yrdsXXWGajGb0cPCofYkUs"

# ── Column headers — must match existing tab structure exactly ────────────────

_SIGNALS_HEADERS = [
    "Timestamp", "Pair", "Price", "RSI", "Signal Found", "Action",
    "Strategy", "Confidence %", "Regime", "Blocked", "Block Reason",
]

_TRADES_HEADERS = [
    "Timestamp", "Date", "Time", "Pair", "Strategy", "Side",
    "Entry Price", "Exit Price", "PnL", "Won/Loss", "Balance After",
    "RSI", "Regime", "Confidence %", "Mode", "Notes",
]

_DAILY_HEADERS = [
    "Date", "Balance $", "Day PnL $", "Total Return %",
    "Trades", "Wins", "Losses", "Win Rate %",
    "Best Strategy", "Worst Strategy", "Regimes Seen", "Notes",
]

_REGIME_HEADERS = [
    "Timestamp", "Pair", "Regime", "ADX", "RSI", "ATR Ratio",
]


class SheetReporter:
    """Async Google Sheets reporter.

    All public methods are fire-and-forget — they enqueue a row and return
    immediately. A daemon thread drains the queue and writes to Sheets.
    Connection is lazy (first write triggers auth).
    """

    def __init__(
        self,
        creds_json: str = None,     # raw JSON string (from GOOGLE_CREDS_JSON)
        creds_file: str = None,     # fallback: path to JSON file
        sheet_id:   str = None,
    ):
        # Credentials: prefer raw JSON env var, fall back to file path
        self._creds_json = (
            creds_json
            or os.getenv("GOOGLE_CREDS_JSON", "")
        )
        self._creds_file = (
            creds_file
            or os.getenv("GOOGLE_SHEETS_CREDENTIALS_FILE", "")
            or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
        )
        self._sheet_id = (
            sheet_id
            or os.getenv("GOOGLE_SHEET_ID", _DEFAULT_SHEET_ID)
        )

        self._gc           = None
        self._sheet        = None
        self._worksheets:  Dict[str, Any] = {}
        self._connected    = False
        self._connect_lock = threading.Lock()

        self._queue:  queue.Queue = queue.Queue(maxsize=1000)
        self._thread  = threading.Thread(
            target=self._worker, daemon=True, name="sheets-reporter"
        )
        self._thread.start()
        logger.info("SheetReporter initialised (sheet_id=%s…)", self._sheet_id[:12])

    # ── Public API ────────────────────────────────────────────────────────────

    def log_signal(
        self,
        symbol:        str,
        price:         float,
        rsi:           float,
        signal_found:  str,           # e.g. "RSI_OVERSOLD", "BB_LOWER_TOUCH"
        action:        str,           # "BUY" | "SELL" | "HOLD"
        strategy:      str   = "",
        confidence:    float = 0.0,
        effective_conf: float = 0.0,
        regime:        str   = "UNKNOWN",
        blocked:       bool  = False,
        block_reason:  str   = "",
    ) -> None:
        """Log every 30s scan result — fires whether or not a trade is taken."""
        self._enqueue("Signals", [
            _now(),
            symbol,
            round(price, 4),
            round(rsi, 2),
            signal_found,
            action.upper(),
            strategy,
            round(effective_conf * 100, 1) if effective_conf else round(confidence * 100, 1),
            regime,
            "YES" if blocked else "",
            block_reason,
        ])

    def log_trade_open(
        self,
        symbol:     str,
        strategy:   str,
        side:       str,
        entry_price: float,
        size:       float,
        balance:    float,
        regime:     str   = "UNKNOWN",
        confidence: float = 0.0,
        rsi:        float = 0.0,
        mode:       str   = "DEMO",
        notes:      str   = "",
    ) -> None:
        now = datetime.now(timezone.utc)
        self._enqueue("Trades", [
            now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            symbol, strategy, side.upper(),
            round(entry_price, 6), "",        # exit_price filled on close
            "",                               # PnL filled on close
            "",                               # Won/Loss filled on close
            round(balance, 2),
            round(rsi, 2),
            regime,
            round(confidence * 100, 1),
            mode.upper(),
            f"OPEN size={round(size,6)}  {notes}",
        ])

    def log_trade_close(
        self,
        symbol:      str,
        strategy:    str,
        side:        str,
        entry_price: float,
        exit_price:  float,
        size:        float,
        pnl:         float,
        outcome:     str,            # "win" | "loss"
        balance:     float,
        regime:      str   = "UNKNOWN",
        confidence:  float = 0.0,
        rsi:         float = 0.0,
        mode:        str   = "DEMO",
        notes:       str   = "",
    ) -> None:
        now = datetime.now(timezone.utc)
        self._enqueue("Trades", [
            now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            symbol, strategy, side.upper(),
            round(entry_price, 6),
            round(exit_price, 6),
            round(pnl, 4),
            outcome.upper(),
            round(balance, 2),
            round(rsi, 2),
            regime,
            round(confidence * 100, 1),
            mode.upper(),
            notes,
        ])

    def log_regime(
        self,
        symbol:    str,
        label:     str,
        adx:       float = 0.0,
        rsi:       float = 0.0,
        atr_ratio: float = 0.0,
    ) -> None:
        self._enqueue("Regime", [
            _now(), symbol, label,
            round(adx, 2), round(rsi, 2), round(atr_ratio, 4),
        ])

    def log_daily_summary(
        self,
        date:           str,
        balance:        float,
        day_pnl:        float,
        start_balance:  float,
        trades:         int,
        wins:           int,
        losses:         int,
        strategy_stats: Dict[str, Dict],
        regimes_seen:   List[str] = None,
        notes:          str = "",
    ) -> None:
        win_rate   = round(wins / trades * 100, 1) if trades else 0.0
        total_return = round((balance - start_balance) / start_balance * 100, 2) \
                       if start_balance else 0.0

        best_s = worst_s = ""
        if strategy_stats:
            by_pnl  = sorted(strategy_stats.items(),
                             key=lambda x: x[1].get("pnl", 0), reverse=True)
            best_s  = by_pnl[0][0]
            worst_s = by_pnl[-1][0]

        regimes_str = ", ".join(sorted(set(regimes_seen or []))) or ""

        self._enqueue("Daily", [
            date,
            round(balance, 2),
            round(day_pnl, 4),
            total_return,
            trades, wins, losses, win_rate,
            best_s, worst_s,
            regimes_str, notes,
        ])

    def is_connected(self) -> bool:
        return self._connected

    def get_status(self) -> Dict[str, Any]:
        return {
            "connected":   self._connected,
            "queue_depth": self._queue.qsize(),
            "sheet_id":    self._sheet_id[:20] + "…",
            "auth":        "GOOGLE_CREDS_JSON" if self._creds_json else
                           (self._creds_file or "none"),
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _enqueue(self, tab: str, row: list) -> None:
        try:
            self._queue.put_nowait((tab, row))
        except queue.Full:
            logger.warning("SheetReporter: queue full, dropping row for tab '%s'", tab)

    def _worker(self) -> None:
        while True:
            try:
                tab, row = self._queue.get(timeout=5)
                self._write_row(tab, row)
                self._queue.task_done()
            except queue.Empty:
                continue
            except Exception as exc:
                logger.error("SheetReporter worker error: %s", exc)
                time.sleep(15)

    def _connect(self) -> bool:
        with self._connect_lock:
            if self._connected:
                return True

            if not self._sheet_id:
                logger.error("SheetReporter: GOOGLE_SHEET_ID not set")
                return False

            # No credentials at all
            if not self._creds_json and not self._creds_file:
                logger.warning(
                    "SheetReporter: no credentials found — set GOOGLE_CREDS_JSON "
                    "or GOOGLE_SHEETS_CREDENTIALS_FILE"
                )
                return False

            try:
                import gspread
                from google.oauth2.service_account import Credentials

                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]

                if self._creds_json:
                    # Primary: raw JSON string from env var
                    info   = json.loads(self._creds_json)
                    creds  = Credentials.from_service_account_info(info, scopes=scopes)
                    logger.info("SheetReporter: auth via GOOGLE_CREDS_JSON")
                else:
                    # Fallback: path to JSON file
                    creds  = Credentials.from_service_account_file(
                        self._creds_file, scopes=scopes
                    )
                    logger.info("SheetReporter: auth via file %s",
                                os.path.basename(self._creds_file))

                self._gc    = gspread.authorize(creds)
                self._sheet = self._gc.open_by_key(self._sheet_id)
                self._connected = True
                logger.info("SheetReporter: connected to '%s'", self._sheet.title)
                return True

            except json.JSONDecodeError as e:
                logger.error("SheetReporter: GOOGLE_CREDS_JSON is not valid JSON: %s", e)
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
            "Signals": _SIGNALS_HEADERS,
            "Trades":  _TRADES_HEADERS,
            "Daily":   _DAILY_HEADERS,
            "Regime":  _REGIME_HEADERS,
        }
        try:
            try:
                ws = self._sheet.worksheet(tab)
            except Exception:
                # Tab doesn't exist yet — create it with headers
                ws = self._sheet.add_worksheet(title=tab, rows=10000, cols=20)
                ws.append_row(headers_map.get(tab, []), value_input_option="USER_ENTERED")
                ws.format("1:1", {"textFormat": {"bold": True}})
                logger.info("SheetReporter: created tab '%s'", tab)

            self._worksheets[tab] = ws
            return ws
        except Exception as exc:
            logger.error("SheetReporter: get/create '%s' failed: %s", tab, exc)
            return None

    def _write_row(self, tab: str, row: list) -> None:
        ws = self._get_worksheet(tab)
        if ws is None:
            return
        try:
            ws.append_row(row, value_input_option="USER_ENTERED")
        except Exception as exc:
            logger.warning("SheetReporter: append to '%s' failed: %s — reconnecting", tab, exc)
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
