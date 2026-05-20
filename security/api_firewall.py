"""API request firewall — validates and sanitizes all external API inputs."""
from __future__ import annotations

import re
from typing import Any, Optional, Tuple


class APIFirewall:
    """Validates Telegram and exchange API inputs against injection attacks."""

    # Allowlisted Telegram command patterns
    ALLOWED_COMMANDS = re.compile(
        r"^/(start|stop|status|balance|positions|help|"
        r"expectancy|sharpe|drawdown|regime|portfolioheat|"
        r"montecarlo|executionquality|liquidity|slippage|"
        r"walkforward|optimizer)(\s.*)?$"
    )

    # Prompt injection patterns to detect and block
    _INJECTION_PATTERNS = [
        re.compile(p, re.IGNORECASE)
        for p in [
            r"ignore previous instructions",
            r"you are now",
            r"system prompt",
            r"act as",
            r"jailbreak",
            r"<[^>]{0,50}>",              # HTML tags
            r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",  # control chars
        ]
    ]

    MAX_INPUT_LENGTH = 2000

    def validate_telegram_command(self, text: str) -> Tuple[bool, str]:
        """Returns (is_valid, sanitized_text_or_reason)."""
        if not text or len(text) > self.MAX_INPUT_LENGTH:
            return False, "Input too long or empty"
        for pattern in self._INJECTION_PATTERNS:
            if pattern.search(text):
                return False, "Blocked: potential injection attempt"
        if not self.ALLOWED_COMMANDS.match(text.strip()):
            return False, "Unknown command"
        return True, text.strip()

    def sanitize_symbol(self, symbol: str) -> Optional[str]:
        """Validate trading symbol format. Returns None if invalid."""
        if re.match(r"^[A-Z]{2,10}-[A-Z]{2,10}$", symbol):
            return symbol
        return None

    def sanitize_numeric(
        self,
        value: Any,
        min_val: float = 0,
        max_val: float = 1e9,
    ) -> Optional[float]:
        """Parse and bounds-check a numeric value."""
        try:
            v = float(value)
            if min_val <= v <= max_val:
                return v
        except (TypeError, ValueError):
            pass
        return None
