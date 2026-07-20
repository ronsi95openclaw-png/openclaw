"""Command blocklist for /run and /py shell execution.

Pure-stdlib, dependency-free. Substring-matches a small set of well-known
dangerous patterns (case-insensitive). The goal is not to be exhaustive — it's
to catch obvious foot-guns and prompt-injection attempts before they reach a
shell. Combined with the chat-id whitelist + audit log, it forms defence-in-
depth, not the only defence.

Usage:
    from security.blocklist import is_blocked

    hit = is_blocked(command)
    if hit:
        # reject; log denial via security.audit.log_command(..., outcome='blocked')
        ...
"""
from __future__ import annotations

from typing import List, Optional

# Substrings matched against the lowercased command. Order is preserved so the
# first match is what we return — keep the more specific patterns near the top
# if you ever need to disambiguate.
_BLOCKED_PATTERNS: List[str] = [
    "rm -rf",
    "del /f /s /q",
    "del /q /s /f",
    "rd /s /q",
    "shutdown",
    "reboot",
    "format c:",
    "mkfs",
    "fdisk",
    "dd if=",
    "chmod 777",
    "curl|sh",
    "wget|sh",
    "|sh",
    ":(){:|:&};:",  # classic fork bomb
]

# Whitespace-stripped form of each pattern, matched against a whitespace-
# stripped haystack. Catches the trivial "curl | sh" / "wget  |  sh" bypass
# of the pipe-based patterns above, which a plain substring check misses
# because it only tests the exact spacing written into _BLOCKED_PATTERNS.
_BLOCKED_PATTERNS_NOSPACE: List[str] = [
    "".join(p.split()) for p in _BLOCKED_PATTERNS
]


def is_blocked(command: str) -> Optional[str]:
    """Return the first matching blocklist pattern, or None if the command is clean.

    Matching is a case-insensitive substring check. The returned pattern is the
    canonical lowercase form from ``_BLOCKED_PATTERNS`` (useful for logging /
    user-facing error messages).

    Args:
        command: Raw command string the user wants to run.

    Returns:
        The matched pattern (truthy) on a hit, otherwise ``None``.
    """
    if not command:
        return None
    haystack = command.lower()
    haystack_nospace = "".join(haystack.split())
    for pattern, pattern_nospace in zip(_BLOCKED_PATTERNS, _BLOCKED_PATTERNS_NOSPACE):
        if pattern in haystack or pattern_nospace in haystack_nospace:
            return pattern
    return None
