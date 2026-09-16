"""Synthetic frames only; no video files needed."""
import unittest

import numpy as np
from PIL import Image

from services import thumbnail_service as ts


class ConversionTests(unittest.TestCase):
    def test_bgr_conversion(self):
        frame = np.full((12, 20, 3), [10, 20, 240], dtype=np.uint8)
        original = frame.copy()
        result = ts._frame_to_image(frame)
        self.assertIsInstance(result, Image.Image)
        self.assertEqual(result.mode, 'RGB')
        self.assertEqual(result.getpixel((0, 0)), (240, 20, 10))
        np.testing.assert_array_equal(frame, original)

    def test_multicolor_noncontiguous_frame(self):
        frame = np.array([[[10, 20, 240], [255, 0, 0], [0, 255, 0]],
                          [[0, 0, 255], [60, 80, 100], [1, 2, 3]]], dtype=np.uint8)
        original = frame.copy()
        result = ts._frame_to_image(frame)
        self.assertEqual(result.getpixel((0, 0)), (240, 20, 10))
        self.assertEqual(result.getpixel((1, 0)), (0, 0, 255))
        self.assertEqual(result.getpixel((2, 1)), (3, 2, 1))
        # Separate test of a non-contiguous spatial view, not channel conversion.
        view = frame[:, ::-1]
        result = ts._frame_to_image(view)
        self.assertEqual(result.getpixel((0, 0)), (0, 255, 0))
        self.assertEqual(result.getpixel((2, 1)), (255, 0, 0))
        np.testing.assert_array_equal(frame, original)

    def test_invalid_frames(self):
        for frame, error in [(None, TypeError), ([], TypeError),
                             (np.zeros((0, 4, 3), dtype=np.uint8), ValueError),
                             (np.zeros((4, 4), dtype=np.uint8), ValueError),
                             (np.zeros((4, 4, 4), dtype=np.uint8), ValueError),
                             (np.zeros((4, 4, 3)), TypeError)]:
            with self.subTest(shape=getattr(frame, 'shape', None)):
                with self.assertRaises(error):
                    ts._frame_to_image(frame)


class ResizeTests(unittest.TestCase):
    def test_default_size_and_input_preserved(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        result = ts.create_thumbnail(frame)
        self.assertEqual(result.size, (320, 180))
        self.assertEqual(result.mode, 'RGB')
        self.assertFalse(frame.any())

    def test_padding_and_center_crop(self):
        frame = np.full((100, 100, 3), [0, 0, 255], dtype=np.uint8)
        padded = ts.create_thumbnail(frame)
        self.assertEqual(padded.getpixel((0, 90)), (0, 0, 0))
        self.assertEqual(padded.getpixel((160, 90)), (255, 0, 0))
        cropped = ts.create_thumbnail(frame, crop=True)
        self.assertEqual(cropped.size, (320, 180))
        self.assertEqual(cropped.getpixel((0, 0)), (255, 0, 0))
        stripes = np.zeros((180, 640, 3), dtype=np.uint8)
        stripes[:, :160] = [255, 0, 0]
        stripes[:, 160:480] = [0, 255, 0]
        stripes[:, 480:] = [0, 0, 255]
        cropped = ts.create_thumbnail(stripes, crop=True)
        self.assertEqual(cropped.getpixel((10, 90)), (0, 255, 0))
        self.assertEqual(cropped.getpixel((310, 90)), (0, 255, 0))
        self.assertEqual(ts.create_thumbnail(frame, (64, 64)).size, (64, 64))

    def test_invalid_size_and_crop(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        for size in (None, (0, 1), (-1, 2), (True, 2), (1.5, 2), (2,), 'bad'):
            with self.subTest(size=size), self.assertRaises(ValueError):
                ts.create_thumbnail(frame, size)
        with self.assertRaises(TypeError):
            ts.create_thumbnail(frame, crop='yes')


class SavingTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from services import file_manager
        self.fm = file_manager
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        patches = patch.multiple(file_manager, PROJECT_ROOT=root, TEMP_DIR=root / 'temp',
                                 OUTPUT_DIR=root / 'output',
                                 THUMBNAIL_DIR=root / 'output' / 'thumbnails')
        patches.start()
        self.addCleanup(patches.stop)

    def test_integration_jpeg_and_png(self):
        from pathlib import Path
        from unittest.mock import patch
        frame = np.full((1080, 1920, 3), [0, 0, 255], dtype=np.uint8)
        thumbnail = ts.create_thumbnail(frame)
        with patch.object(self.fm, 'generate_thumbnail_path',
                          wraps=self.fm.generate_thumbnail_path) as generate:
            jpeg = ts.save_thumbnail(thumbnail)
            png = ts.save_thumbnail(thumbnail, image_format='PNG')
        self.assertEqual(generate.call_count, 2)
        self.assertIsInstance(jpeg, Path)
        self.assertNotEqual(jpeg, png)
        for path, fmt in ((jpeg, 'JPEG'), (png, 'PNG')):
            self.assertTrue(path.is_file())
            self.assertEqual(path.parent, self.fm.THUMBNAIL_DIR)
            with Image.open(path) as saved:
                saved.load()
                self.assertEqual(saved.format, fmt)
                self.assertEqual(saved.size, (320, 180))
                red, green, blue = saved.getpixel((100, 90))
                self.assertGreater(red, 250)
                self.assertLess(green, 5)
                self.assertLess(blue, 5)
        self.fm.cleanup_temp_files(self.fm.create_temp_job_dir())
        self.assertTrue(jpeg.exists())
        self.assertTrue(png.exists())
        self.fm.delete_thumbnail(jpeg)
        self.fm.delete_thumbnail(png)
        self.assertFalse(jpeg.exists())
        self.assertFalse(png.exists())

    def test_invalid_save_inputs_and_destination(self):
        thumbnail = ts.create_thumbnail(np.zeros((10, 10, 3), dtype=np.uint8))
        with self.assertRaises(TypeError):
            ts.save_thumbnail(None)
        with self.assertRaises(ValueError):
            ts.save_thumbnail(Image.new('RGBA', (2, 2)))
        for fmt in ('GIF', '../jpg', None):
            with self.assertRaises(ValueError):
                ts.save_thumbnail(thumbnail, image_format=fmt)
        for quality in (0, 96, True, 3.5):
            with self.assertRaises(ValueError):
                ts.save_thumbnail(thumbnail, quality=quality)
        self.fm.OUTPUT_DIR.write_text('blocking destination')
        with self.assertRaises(OSError):
            ts.save_thumbnail(thumbnail)

    def test_failed_encoding_removes_partial_file(self):
        from unittest.mock import patch
        thumbnail = ts.create_thumbnail(np.zeros((10, 10, 3), dtype=np.uint8))
        def fail(stream, **kwargs):
            stream.write(b'partial')
            raise OSError('encoding failed')
        with patch.object(thumbnail, 'save', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'encoding failed'):
                ts.save_thumbnail(thumbnail)
        self.assertEqual(list(self.fm.THUMBNAIL_DIR.iterdir()), [])

    def test_format_specific_parameters(self):
        from unittest.mock import patch
        thumbnail = ts.create_thumbnail(np.zeros((10, 10, 3), dtype=np.uint8))
        for quality in (1, 90, 95):
            with patch.object(thumbnail, 'save', wraps=thumbnail.save) as save:
                path = ts.save_thumbnail(thumbnail, quality=quality)
                self.assertEqual(save.call_args.kwargs,
                                 dict(format='JPEG', quality=quality, optimize=True))
            self.fm.delete_thumbnail(path)
        for compression in (None, 0, 6, 9):
            with patch.object(thumbnail, 'save', wraps=thumbnail.save) as save:
                path = ts.save_thumbnail(thumbnail, 'PNG', quality=-100,
                                         compress_level=compression)
                self.assertNotIn('quality', save.call_args.kwargs)
                if compression is not None:
                    self.assertEqual(save.call_args.kwargs['compress_level'], compression)
            with Image.open(path) as image:
                image.load()
                self.assertEqual(image.format, 'PNG')
            self.fm.delete_thumbnail(path)
        for compression in (-1, 10, True, 1.5, '6'):
            with self.subTest(compression=compression), self.assertRaises(ValueError):
                ts.save_thumbnail(thumbnail, 'PNG', compress_level=compression)
        with self.assertRaisesRegex(ValueError, 'only supported for PNG'):
            ts.save_thumbnail(thumbnail, compress_level=6)
