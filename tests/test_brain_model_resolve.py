"""Tests for Ollama model auto-detection in core/brain.py."""
from __future__ import annotations
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root on path
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fresh_brain():
    """Reload brain module with clean state."""
    sys.modules.pop("core.brain", None)
    import core.brain as b
    # Reset the module-level cache
    b._resolved_model = None
    b._resolved_model_ts = 0.0
    return b


def test_uses_configured_model_when_installed():
    """If OLLAMA_MODEL env is set and that model is installed, use it."""
    brain = _fresh_brain()
    mock_model = MagicMock()
    mock_model.model = "qwen2.5:14b"

    with patch.dict("os.environ", {"OLLAMA_MODEL": "qwen2.5:14b"}):
        with patch("ollama.list") as mock_list:
            mock_list.return_value = MagicMock(models=[mock_model])
            resolved = brain._resolve_ollama_model()

    assert resolved == "qwen2.5:14b"


def test_falls_back_to_first_available_when_configured_missing():
    """If OLLAMA_MODEL is not installed, use the first installed model."""
    brain = _fresh_brain()
    mock_model = MagicMock()
    mock_model.model = "mistral:latest"

    with patch.dict("os.environ", {"OLLAMA_MODEL": "qwen2.5:14b"}):
        with patch("ollama.list") as mock_list:
            mock_list.return_value = MagicMock(models=[mock_model])
            resolved = brain._resolve_ollama_model()

    assert resolved == "mistral:latest"


def test_raises_offline_error_when_ollama_unreachable():
    """If ollama.list() raises, _resolve_ollama_model raises OllamaOfflineError."""
    brain = _fresh_brain()

    with patch("ollama.list", side_effect=Exception("connection refused")):
        try:
            brain._resolve_ollama_model()
            assert False, "Expected OllamaOfflineError"
        except brain.OllamaOfflineError:
            pass


def test_cache_avoids_repeated_ollama_list_calls():
    """Model is resolved once; second call within TTL skips ollama.list()."""
    brain = _fresh_brain()
    mock_model = MagicMock()
    mock_model.model = "mistral:latest"

    with patch("ollama.list") as mock_list:
        mock_list.return_value = MagicMock(models=[mock_model])
        brain._resolve_ollama_model()
        brain._resolve_ollama_model()

    assert mock_list.call_count == 1  # cached on second call


def test_cache_expires_after_ttl():
    """Cache expires after MODEL_CACHE_TTL seconds."""
    brain = _fresh_brain()
    mock_model = MagicMock()
    mock_model.model = "mistral:latest"

    # Simulate two calls 120 seconds apart (beyond 60s TTL)
    with patch("core.brain.time") as mock_time:
        mock_time.time.side_effect = [1000.0, 1120.0]
        with patch("ollama.list") as mock_list:
            mock_list.return_value = MagicMock(models=[mock_model])
            brain._resolve_ollama_model()
            brain._resolve_ollama_model()

    assert mock_list.call_count == 2  # cache expired, re-fetched
