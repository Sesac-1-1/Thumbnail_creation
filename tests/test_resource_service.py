"""Tests for resource monitoring; psutil is mocked, no video files needed."""
from collections import namedtuple
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

import psutil

from services import file_manager
from services import resource_service as rs

Memory = namedtuple("Memory", "total available percent used")
Disk = namedtuple("Disk", "total used free percent")
MemoryInfo = namedtuple("MemoryInfo", "rss")
GB = 1024 ** 3


class FakeProcess:
    """psutil.Process substitute: fixed RSS and CPU percent (test-only)."""
    cpu = 40.0
    rss = 150 * rs.BYTES_PER_MB

    def __init__(self, *_args):
        pass

    def cpu_percent(self, interval=None):
        return self.cpu

    def memory_info(self):
        return MemoryInfo(self.rss)


def fake_memory(percent=48.5, available_mb=8320, used_mb=7820):
    return Memory(total=16 * GB, available=available_mb * rs.BYTES_PER_MB,
                  percent=percent, used=used_mb * rs.BYTES_PER_MB)


def video_info(width, height, frame_count):
    return {"filename": "sample.mp4", "width": width, "height": height,
            "frame_count": frame_count, "fps": 30.0}


def sampler_threads():
    return [t for t in threading.enumerate() if t.name == rs.SAMPLER_THREAD_NAME and t.is_alive()]


class PsutilMockMixin:
    def setUp(self):
        self.cpu = 31.2
        patches = patch.multiple(
            rs.psutil,
            cpu_percent=lambda interval=None: self.cpu,
            virtual_memory=lambda: fake_memory(),
            Process=FakeProcess,
            cpu_count=lambda logical=True: 4,
            disk_usage=lambda path: Disk(500 * GB, 200 * GB, 300 * GB, 40.0),
        )
        patches.start()
        self.addCleanup(patches.stop)


class SampleCountTests(unittest.TestCase):
    def test_spec_thresholds(self):
        cases = [((10, 20), 20), ((59.99, 0), 20), ((0, 59.99), 20),
                 ((60, 0), 10), ((0, 60), 10), ((79.99, 79.99), 10),
                 ((80, 0), 5), ((0, 80), 5), ((100, 100), 5)]
        for (cpu, memory), expected in cases:
            with self.subTest(cpu=cpu, memory=memory):
                self.assertEqual(rs.choose_sample_count(cpu, memory), expected)

    def test_metadata_caps(self):
        cases = [
            (dict(video_info=video_info(1920, 1080, 3)), 3),
            (dict(video_info=video_info(0, 0, 0)), 20),
            (dict(video_info={}), 20),
            (dict(video_info=video_info(1920, 1080, 1000), memory_available_mb=8192), 20),
            (dict(video_info=video_info(1920, 1080, 1000), memory_available_mb=500), 20),
            (dict(video_info=video_info(3840, 2160, 1000), memory_available_mb=8192), 20),
            (dict(video_info=video_info(3840, 2160, 1000), memory_available_mb=500), 5),
            (dict(video_info=video_info(7680, 4320, 1000), memory_available_mb=500), 1),
            (dict(video_info=video_info(7680, 4320, 1000), memory_available_mb=100), 1),
            (dict(video_info=video_info(7680, 4320, 1000)), 20),
            (dict(video_info=video_info(7680, 4320, 3), memory_available_mb=8192), 3),
            (dict(memory_available_mb=1), 20),
        ]
        for kwargs, expected in cases:
            with self.subTest(**kwargs):
                self.assertEqual(rs.choose_sample_count(10, 10, **kwargs), expected)
        self.assertEqual(rs.choose_sample_count(90, 10, video_info=video_info(640, 480, 1000),
                                                memory_available_mb=8192), 5)

    def test_invalid_inputs(self):
        for cpu, memory in ((-1, 0), (101, 0), (0, -0.1), (0, 100.5), (float("nan"), 0)):
            with self.subTest(cpu=cpu, memory=memory), self.assertRaises(ValueError):
                rs.choose_sample_count(cpu, memory)
        for cpu in (None, "50", True, [50]):
            with self.subTest(cpu=cpu), self.assertRaises(TypeError):
                rs.choose_sample_count(cpu, 0)
        with self.assertRaises(TypeError):
            rs.choose_sample_count(0, 0, video_info=[1920, 1080, 10])
        for bad in (video_info("1920", 1080, 10), video_info(1920.0, 1080, 10),
                    video_info(1920, 1080, True)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                rs.choose_sample_count(0, 0, video_info=bad)
        info = video_info(1920, 1080, 10)
        with self.assertRaises(ValueError):
            rs.choose_sample_count(0, 0, video_info=info, memory_available_mb=-1)
        for available in ("8192", True):
            with self.subTest(available=available), self.assertRaises(TypeError):
                rs.choose_sample_count(0, 0, video_info=info, memory_available_mb=available)


class SnapshotTests(PsutilMockMixin, unittest.TestCase):
    def _symlink(self, link, target, *, target_is_directory=False):
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows requires symlink privilege for this test")
            raise

    def test_system_and_process_snapshot(self):
        self.cpu = 31.234
        result = rs.get_system_resource()
        self.assertEqual(result, {
            "cpu_percent": 31.23, "memory_percent": 48.5,
            "memory_used_mb": 7820.0, "memory_available_mb": 8320.0,
            "process_cpu_percent": 10.0, "process_memory_mb": 150.0,
        })
        self.assertTrue(all(isinstance(value, float) for value in result.values()))
        with patch.object(FakeProcess, "cpu", 1000.0):
            self.assertEqual(rs.get_system_resource()["process_cpu_percent"], 100.0)
        with patch.object(rs.psutil, "cpu_count", lambda logical=True: None), \
                patch.object(rs.os, "cpu_count", lambda: None):
            self.assertEqual(rs.get_system_resource()["process_cpu_percent"], 40.0)

    def test_zero_cpu_read_is_extended(self):
        readings = iter([0.0, 0.0, 25.0])
        with patch.object(rs.psutil, "cpu_percent", lambda interval=None: next(readings)) as _, \
                patch.object(rs.time, "sleep") as sleep:
            self.assertEqual(rs.get_system_resource()["cpu_percent"], 25.0)
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(rs.CPU_SAMPLE_INTERVAL)
        with patch.object(rs.psutil, "cpu_percent", lambda interval=None: 0.0), \
                patch.object(rs.time, "sleep") as sleep:
            self.assertEqual(rs.get_system_resource()["cpu_percent"], 0.0)
        self.assertEqual(sleep.call_count, rs.CPU_ZERO_READ_RETRIES)

    def test_disk_snapshot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = dict(PROJECT_ROOT=root, TEMP_DIR=root / "temp", OUTPUT_DIR=root / "output",
                         THUMBNAIL_DIR=root / "output" / "thumbnails")
            with patch.multiple(file_manager, **paths):
                job = root / "temp" / "job"
                job.mkdir(parents=True)
                (job / "video.bin").write_bytes(b"x" * (3 * rs.BYTES_PER_MB))
                (root / "temp" / "upload.bin").write_bytes(b"y" * (rs.BYTES_PER_MB // 2))
                outside = root / "outside.bin"
                outside.write_bytes(b"z" * rs.BYTES_PER_MB)
                self._symlink(root / "temp" / "link.bin", outside)
                self._symlink(root / "temp" / "linked_dir", root, target_is_directory=True)
                result = rs.get_disk_resource()
        self.assertEqual(result, {"disk_free_mb": 300 * 1024.0, "disk_percent": 40.0,
                                  "temp_dir_mb": 3.5, "thumbnail_dir_mb": 0.0})

    def test_psutil_failure_wrapped(self):
        with patch.object(rs.psutil, "virtual_memory", side_effect=OSError("sensor offline")):
            with self.assertRaisesRegex(rs.ResourceServiceError, "OSError: sensor offline"):
                rs.get_system_resource()
            with self.assertRaises(rs.ResourceServiceError):
                rs.start_measurement()
        with patch.object(rs.psutil, "Process", side_effect=psutil.AccessDenied(1)):
            with self.assertRaisesRegex(rs.ResourceServiceError, "AccessDenied"):
                rs.get_system_resource()
        with patch.object(rs.psutil, "cpu_percent", side_effect=psutil.Error()):
            with self.assertRaisesRegex(rs.ResourceServiceError, "resources: Error$"):
                rs.get_system_resource()
        with patch.object(rs.psutil, "disk_usage", side_effect=OSError("no disk")):
            with self.assertRaisesRegex(rs.ResourceServiceError, "no disk"):
                rs.get_disk_resource()
        self.assertEqual(sampler_threads(), [])


class MeasurementTests(PsutilMockMixin, unittest.TestCase):
    def tearDown(self):
        self.assertEqual(sampler_threads(), [], "a sampler thread was left running")

    def test_snapshot_only_measurement(self):
        with patch.object(rs.time, "perf_counter", side_effect=[100.0, 101.7]):
            start = rs.start_measurement(sample_interval=None)
            self.assertIsNone(start["_sampler"])
            self.cpu = 37.0
            with patch.object(FakeProcess, "rss", 200 * rs.BYTES_PER_MB), \
                    patch.object(rs.psutil, "virtual_memory",
                                 lambda: fake_memory(percent=50.0, used_mb=7882)):
                result = rs.finish_measurement(start)
        timeline = result.pop("timeline")
        self.assertEqual(result, {
            "elapsed_seconds": 1.7, "cpu_before": 31.2, "cpu_after": 37.0,
            "memory_before_mb": 7820.0, "memory_after_mb": 7882.0, "memory_delta_mb": 62.0,
            "process_memory_before_mb": 150.0, "process_memory_after_mb": 200.0,
            "process_memory_delta_mb": 50.0,
            "peak_cpu_percent": 37.0, "avg_cpu_percent": 34.1,
            "peak_memory_percent": 50.0, "peak_process_memory_mb": 200.0,
            "disk_before": {"disk_free_mb": 300 * 1024.0, "disk_percent": 40.0,
                            "temp_dir_mb": rs._directory_size_mb(file_manager.TEMP_DIR),
                            "thumbnail_dir_mb": rs._directory_size_mb(file_manager.THUMBNAIL_DIR)},
            "disk_after": result["disk_before"],
            "sampling_error": None,
        })
        self.assertEqual([point["elapsed_seconds"] for point in timeline], [0.0, 1.7])
        self.assertEqual(timeline[0]["cpu_percent"], 31.2)
        self.assertEqual(timeline[1]["process_memory_mb"], 200.0)

    def test_sampler_timeline(self):
        collected = threading.Event()
        calls = []

        def cpu_percent(interval=None):
            calls.append(interval)
            if len(calls) >= 6:
                collected.set()
            return 50.0

        with patch.object(rs.psutil, "cpu_percent", cpu_percent):
            start = rs.start_measurement(sample_interval=0.01)
            self.assertTrue(collected.wait(2.0), "sampler did not collect samples in time")
            result = rs.finish_measurement(start)
        self.assertFalse(start["_sampler"]._thread.is_alive())
        self.assertIsNone(result["sampling_error"])
        self.assertGreaterEqual(len(result["timeline"]), 6)
        self.assertIsNone(calls[1], "sampler priming read must be non-blocking")
        self.assertTrue(all(interval is None for interval in calls[2:-1]))
        elapsed = [point["elapsed_seconds"] for point in result["timeline"]]
        self.assertEqual(elapsed, sorted(elapsed))
        self.assertEqual(elapsed[0], 0.0)
        self.assertGreaterEqual(result["elapsed_seconds"], 0.0)
        self.assertEqual(result["peak_cpu_percent"], 50.0)
        self.assertEqual(result["avg_cpu_percent"], 50.0)
        self.assertEqual(result["peak_process_memory_mb"], 150.0)
        for point in result["timeline"]:
            self.assertEqual(set(point), {"elapsed_seconds", "cpu_percent", "memory_percent",
                                          "memory_used_mb", "memory_available_mb",
                                          "process_cpu_percent", "process_memory_mb"})

    def test_sampler_error_and_self_stop(self):
        fail = threading.Event()
        failed = threading.Event()

        def virtual_memory():
            if fail.is_set():
                failed.set()
                raise OSError("sensor offline")
            return fake_memory()

        with patch.object(rs.psutil, "virtual_memory", virtual_memory):
            start = rs.start_measurement(sample_interval=0.01)
            fail.set()
            self.assertTrue(failed.wait(2.0))
            start["_sampler"]._thread.join(2.0)
            self.assertFalse(start["_sampler"]._thread.is_alive())
            fail.clear()
            result = rs.finish_measurement(start)
        self.assertIn("sensor offline", result["sampling_error"])
        self.assertGreaterEqual(len(result["timeline"]), 2)
        self.assertEqual(result["cpu_after"], 31.2)
        with patch.object(rs, "MAX_TIMELINE_SAMPLES", 2):
            start = rs.start_measurement(sample_interval=0.01)
            start["_sampler"]._thread.join(2.0)
            self.assertFalse(start["_sampler"]._thread.is_alive())
            result = rs.finish_measurement(start)
        self.assertIsNone(result["sampling_error"])
        self.assertEqual(len(result["timeline"]), 4)

    def test_measure_context_manager(self):
        with self.assertRaisesRegex(RuntimeError, "processing failed"):
            with rs.measure(sample_interval=0.01) as report:
                self.assertEqual(len(sampler_threads()), 1)
                raise RuntimeError("processing failed")
        self.assertEqual(sampler_threads(), [])
        self.assertIsNone(report["error"])
        self.assertGreaterEqual(report["elapsed_seconds"], 0.0)
        self.assertGreaterEqual(len(report["timeline"]), 2)

        ran = False
        with patch.object(rs.psutil, "virtual_memory", side_effect=OSError("offline")):
            with rs.measure() as report:
                ran = True
        self.assertTrue(ran)
        self.assertIn("offline", report["error"])
        self.assertGreaterEqual(report["elapsed_seconds"], 0.0)
        self.assertNotIn("timeline", report)

        with patch.object(rs.psutil, "virtual_memory",
                          side_effect=[fake_memory(), OSError("late failure")]):
            with rs.measure(sample_interval=None) as report:
                pass
        self.assertIn("late failure", report["error"])
        self.assertGreaterEqual(report["elapsed_seconds"], 0.0)

    def test_invalid_arguments(self):
        for interval in (0, -1, -0.5):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                rs.start_measurement(interval)
        for interval in ("x", True, [1]):
            with self.subTest(interval=interval), self.assertRaises(TypeError):
                rs.start_measurement(interval)
        for start in (None, {}, {"started_at": 1.0}, "start"):
            with self.subTest(start=start), self.assertRaises(ValueError):
                rs.finish_measurement(start)


class IsolationTests(unittest.TestCase):
    def test_no_image_libraries_imported(self):
        code = ("import sys; import services.resource_service; "
                "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('cv2', 'PIL')); "
                "print(loaded); sys.exit(1 if loaded else 0)")
        completed = subprocess.run([sys.executable, "-c", code], cwd=file_manager.PROJECT_ROOT,
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
