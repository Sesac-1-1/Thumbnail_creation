"""Session lifecycle and rerun behavior without a Streamlit runtime."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import workflow as wf
from services import file_manager as fm, video_service as vs, resource_service as rs
from test_video_service import FakeCapture
from test_resource_service import snapshot


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        patches = patch.multiple(fm, PROJECT_ROOT=root, TEMP_DIR=root / 'temp',
                                 OUTPUT_DIR=root / 'output', THUMBNAIL_DIR=root / 'output' / 'thumbnails')
        patches.start()
        self.addCleanup(patches.stop)
        capture = patch.object(vs.cv2, 'VideoCapture', side_effect=lambda *_: FakeCapture())
        capture.start()
        self.addCleanup(capture.stop)
        resources = patch.object(rs, 'get_system_resource', side_effect=lambda: snapshot())
        self.resources = resources.start()
        self.addCleanup(resources.stop)
        self.state = {}
        self.upload = BytesIO(b'tiny synthetic video')

    def prepare(self):
        wf.prepare_upload(self.state, self.upload, 'demo.mp4', 'upload-a')
        wf.process_candidates(self.state)

    def test_single_snapshot_policy_and_rerun(self):
        self.prepare()
        self.assertEqual(self.resources.call_count, 2)  # one before, one after
        self.assertEqual(self.state['requested_sample_count'], 20)
        self.assertEqual(self.state['resource_metrics']['system_cpu_before'], 31)
        with patch.object(vs, 'get_video_info', side_effect=AssertionError('reread metadata')):
            with patch.object(vs, 'iter_frames', side_effect=AssertionError('regenerated candidates')):
                self.assertFalse(wf.prepare_upload(self.state, self.upload, 'demo.mp4', 'upload-a'))
                wf.process_candidates(self.state)
                first = wf.generate_selected_thumbnail(self.state, 0)
                with patch.object(vs, 'extract_frame_at_index', side_effect=AssertionError('reread selection')):
                    self.assertEqual(wf.generate_selected_thumbnail(self.state, 0), first)
                second = wf.generate_selected_thumbnail(self.state, self.state['candidate_positions'][1], 'PNG')
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(self.resources.call_count, 2)
        self.assertNotIn('frame', self.state['candidate_previews'][0])
        self.assertIsInstance(self.state['candidate_previews'][0]['preview'], bytes)
        self.assertNotIn('started_at', self.state)

    def test_new_upload_and_reset_cleanup(self):
        self.prepare()
        old_job = fm.TEMP_DIR / self.state['job_id']
        old_video_id = self.state['video_id']
        old_thumbnail = wf.generate_selected_thumbnail(self.state, 0)
        self.state.update(candidate_frames=['legacy'], selected_frame='legacy')
        other_job = fm.create_temp_job_dir()
        self.assertTrue(wf.prepare_upload(self.state, BytesIO(b'different'), 'demo.mp4', 'upload-b'))
        self.assertFalse(old_job.exists())
        self.assertFalse(old_thumbnail.exists())
        self.assertTrue(other_job.exists())
        self.assertNotEqual(old_video_id, self.state['video_id'])
        for key in ('video_metadata', 'candidate_frames', 'selected_frame', 'candidate_previews',
                    'processing_result', 'saved_thumbnail_path', 'resource_metrics'):
            self.assertNotIn(key, self.state)
        wf.process_candidates(self.state)
        wf.reset_session(self.state)
        wf.reset_session(self.state)
        self.assertFalse(any(key in self.state for key in wf.SESSION_KEYS))
        self.assertTrue(other_job.exists())

    def test_metadata_identity_mismatch(self):
        self.prepare()
        self.state['video_metadata']['video_id'] = 'other-upload'
        with self.assertRaisesRegex(ValueError, 'another upload'):
            wf.process_candidates(self.state)
        with self.assertRaises(ValueError):
            wf.generate_selected_thumbnail(self.state, 0)

    def test_resource_failure_uses_five_without_fake_metrics(self):
        self.resources.side_effect = rs.ResourceServiceError('unavailable')
        self.prepare()
        report = self.state['resource_metrics']
        self.assertEqual(self.resources.call_count, 1)
        self.assertEqual(self.state['requested_sample_count'], rs.FALLBACK_SAMPLE_COUNT)
        self.assertEqual(self.state['actual_sample_count'], 5)
        self.assertFalse(report['resource_available'])
        self.assertNotIn('system_cpu_before', report)
        self.assertNotIn('process_memory_delta_mb', report)
        self.assertTrue(wf.generate_selected_thumbnail(self.state, 0).exists())

    def test_after_measurement_failure_preserves_previews_and_before(self):
        self.resources.side_effect = [snapshot(), rs.ResourceServiceError('unavailable')]
        self.prepare()
        report = self.state['resource_metrics']
        self.assertFalse(report['resource_available'])
        self.assertEqual(report['system_cpu_before'], 31)
        self.assertEqual(self.state['actual_sample_count'], 20)

    def test_encoding_failure_releases_capture(self):
        wf.prepare_upload(self.state, self.upload, 'demo.mp4', 'upload-a')
        capture = FakeCapture()
        with patch.object(vs.cv2, 'VideoCapture', return_value=capture):
            with patch.object(wf.ts, 'create_preview', side_effect=ValueError('invalid frame')):
                with self.assertRaises(ValueError):
                    wf.process_candidates(self.state)
        self.assertTrue(capture.released)
        self.assertNotIn('processing_result', self.state)
        self.assertNotIn('candidate_previews', self.state)

    def test_unknown_count_report(self):
        wf.prepare_upload(self.state, self.upload, 'demo.mp4', 'upload-a')
        info = vs.get_video_info(self.state['video_path'])
        info['frame_count'] = 0
        self.state['video_metadata'] = info
        wf.process_candidates(self.state)
        self.assertIsNone(self.state['resource_metrics']['sampling_ratio'])
        self.assertEqual(self.state['actual_sample_count'], 20)

    def test_failed_upload_does_not_leak_job(self):
        with self.assertRaises(ValueError):
            wf.prepare_upload(self.state, self.upload, '../bad.txt', 'bad-upload')
        self.assertEqual(list(fm.TEMP_DIR.iterdir()), [])
        self.assertEqual(self.state, {})
