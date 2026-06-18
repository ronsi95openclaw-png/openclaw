"""Hermes — 24/7 personal overseer for the OpenClaw multi-bot system.

Hermes watches every project (the ClawBot crypto trader in this repo and the
HaulYeah lead-gen bot in trash_hauling_bot/) by inspecting their on-disk signals
only — it never imports their heavy runtimes. It has its own Telegram bot and
shares the Flask dashboard.

Modules:
    health    — pure functions that read on-disk signals → per-bot health dict
    briefing  — compose a plain-text morning briefing from health output
    overseer  — the oversight loop: gather health, build briefing, send to Telegram
"""

__version__ = "0.1.0"
