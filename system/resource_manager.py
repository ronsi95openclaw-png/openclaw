"""Resource Manager — monitors CPU/RAM and enforces operational limits."""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("openclaw.system.resource_manager")

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    logger.warning("psutil not available — resource metrics will return stubs")


class ResourceManager:
    """Monitors system resources and enforces operational limits."""

    def __init__(
        self,
        max_cpu_pct: float = 85.0,
        max_ram_pct: float = 80.0,
        max_ram_gb: float = 12.0,
        min_free_ram_gb: float = 2.0,
    ) -> None:
        self.max_cpu_pct = max_cpu_pct
        self.max_ram_pct = max_ram_pct
        self.max_ram_gb = max_ram_gb
        self.min_free_ram_gb = min_free_ram_gb

    # ── CPU ────────────────────────────────────────────────────────────────

    def get_cpu_pct(self) -> float:
        """Return CPU utilisation 0.0–100.0; falls back to 0.0 if psutil absent."""
        if not HAS_PSUTIL:
            return 0.0
        try:
            return float(psutil.cpu_percent(interval=0.1))
        except Exception:
            return 0.0

    # ── RAM ────────────────────────────────────────────────────────────────

    def get_ram_used_gb(self) -> float:
        """Return RAM used in GiB."""
        if not HAS_PSUTIL:
            return 0.0
        try:
            return psutil.virtual_memory().used / (1024 ** 3)
        except Exception:
            return 0.0

    def get_ram_pct(self) -> float:
        """Return RAM usage percentage 0.0–100.0."""
        if not HAS_PSUTIL:
            return 0.0
        try:
            return float(psutil.virtual_memory().percent)
        except Exception:
            return 0.0

    def _get_free_ram_gb(self) -> float:
        if not HAS_PSUTIL:
            return 999.0
        try:
            return psutil.virtual_memory().available / (1024 ** 3)
        except Exception:
            return 999.0

    # ── Swap ───────────────────────────────────────────────────────────────

    def get_swap_used_gb(self) -> float:
        """Return swap used in GiB."""
        if not HAS_PSUTIL:
            return 0.0
        try:
            return psutil.swap_memory().used / (1024 ** 3)
        except Exception:
            return 0.0

    # ── Decision helpers ───────────────────────────────────────────────────

    def is_memory_critical(self) -> bool:
        """True if RAM usage exceeds configured maximum or free RAM is low."""
        return (
            self.get_ram_pct() > self.max_ram_pct
            or self._get_free_ram_gb() < self.min_free_ram_gb
        )

    def is_cpu_overloaded(self) -> bool:
        """True if CPU utilisation exceeds configured maximum."""
        return self.get_cpu_pct() > self.max_cpu_pct

    def should_defer_inference(self) -> bool:
        """True if either CPU or RAM is critical — inference should wait."""
        return self.is_cpu_overloaded() or self.is_memory_critical()

    # ── Status snapshot ────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Return all resource metrics plus a composite is_healthy flag."""
        cpu = self.get_cpu_pct()
        ram_pct = self.get_ram_pct()
        ram_gb = self.get_ram_used_gb()
        free_gb = self._get_free_ram_gb()
        swap_gb = self.get_swap_used_gb()
        mem_critical = self.is_memory_critical()
        cpu_over = self.is_cpu_overloaded()

        return {
            "cpu_pct": round(cpu, 2),
            "ram_pct": round(ram_pct, 2),
            "ram_used_gb": round(ram_gb, 3),
            "ram_free_gb": round(free_gb, 3),
            "swap_used_gb": round(swap_gb, 3),
            "is_memory_critical": mem_critical,
            "is_cpu_overloaded": cpu_over,
            "should_defer_inference": self.should_defer_inference(),
            "is_healthy": not mem_critical and not cpu_over,
            "limits": {
                "max_cpu_pct": self.max_cpu_pct,
                "max_ram_pct": self.max_ram_pct,
                "max_ram_gb": self.max_ram_gb,
                "min_free_ram_gb": self.min_free_ram_gb,
            },
        }
