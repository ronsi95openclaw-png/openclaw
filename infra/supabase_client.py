"""Supabase client singleton for OpenClaw cloud state persistence.

Uses SUPABASE_URL + SUPABASE_ANON_KEY env vars.
Falls back to None when not configured (file-based state used instead).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("openclaw.infra.supabase")

_client = None
_init_attempted = False

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://gotdcwcdcampwysydbzg.supabase.co")
SUPABASE_KEY = os.getenv(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdvdGRjd2NkY2FtcHd5c3lkYnpnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk3MzYxNzQsImV4cCI6MjA5NTMxMjE3NH0.ZLv8C6P83Ok08BuKpYkEvJs4LpP_6Sq7y3zc7errBG4",
)


def get_client():
    """Return the Supabase client, initialising it on first call.

    Returns None if the supabase package is not installed or credentials
    are missing, so callers can fall back to file-based storage.
    """
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.info("Supabase not configured — using file-based state")
        return None

    try:
        from supabase import create_client
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Supabase client initialised → %s", SUPABASE_URL)
    except ImportError:
        logger.warning("supabase package not installed — using file-based state")
    except Exception as exc:
        logger.warning("Supabase init failed (%s) — using file-based state", exc)

    return _client
