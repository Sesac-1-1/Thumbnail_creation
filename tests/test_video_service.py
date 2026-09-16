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
        self.assertEqual(vs.calculate_sample_positions(10, 0), [])

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


if __name__ == "__main__":
    unittest.main()
