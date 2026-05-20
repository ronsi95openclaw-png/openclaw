"""GPU Monitor — NVIDIA GPU utilisation via nvidia-smi subprocess."""
from __future__ import annotations

import logging
import subprocess
from typing import Any, Dict

logger = logging.getLogger("openclaw.system.gpu_monitor")


class GPUMonitor:
    """Monitors GPU utilisation via nvidia-smi subprocess."""

    def __init__(self) -> None:
        self._available = self._check_available()
        if self._available:
            logger.info("GPUMonitor: nvidia-smi detected, GPU monitoring active")
        else:
            logger.info("GPUMonitor: nvidia-smi not found, GPU monitoring disabled")

    # ── Availability check ─────────────────────────────────────────────────

    def _check_available(self) -> bool:
        try:
            result = subprocess.run(
                ["nvidia-smi"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    def is_available(self) -> bool:
        return self._available

    # ── nvidia-smi query helper ────────────────────────────────────────────

    def _query(self, field: str) -> str:
        """Run nvidia-smi --query-gpu and return the first line of output."""
        if not self._available:
            return ""
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    f"--query-gpu={field}",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip().split("\n")[0].strip()
        except Exception:
            pass
        return ""

    def _query_float(self, field: str) -> float:
        raw = self._query(field)
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return 0.0

    # ── Public metrics ─────────────────────────────────────────────────────

    def get_gpu_utilization(self) -> float:
        """Return GPU utilisation 0.0–100.0, or 0.0 if not available."""
        return self._query_float("utilization.gpu")

    def get_gpu_memory_used_mb(self) -> float:
        """Return GPU memory used in MB."""
        return self._query_float("memory.used")

    def get_gpu_memory_total_mb(self) -> float:
        """Return GPU memory total in MB."""
        return self._query_float("memory.total")

    def get_gpu_temperature_c(self) -> float:
        """Return GPU temperature in Celsius."""
        return self._query_float("temperature.gpu")

    # ── Status snapshot ────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        if not self._available:
            return {
                "available": False,
                "utilization_pct": 0.0,
                "memory_used_mb": 0.0,
                "memory_total_mb": 0.0,
                "temperature_c": 0.0,
                "memory_free_mb": 0.0,
            }

        used_mb = self.get_gpu_memory_used_mb()
        total_mb = self.get_gpu_memory_total_mb()
        return {
            "available": True,
            "utilization_pct": round(self.get_gpu_utilization(), 2),
            "memory_used_mb": round(used_mb, 1),
            "memory_total_mb": round(total_mb, 1),
            "memory_free_mb": round(max(0.0, total_mb - used_mb), 1),
            "temperature_c": round(self.get_gpu_temperature_c(), 1),
        }
