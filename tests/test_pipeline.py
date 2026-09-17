"""pipeline.py 테스트. 작은 합성 영상을 만들어 쓰므로 실제 영상 파일이 필요 없다."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

import pipeline
from services import file_manager
from services.resource_service import DEFAULT_SAMPLE_COUNT, ResourceServiceError
from services.video_service import VideoServiceError

FRAME_COUNT = 12


def write_synthetic_video(path: Path, size=(64, 48)) -> bool:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 6.0, size)
    if not writer.isOpened():
        return False
    try:
        for i in range(FRAME_COUNT):
            writer.write(np.full((size[1], size[0], 3), (i * 20 % 255, 20, 240), dtype=np.uint8))
    finally:
        writer.release()
    return path.is_file() and path.stat().st_size > 0


@unittest.skipUnless(cv2 is not None, "opencv가 필요합니다")
class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patches = patch.multiple(file_manager, PROJECT_ROOT=self.root, TEMP_DIR=self.root / "temp",
                                 OUTPUT_DIR=self.root / "output",
                                 THUMBNAIL_DIR=self.root / "output" / "thumbnails")
        patches.start()
        self.addCleanup(patches.stop)
        self.video = self.root / "source.avi"
        if not write_synthetic_video(self.video):
            self.skipTest("MJPEG VideoWriter를 사용할 수 없습니다")

    def test_save_upload_streams_to_temp(self):
        data = self.video.read_bytes()
        stream = BytesIO(data)
        stream.seek(3)  # 현재 위치와 무관하게 처음부터 저장해야 한다
        saved = pipeline.save_upload(stream, "clip.avi")
        self.assertEqual(saved.parent, file_manager.TEMP_DIR)
        self.assertEqual(saved.suffix, ".avi")
        self.assertEqual(saved.read_bytes(), data)
        self.assertNotEqual(pipeline.save_upload(BytesIO(data), "clip.avi"), saved)
        self.assertEqual(pipeline.save_upload(BytesIO(data), "noext").suffix, ".mp4")
        with self.assertRaises(ValueError):
            pipeline.save_upload(BytesIO(b""), "empty.mp4")
        self.assertEqual(len(list(file_manager.TEMP_DIR.iterdir())), 3, "빈 업로드는 파일을 남기지 않는다")

    def test_manual_sample_count(self):
        result = pipeline.run_pipeline(self.video, size=(64, 36), crop=True,
                                       image_format="PNG", manual_sample_count=3)
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["reason"], "수동 설정")
        self.assertIsNone(result["before"])
        self.assertEqual(result["filename"], "source.avi")
        self.assertEqual(result["info"]["frame_count"], FRAME_COUNT)
        self.assertEqual([t["index"] for t in result["thumbnails"]], [0, 6, 11])
        for item in result["thumbnails"]:
            self.assertEqual(item["path"].parent, file_manager.THUMBNAIL_DIR)
            self.assertEqual(item["path"].suffix, ".png")
            with Image.open(item["path"]) as image:
                self.assertEqual(image.size, (64, 36))
        self.assertTrue(self.video.is_file(), "파이프라인은 영상 파일을 지우지 않는다")
        self.assertGreaterEqual(result["report"]["elapsed_seconds"], 0.0)
        self.assertEqual(result["options"]["image_format"], "PNG")
        pipeline.delete_thumbnails(result["thumbnails"])
        pipeline.delete_thumbnails(result["thumbnails"])
        self.assertEqual(list(file_manager.THUMBNAIL_DIR.iterdir()), [])

    def test_auto_sample_count_uses_resources(self):
        snapshot = {"cpu_percent": 85.0, "memory_percent": 40.0, "memory_used_mb": 1.0,
                    "memory_available_mb": 8000.0, "process_cpu_percent": 1.0, "process_memory_mb": 1.0}
        with patch.object(pipeline, "get_system_resource", return_value=snapshot):
            result = pipeline.run_pipeline(self.video)
        self.assertEqual(result["sample_count"], 5)
        self.assertEqual(result["before"], snapshot)
        self.assertIn("CPU 85.0%", result["reason"])
        self.assertEqual(len(result["thumbnails"]), 5)
        with Image.open(result["thumbnails"][0]["path"]) as image:
            self.assertEqual(image.size, (320, 180))
            self.assertEqual(image.format, "JPEG")

    def test_resource_failure_falls_back_to_default(self):
        with patch.object(pipeline, "get_system_resource", side_effect=ResourceServiceError("offline")):
            result = pipeline.run_pipeline(self.video)
        self.assertEqual(result["sample_count"], DEFAULT_SAMPLE_COUNT)
        self.assertIn("offline", result["reason"])
        self.assertEqual(len(result["thumbnails"]), DEFAULT_SAMPLE_COUNT)

    def test_cleanup_all_outputs_removes_only_app_files(self):
        file_manager.ensure_directories()
        (file_manager.TEMP_DIR / "upload_old.mp4").write_bytes(b"leftover")
        (file_manager.TEMP_DIR / "nested").mkdir()
        (file_manager.TEMP_DIR / "nested" / "part").write_bytes(b"x")
        thumbs = file_manager.THUMBNAIL_DIR
        (thumbs / ("thumbnail_" + "a" * 32 + ".jpg")).write_bytes(b"old")
        (thumbs / ("thumbnail_" + "b" * 32 + ".PNG")).write_bytes(b"old")
        (thumbs / "keep.txt").write_text("keep")
        (thumbs / "photo.jpg").write_bytes(b"not ours")
        pipeline.cleanup_all_outputs()
        self.assertEqual(list(file_manager.TEMP_DIR.iterdir()), [])
        self.assertEqual(sorted(p.name for p in thumbs.iterdir()), ["keep.txt", "photo.jpg"])
        pipeline.cleanup_all_outputs()  # 비어 있어도, 두 번 불러도 문제없다
        pipeline.cleanup_at_exit()

    def test_unreadable_or_missing_video(self):
        bad = pipeline.save_upload(BytesIO(b"this is not a video"), "bad.mp4")
        with self.assertRaises(VideoServiceError):
            pipeline.run_pipeline(bad)
        with self.assertRaises(VideoServiceError):
            pipeline.run_pipeline(self.root / "missing.mp4")
        self.assertFalse(file_manager.THUMBNAIL_DIR.exists() and any(file_manager.THUMBNAIL_DIR.iterdir()))


if __name__ == "__main__":
    unittest.main()
