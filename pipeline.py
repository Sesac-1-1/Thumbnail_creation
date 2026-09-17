"""썸네일 생성 파이프라인. Streamlit에 의존하지 않으므로 브라우저 없이 테스트할 수 있다.

업로드 저장 → 메타데이터 → 샘플 수 결정 → [측정] 프레임 추출 + 썸네일 저장 순서로
services/ 모듈을 호출한다. 화면은 views/ 아래 페이지가 담당한다.
"""
from pathlib import Path
import shutil
import sys
from typing import Any, BinaryIO
from uuid import uuid4

from services import file_manager
from services.resource_service import (
    DEFAULT_SAMPLE_COUNT,
    ResourceServiceError,
    choose_sample_count,
    get_system_resource,
    measure,
)
from services.thumbnail_service import create_thumbnail, save_thumbnail
from services.video_service import extract_frames, get_video_info

THUMBNAIL_SUFFIXES = {".jpg", ".jpeg", ".png"}


def save_upload(upload: BinaryIO, filename: str) -> Path:
    """업로드 스트림을 TEMP_DIR에 흘려 써서 저장하고 경로를 돌려준다.

    getvalue()로 바이트를 통째로 꺼내지 않으므로 메모리에 두 번째 복사본이 생기지 않는다.
    임시 파일의 수명은 호출자(세션)가 관리한다.
    """
    file_manager.ensure_directories()
    suffix = Path(filename).suffix.lower() or ".mp4"
    path = file_manager.TEMP_DIR / f"upload_{uuid4().hex}{suffix}"
    upload.seek(0)
    try:
        with path.open("xb") as target:
            shutil.copyfileobj(upload, target, length=8 * 1024 * 1024)
        if path.stat().st_size == 0:
            raise ValueError("업로드된 영상이 비어 있습니다")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def decide_sample_count(info: dict[str, Any], manual_sample_count: int | None = None) -> dict[str, Any]:
    """샘플 프레임 수와 그 근거를 정한다. 자원 측정이 실패해도 기본값으로 진행한다."""
    if manual_sample_count is not None:
        return {"sample_count": manual_sample_count, "before": None, "reason": "수동 설정"}
    try:
        before = get_system_resource()
    except ResourceServiceError as error:
        return {"sample_count": DEFAULT_SAMPLE_COUNT, "before": None,
                "reason": f"자원 측정 실패로 기본값 {DEFAULT_SAMPLE_COUNT}개 사용 ({error})"}
    sample_count = choose_sample_count(
        before["cpu_percent"], before["memory_percent"],
        video_info=info, memory_available_mb=before["memory_available_mb"],
    )
    reason = (f"CPU {before['cpu_percent']:.1f}%, RAM {before['memory_percent']:.1f}%, "
              f"가용 메모리 {before['memory_available_mb']:,.0f} MB, 해상도 {info['resolution']}, "
              f"전체 {info['frame_count']}프레임")
    return {"sample_count": sample_count, "before": before, "reason": reason}


def delete_thumbnails(thumbnails: list[dict[str, Any]]) -> None:
    """이 앱이 저장한 썸네일 파일을 지운다. 이미 없는 파일은 무시한다."""
    for item in thumbnails:
        Path(item["path"]).unlink(missing_ok=True)


def cleanup_all_outputs() -> None:
    """temp의 임시 영상과 output/thumbnails의 썸네일을 모두 지운다.

    앱 시작 시(지난 비정상 종료가 남긴 것)와 정상 종료 시 호출된다.
    썸네일은 file_manager가 만든 이름(thumbnail_*.jpg/jpeg/png)만 지우고,
    다른 파일이나 심볼릭 링크는 건드리지 않는다.
    """
    file_manager.cleanup_temp_files()
    directory = file_manager.THUMBNAIL_DIR
    if not directory.is_dir():
        return
    for path in directory.iterdir():
        if (path.name.startswith("thumbnail_") and path.suffix.lower() in THUMBNAIL_SUFFIXES
                and path.is_file() and not path.is_symlink()):
            path.unlink(missing_ok=True)


def cleanup_at_exit() -> None:
    """atexit용. 종료 중이라 화면이 없으므로 실패는 stderr에만 남긴다."""
    try:
        cleanup_all_outputs()
    except (OSError, ValueError) as error:
        print(f"[app] 종료 시 파일 정리 실패: {error}", file=sys.stderr)


def run_pipeline(
    video_path: Path,
    *,
    size: tuple[int, int] = (320, 180),
    crop: bool = False,
    image_format: str = "JPEG",
    manual_sample_count: int | None = None,
) -> dict[str, Any]:
    """저장된 영상 → 메타데이터 → 샘플 수 결정 → [측정] 프레임 추출 + 썸네일 저장.

    측정 구간(measure)은 프레임 추출과 썸네일 생성·저장을 모두 포함한다.
    영상 파일은 지우지 않는다. 같은 영상으로 옵션만 바꿔 다시 생성할 수 있어야 하기 때문이다.
    """
    video_path = Path(video_path)
    thumbnails: list[dict[str, Any]] = []
    try:
        info = get_video_info(video_path)
        decision = decide_sample_count(info, manual_sample_count)
        with measure() as report:
            frames = extract_frames(video_path, sample_count=decision["sample_count"])
            for item in frames:
                thumbnail = create_thumbnail(item["frame"], size, crop)
                thumbnails.append({
                    "index": item["index"],
                    "timestamp_seconds": item["timestamp_seconds"],
                    "path": save_thumbnail(thumbnail, image_format),
                })
    except BaseException:
        delete_thumbnails(thumbnails)  # 중간에 실패하면 반쪽 결과를 남기지 않는다
        raise
    return {
        "filename": info["filename"],
        "video_path": str(video_path),
        "info": info,
        **decision,
        "report": report,
        "thumbnails": thumbnails,
        "options": {"size": size, "crop": crop, "image_format": image_format,
                    "manual_sample_count": manual_sample_count},
    }
