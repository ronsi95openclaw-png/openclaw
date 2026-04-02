"""Logging utilities for OpenClaw.

Provides a simple trade logger that writes decisions to
`data/logs/trades.log`. Ensures directories exist and configures a file
handler. The logger is intentionally lightweight so it can be imported
in any module.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

from core.telegram_bot import AlertType, send_alert_sync


LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "data", "logs")
LOG_DIR = os.path.abspath(LOG_DIR)
LOG_FILE = os.path.join(LOG_DIR, "trades.log")


def _ensure_log_dir() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


def get_logger(name: str = "openclaw") -> logging.Logger:
    """Return a configured logger that writes to the trades log file.

    The handler uses rotation to avoid unbounded file growth.
    """
    _ensure_log_dir()

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Also keep a simple console handler for dev convenience
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    return logger


def log_trade(decision: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Log a trade decision with optional metadata and fire a Telegram alert.

    Args:
        decision: Human-readable summary of the decision.
        metadata: Arbitrary mapping that will be included in the log. Recognised
            keys: ``alert_type``, ``asset``, ``action``, ``reasoning``.
    """
    logger = get_logger()
    timestamp = datetime.utcnow().isoformat() + "Z"
    if metadata:
        logger.info(f"TRADE_DECISION | {timestamp} | {decision} | metadata={metadata}")
    else:
        logger.info(f"TRADE_DECISION | {timestamp} | {decision}")

    meta = metadata or {}
    try:
        alert_type = AlertType(meta.get("alert_type", AlertType.TRADE_SIGNAL))
    except ValueError:
        alert_type = AlertType.TRADE_SIGNAL

    send_alert_sync(
        alert_type,
        asset=meta.get("asset"),
        action=meta.get("action"),
        reasoning=meta.get("reasoning") or decision,
    )
