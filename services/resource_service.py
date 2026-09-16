"""Stateless system snapshots and process RSS measurement using psutil.

Memory fields ending in _mb use 1024**2 bytes (MiB). CPU snapshots sample the
whole system over 0.1 seconds; they are not process CPU or whole-job averages.
RSS deltas describe this Python process, not a single Streamlit session: other
sessions/threads and allocator caching can influence them. They are not peaks.
Keep start_measurement() local to synchronous processing; store only the
finished report for reruns. Never keep a timer across user interaction.
"""
from collections.abc import Mapping
import math
from numbers import Real
import time
from typing import Any

import psutil

_BYTES_PER_MB = 1024 * 1024
FALLBACK_SAMPLE_COUNT = 5


class ResourceServiceError(RuntimeError):
    """Resource collection failed; callers may show an unavailable status."""


def _number(value: float, name: str, maximum: float | None = None) -> float:
    if (isinstance(value, bool) or not isinstance(value, Real)
            or not math.isfinite(value) or value < 0
            or (maximum is not None and value > maximum)):
        limit = f" from 0 to {maximum:g}" if maximum is not None else " >= 0"
        raise ValueError(f"{name} must be a finite number{limit}")
    return float(value)


def get_system_resource() -> dict[str, float]:
    """Return sampled system CPU, system RAM and current-process RSS.

    Uses a blocking 0.1-second CPU sample to avoid the meaningless first
    nonblocking reading. Failures raise ResourceServiceError, never fake zeros.
    """
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        rss = psutil.Process().memory_info().rss
        return {
            "cpu_percent": _number(cpu, 'cpu_percent', 100),
            "memory_percent": _number(memory.percent, 'memory_percent', 100),
            "memory_used_mb": _number(memory.used, 'memory.used') / _BYTES_PER_MB,
            "memory_available_mb": _number(memory.available, 'memory.available') / _BYTES_PER_MB,
            "process_memory_mb": _number(rss, 'process RSS') / _BYTES_PER_MB,
        }
    except (psutil.Error, OSError, ValueError) as error:
        raise ResourceServiceError("Unable to collect system/process resource metrics") from error


def choose_sample_count(cpu_percent: float, memory_percent: float) -> int:
    """Choose 5/10/20 samples at the inclusive 80/60 percent thresholds."""
    cpu = _number(cpu_percent, 'cpu_percent', 100)
    memory = _number(memory_percent, 'memory_percent', 100)
    if cpu >= 80 or memory >= 80:
        return 5
    if cpu >= 60 or memory >= 60:
        return 10
    return 20


def start_measurement() -> dict[str, Any]:
    """Snapshot resources, then start the processing timer (perf_counter)."""
    resources = get_system_resource()
    return {"resources": resources, "started_at": time.perf_counter()}


def finish_measurement(
    start: Mapping[str, Any],
    *,
    requested_sample_count: int | None = None,
    actual_sample_count: int | None = None,
    total_frames: int | None = None,
) -> dict[str, float | int | None]:
    """Finish timing before the final resource snapshot and return a report.

    Both 0.1-second snapshot sampling intervals are excluded from elapsed time.
    Supply all three optional counts together to include sampling statistics.
    actual_sample_count must be len(extracted_frames), never the requested count.
    sampling_ratio is actual/total in [0,1], or None if total_frames is unknown
    (zero); frames_processed is the actual count.
    Omit counts for a generic resource-only measurement.
    """
    if not isinstance(start, Mapping) or not {'started_at', 'resources'} <= start.keys():
        raise ValueError("start must be the result of start_measurement()")
    started_at = _number(start['started_at'], 'started_at')
    before = start['resources']
    if not isinstance(before, Mapping):
        raise ValueError("start.resources must be a resource snapshot")
    fields = ('cpu_percent', 'memory_percent', 'memory_used_mb',
              'memory_available_mb', 'process_memory_mb')
    if not set(fields) <= before.keys():
        raise ValueError("start.resources is missing required metrics")
    before = {key: _number(before[key], key, 100 if key.endswith('percent') else None)
              for key in fields}
    counts = (requested_sample_count, actual_sample_count, total_frames)
    if any(value is not None for value in counts):
        if (any(type(value) is not int for value in counts)
                or requested_sample_count < 1 or total_frames < 0
                or not 0 <= actual_sample_count <= requested_sample_count
                or (total_frames > 0 and actual_sample_count > total_frames)):
            raise ValueError("Provide integer counts: requested >= 1, total >= 0, "
                             "0 <= actual <= requested and <= total when known")
    elapsed = time.perf_counter() - started_at
    if elapsed < 0:
        raise ValueError("start.started_at is later than the current performance counter")
    after = get_system_resource()
    report = {
        'elapsed_seconds': elapsed,
        'system_cpu_before': before['cpu_percent'],
        'system_cpu_after': after['cpu_percent'],
        'system_memory_percent_before': before['memory_percent'],
        'system_memory_percent_after': after['memory_percent'],
        'system_memory_used_before_mb': before['memory_used_mb'],
        'system_memory_used_after_mb': after['memory_used_mb'],
        'system_memory_available_before_mb': before['memory_available_mb'],
        'system_memory_available_after_mb': after['memory_available_mb'],
        'process_memory_before_mb': before['process_memory_mb'],
        'process_memory_after_mb': after['process_memory_mb'],
        'process_memory_delta_mb': after['process_memory_mb'] - before['process_memory_mb'],
    }
    if requested_sample_count is not None:
        report.update(requested_sample_count=requested_sample_count,
                      actual_sample_count=actual_sample_count,
                      total_frames=total_frames, frames_processed=actual_sample_count,
                      sampling_ratio=actual_sample_count / total_frames if total_frames else None)
    return report
