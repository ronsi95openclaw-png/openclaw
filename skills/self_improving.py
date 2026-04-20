from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).parent
SELF_IMPROVING_ROOT = Path.home() / "self-improving"

TEMPLATE_FILES = {
    "memory.md": ROOT / "memory-template.md",
    "corrections.md": ROOT / "corrections.md",
    "heartbeat-state.md": ROOT / "heartbeat-state.md",
}

DEFAULT_INDEX = "# Self-Improving Index\n\nUse this index to organize notes, projects, and domain memos.\n"


def _ensure_root() -> None:
    SELF_IMPROVING_ROOT.mkdir(parents=True, exist_ok=True)
    for folder in ["projects", "domains", "archive"]:
        (SELF_IMPROVING_ROOT / folder).mkdir(parents=True, exist_ok=True)

    for filename, source in TEMPLATE_FILES.items():
        target = SELF_IMPROVING_ROOT / filename
        if not target.exists() and source.exists():
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    index_file = SELF_IMPROVING_ROOT / "index.md"
    if not index_file.exists():
        index_file.write_text(DEFAULT_INDEX, encoding="utf-8")


def initialize_self_improving() -> Dict[str, Any]:
    """Initialize the self-improving workspace in the user home directory."""
    _ensure_root()
    summary = {
        "path": str(SELF_IMPROVING_ROOT),
        "files": [],
        "folders": [],
    }
    for filename in ["memory.md", "corrections.md", "index.md", "heartbeat-state.md"]:
        summary["files"].append(filename)
    for folder in ["projects", "domains", "archive"]:
        summary["folders"].append(folder)
    return summary


def _read_file(name: str) -> str:
    _ensure_root()
    path = SELF_IMPROVING_ROOT / name
    if not path.exists():
        raise FileNotFoundError(f"{name} not found")
    return path.read_text(encoding="utf-8")


def _append_line(filename: str, text: str) -> None:
    _ensure_root()
    path = SELF_IMPROVING_ROOT / filename
    timestamp = datetime.now(timezone.utc).isoformat()
    entry = f"- [{timestamp}] {text.strip()}\n"
    path.write_text(path.read_text(encoding="utf-8") + entry, encoding="utf-8")


def append_correction(text: str, context: Optional[str] = None) -> None:
    """Append a correction entry to corrections.md."""
    if context:
        text = f"{text.strip()} (context: {context.strip()})"
    _append_line("corrections.md", text)


def append_memory(text: str) -> None:
    """Append a memory entry to memory.md."""
    _append_line("memory.md", text)


def get_status() -> Dict[str, Any]:
    _ensure_root()
    files = list(SELF_IMPROVING_ROOT.glob("*.md"))
    has_heartbeat = (SELF_IMPROVING_ROOT / "heartbeat-state.md").exists()
    counts = {
        "memory_lines": _count_lines("memory.md"),
        "corrections_lines": _count_lines("corrections.md"),
        "projects": len(list((SELF_IMPROVING_ROOT / "projects").glob("*.md"))),
        "domains": len(list((SELF_IMPROVING_ROOT / "domains").glob("*.md"))),
        "archive": len(list((SELF_IMPROVING_ROOT / "archive").glob("*.md"))),
        "heartbeat_exists": has_heartbeat,
        "path": str(SELF_IMPROVING_ROOT),
        "files": [p.name for p in files],
    }
    return counts


def _count_lines(name: str) -> int:
    path = SELF_IMPROVING_ROOT / name
    if not path.exists():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def get_file_preview(name: str, lines: int = 20) -> str:
    _ensure_root()
    path = SELF_IMPROVING_ROOT / name
    if not path.exists():
        raise FileNotFoundError(f"{name} not found")
    content = path.read_text(encoding="utf-8").splitlines()
    preview = content[:lines]
    if len(content) > lines:
        preview.append("... (truncated)")
    return "\n".join(preview)


def resolve_file_name(arg: str) -> str:
    mapping = {
        "memory": "memory.md",
        "corrections": "corrections.md",
        "heartbeat": "heartbeat-state.md",
        "index": "index.md",
    }
    return mapping.get(arg.lower(), arg)
