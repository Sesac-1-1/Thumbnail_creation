"""Offline integration using a tiny, temporary MJPEG video (no fixtures)."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from services import file_manager as fm
from services.thumbnail_service import create_thumbnail, save_thumbnail


class OpenCVIntegrationTests(unittest.TestCase):
    def test_video_capture_to_saved_thumbnail(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(fm, PROJECT_ROOT=root, TEMP_DIR=root / 'temp',
                                OUTPUT_DIR=root / 'output',
                                THUMBNAIL_DIR=root / 'output' / 'thumbnails'):
                job = fm.create_temp_job_dir()
                video = job / 'tiny.avi'
                writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'),
                                         5.0, (64, 48))
                try:
                    self.assertTrue(writer.isOpened(), 'MJPEG VideoWriter unavailable')
                    for _ in range(3):
                        writer.write(np.full((48, 64, 3), [10, 20, 240], dtype=np.uint8))
                finally:
                    writer.release()
                capture = cv2.VideoCapture(str(video))
                try:
                    self.assertTrue(capture.isOpened(), 'VideoCapture failed')
                    success, frame = capture.read()
                    self.assertTrue(success)
                    self.assertIsNotNone(frame)
                    self.assertEqual(frame.dtype, np.uint8)
                    self.assertEqual(frame.ndim, 3)
                    self.assertEqual(frame.shape, (48, 64, 3))
                finally:
                    capture.release()
                thumbnail = create_thumbnail(frame)
                saved_path = save_thumbnail(thumbnail)
                self.assertTrue(saved_path.is_file())
                with Image.open(saved_path) as saved:
                    saved.load()
                    self.assertEqual(saved.size, (320, 180))
                    self.assertEqual(saved.format, 'JPEG')
                    # Allow lossy video and JPEG codec rounding.
                    actual = np.array(saved.getpixel((160, 90)), dtype=int)
                    self.assertTrue(np.all(np.abs(actual - [240, 20, 10]) < 15))
                fm.cleanup_temp_files(job)
                self.assertFalse(job.exists())
                self.assertTrue(saved_path.exists())
                fm.delete_thumbnail(saved_path)
                self.assertFalse(saved_path.exists())
