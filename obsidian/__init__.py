"""OpenClaw Obsidian Vault integration.

Writes structured markdown notes to the vault defined by OBSIDIAN_VAULT_PATH.
All writes are non-blocking and fail-silent — trading logic is never gated
on vault availability.

Set OBSIDIAN_VAULT_PATH in .env for local use.
Do NOT set it on Railway — its absence triggers a graceful skip.

Folder mapping:
    05_Trading/     — individual trade journal entries
    06_Strategies/  — strategy weight evolution
    07_Optimization/— daily performance + Opus analysis snapshots
    20_Daily_Notes/ — daily session notes
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("openclaw.obsidian")

# Read from env; defaults to legacy home path only if env is explicitly not set
# and the legacy path actually exists (local dev machine only).
_ENV_PATH   = os.getenv("OBSIDIAN_VAULT_PATH", "")
_LEGACY     = Path.home() / "AI-Operating-System-Vault"
VAULT_ROOT: Optional[Path] = (
    Path(_ENV_PATH) if _ENV_PATH
    else (_LEGACY   if _LEGACY.exists() else None)
)


def vault_path(folder: str) -> Optional[Path]:
    """Return the folder path inside the vault, or None if vault is unavailable.

    Callers must check for None before writing — this module never raises.
    """
    if VAULT_ROOT is None:
        logger.debug(
            "Obsidian: vault unavailable (OBSIDIAN_VAULT_PATH not set and "
            "default path does not exist) — skipping write"
        )
        return None
    if not VAULT_ROOT.exists():
        logger.warning(
            "Obsidian: vault path %s does not exist — skipping write. "
            "Set OBSIDIAN_VAULT_PATH in .env to enable local journal.",
            VAULT_ROOT,
        )
        return None
    p = VAULT_ROOT / folder
    p.mkdir(parents=True, exist_ok=True)
    return p
