"""OpenClaw Obsidian Vault integration.

Writes structured markdown notes to ~/AI-Operating-System-Vault/
as the persistent cognitive substrate of the AI operating system.

All writes are non-blocking and fail-silent — trading logic is never gated
on vault availability. Writers append to JSONL indices for fast retrieval.

Folder mapping:
    05_Trading/     — individual trade journal entries
    06_Strategies/  — strategy weight evolution
    07_Optimization/— daily performance + Opus analysis snapshots
    20_Daily_Notes/ — daily session notes
"""
from __future__ import annotations

from pathlib import Path

VAULT_ROOT = Path.home() / "AI-Operating-System-Vault"


def vault_path(folder: str) -> Path:
    p = VAULT_ROOT / folder
    p.mkdir(parents=True, exist_ok=True)
    return p
