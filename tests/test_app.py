"""두 페이지 Streamlit 앱 렌더링 테스트 (AppTest, 브라우저 불필요)."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None
try:
    import streamlit
    from streamlit.testing.v1 import AppTest
except ImportError:  # pragma: no cover
    AppTest = None

from services import file_manager
from services import resource_service as rs
from services.resource_service import DEFAULT_SAMPLE_COUNT

FRAME_COUNT = 12
PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = PROJECT_ROOT / "app.py"
RESOURCE_PAGE = "views/resource.py"
SNAPSHOT = {"cpu_percent": 12.3, "memory_percent": 45.6, "memory_used_mb": 7000.0,
            "memory_available_mb": 9000.0, "process_cpu_percent": 0.5, "process_memory_mb": 120.0}


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


def metrics_of(at) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


@unittest.skipUnless(AppTest is not None, "streamlit이 필요합니다")
class AppRenderTests(unittest.TestCase):
    """file_manager 디렉터리를 임시 폴더로 바꾸고, atexit 등록은 가짜로 바꾼다.

    실제로 등록되면 테스트 프로세스가 끝날 때(패치가 풀린 뒤) 진짜 temp/output 폴더를 지우게 된다.
    cache_resource도 테스트마다 비워 서버 수명주기와 공용 결과 저장소가 새로 시작하게 한다.
    """

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patches = patch.multiple(file_manager, PROJECT_ROOT=self.root, TEMP_DIR=self.root / "temp",
                                 OUTPUT_DIR=self.root / "output",
                                 THUMBNAIL_DIR=self.root / "output" / "thumbnails")
        patches.start()
        self.addCleanup(patches.stop)
        register = patch("atexit.register")
        self.atexit_register = register.start()
        self.addCleanup(register.stop)
        streamlit.cache_resource.clear()

    def run_app(self, **kwargs):
        at = AppTest.from_file(str(APP_PATH), default_timeout=60, **kwargs)
        at.run()
        self.assertFalse(at.exception, [str(e) for e in at.exception])
        return at

    def make_video(self) -> Path:
        video = self.root / "source.avi"
        if not write_synthetic_video(video):
            self.skipTest("MJPEG VideoWriter를 사용할 수 없습니다")
        return video

    def test_thumbnail_page_without_upload(self):
        at = self.run_app()
        self.assertEqual(at.title[0].value, "썸네일 생성")
        self.assertIn("업로드하세요", at.info[0].value)
        self.assertEqual(len(at.sidebar.toggle), 1)
        self.assertEqual(len(at.sidebar.selectbox), 1)
        self.assertEqual(len(at.sidebar.radio), 1)
        at.sidebar.toggle[0].set_value(False).run()
        self.assertFalse(at.exception)
        self.assertEqual(at.sidebar.slider[0].value, DEFAULT_SAMPLE_COUNT)

    def test_server_start_cleans_leftovers_once_and_registers_exit_hook(self):
        file_manager.ensure_directories()
        (file_manager.TEMP_DIR / "upload_old.mp4").write_bytes(b"leftover")
        old_thumb = file_manager.THUMBNAIL_DIR / ("thumbnail_" + "c" * 32 + ".jpg")
        old_thumb.write_bytes(b"old")
        at = self.run_app()
        self.assertEqual(list(file_manager.TEMP_DIR.iterdir()), [])
        self.assertFalse(old_thumb.exists())
        self.assertEqual(self.atexit_register.call_count, 1)
        self.assertEqual(self.atexit_register.call_args[0][0].__name__, "cleanup_at_exit")
        # 같은 프로세스에서 다시 실행(새로고침·새 세션·다른 페이지)해도 정리와 등록은 반복되지 않는다
        (file_manager.TEMP_DIR / "upload_new.mp4").write_bytes(b"in use")
        at.run()
        at.switch_page(RESOURCE_PAGE).run()
        AppTest.from_file(str(APP_PATH), default_timeout=60).run()
        self.assertTrue((file_manager.TEMP_DIR / "upload_new.mp4").exists())
        self.assertEqual(self.atexit_register.call_count, 1)

    def test_resource_page_without_result(self):
        with patch.object(rs, "get_system_resource", return_value=SNAPSHOT):
            at = self.run_app()
            at.switch_page(RESOURCE_PAGE).run()
        self.assertFalse(at.exception, [str(e) for e in at.exception])
        self.assertEqual(at.title[0].value, "자원 모니터링")
        self.assertEqual(len(at.toggle), 1)
        metrics = metrics_of(at)
        self.assertEqual(metrics["시스템 CPU"], "12.3%")
        self.assertEqual(metrics["시스템 RAM"], "45.6%")
        self.assertEqual(metrics["지금 처리하면"], "20장")
        self.assertIn("디스크 여유", metrics)
        self.assertIn("아직 처리 기록이 없습니다", at.info[0].value)
        self.assertNotIn("최근 처리 보고서", [h.value for h in at.subheader])

    @unittest.skipUnless(cv2 is not None, "opencv가 필요합니다")
    def test_full_flow_and_cross_page_sharing(self):
        """페이지 1에서 업로드·생성 → 페이지 2(다른 세션)에서 같은 결과를 본다."""
        video = self.make_video()
        data = video.read_bytes()

        class FakeUpload(BytesIO):
            name, size, file_id = "clip.avi", len(data), "upload-1"

        temp_dir, thumbs_dir = file_manager.TEMP_DIR, file_manager.THUMBNAIL_DIR
        with patch.object(streamlit, "file_uploader", lambda *a, **k: FakeUpload(data)):
            at = self.run_app()
            self.assertEqual(at.subheader[0].value, "영상 정보")
            self.assertEqual([b.label for b in at.button], ["썸네일 생성", "결과 지우기"])
            video_path = Path(at.session_state["video_path"])
            self.assertEqual(list(temp_dir.iterdir()), [video_path], "업로드당 임시 파일은 하나")

            at.sidebar.toggle[0].set_value(False).run()
            at.sidebar.slider[0].set_value(4).run()
            at.button[0].click().run()
            self.assertFalse(at.exception, [str(e) for e in at.exception])
            self.assertEqual([h.value for h in at.subheader], ["영상 정보", "썸네일 4장"])
            self.assertIn("샘플 4장 선택, 4장 처리", at.markdown[0].value)
            self.assertEqual(len(at.session_state["result"]["thumbnails"]), 4)
            self.assertEqual(len(list(thumbs_dir.iterdir())), 4)
            self.assertEqual(at.warning, [], [w.value for w in at.warning])

            # 다른 브라우저 창 = 다른 세션. 공용 저장소를 통해 같은 결과를 본다.
            with patch.object(rs, "get_system_resource", return_value=SNAPSHOT):
                other = AppTest.from_file(str(APP_PATH), default_timeout=60)
                other.switch_page(RESOURCE_PAGE).run()
            self.assertFalse(other.exception, [str(e) for e in other.exception])
            self.assertIn("최근 처리 보고서", [h.value for h in other.subheader])
            metrics = metrics_of(other)
            self.assertEqual(metrics["선택된 샘플 수"], "4")
            self.assertEqual(metrics["실제 처리 프레임"], f"4 / {FRAME_COUNT}")
            self.assertEqual(metrics["지금 처리하면"], "12장", "최근 영상의 프레임 수(12)로 상한이 잡힌다")
            self.assertIn("처리 중 피크 / 평균 CPU", metrics)
            self.assertEqual(other.warning, [], [w.value for w in other.warning])

            at.sidebar.checkbox[0].set_value(True).run()
            self.assertIn("옵션이 바뀌었습니다", at.info[0].value)
            at.button[0].click().run()  # 새 옵션으로 다시 생성: 이전 썸네일은 교체된다
            self.assertFalse(at.exception, [str(e) for e in at.exception])
            self.assertEqual(len(list(thumbs_dir.iterdir())), 4)
            self.assertTrue(at.session_state["result"]["options"]["crop"])

            at.button[1].click().run()  # 결과 지우기
            self.assertFalse(at.exception)
            self.assertNotIn("result", at.session_state)
            self.assertEqual(list(thumbs_dir.iterdir()), [])
            self.assertTrue(video_path.is_file(), "결과 지우기는 영상을 남겨 다시 생성할 수 있게 한다")
            with patch.object(rs, "get_system_resource", return_value=SNAPSHOT):
                other.run()
            self.assertIn("아직 처리 기록이 없습니다", other.info[0].value, "지우면 다른 창에서도 사라진다")


if __name__ == "__main__":
    unittest.main()
