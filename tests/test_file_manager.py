"""Run with: python3 -m unittest discover -s tests -v"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
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
        fm.cleanup_temp_files()
        fm.ensure_directories()
        outside = self.root / 'keep.txt'
        outside.write_text('keep')
        saved = fm.THUMBNAIL_DIR / 'keep.jpg'
        saved.write_bytes(b'keep')
        (fm.TEMP_DIR / 'nested').mkdir()
        (fm.TEMP_DIR / 'nested' / 'file').write_text('remove')
        (fm.TEMP_DIR / 'link').symlink_to(self.root, target_is_directory=True)
        fm.cleanup_temp_files()
        fm.cleanup_temp_files()
        self.assertEqual(list(fm.TEMP_DIR.iterdir()), [])
        self.assertEqual(outside.read_text(), 'keep')
        self.assertEqual(saved.read_bytes(), b'keep')
        fm.TEMP_DIR.rmdir()
        fm.TEMP_DIR.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            fm.cleanup_temp_files()
        self.assertTrue(outside.exists())

    def test_directory_failure_and_exclusive_writes(self):
        fm.TEMP_DIR.write_text('blocking file')
        with self.assertRaises(OSError):
            fm.ensure_directories()
        fm.TEMP_DIR.unlink()
        path = fm.generate_thumbnail_path()
        with fm._open_thumbnail_file(path) as stream:
            stream.write(b'original')
        with self.assertRaises(FileExistsError):
            with fm._open_thumbnail_file(path):
                pass
        self.assertEqual(path.read_bytes(), b'original')
        failed = fm.generate_thumbnail_path()
        with self.assertRaisesRegex(OSError, 'write failure'):
            with fm._open_thumbnail_file(failed) as stream:
                stream.write(b'partial')
                raise OSError('write failure')
        self.assertFalse(failed.exists())


if __name__ == '__main__':
    unittest.main()
