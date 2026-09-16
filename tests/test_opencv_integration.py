"""Offline integration using a temporary video, with explicit codec fallback."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock

import cv2
import numpy as np
from PIL import Image

from services import file_manager as fm
from services import video_service as vs
from services import resource_service as rs
from services.thumbnail_service import create_thumbnail, save_thumbnail


def _write_test_video(job: Path, size: tuple[int, int] = (64, 48)) -> Path:
    """Try MJPEG/AVI then mp4v/MP4; skip only if neither writer opens."""
    for codec, extension in (('MJPG', 'avi'), ('mp4v', 'mp4')):
        video = job / f'tiny.{extension}'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*codec), 5.0, size)
        try:
            if not writer.isOpened():
                continue
            for _ in range(3):
                writer.write(np.full((size[1], size[0], 3), [10, 20, 240], dtype=np.uint8))
            return video
        finally:
            writer.release()
    raise unittest.SkipTest('Codec unavailable: neither MJPG/AVI nor mp4v/MP4 encoder opens')


class CodecSelectionTests(unittest.TestCase):
    def test_fallback_codec(self):
        unavailable, fallback = MagicMock(), MagicMock()
        unavailable.isOpened.return_value = False
        fallback.isOpened.return_value = True
        with TemporaryDirectory() as directory:
            with patch.object(cv2, 'VideoWriter', side_effect=[unavailable, fallback]) as factory:
                path = _write_test_video(Path(directory))
        self.assertEqual(path.suffix, '.mp4')
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(fallback.write.call_count, 3)
        unavailable.release.assert_called_once()
        fallback.release.assert_called_once()

    def test_missing_codecs_have_explicit_skip_reason(self):
        writer = MagicMock()
        writer.isOpened.return_value = False
        with TemporaryDirectory() as directory:
            with patch.object(cv2, 'VideoWriter', return_value=writer):
                with self.assertRaisesRegex(unittest.SkipTest, 'Codec unavailable'):
                    _write_test_video(Path(directory))
        self.assertEqual(writer.release.call_count, 2)

    def test_write_errors_are_not_hidden_as_codec_skips(self):
        writer = MagicMock()
        writer.isOpened.return_value = True
        writer.write.side_effect = cv2.error('write failed')
        with TemporaryDirectory() as directory:
            with patch.object(cv2, 'VideoWriter', return_value=writer):
                with self.assertRaises(cv2.error):
                    _write_test_video(Path(directory))
        writer.release.assert_called_once()


class OpenCVIntegrationTests(unittest.TestCase):
    def test_video_capture_to_saved_thumbnail(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(fm, PROJECT_ROOT=root, TEMP_DIR=root / 'temp',
                                OUTPUT_DIR=root / 'output',
                                THUMBNAIL_DIR=root / 'output' / 'thumbnails'):
                job = fm.create_temp_job_dir()
                video = _write_test_video(job)
                with patch.object(vs.cv2, 'VideoCapture', wraps=cv2.VideoCapture) as capture:
                    info = vs.get_video_info(video)
                    self.assertEqual(info['frame_count'], 3)
                    measurement = rs.start_measurement()
                    before = measurement['resources']
                    requested_sample_count = rs.choose_sample_count(
                        before['cpu_percent'], before['memory_percent'])
                    frames = vs.extract_frames(video, requested_sample_count, video_info=info)
                    self.assertEqual(capture.call_count, 2)
                self.assertEqual(len(frames), 3)
                for item in frames:
                    frame = item['frame']
                    self.assertIsInstance(frame, np.ndarray)
                    self.assertEqual(frame.dtype, np.uint8)
                    self.assertEqual(frame.ndim, 3)
                    self.assertEqual(frame.shape, (48, 64, 3))
                frame = frames[0]['frame']
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
                report = rs.finish_measurement(
                    measurement, requested_sample_count=requested_sample_count,
                    actual_sample_count=len(frames), total_frames=info['frame_count'])
                self.assertEqual(report['requested_sample_count'], requested_sample_count)
                self.assertEqual(report['actual_sample_count'], 3)
                self.assertEqual(report['frames_processed'], 3)
                self.assertEqual(report['sampling_ratio'], 1.0)
                self.assertGreaterEqual(report['elapsed_seconds'], 0)
                self.assertGreater(report['process_memory_before_mb'], 0)
                self.assertGreater(report['process_memory_after_mb'], 0)
                fm.cleanup_temp_files(job)
                self.assertFalse(job.exists())
                self.assertTrue(saved_path.exists())
                fm.delete_thumbnail(saved_path)
                self.assertFalse(saved_path.exists())
