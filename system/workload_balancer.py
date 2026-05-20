"""Workload Balancer — routes inference to CPU or GPU based on availability."""
from __future__ import annotations

import logging
from typing import Literal

from system.gpu_monitor import GPUMonitor
from system.resource_manager import ResourceManager

logger = logging.getLogger("openclaw.system.workload_balancer")

_GPU_TEMP_LIMIT_C = 80.0
_GPU_UTIL_LIMIT_PCT = 90.0
_GPU_MEM_HEADROOM_MB = 512.0


class WorkloadBalancer:
    """Balances workloads across CPU/GPU based on resource availability."""

    def __init__(
        self,
        resource_manager: ResourceManager,
        gpu_monitor: GPUMonitor,
        prefer_gpu: bool = True,
    ) -> None:
        self._rm = resource_manager
        self._gpu = gpu_monitor
        self._prefer_gpu = prefer_gpu

    # ── Routing decision ───────────────────────────────────────────────────

    def route_inference(self, model_size_gb: float) -> Literal["gpu", "cpu", "defer"]:
        """Return "gpu", "cpu", or "defer" based on current resource state.

        Args:
            model_size_gb: Approximate model VRAM requirement in GiB.

        Returns:
            "defer"  — both backends are overloaded; try again later.
            "gpu"    — GPU is available, cool, and has enough VRAM.
            "cpu"    — fall back to CPU (GPU unavailable or saturated).
        """
        cpu_ok = not self._rm.is_cpu_overloaded()
        mem_ok = not self._rm.is_memory_critical()

        # Check GPU suitability
        gpu_suitable = self._is_gpu_suitable(model_size_gb)

        if self._prefer_gpu and gpu_suitable:
            return "gpu"

        if cpu_ok and mem_ok:
            return "cpu"

        # GPU not preferred / not suitable — check if CPU can handle it
        if not self._prefer_gpu and gpu_suitable:
            return "gpu"

        # Both paths exhausted
        return "defer"

    def _is_gpu_suitable(self, model_size_gb: float) -> bool:
        """Return True if the GPU can accommodate the requested workload."""
        if not self._gpu.is_available():
            return False

        temp_c = self._gpu.get_gpu_temperature_c()
        if temp_c > _GPU_TEMP_LIMIT_C:
            logger.debug("GPU temperature %.1f°C exceeds limit — not routing to GPU", temp_c)
            return False

        util = self._gpu.get_gpu_utilization()
        if util > _GPU_UTIL_LIMIT_PCT:
            logger.debug("GPU utilisation %.1f%% exceeds limit — not routing to GPU", util)
            return False

        free_mb = self._gpu.get_gpu_memory_total_mb() - self._gpu.get_gpu_memory_used_mb()
        required_mb = model_size_gb * 1024.0 + _GPU_MEM_HEADROOM_MB
        if free_mb < required_mb:
            logger.debug(
                "GPU free VRAM %.0fMB < required %.0fMB — not routing to GPU",
                free_mb,
                required_mb,
            )
            return False

        return True

    # ── Batch size recommendation ──────────────────────────────────────────

    def get_recommended_batch_size(self) -> int:
        """Return 1–4 based on available resources.

        1 is most conservative; 4 assumes resources are plentiful.
        """
        cpu_pct = self._rm.get_cpu_pct()
        ram_pct = self._rm.get_ram_pct()

        pressure = max(cpu_pct, ram_pct)

        if pressure > 80.0:
            return 1
        if pressure > 65.0:
            return 2
        if pressure > 50.0:
            return 3
        return 4
