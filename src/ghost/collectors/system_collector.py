"""System and Hardware Collector for GHOST Universal Intelligence System.

Conforms to ARCHITECTURE_TARGET.md §Layer 1 & RESOURCE_MANAGEMENT_PLAN.md:
- Deterministic, zero-dependency procfs sampling for Linux host metrics.
- Sub-second non-blocking NVML/nvidia-smi sampler for NVIDIA GTX 1650 Mobile metrics.
- Dispatches system.telemetry.sample events to EventBus every N seconds.
- Dispatches system.resource.alert events when CPU, RAM, or VRAM exceed safety thresholds.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

from ghost.core.events import Event, EventBus, EventSeverity, get_event_bus
from ghost.core.system_state import HardwareVitals

logger = logging.getLogger("ghost.collectors.system")


class SystemCollector:
    """Passively samples kernel and GPU metrics and publishes typed telemetry events."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        alert_cpu_threshold: float = 85.0,
        alert_ram_threshold: float = 90.0,
        alert_gpu_threshold: float = 90.0,
        alert_vram_threshold: float = 90.0,
        alert_disk_min_free_gb: float = 5.0,
        rolling_window_samples: int = 4,
        sustained_ratio: float = 0.75,
    ):
        self.event_bus = event_bus or get_event_bus()
        self.alert_cpu_threshold = alert_cpu_threshold
        self.alert_ram_threshold = alert_ram_threshold
        self.alert_gpu_threshold = alert_gpu_threshold
        self.alert_vram_threshold = alert_vram_threshold
        self.alert_disk_min_free_gb = alert_disk_min_free_gb
        self.rolling_window_samples = max(2, rolling_window_samples)
        self.sustained_ratio = sustained_ratio

        # Rolling history buffers
        self._cpu_history: deque[float] = deque(maxlen=self.rolling_window_samples)
        self._ram_history: deque[float] = deque(maxlen=self.rolling_window_samples)
        self._gpu_history: deque[float] = deque(maxlen=self.rolling_window_samples)
        self._vram_history: deque[float] = deque(maxlen=self.rolling_window_samples)
        self._disk_history: deque[float] = deque(maxlen=self.rolling_window_samples)

        # Active sustained anomaly tracking
        self._active_sustained_metrics: Set[str] = set()

        self._prev_cpu_times: Optional[Tuple[float, float]] = None  # (total, idle)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def read_cpu_percent(self) -> float:
        """Reads CPU utilization percentage from /proc/stat.
        
        Calculates delta since previous read. If first call, calculates over a 50ms window.
        Falls back to /proc/loadavg if /proc/stat is unreadable.
        """
        try:
            with open("/proc/stat", "r", encoding="utf-8") as f:
                fields = [float(x) for x in f.readline().split()[1:]]
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0.0)
            total = sum(fields)

            if self._prev_cpu_times is None:
                # First reading: sleep 50ms to get immediate delta
                time.sleep(0.05)
                with open("/proc/stat", "r", encoding="utf-8") as f:
                    fields2 = [float(x) for x in f.readline().split()[1:]]
                idle2 = fields2[3] + (fields2[4] if len(fields2) > 4 else 0.0)
                total2 = sum(fields2)

                delta_total = max(1.0, total2 - total)
                delta_idle = max(0.0, idle2 - idle)
                self._prev_cpu_times = (total2, idle2)
                return max(0.0, min(100.0, 100.0 * (1.0 - delta_idle / delta_total)))

            prev_total, prev_idle = self._prev_cpu_times
            delta_total = max(1.0, total - prev_total)
            delta_idle = max(0.0, idle - prev_idle)
            self._prev_cpu_times = (total, idle)
            return max(0.0, min(100.0, 100.0 * (1.0 - delta_idle / delta_total)))

        except Exception as e:
            logger.debug(f"procfs stat read failed ({e}), checking /proc/loadavg")
            try:
                with open("/proc/loadavg", "r", encoding="utf-8") as f:
                    load1 = float(f.read().split()[0])
                cores = os.cpu_count() or 4
                return max(0.0, min(100.0, (load1 / cores) * 100.0))
            except Exception:
                return 0.0

    def read_memory_vitals(self) -> Tuple[float, float, float]:
        """Reads RAM usage from /proc/meminfo.
        
        Returns:
            Tuple of (ram_used_mb, ram_total_mb, ram_percent)
        """
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                lines = {}
                for line in f:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        lines[k.strip()] = v.strip()

            total_kb = float(lines.get("MemTotal", "0").split()[0])
            avail_kb = float(lines.get("MemAvailable", "0").split()[0])
            if total_kb <= 0:
                return 0.0, 0.0, 0.0

            used_kb = max(0.0, total_kb - avail_kb)
            total_mb = total_kb / 1024.0
            used_mb = used_kb / 1024.0
            percent = (used_kb / total_kb) * 100.0
            return round(used_mb, 1), round(total_mb, 1), round(percent, 1)

        except Exception as e:
            logger.warning(f"Failed to read /proc/meminfo: {e}")
            return 0.0, 0.0, 0.0

    def read_disk_free_gb(self, path: str = "/") -> float:
        """Reads free disk space in Gigabytes."""
        try:
            total, used, free = shutil.disk_usage(path)
            return round(free / (1024.0 ** 3), 1)
        except Exception as e:
            logger.warning(f"Failed to check disk usage: {e}")
            return 0.0

    def read_nvidia_gpu_vitals(self) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Queries NVIDIA GPU metrics via nvidia-smi.
        
        Returns:
            Tuple of (gpu_utilization_percent, gpu_temp_celsius, gpu_vram_used_mb, gpu_vram_total_mb)
        """
        try:
            res = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                parts = [p.strip() for p in res.stdout.strip().split(",")]
                if len(parts) >= 4:
                    util = float(parts[0])
                    temp = float(parts[1])
                    vram_used = float(parts[2])
                    vram_total = float(parts[3])
                    return util, temp, vram_used, vram_total
        except Exception:
            # Expected on non-NVIDIA or when GPU is powered off in prime-run
            pass
        return None, None, None, None

    def sample(self) -> HardwareVitals:
        """Produces a deterministic, complete HardwareVitals sample."""
        cpu_pct = self.read_cpu_percent()
        ram_used, ram_total, ram_pct = self.read_memory_vitals()
        disk_free = self.read_disk_free_gb()
        gpu_util, gpu_temp, vram_used, vram_total = self.read_nvidia_gpu_vitals()

        return HardwareVitals(
            cpu_percent=round(cpu_pct, 1),
            ram_used_mb=ram_used,
            ram_total_mb=ram_total,
            ram_percent=ram_pct,
            disk_free_gb=disk_free,
            gpu_utilization_percent=gpu_util,
            gpu_temp_celsius=gpu_temp,
            gpu_vram_used_mb=vram_used,
            gpu_vram_total_mb=vram_total,
        )

    def _evaluate_metric(
        self,
        metric_name: str,
        current_val: float,
        threshold: float,
        history: deque[float],
        is_less_than: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Evaluates whether a metric constitutes a transient spike or sustained anomaly."""
        history.append(current_val)
        exceeded = (current_val <= threshold) if is_less_than else (current_val >= threshold)

        exceeded_count = sum(1 for v in history if ((v <= threshold) if is_less_than else (v >= threshold)))
        ratio = exceeded_count / len(history)
        is_sustained = (len(history) >= 2 and ratio >= self.sustained_ratio)

        if exceeded:
            avg_val = sum(history) / len(history)
            return {
                "metric": metric_name,
                "current": current_val,
                "threshold": threshold,
                "average": round(avg_val, 1),
                "is_sustained": is_sustained,
                "samples_count": len(history),
                "sustained_ratio": round(ratio, 2),
            }
        return None

    async def sample_and_publish(self) -> Event:
        """Samples hardware metrics, performs rolling window anomaly detection, and publishes events."""
        loop = asyncio.get_running_loop()
        vitals = await loop.run_in_executor(None, self.sample)

        # 1. Evaluate rolling thresholds
        sustained_alerts: Dict[str, Any] = {}
        transient_spikes: Dict[str, Any] = {}
        current_alert_metrics: Set[str] = set()

        # CPU
        cpu_res = self._evaluate_metric("cpu", vitals.cpu_percent, self.alert_cpu_threshold, self._cpu_history)
        if cpu_res:
            current_alert_metrics.add("cpu")
            if cpu_res["is_sustained"]:
                sustained_alerts["cpu"] = cpu_res
            else:
                transient_spikes["cpu"] = cpu_res

        # RAM
        ram_res = self._evaluate_metric("ram", vitals.ram_percent, self.alert_ram_threshold, self._ram_history)
        if ram_res:
            current_alert_metrics.add("ram")
            if ram_res["is_sustained"]:
                sustained_alerts["ram"] = ram_res
            else:
                transient_spikes["ram"] = ram_res

        # Disk
        disk_res = self._evaluate_metric("disk", vitals.disk_free_gb, self.alert_disk_min_free_gb, self._disk_history, is_less_than=True)
        if disk_res:
            current_alert_metrics.add("disk")
            if disk_res["is_sustained"]:
                sustained_alerts["disk"] = disk_res
            else:
                transient_spikes["disk"] = disk_res

        # GPU utilization & VRAM
        if vitals.gpu_utilization_percent is not None:
            gpu_res = self._evaluate_metric("gpu", vitals.gpu_utilization_percent, self.alert_gpu_threshold, self._gpu_history)
            if gpu_res:
                current_alert_metrics.add("gpu")
                if gpu_res["is_sustained"]:
                    sustained_alerts["gpu"] = gpu_res
                else:
                    transient_spikes["gpu"] = gpu_res

        if (
            vitals.gpu_vram_used_mb is not None
            and vitals.gpu_vram_total_mb is not None
            and vitals.gpu_vram_total_mb > 0
        ):
            vram_pct = round((vitals.gpu_vram_used_mb / vitals.gpu_vram_total_mb) * 100.0, 1)
            vram_res = self._evaluate_metric("vram", vram_pct, self.alert_vram_threshold, self._vram_history)
            if vram_res:
                current_alert_metrics.add("vram")
                if vram_res["is_sustained"]:
                    sustained_alerts["vram"] = vram_res
                else:
                    transient_spikes["vram"] = vram_res

        # 2. Publish Recoveries for previously sustained metrics that normalized
        recovered = self._active_sustained_metrics - current_alert_metrics
        for rec_metric in recovered:
            self._active_sustained_metrics.discard(rec_metric)
            rec_event = Event(
                event_type="system.resource.recovered",
                source="collector.system",
                severity=EventSeverity.INFO,
                payload={"metric": rec_metric, "vitals": vitals.to_dict()},
            )
            await self.event_bus.publish(rec_event)

        # 3. Publish Sustained Anomalies
        if sustained_alerts:
            self._active_sustained_metrics.update(sustained_alerts.keys())
            is_critical = (
                vitals.ram_percent >= 95.0
                or vitals.cpu_percent >= 96.0
                or vitals.disk_free_gb <= 2.0
            )
            sustained_event = Event(
                event_type="system.resource.sustained_anomaly",
                source="collector.system",
                severity=EventSeverity.CRITICAL if is_critical else EventSeverity.WARNING,
                payload={
                    "alerts": sustained_alerts,
                    "vitals": vitals.to_dict(),
                    "is_sustained": True,
                },
            )
            await self.event_bus.publish(sustained_event)

        # 4. Publish Transient Spikes (low severity notification)
        if transient_spikes:
            spike_event = Event(
                event_type="system.resource.spike",
                source="collector.system",
                severity=EventSeverity.INFO,
                payload={
                    "spikes": transient_spikes,
                    "vitals": vitals.to_dict(),
                    "is_transient": True,
                },
            )
            await self.event_bus.publish(spike_event)

        # 4b. Publish General system.resource.alert for any threshold breach
        combined_alerts = {**sustained_alerts, **transient_spikes}
        if combined_alerts:
            alert_event = Event(
                event_type="system.resource.alert",
                source="collector.system",
                severity=EventSeverity.WARNING if vitals.ram_percent < 96.0 else EventSeverity.CRITICAL,
                payload={
                    "alerts": combined_alerts,
                    "vitals": vitals.to_dict(),
                    "is_sustained": bool(sustained_alerts),
                },
            )
            await self.event_bus.publish(alert_event)

        # 5. Standard Periodic Telemetry Sample
        telemetry_event = Event(
            event_type="system.telemetry.sample",
            source="collector.system",
            severity=EventSeverity.INFO,
            payload=vitals.to_dict(),
        )
        await self.event_bus.publish(telemetry_event)
        return telemetry_event

    async def run_loop(self, interval_s: float = 15.0) -> None:
        """Asynchronous background telemetry collection loop."""
        self._running = True
        logger.info(f"SystemCollector started with {interval_s}s interval")
        try:
            while self._running:
                try:
                    await self.sample_and_publish()
                except Exception as e:
                    logger.error(f"Error in SystemCollector sample cycle: {e}")
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            logger.info("SystemCollector background task cancelled")
        finally:
            self._running = False

    def start(self, interval_s: float = 15.0) -> asyncio.Task:
        """Spawns run_loop in the active asyncio event loop."""
        if self._task and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run_loop(interval_s=interval_s))
        return self._task

    def stop(self) -> None:
        """Stops background telemetry sampling."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
