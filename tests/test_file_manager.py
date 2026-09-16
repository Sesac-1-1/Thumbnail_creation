"""Run with: python3 -m unittest discover -s tests -v"""
from pathlib import Path
from io import BytesIO
from tempfile import TemporaryDirectory
import unittest
import os
from unittest.mock import patch

from services import file_manager as fm


class FileManagerTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        paths = dict(PROJECT_ROOT=self.root, TEMP_DIR=self.root / "temp",
                     OUTPUT_DIR=self.root / "output",
                     THUMBNAIL_DIR=self.root / "output" / "thumbnails")
        patches = patch.multiple(fm, **paths)
        patches.start()
        self.addCleanup(patches.stop)

    def _symlink(self, link, target, *, target_is_directory=False):
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows requires symlink privilege for this test')
            raise

    def test_directories_and_paths(self):
        fm.ensure_directories()
        fm.ensure_directories()
        for directory in (fm.TEMP_DIR, fm.OUTPUT_DIR, fm.THUMBNAIL_DIR):
            self.assertTrue(directory.is_dir())
        paths = {fm.generate_thumbnail_path() for _ in range(1000)}
        self.assertEqual(len(paths), 1000)
        self.assertTrue(all(p.parent == fm.THUMBNAIL_DIR and p.suffix == '.jpg' for p in paths))
        for extension in ('../bad', '', 'gif'):
            with self.assertRaises(ValueError):
                fm.generate_thumbnail_path(extension)

    def test_cleanup_boundary(self):
        job_a = fm.create_temp_job_dir()
        job_b = fm.create_temp_job_dir()
        self.assertNotEqual(job_a, job_b)
        self.assertEqual(fm.create_temp_job_dir(job_b.name), job_b)
        (job_b / 'keep.txt').write_text('keep')
        outside = self.root / 'keep.txt'
        outside.write_text('keep')
        saved = fm.THUMBNAIL_DIR / 'keep.jpg'
        saved.write_bytes(b'keep')
        (job_a / 'nested').mkdir()
        (job_a / 'nested' / 'file').write_text('remove')
        self._symlink(job_a / 'link', self.root, target_is_directory=True)
        fm.cleanup_temp_files(job_a)
        fm.cleanup_temp_files(job_a)
        self.assertFalse(job_a.exists())
        self.assertEqual((job_b / 'keep.txt').read_text(), 'keep')
        self.assertEqual(outside.read_text(), 'keep')
        self.assertEqual(saved.read_bytes(), b'keep')

    def test_reject_invalid_job_paths(self):
        job = fm.create_temp_job_dir()
        invalid = [fm.TEMP_DIR, self.root, fm.THUMBNAIL_DIR,
                   Path('temp') / job.name, job / 'nested',
                   fm.TEMP_DIR / '..' / 'temp' / job.name]
        for path in invalid:
            with self.subTest(path=path), self.assertRaises(ValueError):
                fm.cleanup_temp_files(path)
        for job_id in ('../escape', '', '.', 'a/b', 'a\\b', 'CON', 12):
            with self.subTest(job_id=job_id), self.assertRaises(ValueError):
                fm.create_temp_job_dir(job_id)
        link = fm.TEMP_DIR / ('a' * 32)
        self._symlink(link, job, target_is_directory=True)
        with self.assertRaises(ValueError):
            fm.cleanup_temp_files(link)
        with self.assertRaises(ValueError):
            fm.create_temp_job_dir(link.name)
        self.assertTrue(job.exists())

    def test_symlink_temp_root(self):
        self._symlink(fm.TEMP_DIR, self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            fm.create_temp_job_dir()
        with self.assertRaises(ValueError):
            fm.cleanup_temp_files(fm.TEMP_DIR / ('a' * 32))

    def test_directory_failure_and_exclusive_writes(self):
        fm.TEMP_DIR.write_text('blocking file')
        with self.assertRaises(OSError):
            fm.ensure_directories()
        fm.TEMP_DIR.unlink()
        path = fm.generate_thumbnail_path()
        with fm.open_thumbnail_file(path) as stream:
            stream.write(b'original')
        with self.assertRaises(FileExistsError):
            with fm.open_thumbnail_file(path):
                pass
        self.assertEqual(path.read_bytes(), b'original')
        failed = fm.generate_thumbnail_path()
        with self.assertRaisesRegex(OSError, 'write failure'):
            with fm.open_thumbnail_file(failed) as stream:
                stream.write(b'partial')
                raise OSError('write failure')
        self.assertFalse(failed.exists())

    def test_thumbnail_lifecycle_boundaries(self):
        path = fm.generate_thumbnail_path()
        with fm.open_thumbnail_file(path) as stream:
            stream.write(b'owned image')
        fm.delete_thumbnail(path)
        fm.delete_thumbnail(path)
        self.assertFalse(path.exists())
        outside = self.root / 'outside.jpg'
        outside.write_bytes(b'keep')
        link = fm.THUMBNAIL_DIR / 'link.jpg'
        self._symlink(link, outside)
        directory = fm.THUMBNAIL_DIR / 'directory.jpg'
        directory.mkdir()
        text = fm.THUMBNAIL_DIR / 'keep.txt'
        text.write_text('keep')
        invalid = [outside, link, directory, text, fm.THUMBNAIL_DIR,
                   Path('output/thumbnails/file.jpg'),
                   fm.THUMBNAIL_DIR / '..' / 'thumbnails' / 'file.jpg']
        for target in invalid:
            with self.subTest(target=target), self.assertRaises(ValueError):
                fm.delete_thumbnail(target)
        self.assertEqual(outside.read_bytes(), b'keep')
        self.assertEqual(text.read_text(), 'keep')
        for target in [outside, link, Path('relative.jpg'),
                       fm.THUMBNAIL_DIR / '..' / 'escape.jpg']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                with fm.open_thumbnail_file(target):
                    pass
        self.assertEqual(outside.read_bytes(), b'keep')

    def test_output_root_symlink(self):
        self._symlink(fm.OUTPUT_DIR, self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            fm.delete_thumbnail(fm.THUMBNAIL_DIR / 'file.jpg')
        with self.assertRaises(ValueError):
            fm.generate_thumbnail_path()

    def test_stale_cleanup_age_and_names(self):
        self.assertEqual(fm.cleanup_stale_thumbnails(100), 0)
        fm.ensure_directories()
        old = [fm.generate_thumbnail_path(ext) for ext in ('jpg', 'jpeg', 'png')]
        fresh = fm.generate_thumbnail_path()
        boundary = fm.generate_thumbnail_path()
        unrelated = fm.THUMBNAIL_DIR / 'holiday.jpg'
        wrong_extension = fm.THUMBNAIL_DIR / ('thumbnail_' + 'c' * 32 + '.txt')
        outside = self.root / old[0].name
        nested = fm.THUMBNAIL_DIR / 'nested'
        nested.mkdir()
        nested_file = nested / old[0].name
        directory = fm.generate_thumbnail_path()
        directory.mkdir()
        for path in [*old, fresh, boundary, unrelated, wrong_extension, outside, nested_file]:
            path.write_bytes(b'keep or remove by age')
            os.utime(path, (800, 800))
        os.utime(fresh, (950, 950))
        os.utime(boundary, (900, 900))
        os.utime(directory, (800, 800))
        with patch.object(fm.time, 'time', return_value=1000):
            self.assertEqual(fm.cleanup_stale_thumbnails(100), 3)
            self.assertEqual(fm.cleanup_stale_thumbnails(100), 0)
        self.assertTrue(all(not path.exists() for path in old))
        self.assertTrue(all(path.exists() for path in
                            (fresh, boundary, unrelated, wrong_extension, outside, nested_file, directory)))

    def test_stale_cleanup_skips_symlinks(self):
        fm.ensure_directories()
        outside = self.root / 'outside.jpg'
        outside.write_bytes(b'keep')
        os.utime(outside, (1, 1))
        link = fm.generate_thumbnail_path()
        self._symlink(link, outside)
        dangling = fm.generate_thumbnail_path()
        self._symlink(dangling, self.root / 'missing.jpg')
        with patch.object(fm.time, 'time', return_value=1000):
            self.assertEqual(fm.cleanup_stale_thumbnails(100), 0)
        self.assertTrue(link.is_symlink())
        self.assertTrue(dangling.is_symlink())
        self.assertEqual(outside.read_bytes(), b'keep')

    def test_stale_cleanup_invalid_age_and_root(self):
        for value in (0, -1, True, 1.5, '100', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                fm.cleanup_stale_thumbnails(value)
        fm.OUTPUT_DIR.mkdir()
        self._symlink(fm.THUMBNAIL_DIR, self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            fm.cleanup_stale_thumbnails(100)

    def test_save_upload_stream_and_no_overwrite(self):
        job = fm.create_temp_job_dir()
        upload = BytesIO(b'video content')
        upload.seek(3)
        path = fm.save_uploaded_video(job, upload, '.mp4')
        self.assertEqual(path.parent, job)
        self.assertEqual(path.read_bytes(), b'video content')
        self.assertEqual(upload.tell(), 3)
        with self.assertRaises(FileExistsError):
            fm.save_uploaded_video(job, BytesIO(b'replace'), '.mp4')
        self.assertEqual(path.read_bytes(), b'video content')
        with self.assertRaises(ValueError):
            fm.save_uploaded_video(self.root, upload)
        with self.assertRaises(ValueError):
            fm.save_uploaded_video(job, upload, '/../escape.mp4')

    def test_partial_upload_removed(self):
        job = fm.create_temp_job_dir()
        upload = BytesIO(b'video content')
        with patch.object(upload, 'read', side_effect=[b'partial', OSError('read failed')]):
            with self.assertRaisesRegex(OSError, 'read failed'):
                fm.save_uploaded_video(job, upload)
        self.assertEqual(list(job.iterdir()), [])
        self.assertEqual(upload.tell(), 0)


if __name__ == '__main__':
    unittest.main()
