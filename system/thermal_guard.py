"""Thermal Guard — protects hardware from overheating."""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("openclaw.system.thermal_guard")

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


class ThermalGuard:
    """Protects hardware from thermal overload."""

    def __init__(
        self,
        cpu_temp_warning_c: float = 80.0,
        cpu_temp_critical_c: float = 90.0,
        gpu_temp_warning_c: float = 75.0,
        gpu_temp_critical_c: float = 85.0,
    ) -> None:
        self.cpu_temp_warning_c = cpu_temp_warning_c
        self.cpu_temp_critical_c = cpu_temp_critical_c
        self.gpu_temp_warning_c = gpu_temp_warning_c
        self.gpu_temp_critical_c = gpu_temp_critical_c

        # Lazily imported GPUMonitor to avoid circular imports
        self._gpu_monitor = None

    # ── GPU monitor accessor (lazy to keep imports clean) ──────────────────

    def _get_gpu_temp(self) -> float:
        if self._gpu_monitor is None:
            try:
                from system.gpu_monitor import GPUMonitor
                self._gpu_monitor = GPUMonitor()
            except Exception:
                return 0.0
        try:
            return self._gpu_monitor.get_gpu_temperature_c()
        except Exception:
            return 0.0

    # ── Temperature reads ──────────────────────────────────────────────────

    def get_cpu_temperature_c(self) -> float:
        """Return CPU temperature in Celsius; falls back to 0.0 if unavailable."""
        if not HAS_PSUTIL:
            return 0.0
        try:
            sensors = psutil.sensors_temperatures()
            if not sensors:
                return 0.0

            # Priority: coretemp, k10temp, cpu-thermal, acpitz
            for key in ("coretemp", "k10temp", "cpu-thermal", "acpitz"):
                if key in sensors and sensors[key]:
                    return float(sensors[key][0].current)

            # Fall back to first available sensor
            for entries in sensors.values():
                if entries:
                    return float(entries[0].current)
        except Exception:
            pass
        return 0.0

    def get_gpu_temperature_c(self) -> float:
        """Return GPU temperature in Celsius; 0.0 if not available."""
        return self._get_gpu_temp()

    # ── Throttle decisions ─────────────────────────────────────────────────

    def is_throttling_needed(self) -> bool:
        """True if CPU > warning threshold OR GPU > warning threshold."""
        return (
            self.get_cpu_temperature_c() >= self.cpu_temp_warning_c
            or self.get_gpu_temperature_c() >= self.gpu_temp_warning_c
        )

    def is_emergency_throttle(self) -> bool:
        """True if CPU > critical OR GPU > critical — triggers workload pause."""
        return (
            self.get_cpu_temperature_c() >= self.cpu_temp_critical_c
            or self.get_gpu_temperature_c() >= self.gpu_temp_critical_c
        )

    # ── Status snapshot ────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        cpu_t = self.get_cpu_temperature_c()
        gpu_t = self.get_gpu_temperature_c()
        return {
            "cpu_temperature_c": round(cpu_t, 1),
            "gpu_temperature_c": round(gpu_t, 1),
            "cpu_temp_warning_c": self.cpu_temp_warning_c,
            "cpu_temp_critical_c": self.cpu_temp_critical_c,
            "gpu_temp_warning_c": self.gpu_temp_warning_c,
            "gpu_temp_critical_c": self.gpu_temp_critical_c,
            "is_throttling_needed": self.is_throttling_needed(),
            "is_emergency_throttle": self.is_emergency_throttle(),
            "psutil_available": HAS_PSUTIL,
        }
