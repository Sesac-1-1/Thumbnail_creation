"""Automated Streamlit rendering tests; no browser or video fixture required."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from services import file_manager as fm, video_service as vs, resource_service as rs
from test_video_service import FakeCapture
from test_resource_service import snapshot

APP_PATH = Path(__file__).resolve().parents[1] / 'app.py'


class AppTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        patches = patch.multiple(fm, PROJECT_ROOT=root, TEMP_DIR=root / 'temp',
                                 OUTPUT_DIR=root / 'output', THUMBNAIL_DIR=root / 'output' / 'thumbnails')
        patches.start()
        self.addCleanup(patches.stop)

    def test_empty_app_initializes(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.get("file_uploader")), 1)
        self.assertEqual(len(app.info), 1)

    def test_selection_and_format_reruns_do_not_regenerate(self):
        upload = BytesIO(b'video bytes')
        upload.name = 'demo.mp4'
        with patch('streamlit.file_uploader', return_value=upload):
            with patch.object(vs.cv2, 'VideoCapture', side_effect=lambda *_: FakeCapture()) as capture:
                with patch.object(rs, 'get_system_resource', side_effect=lambda: snapshot()) as metrics:
                    app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
                    self.assertEqual(len(app.exception), 0)
                    self.assertEqual(len(app.error), 0)
                    self.assertEqual(capture.call_count, 3)  # metadata, candidates, selected
                    app.radio[0].set_value(app.session_state['candidate_positions'][1]).run()
                    self.assertEqual(len(app.exception), 0)
                    self.assertEqual(capture.call_count, 4)
                    app.selectbox[0].set_value('PNG').run()
                    self.assertEqual(len(app.exception), 0)
                    self.assertEqual(capture.call_count, 5)
                    app.run()  # download/plain rerun uses saved file
                    self.assertEqual(len(app.exception), 0)
                    self.assertEqual(capture.call_count, 5)
                    self.assertEqual(metrics.call_count, 2)
                    self.assertEqual(app.session_state['saved_thumbnail_path'].suffix, '.png')

    def test_expected_error_has_message_without_traceback(self):
        upload = BytesIO(b'invalid video')
        upload.name = 'bad.mp4'
        with patch('streamlit.file_uploader', return_value=upload):
            with patch.object(vs, 'get_video_info', side_effect=vs.VideoServiceError('private details')):
                app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.error), 1)
        self.assertIn('Unable to read', app.error[0].value)
        self.assertNotIn('private details', app.error[0].value)
