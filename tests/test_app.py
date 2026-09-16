"""app.py 파이프라인·렌더링 테스트. 작은 합성 영상을 만들어 쓰므로 실제 영상 파일이 필요 없다."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

try:
    import cv2
except ImportError:  # pragma: no cover - 환경에 따라 다름
    cv2 = None
try:
    from streamlit.testing.v1 import AppTest
except ImportError:  # pragma: no cover
    AppTest = None

from services import file_manager
from services.resource_service import DEFAULT_SAMPLE_COUNT, ResourceServiceError
from services.video_service import VideoServiceError

FRAME_COUNT = 12
APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


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


class ManagedDirsMixin:
    """file_manager 디렉터리를 임시 폴더로 바꾼다 (다른 테스트 파일과 같은 방식)."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patches = patch.multiple(file_manager, PROJECT_ROOT=self.root, TEMP_DIR=self.root / "temp",
                                 OUTPUT_DIR=self.root / "output",
                                 THUMBNAIL_DIR=self.root / "output" / "thumbnails")
        patches.start()
        self.addCleanup(patches.stop)

    def make_video(self) -> Path:
        video = self.root / "source.avi"
        if not write_synthetic_video(video):
            self.skipTest("MJPEG VideoWriter를 사용할 수 없습니다")
        return video


@unittest.skipUnless(AppTest is not None and cv2 is not None, "streamlit과 opencv가 필요합니다")
class PipelineTests(ManagedDirsMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        import app
        self.app = app
        self.video = self.make_video()

    def test_save_upload_streams_to_temp(self):
        data = self.video.read_bytes()
        stream = BytesIO(data)
        stream.seek(3)  # 현재 위치와 무관하게 처음부터 저장해야 한다
        saved = self.app.save_upload(stream, "clip.avi")
        self.assertEqual(saved.parent, file_manager.TEMP_DIR)
        self.assertEqual(saved.suffix, ".avi")
        self.assertEqual(saved.read_bytes(), data)
        self.assertNotEqual(self.app.save_upload(BytesIO(data), "clip.avi"), saved)
        self.assertEqual(self.app.save_upload(BytesIO(data), "noext").suffix, ".mp4")
        with self.assertRaises(ValueError):
            self.app.save_upload(BytesIO(b""), "empty.mp4")
        self.assertEqual(len(list(file_manager.TEMP_DIR.iterdir())), 3, "빈 업로드는 파일을 남기지 않는다")

    def test_manual_sample_count(self):
        result = self.app.run_pipeline(self.video, size=(64, 36), crop=True,
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
        with zipfile.ZipFile(BytesIO(self.app.zip_thumbnails(result["thumbnails"]))) as archive:
            self.assertEqual(sorted(archive.namelist()),
                             ["frame_000000.png", "frame_000006.png", "frame_000011.png"])
        self.app.delete_thumbnails(result["thumbnails"])
        self.app.delete_thumbnails(result["thumbnails"])
        self.assertEqual(list(file_manager.THUMBNAIL_DIR.iterdir()), [])

    def test_auto_sample_count_uses_resources(self):
        snapshot = {"cpu_percent": 85.0, "memory_percent": 40.0, "memory_used_mb": 1.0,
                    "memory_available_mb": 8000.0, "process_cpu_percent": 1.0, "process_memory_mb": 1.0}
        with patch.object(self.app, "get_system_resource", return_value=snapshot):
            result = self.app.run_pipeline(self.video)
        self.assertEqual(result["sample_count"], 5)
        self.assertEqual(result["before"], snapshot)
        self.assertIn("CPU 85.0%", result["reason"])
        self.assertEqual(len(result["thumbnails"]), 5)
        with Image.open(result["thumbnails"][0]["path"]) as image:
            self.assertEqual(image.size, (320, 180))
            self.assertEqual(image.format, "JPEG")

    def test_resource_failure_falls_back_to_default(self):
        with patch.object(self.app, "get_system_resource", side_effect=ResourceServiceError("offline")):
            result = self.app.run_pipeline(self.video)
        self.assertEqual(result["sample_count"], DEFAULT_SAMPLE_COUNT)
        self.assertIn("offline", result["reason"])
        self.assertEqual(len(result["thumbnails"]), DEFAULT_SAMPLE_COUNT)

    def test_unreadable_or_missing_video(self):
        bad = self.app.save_upload(BytesIO(b"this is not a video"), "bad.mp4")
        with self.assertRaises(VideoServiceError):
            self.app.run_pipeline(bad)
        with self.assertRaises(VideoServiceError):
            self.app.run_pipeline(self.root / "missing.mp4")
        self.assertEqual(list(file_manager.THUMBNAIL_DIR.iterdir()) if file_manager.THUMBNAIL_DIR.exists() else [], [])


@unittest.skipUnless(AppTest is not None, "streamlit이 필요합니다")
class AppRenderTests(ManagedDirsMixin, unittest.TestCase):
    def test_renders_without_upload(self):
        at = AppTest.from_file(str(APP_PATH), default_timeout=30)
        at.run()
        self.assertFalse(at.exception, [str(e) for e in at.exception])
        self.assertEqual(at.title[0].value, "자원 인식 영상 썸네일 생성기")
        self.assertIn("업로드하세요", at.info[0].value)
        self.assertEqual(len(at.sidebar.toggle), 1)
        self.assertEqual(len(at.sidebar.selectbox), 1)
        self.assertEqual(len(at.sidebar.radio), 1)

    def test_manual_mode_shows_slider(self):
        at = AppTest.from_file(str(APP_PATH), default_timeout=30)
        at.run()
        at.sidebar.toggle[0].set_value(False).run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.sidebar.slider), 1)
        self.assertEqual(at.sidebar.slider[0].value, DEFAULT_SAMPLE_COUNT)

    def test_session_start_cleans_leftover_temp(self):
        file_manager.ensure_directories()
        (file_manager.TEMP_DIR / "upload_old.mp4").write_bytes(b"leftover")
        at = AppTest.from_file(str(APP_PATH), default_timeout=30)
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(list(file_manager.TEMP_DIR.iterdir()), [])

    @unittest.skipUnless(cv2 is not None, "opencv가 필요합니다")
    def test_full_flow_renders_with_upload(self):
        """업로더만 가짜 업로드 객체로 바꿔 업로드 → 생성 → 결과 화면 전체를 렌더링한다."""
        video = self.make_video()
        data = video.read_bytes()

        class FakeUpload(BytesIO):
            name, size, file_id = "clip.avi", len(data), "upload-1"

        import streamlit
        temp_dir = file_manager.TEMP_DIR
        thumbs_dir = file_manager.THUMBNAIL_DIR
        with patch.object(streamlit, "file_uploader", lambda *a, **k: FakeUpload(data)):
            at = AppTest.from_file(str(APP_PATH), default_timeout=60)
            at.run()
            self.assertFalse(at.exception, [str(e) for e in at.exception])
            self.assertEqual(at.subheader[0].value, "영상 정보")
            self.assertEqual(len(at.table), 1)
            self.assertEqual([b.label for b in at.button], ["썸네일 생성", "결과 지우기"])
            video_path = Path(at.session_state["video_path"])
            self.assertEqual(video_path.parent, temp_dir)
            self.assertEqual(len(list(temp_dir.iterdir())), 1, "업로드당 임시 파일은 하나")

            at.sidebar.toggle[0].set_value(False).run()
            at.sidebar.slider[0].set_value(4).run()
            at.button[0].click().run()
            self.assertFalse(at.exception, [str(e) for e in at.exception])
            self.assertEqual([h.value for h in at.subheader],
                             ["영상 정보", "샘플링 결정", "자원 사용", "썸네일 4장"])
            metrics = {m.label: m.value for m in at.metric}
            self.assertEqual(metrics["선택된 샘플 수"], "4")
            self.assertEqual(metrics["실제 처리 프레임"], f"4 / {FRAME_COUNT}")
            self.assertIn("처리 시간", metrics)
            self.assertIn("처리 중 피크 CPU", metrics)
            self.assertEqual(len(at.session_state["result"]["thumbnails"]), 4)
            self.assertEqual(len(list(thumbs_dir.iterdir())), 4)
            self.assertEqual(list(temp_dir.iterdir()), [video_path], "생성 후에도 영상은 재사용을 위해 유지")
            self.assertEqual(at.warning, [], [w.value for w in at.warning])

            at.sidebar.checkbox[0].set_value(True).run()
            self.assertIn("옵션이 바뀌었습니다", at.info[0].value)

            at.button[0].click().run()  # 새 옵션으로 다시 생성: 이전 썸네일은 교체된다
            self.assertFalse(at.exception, [str(e) for e in at.exception])
            self.assertEqual(len(list(thumbs_dir.iterdir())), 4)
            self.assertTrue(at.session_state["result"]["options"]["crop"])

            at.button[1].click().run()
            self.assertFalse(at.exception)
            self.assertNotIn("result", at.session_state)
            self.assertEqual(list(thumbs_dir.iterdir()), [])
            self.assertTrue(video_path.is_file(), "결과 지우기는 영상을 남겨 다시 생성할 수 있게 한다")


if __name__ == "__main__":
    unittest.main()
