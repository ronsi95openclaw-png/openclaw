"""LLM interface using the Ollama Python library for local model inference.

Provides ask_llm(prompt) which calls the local Ollama server via the
`ollama` Python client. Requires Ollama to be running (`ollama serve`).
Model is configured via the OLLAMA_MODEL env var (default: qwen2.5:14b).
"""
from __future__ import annotations

import os
from typing import Optional

from ollama import chat


DEFAULT_MODEL = "qwen2.5:14b"


def ask_llm(prompt: str, model: Optional[str] = None) -> str:
    """Ask the local Ollama model for a textual response.

    Args:
        prompt: The user prompt to send to the model.
        model: Optional model identifier. Falls back to OLLAMA_MODEL env var
            or the module default.

    Returns:
        The model's textual response.

    Raises:
        RuntimeError: If the Ollama server is unreachable or generation fails.
    """
    model = model or os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)

    try:
        response = chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.message.content.strip()
    except Exception as exc:
        raise RuntimeError(f"Ollama generation failed: {exc}") from exc
