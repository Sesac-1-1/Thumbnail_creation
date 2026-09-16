"""자원 인식 영상 썸네일 생성기 (Streamlit UI).

프로젝트 루트에서 실행:  streamlit run app.py

services/ 아래 모듈은 Streamlit을 모른다. 이 파일은 그 모듈들을 순서대로 호출하고
결과를 화면에 그리는 일만 한다. run_pipeline()은 Streamlit에 의존하지 않으므로
브라우저 없이 테스트할 수 있다.
"""
import atexit
from io import BytesIO
from pathlib import Path
import shutil
import sys
from typing import Any, BinaryIO
from uuid import uuid4
import zipfile

import pandas as pd
import streamlit as st

from services import file_manager
from services.resource_service import (
    DEFAULT_SAMPLE_COUNT,
    MAX_SAMPLE_COUNT,
    MIN_SAMPLE_COUNT,
    ResourceServiceError,
    choose_sample_count,
    get_system_resource,
    measure,
)
from services.thumbnail_service import create_thumbnail, save_thumbnail
from services.video_service import VideoServiceError, extract_frames, get_video_info

VIDEO_EXTENSIONS = ["mp4", "mov", "avi", "mkv", "webm"]
SIZE_PRESETS = {"320 × 180": (320, 180), "640 × 360": (640, 360), "160 × 90": (160, 90)}
IMAGE_FORMATS = ["JPEG", "PNG"]
GRID_COLUMNS = 5
TIMELINE_LABELS = {
    "cpu_percent": "시스템 CPU %",
    "memory_percent": "시스템 RAM %",
    "process_cpu_percent": "프로세스 CPU %",
    "process_memory_mb": "프로세스 메모리 MB",
}


# ----------------------------------------------------------------------------
# 처리 파이프라인 (Streamlit 의존 없음)
# ----------------------------------------------------------------------------

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
        if (path.name.startswith("thumbnail_") and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
                and path.is_file() and not path.is_symlink()):
            path.unlink(missing_ok=True)


def _cleanup_at_exit() -> None:
    try:
        cleanup_all_outputs()
    except (OSError, ValueError) as error:  # 종료 중이라 화면이 없으므로 stderr에만 남긴다
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
        "info": info,
        **decision,
        "report": report,
        "thumbnails": thumbnails,
        "options": {"size": size, "crop": crop, "image_format": image_format,
                    "manual_sample_count": manual_sample_count},
    }


def zip_thumbnails(thumbnails: list[dict[str, Any]]) -> bytes:
    """썸네일 파일들을 zip 바이트로 묶는다 (다운로드용)."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in thumbnails:
            path = Path(item["path"])
            archive.write(path, arcname=f"frame_{item['index']:06d}{path.suffix}")
    return buffer.getvalue()


# ----------------------------------------------------------------------------
# 화면
# ----------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _server_lifecycle() -> bool:
    """streamlit run 프로세스당 한 번만 실행된다 (브라우저 세션·새로고침과 무관).

    지난 실행이 비정상 종료로 남긴 파일을 지우고, 이번 프로세스가 정상 종료될 때
    (Ctrl+C, kill) 같은 정리를 하도록 atexit에 등록한다.
    """
    cleanup_all_outputs()
    atexit.register(_cleanup_at_exit)
    return True


def _upload_key(upload: Any) -> str:
    return getattr(upload, "file_id", None) or f"{upload.name}:{upload.size}"


def _discard_result(state: Any) -> None:
    """이전 결과의 썸네일 파일을 지우고 세션에서 결과를 뺀다."""
    if state.get("result"):
        delete_thumbnails(state["result"]["thumbnails"])
    state.pop("result", None)


def render_video_info(info: dict[str, Any]) -> None:
    st.subheader("영상 정보")
    duration = f"{info['duration_seconds']:.2f}초" if info["frame_count"] else "알 수 없음"
    st.table({
        "항목": ["파일명", "크기", "길이", "해상도", "FPS", "전체 프레임 수"],
        "값": [info["filename"], f"{info['file_size_mb']:.2f} MB", duration,
               info["resolution"], f"{info['fps']:.2f}", str(info["frame_count"] or "알 수 없음")],
    })


def render_sampling(result: dict[str, Any]) -> None:
    st.subheader("샘플링 결정")
    total = result["info"]["frame_count"]
    processed = len(result["thumbnails"])
    col1, col2, col3 = st.columns(3)
    col1.metric("선택된 샘플 수", result["sample_count"])
    col2.metric("실제 처리 프레임", f"{processed} / {total or '?'}")
    col3.metric("처리 비율", f"{processed / total:.2%}" if total else "알 수 없음")
    st.caption("근거: " + result["reason"])


def render_resources(report: dict[str, Any]) -> None:
    st.subheader("자원 사용")
    if report.get("error"):
        st.warning(f"자원 측정을 완료하지 못했습니다: {report['error']}")
        st.metric("처리 시간", f"{report['elapsed_seconds']:.2f}초")
        return
    if report.get("sampling_error"):
        st.warning(f"처리 중 샘플링이 중단되었습니다: {report['sampling_error']}")

    row1 = st.columns(4)
    row1[0].metric("처리 시간", f"{report['elapsed_seconds']:.2f}초")
    row1[1].metric("시스템 CPU (후)", f"{report['cpu_after']:.1f}%",
                   delta=f"{report['cpu_after'] - report['cpu_before']:+.1f}%p (전 {report['cpu_before']:.1f}%)")
    row1[2].metric("시스템 RAM 사용 (후)", f"{report['memory_after_mb']:,.0f} MB",
                   delta=f"{report['memory_delta_mb']:+,.1f} MB")
    row1[3].metric("프로세스 메모리 (후)", f"{report['process_memory_after_mb']:,.1f} MB",
                   delta=f"{report['process_memory_delta_mb']:+,.1f} MB")
    row2 = st.columns(4)
    row2[0].metric("처리 중 피크 CPU", f"{report['peak_cpu_percent']:.1f}%")
    row2[1].metric("처리 중 평균 CPU", f"{report['avg_cpu_percent']:.1f}%")
    row2[2].metric("처리 중 피크 RAM", f"{report['peak_memory_percent']:.1f}%")
    row2[3].metric("디스크 여유 공간", f"{report['disk_after']['disk_free_mb'] / 1024:,.1f} GB")

    timeline = pd.DataFrame(report["timeline"]).rename(columns=TIMELINE_LABELS)
    if len(timeline) >= 2:
        left, right = st.columns(2)
        with left:
            st.caption("처리 중 CPU / RAM 사용률 (%)")
            st.line_chart(timeline, x="elapsed_seconds",
                          y=[TIMELINE_LABELS["cpu_percent"], TIMELINE_LABELS["memory_percent"],
                             TIMELINE_LABELS["process_cpu_percent"]],
                          x_label="경과 시간 (초)", y_label="%")
        with right:
            st.caption("처리 중 프로세스 메모리 (MB)")
            st.line_chart(timeline, x="elapsed_seconds", y=TIMELINE_LABELS["process_memory_mb"],
                          x_label="경과 시간 (초)", y_label="MB")
    with st.expander("디스크 사용량 전후"):
        before, after = report["disk_before"], report["disk_after"]
        st.table({
            "항목": ["여유 공간 (MB)", "temp/ 사용량 (MB)", "output/thumbnails/ 사용량 (MB)"],
            "처리 전": [f"{before['disk_free_mb']:,.1f}", f"{before['temp_dir_mb']:.2f}", f"{before['thumbnail_dir_mb']:.2f}"],
            "처리 후": [f"{after['disk_free_mb']:,.1f}", f"{after['temp_dir_mb']:.2f}", f"{after['thumbnail_dir_mb']:.2f}"],
        })


def render_thumbnails(result: dict[str, Any]) -> None:
    thumbnails = result["thumbnails"]
    options = result["options"]
    st.subheader(f"썸네일 {len(thumbnails)}장")
    st.caption(f"{options['size'][0]} × {options['size'][1]} · {options['image_format']} · "
               f"{'중앙 크롭' if options['crop'] else '비율 유지 (여백)'} · 저장 위치: output/thumbnails/")
    columns = st.columns(GRID_COLUMNS)
    for position, item in enumerate(thumbnails):
        columns[position % GRID_COLUMNS].image(
            str(item["path"]), caption=f"프레임 {item['index']} · {item['timestamp_seconds']:.2f}초")
    st.download_button("썸네일 전체 다운로드 (zip)", data=zip_thumbnails(thumbnails),
                       file_name="thumbnails.zip", mime="application/zip")


def main() -> None:
    st.set_page_config(page_title="영상 썸네일 생성기", page_icon="🎬", layout="wide")
    st.title("자원 인식 영상 썸네일 생성기")
    st.caption("영상을 올리면 시스템 자원 상태에 맞춰 샘플 프레임 수를 정하고, "
               "처리 중 CPU/RAM 변화를 기록하면서 썸네일을 만듭니다.")

    with st.sidebar:
        st.header("옵션")
        auto = st.toggle("샘플 수 자동 결정 (자원 기반)", value=True)
        manual = None if auto else st.slider("샘플 프레임 수", MIN_SAMPLE_COUNT, MAX_SAMPLE_COUNT, DEFAULT_SAMPLE_COUNT)
        size = SIZE_PRESETS[st.selectbox("썸네일 크기", list(SIZE_PRESETS))]
        crop = st.checkbox("중앙 크롭 (여백 대신 잘라내기)", value=False)
        image_format = st.radio("저장 형식", IMAGE_FORMATS, horizontal=True)
        st.caption("자동 결정 규칙: CPU 또는 RAM 80% 이상 → 5장, 60% 이상 → 10장, 그 외 20장. "
                   "해상도와 가용 메모리에 따라 상한이 더 낮아질 수 있습니다.")
    options = {"size": size, "crop": crop, "image_format": image_format, "manual_sample_count": manual}

    _server_lifecycle()
    state = st.session_state

    upload = st.file_uploader("영상 업로드", type=VIDEO_EXTENSIONS)
    if upload is None:
        st.info("영상 파일(mp4, mov, avi, mkv, webm)을 업로드하세요.")
        return

    key = _upload_key(upload)
    if state.get("upload_key") != key:
        _discard_result(state)
        state.pop("info", None)
        state.pop("video_path", None)
        file_manager.cleanup_temp_files()
        state["upload_key"] = key
        try:
            with st.spinner("영상을 임시 저장하고 정보를 읽는 중..."):
                state["video_path"] = save_upload(upload, upload.name)
                state["info"] = get_video_info(state["video_path"])
        except (VideoServiceError, OSError, ValueError) as error:
            state["info_error"] = str(error)
        else:
            state.pop("info_error", None)
    if state.get("info") is None:
        st.error(f"영상을 읽을 수 없습니다: {state.get('info_error', '알 수 없는 오류')}")
        return
    render_video_info(state["info"])

    left, right = st.columns([1, 5])
    run = left.button("썸네일 생성", type="primary")
    clear = right.button("결과 지우기")
    if clear:
        _discard_result(state)
    if run:
        try:
            with st.spinner("프레임을 추출하고 썸네일을 만드는 중..."):
                result = run_pipeline(state["video_path"], **options)
        except (VideoServiceError, OSError, ValueError) as error:
            st.error(f"처리에 실패했습니다: {error}")
            return
        _discard_result(state)
        state["result"] = result

    result = state.get("result")
    if result is None:
        return
    if result["options"] != options:
        st.info("옵션이 바뀌었습니다. '썸네일 생성'을 다시 누르면 새 설정으로 처리합니다.")
    render_sampling(result)
    render_resources(result["report"])
    render_thumbnails(result)


if __name__ == "__main__":
    main()
