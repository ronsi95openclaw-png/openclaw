"""Configuration loader for OpenClaw.

Loads environment variables from a `.env` file and exposes a Settings
dataclass with typed fields.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


load_dotenv()  # Read .env into environment if present


@dataclass
class Settings:
    CRYPTOCOM_API_KEY: Optional[str] = None
    CRYPTOCOM_SECRET: Optional[str] = None
    BLOFIN_API_KEY: Optional[str] = None
    BLOFIN_SECRET: Optional[str] = None
    OLLAMA_MODEL: str = "qwen2.5:14b"


def load_settings() -> Settings:
    """Load settings from environment and return a Settings object.

    The `.env` file is optional — missing keys will be `None`.
    """
    return Settings(
        CRYPTOCOM_API_KEY=os.getenv("CRYPTOCOM_API_KEY"),
        CRYPTOCOM_SECRET=os.getenv("CRYPTOCOM_SECRET"),
        BLOFIN_API_KEY=os.getenv("BLOFIN_API_KEY"),
        BLOFIN_SECRET=os.getenv("BLOFIN_SECRET"),
        OLLAMA_MODEL=os.getenv("OLLAMA_MODEL", "qwen2.5:14b"),
    )
