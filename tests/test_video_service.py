"""Tests for video metadata analysis and frame sampling."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from services import video_service as vs


class FakeCapture:
    """Small cv2.VideoCapture substitute so tests need no real video codec."""

    def __init__(self, *_args):
        self.position = 0
        self.released = False

    def isOpened(self):
        return True

    def get(self, property_id):
        values = {
            5: 10.0,    # cv2.CAP_PROP_FPS
            7: 100.0,   # cv2.CAP_PROP_FRAME_COUNT
            3: 640.0,   # cv2.CAP_PROP_FRAME_WIDTH
            4: 360.0,   # cv2.CAP_PROP_FRAME_HEIGHT
        }
        return values.get(property_id, 0.0)

    def set(self, _property_id, value):
        self.position = int(value)
        return True

    def read(self):
        frame = np.full((8, 12, 3), self.position % 255, dtype=np.uint8)
        return True, frame

    def release(self):
        self.released = True


class VideoServiceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.video_path = Path(temp.name) / "sample.mp4"
        self.video_path.write_bytes(b"synthetic video")

    def test_calculate_sample_positions(self):
        self.assertEqual(vs.calculate_sample_positions(100, 5), [0, 25, 50, 74, 99])
        self.assertEqual(vs.calculate_sample_positions(3, 10), [0, 1, 2])
        self.assertEqual(vs.calculate_sample_positions(0, 5), [])
        self.assertEqual(vs.calculate_sample_positions(10, 1), [0])

    def test_invalid_sample_counts(self):
        for value in (0, -1, 1.5, True, "10", None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'sample_count'):
                    vs.calculate_sample_positions(10, value)
                with patch.object(vs.cv2, 'VideoCapture') as capture:
                    with self.assertRaisesRegex(ValueError, 'sample_count'):
                        vs.extract_frames(self.video_path, value)
                    capture.assert_not_called()

    def test_invalid_total_frames(self):
        for value in (-1, 1.5, True, "10", None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'total_frames'):
                vs.calculate_sample_positions(value)

    @patch.object(vs.cv2, "VideoCapture", FakeCapture)
    def test_get_video_info(self):
        info = vs.get_video_info(self.video_path)
        self.assertEqual(info["filename"], "sample.mp4")
        self.assertEqual(info["file_size_bytes"], len(b"synthetic video"))
        self.assertEqual(info["duration_seconds"], 10.0)
        self.assertEqual(info["fps"], 10.0)
        self.assertEqual(info["frame_count"], 100)
        self.assertEqual(info["resolution"], "640x360")

    @patch.object(vs.cv2, "VideoCapture", FakeCapture)
    def test_extract_frames_returns_requested_samples(self):
        frames = vs.extract_frames(self.video_path, sample_count=4)
        self.assertEqual(len(frames), 4)
        self.assertEqual([item["index"] for item in frames], [0, 33, 66, 99])
        self.assertEqual(frames[1]["timestamp_seconds"], 3.3)
        self.assertIsInstance(frames[0]["frame"], np.ndarray)
        self.assertEqual(frames[0]["frame"].shape, (8, 12, 3))

    def test_invalid_video_path(self):
        with self.assertRaises(vs.VideoServiceError):
            vs.get_video_info(self.video_path.parent / "missing.mp4")

    @patch.object(vs.cv2, "VideoCapture")
    def test_unreadable_video(self, video_capture):
        capture = video_capture.return_value
        capture.isOpened.return_value = False
        with self.assertRaises(vs.VideoServiceError):
            vs.get_video_info(self.video_path)

    def test_single_capture_and_metadata_reuse(self):
        capture = FakeCapture()
        with patch.object(vs.cv2, 'VideoCapture', return_value=capture) as factory:
            frames = vs.extract_frames(self.video_path, 3)
            self.assertEqual(len(frames), 3)
            factory.assert_called_once()
            self.assertTrue(capture.released)
        with patch.object(vs.cv2, 'VideoCapture', side_effect=lambda *_: FakeCapture()) as factory:
            info = vs.get_video_info(self.video_path)
            with patch.object(vs, '_read_video_info', side_effect=AssertionError('metadata reread')):
                frames = vs.extract_frames(self.video_path, 3, video_info=info)
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(len(frames), 3)

    def test_partial_seek_and_read_failures(self):
        capture = FakeCapture()
        original_set = capture.set
        def seek(prop, index):
            return False if index == 33 else original_set(prop, index)
        original_read = capture.read
        def read():
            return (False, None) if capture.position == 66 else original_read()
        with patch.object(capture, 'set', side_effect=seek), patch.object(capture, 'read', side_effect=read):
            with patch.object(vs.cv2, 'VideoCapture', return_value=capture):
                frames = vs.extract_frames(self.video_path, 4)
        self.assertEqual(len(frames), 2)
        self.assertEqual([item['index'] for item in frames], [0, 99])
        self.assertTrue(capture.released)

    def test_all_failures_release_capture(self):
        for invalid in (None, np.zeros((2, 2)), np.zeros((2, 2, 4), dtype=np.uint8),
                        np.zeros((0, 2, 3), dtype=np.uint8), np.zeros((2, 2, 3))):
            capture = FakeCapture()
            with self.subTest(shape=getattr(invalid, 'shape', None)):
                with patch.object(capture, 'read', return_value=(True, invalid)):
                    with patch.object(vs.cv2, 'VideoCapture', return_value=capture):
                        with self.assertRaisesRegex(vs.VideoServiceError, 'any sampled frames'):
                            vs.extract_frames(self.video_path, 2)
                self.assertTrue(capture.released)
        capture = FakeCapture()
        with patch.object(capture, 'set', side_effect=vs.cv2.error('seek failed')):
            with patch.object(vs.cv2, 'VideoCapture', return_value=capture):
                with self.assertRaises(vs.VideoServiceError):
                    vs.extract_frames(self.video_path, 2)
        self.assertTrue(capture.released)

    def test_invalid_metadata_and_empty_video(self):
        for info in ({}, [], {'frame_count': True, 'fps': 30},
                     {'frame_count': 10, 'fps': float('nan')},
                     {'frame_count': 10, 'fps': -1}, {'frame_count': 10, 'fps': True}):
            with self.subTest(info=info), self.assertRaises(ValueError):
                vs.extract_frames(self.video_path, video_info=info)
        capture = FakeCapture()
        with patch.object(vs.cv2, 'VideoCapture', return_value=capture):
            with self.assertRaisesRegex(vs.VideoServiceError, 'no readable frames'):
                vs.extract_frames(self.video_path, video_info={'frame_count': 0, 'fps': 30})
        self.assertTrue(capture.released)
        capture = FakeCapture()
        with patch.object(capture, 'get', return_value=float('nan')):
            with patch.object(vs.cv2, 'VideoCapture', return_value=capture):
                with self.assertRaisesRegex(vs.VideoServiceError, 'metadata'):
                    vs.get_video_info(self.video_path)
        self.assertTrue(capture.released)


if __name__ == "__main__":
    unittest.main()
