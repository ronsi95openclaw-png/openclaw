import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self, log_file: str = "data/audit.log"):
        self._path = Path(log_file)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, agent: str, action: str, details: Dict[str, Any] = None) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "action": action,
            "details": details or {},
        }
        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            logger.error("Audit write failed: %s", exc)
        logger.info("[AUDIT] %s.%s %s", agent, action, details or "")
