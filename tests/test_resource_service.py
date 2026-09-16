"""Deterministic policy, resource snapshot and measurement tests."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from services import resource_service as rs


def snapshot(process_memory=142.0):
    return dict(cpu_percent=31.0, memory_percent=48.5, memory_used_mb=7820.0,
                memory_available_mb=8320.0, process_memory_mb=process_memory)


class ResourceServiceTests(unittest.TestCase):
    def test_thresholds(self):
        for cpu, memory, expected in ((0, 0, 20), (59.99, 59.99, 20), (60, 0, 10),
                                       (0, 60, 10), (79.99, 79.99, 10), (80, 0, 5),
                                       (0, 80, 5), (100, 100, 5)):
            with self.subTest(cpu=cpu, memory=memory):
                self.assertEqual(rs.choose_sample_count(cpu, memory), expected)

    def test_invalid_percentages(self):
        for invalid in (-1, 101, True, '50', None, float('nan'), float('inf')):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    rs.choose_sample_count(invalid, 30)
                with self.assertRaises(ValueError):
                    rs.choose_sample_count(30, invalid)

    def test_system_and_process_snapshot(self):
        memory = SimpleNamespace(percent=48.5, used=7820 * 1024**2,
                                 available=8320 * 1024**2)
        with patch.object(rs.psutil, 'cpu_percent', return_value=31) as cpu:
            with patch.object(rs.psutil, 'virtual_memory', return_value=memory):
                with patch.object(rs.psutil, 'Process') as process:
                    process.return_value.memory_info.return_value.rss = 142 * 1024**2
                    self.assertEqual(rs.get_system_resource(), snapshot())
                    process.assert_called_once_with()
            cpu.assert_called_once_with(interval=0.1)

    def test_metric_failure_explicit(self):
        with patch.object(rs.psutil, 'cpu_percent', side_effect=OSError('unavailable')):
            with self.assertRaises(rs.ResourceServiceError):
                rs.get_system_resource()
        with patch.object(rs.psutil, 'Process', side_effect=rs.psutil.AccessDenied()):
            with patch.object(rs.psutil, 'cpu_percent', return_value=0):
                with self.assertRaises(rs.ResourceServiceError):
                    rs.get_system_resource()

    def test_measurement_uses_process_delta_and_actual_count(self):
        after = dict(snapshot(179), cpu_percent=37, memory_used_mb=9000)
        with patch.object(rs, 'get_system_resource', side_effect=[snapshot(), after]):
            with patch.object(rs.time, 'perf_counter', side_effect=[100, 101.72]):
                start = rs.start_measurement()
                report = rs.finish_measurement(start, requested_sample_count=10,
                                               actual_sample_count=7, total_frames=100)
        self.assertAlmostEqual(report['elapsed_seconds'], 1.72)
        self.assertEqual(report['system_cpu_before'], 31)
        self.assertEqual(report['system_cpu_after'], 37)
        self.assertEqual(report['process_memory_delta_mb'], 37)
        self.assertEqual(report['system_memory_used_after_mb'], 9000)
        self.assertEqual(report['requested_sample_count'], 10)
        self.assertEqual(report['actual_sample_count'], 7)
        self.assertEqual(report['frames_processed'], 7)
        self.assertEqual(report['sampling_ratio'], 0.07)
        self.assertEqual(start['resources'], snapshot())

    def test_snapshot_time_excluded_and_negative_rss_delta(self):
        events = []
        def resources():
            events.append('snapshot')
            return snapshot(120 if len(events) > 1 else 142)
        def clock():
            events.append('clock')
            return 10.0
        with patch.object(rs, 'get_system_resource', side_effect=resources):
            with patch.object(rs.time, 'perf_counter', side_effect=clock):
                report = rs.finish_measurement(rs.start_measurement())
        self.assertEqual(events, ['snapshot', 'clock', 'clock', 'snapshot'])
        self.assertEqual(report['elapsed_seconds'], 0)
        self.assertEqual(report['process_memory_delta_mb'], -22)
        self.assertNotIn('frames_processed', report)

    def test_invalid_measurement_state(self):
        for state in (None, {}, {'started_at': True, 'resources': snapshot()},
                      {'started_at': 0, 'resources': {}},
                      {'started_at': 0, 'resources': dict(snapshot(), cpu_percent=101)}):
            with self.subTest(state=state), self.assertRaises(ValueError):
                rs.finish_measurement(state)
        with patch.object(rs.time, 'perf_counter', return_value=1):
            with self.assertRaises(ValueError):
                rs.finish_measurement({'started_at': 10, 'resources': snapshot()})

    def test_invalid_counts(self):
        start = {'started_at': 0, 'resources': snapshot()}
        for requested, actual, total in ((10, None, 100), (True, 1, 100), (0, 0, 100),
                                         (10, -1, 100), (10, 11, 100), (10, 8, 7),
                                         (10, 1, 0), (10, 1.5, 100)):
            with self.subTest(counts=(requested, actual, total)), self.assertRaises(ValueError):
                rs.finish_measurement(start, requested_sample_count=requested,
                                      actual_sample_count=actual, total_frames=total)
