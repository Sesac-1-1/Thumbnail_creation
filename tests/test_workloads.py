"""1080p real-codec flow and 4K retained-frame architecture checks."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import weakref

import numpy as np
from PIL import Image

import workflow as wf
from services import file_manager as fm, video_service as vs, resource_service as rs
from test_opencv_integration import _write_test_video
from test_resource_service import snapshot


class WorkloadTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        patcher = patch.multiple(fm, PROJECT_ROOT=root, TEMP_DIR=root / 'temp',
                                OUTPUT_DIR=root / 'output', THUMBNAIL_DIR=root / 'output' / 'thumbnails')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_1080p_end_to_end(self):
        source_job = fm.create_temp_job_dir()
        video = _write_test_video(source_job, (1920, 1080))
        state = {}
        with video.open('rb') as upload:
            wf.prepare_upload(state, upload, video.name, '1080p-upload')
        fm.cleanup_temp_files(source_job)
        wf.process_candidates(state)
        self.assertEqual(state['video_metadata']['resolution'], '1920x1080')
        self.assertEqual(state['actual_sample_count'], 3)
        for item in state['candidate_previews']:
            self.assertNotIn('frame', item)
            with Image.open(BytesIO(item['preview'])) as preview:
                preview.load()
                self.assertEqual(preview.size, (320, 180))
        self.assertTrue(state['resource_metrics']['resource_available'])
        self.assertEqual(state['resource_metrics']['frames_processed'], 3)
        self.assertEqual(state['resource_metrics']['sampling_ratio'], 1)
        path = wf.generate_selected_thumbnail(state, state['candidate_positions'][1], 'PNG')
        with Image.open(path) as image:
            image.load()
            self.assertEqual(image.size, (320, 180))
            self.assertEqual(image.format, 'PNG')
        job = fm.TEMP_DIR / state['job_id']
        wf.reset_session(state)
        self.assertFalse(job.exists())
        self.assertFalse(path.exists())
        self.assertEqual(state, {})
        self.assertEqual(list(fm.TEMP_DIR.iterdir()), [])
        self.assertEqual(list(fm.THUMBNAIL_DIR.iterdir()), [])

    def test_4k_twenty_previews_retain_at_most_one_decoded_frame(self):
        refs = []
        live_counts = []
        captures = []
        class Capture:
            def __init__(self, *_args):
                self.released = False
                captures.append(self)
            def isOpened(self): return True
            def get(self, prop):
                return {vs.cv2.CAP_PROP_FPS: 30, vs.cv2.CAP_PROP_FRAME_COUNT: 20,
                        vs.cv2.CAP_PROP_FRAME_WIDTH: 3840, vs.cv2.CAP_PROP_FRAME_HEIGHT: 2160}[prop]
            def set(self, *_args): return True
            def read(self):
                alive = sum(reference() is not None for reference in refs)
                if alive:
                    raise AssertionError('Previous full-resolution frame still retained at next read')
                frame = np.full((2160, 3840, 3), [10, 20, 240], dtype=np.uint8)
                refs.append(weakref.ref(frame))
                live_counts.append(alive + 1)
                return True, frame
            def release(self): self.released = True
        state = {}
        wf.prepare_upload(state, BytesIO(b'mocked 4K input'), '4k.mp4', '4k-upload')
        with patch.object(vs.cv2, 'VideoCapture', Capture):
            with patch.object(rs, 'get_system_resource', side_effect=lambda: snapshot()):
                wf.process_candidates(state)
            self.assertEqual(state['actual_sample_count'], 20)
            self.assertEqual(len(refs), 20)
            self.assertTrue(all(reference() is None for reference in refs))
            previews = state['candidate_previews']
            retained_bytes = sum(len(item['preview']) for item in previews)
            self.assertLess(retained_bytes, 20 * 320 * 180 * 3)
            for item in previews:
                self.assertEqual(set(item), {'index', 'timestamp_seconds', 'preview'})
                with Image.open(BytesIO(item['preview'])) as preview:
                    self.assertEqual(preview.size, (320, 180))
            saved = wf.generate_selected_thumbnail(state, 10)
            self.assertEqual(len(refs), 21)  # Selection reads exactly one additional frame.
            self.assertTrue(all(reference() is None for reference in refs))
        self.assertEqual(max(live_counts), 1)
        self.assertTrue(all(capture.released for capture in captures))
        print(f'4K memory architecture: {len(previews)} JPEG previews = {retained_bytes} bytes; '
              f'20 raw frames = {20 * 3840 * 2160 * 3} bytes; max live decoded arrays = 1')
        self.assertTrue(saved.exists())
        wf.reset_session(state)
