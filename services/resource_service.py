"""System, process, and disk resource monitoring; no OpenCV or Pillow.

Measures CPU/RAM before and after video processing, samples them on a
background thread while processing runs, and chooses how many frames
``video_service.extract_frames`` should sample. Video metadata is accepted as
the plain dict returned by ``video_service.get_video_info``; this module never
imports video_service, OpenCV, or Pillow.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time
from typing import Any, Iterator

import psutil

from . import file_manager

CPU_SAMPLE_INTERVAL = 0.1
CPU_ZERO_READ_RETRIES = 10  # extend a 0.0 read by CPU_SAMPLE_INTERVAL steps, 1.1 s in total
MEDIUM_LOAD_PERCENT = 60.0
HIGH_LOAD_PERCENT = 80.0
LOW_LOAD_SAMPLE_COUNT = 20
MEDIUM_LOAD_SAMPLE_COUNT = 10
HIGH_LOAD_SAMPLE_COUNT = 5
DEFAULT_SAMPLE_COUNT = MEDIUM_LOAD_SAMPLE_COUNT
MIN_SAMPLE_COUNT = 1
MAX_SAMPLE_COUNT = LOW_LOAD_SAMPLE_COUNT
FRAME_BYTES_PER_PIXEL = 3
FRAME_MEMORY_BUDGET_RATIO = 0.25
DEFAULT_SAMPLE_INTERVAL = 0.5
MAX_TIMELINE_SAMPLES = 3600
SAMPLER_JOIN_TIMEOUT = 2.0
BYTES_PER_MB = 1024 ** 2
SAMPLER_THREAD_NAME = "resource-sampler"


class ResourceServiceError(RuntimeError):
    """Raised when system resources cannot be measured."""


def _mb(n_bytes: float) -> float:
    return round(n_bytes / BYTES_PER_MB, 2)


def _percent(value: float) -> float:
    return round(min(100.0, max(0.0, float(value))), 2)


def cpu_count() -> int:
    """process_cpu_percent 정규화에 쓰는 논리 코어 수. 코어 기준 %로 되돌릴 때 같은 값을 쓴다."""
    return psutil.cpu_count() or os.cpu_count() or 1


def _describe(error: BaseException) -> str:
    """Name the error type; psutil.Error subclasses may carry an empty message."""
    message = str(error)
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


def _validate_number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number; got {type(value).__name__}")
    return float(value)


def _validate_percent(name: str, value: Any) -> float:
    number = _validate_number(name, value)
    if not 0 <= number <= 100:
        raise ValueError(f"{name} must be between 0 and 100; got {value}")
    return number


def _directory_size_mb(directory: Path) -> float:
    """Sum regular file sizes below directory; symlinks are skipped, missing is 0."""
    total = 0
    for root, dirs, files in os.walk(directory):
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))]
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue
            try:
                total += os.path.getsize(path)
            except FileNotFoundError:
                continue  # removed between listing and stat; nothing to count
    return _mb(total)


def _read_cpu_window(process: psutil.Process) -> tuple[float, float]:
    """Measure system and process CPU over a short blocking window.

    macOS refreshes its CPU counters only about once per second, so a
    CPU_SAMPLE_INTERVAL window reads exactly 0.0 roughly 40% of the time. A 0.0
    read is extended in CPU_SAMPLE_INTERVAL steps, at most CPU_ZERO_READ_RETRIES
    times. Ticks accumulate across steps, so the first nonzero value is the busy
    ratio over the whole extended window. Linux and Windows return on the first read.
    """
    process.cpu_percent(None)
    cpu = psutil.cpu_percent(interval=CPU_SAMPLE_INTERVAL)
    for _ in range(CPU_ZERO_READ_RETRIES):
        if cpu != 0.0:
            break
        time.sleep(CPU_SAMPLE_INTERVAL)
        cpu = psutil.cpu_percent(None)
    return cpu, process.cpu_percent(None) / cpu_count()


def _snapshot(process: psutil.Process, blocking: bool) -> dict[str, Any]:
    """Read system and process CPU/RAM.

    Blocking reads measure a fresh window (see _read_cpu_window). Non-blocking
    reads report usage since the previous call on this thread and process object.
    """
    if blocking:
        cpu, process_cpu = _read_cpu_window(process)
    else:
        cpu = psutil.cpu_percent(None)
        process_cpu = process.cpu_percent(None) / cpu_count()
    memory = psutil.virtual_memory()
    return {
        "cpu_percent": _percent(cpu),
        "memory_percent": _percent(memory.percent),
        "memory_used_mb": _mb(memory.used),
        "memory_available_mb": _mb(memory.available),
        "process_cpu_percent": _percent(process_cpu),
        "process_memory_mb": _mb(process.memory_info().rss),
    }


def get_system_resource() -> dict[str, Any]:
    """Return current system and process CPU/RAM usage.

    Blocks for CPU_SAMPLE_INTERVAL seconds to measure CPU, longer (up to about
    1 s) only while the platform keeps reporting 0.0. ``process_cpu_percent`` is normalized
    by CPU count so every percentage stays within 0..100.
    """
    try:
        return _snapshot(psutil.Process(), blocking=True)
    except (psutil.Error, OSError) as error:
        raise ResourceServiceError(f"Unable to read system resources: {_describe(error)}") from error


def get_disk_resource() -> dict[str, Any]:
    """Return free disk space and the sizes of TEMP_DIR and THUMBNAIL_DIR in MB."""
    try:
        usage = psutil.disk_usage(str(file_manager.PROJECT_ROOT))
        return {
            "disk_free_mb": _mb(usage.free),
            "disk_percent": _percent(usage.percent),
            "temp_dir_mb": _directory_size_mb(file_manager.TEMP_DIR),
            "thumbnail_dir_mb": _directory_size_mb(file_manager.THUMBNAIL_DIR),
        }
    except (psutil.Error, OSError) as error:
        raise ResourceServiceError(f"Unable to read disk usage: {_describe(error)}") from error


def _base_sample_count(cpu_percent: float, memory_percent: float) -> int:
    load = max(cpu_percent, memory_percent)
    if load >= HIGH_LOAD_PERCENT:
        return HIGH_LOAD_SAMPLE_COUNT
    if load >= MEDIUM_LOAD_PERCENT:
        return MEDIUM_LOAD_SAMPLE_COUNT
    return LOW_LOAD_SAMPLE_COUNT


def _metadata_int(video_info: dict[str, Any], key: str) -> int:
    """Return a non-negative metadata integer; missing or negative means unknown (0)."""
    value = video_info.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"video_info[{key!r}] must be an integer; got {value!r}")
    return max(value, 0)


def choose_sample_count(
    cpu_percent: float,
    memory_percent: float,
    *,
    video_info: dict[str, Any] | None = None,
    memory_available_mb: float | None = None,
) -> int:
    """Choose how many frames to sample for the current load.

    Policy: CPU or RAM >= 80% -> 5, >= 60% -> 10, otherwise 20. When
    ``video_info`` (the dict from ``video_service.get_video_info``) is given, the
    count never exceeds ``frame_count``; when ``memory_available_mb`` is also
    given, all sampled BGR frames (width x height x 3 bytes each) must fit within
    FRAME_MEMORY_BUDGET_RATIO of available memory. Zero metadata values mean
    unknown and skip their cap. The result is within [MIN, MAX]_SAMPLE_COUNT.
    """
    cpu = _validate_percent("cpu_percent", cpu_percent)
    memory = _validate_percent("memory_percent", memory_percent)
    count = _base_sample_count(cpu, memory)
    if video_info is not None:
        if not isinstance(video_info, dict):
            raise TypeError("video_info must be the dict returned by get_video_info, or None")
        width = _metadata_int(video_info, "width")
        height = _metadata_int(video_info, "height")
        frame_count = _metadata_int(video_info, "frame_count")
        if frame_count > 0:
            count = min(count, frame_count)
        if width > 0 and height > 0 and memory_available_mb is not None:
            available = _validate_number("memory_available_mb", memory_available_mb)
            if available < 0:
                raise ValueError(f"memory_available_mb must not be negative; got {memory_available_mb}")
            frame_bytes = width * height * FRAME_BYTES_PER_PIXEL
            budget = available * BYTES_PER_MB * FRAME_MEMORY_BUDGET_RATIO
            count = min(count, int(budget // frame_bytes))
    return max(MIN_SAMPLE_COUNT, min(MAX_SAMPLE_COUNT, count))


class _ResourceSampler:
    """Daemon thread recording non-blocking CPU/RAM snapshots at a fixed interval.

    Exceptions inside the thread are recorded in ``error`` and end sampling;
    they never propagate. Sampling also stops by itself after
    MAX_TIMELINE_SAMPLES so an unstopped sampler cannot grow without bound.
    Intervals below about 1 s yield occasional 0.0 CPU samples on macOS, whose
    kernel refreshes CPU counters roughly once per second.
    """

    def __init__(self, interval: float, started_at: float) -> None:
        self.interval = interval
        self.started_at = started_at
        self.timeline: list[dict[str, Any]] = []
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=SAMPLER_THREAD_NAME, daemon=True)

    def start(self) -> None:
        self._process = psutil.Process()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(SAMPLER_JOIN_TIMEOUT)
        if self._thread.is_alive() and self.error is None:
            self.error = f"Sampler did not stop within {SAMPLER_JOIN_TIMEOUT} seconds"

    def _run(self) -> None:
        try:
            # psutil keeps non-blocking CPU state per thread and per Process object;
            # prime both here so the first sample is not a meaningless 0.0.
            psutil.cpu_percent(None)
            self._process.cpu_percent(None)
            while not self._stop.wait(self.interval) and len(self.timeline) < MAX_TIMELINE_SAMPLES:
                sample = _snapshot(self._process, blocking=False)
                sample["elapsed_seconds"] = round(time.perf_counter() - self.started_at, 2)
                self.timeline.append(sample)
        except Exception as error:  # recorded for the report; a thread cannot raise to the caller
            self.error = _describe(error)


def start_measurement(sample_interval: float | None = DEFAULT_SAMPLE_INTERVAL) -> dict[str, Any]:
    """Record the start time and resource state; optionally start background sampling.

    Pass ``sample_interval=None`` to keep only the start and finish snapshots.
    Hand the returned dict to ``finish_measurement``. It holds a live thread, so
    never store it in Streamlit session_state; prefer ``measure()`` so the
    thread is always stopped.
    """
    if sample_interval is not None:
        interval = _validate_number("sample_interval", sample_interval)
        if interval <= 0:
            raise ValueError(f"sample_interval must be positive seconds or None; got {sample_interval}")
    resource = get_system_resource()
    disk = get_disk_resource()
    started_at = time.perf_counter()
    sampler = None
    if sample_interval is not None:
        sampler = _ResourceSampler(interval, started_at)
        try:
            sampler.start()
        except (psutil.Error, OSError) as error:
            raise ResourceServiceError(f"Unable to start resource sampling: {_describe(error)}") from error
    return {"started_at": started_at, "resource": resource, "disk": disk, "_sampler": sampler}


def finish_measurement(start: dict[str, Any]) -> dict[str, Any]:
    """Stop sampling, take the final snapshot, and return the measurement report.

    The report holds the spec keys (elapsed_seconds, cpu_before/after,
    memory_before_mb/after_mb/delta_mb) plus process memory, peak/average
    values, a chartable ``timeline`` (start snapshot, samples, finish snapshot),
    disk usage before/after, and ``sampling_error`` (None when sampling was clean).
    """
    if not isinstance(start, dict) or not {"started_at", "resource", "disk", "_sampler"} <= start.keys():
        raise ValueError("start must be the dict returned by start_measurement")
    sampler = start["_sampler"]
    if sampler is not None:
        sampler.stop()
    elapsed = round(time.perf_counter() - start["started_at"], 2)
    before = start["resource"]
    after = get_system_resource()
    disk_after = get_disk_resource()
    timeline = [
        dict(before, elapsed_seconds=0.0),
        *(sampler.timeline if sampler is not None else []),
        dict(after, elapsed_seconds=elapsed),
    ]
    cpu_values = [point["cpu_percent"] for point in timeline]
    return {
        "elapsed_seconds": elapsed,
        "cpu_before": before["cpu_percent"],
        "cpu_after": after["cpu_percent"],
        "memory_before_mb": before["memory_used_mb"],
        "memory_after_mb": after["memory_used_mb"],
        "memory_delta_mb": round(after["memory_used_mb"] - before["memory_used_mb"], 2),
        "process_memory_before_mb": before["process_memory_mb"],
        "process_memory_after_mb": after["process_memory_mb"],
        "process_memory_delta_mb": round(after["process_memory_mb"] - before["process_memory_mb"], 2),
        "peak_cpu_percent": max(cpu_values),
        "avg_cpu_percent": round(sum(cpu_values) / len(cpu_values), 2),
        "peak_memory_percent": max(point["memory_percent"] for point in timeline),
        "peak_process_memory_mb": max(point["process_memory_mb"] for point in timeline),
        "timeline": timeline,
        "disk_before": start["disk"],
        "disk_after": disk_after,
        "sampling_error": sampler.error if sampler is not None else None,
    }


@contextmanager
def measure(sample_interval: float | None = DEFAULT_SAMPLE_INTERVAL) -> Iterator[dict[str, Any]]:
    """Measure the enclosed block; the yielded report dict is filled in on exit.

    The sampler thread is always stopped, even when the block raises. A
    monitoring failure never interrupts the block: it is recorded in
    ``report["error"]`` and ``elapsed_seconds`` is still reported.
    """
    report: dict[str, Any] = {"error": None}
    started_at = time.perf_counter()
    start = None
    try:
        start = start_measurement(sample_interval)
    except ResourceServiceError as error:
        report["error"] = str(error)
    try:
        yield report
    finally:
        if start is None:
            report["elapsed_seconds"] = round(time.perf_counter() - started_at, 2)
        else:
            try:
                report.update(finish_measurement(start))
            except ResourceServiceError as error:
                if start["_sampler"] is not None:
                    start["_sampler"].stop()
                report["error"] = str(error)
                report["elapsed_seconds"] = round(time.perf_counter() - start["started_at"], 2)
