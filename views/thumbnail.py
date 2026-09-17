"""페이지 1 · 썸네일 생성: 업로드, 옵션, 생성, 썸네일 그리드."""
from typing import Any

import cv2
import streamlit as st

import pipeline
from services import file_manager
from services.resource_service import DEFAULT_SAMPLE_COUNT, MAX_SAMPLE_COUNT, MIN_SAMPLE_COUNT
from services.video_service import VideoServiceError, get_video_info
from services import ai_service
from services.thumbnail_service import add_text_overlay, save_thumbnail
from views import common

GRID_COLUMNS = 5


def sidebar_options() -> dict[str, Any]:
    with st.sidebar:
        st.header("옵션")
        auto = st.toggle("샘플 수 자동 결정 (자원 기반)", value=True)
        manual = None if auto else st.slider("샘플 프레임 수", MIN_SAMPLE_COUNT, MAX_SAMPLE_COUNT,
                                             DEFAULT_SAMPLE_COUNT)
        size = common.SIZE_PRESETS[st.selectbox("썸네일 크기", list(common.SIZE_PRESETS))]
        crop = st.checkbox("중앙 크롭 (여백 대신 잘라내기)", value=False)
        image_format = st.radio("저장 형식", common.IMAGE_FORMATS, horizontal=True)
        st.caption("자동 결정 규칙: " + common.SAMPLE_POLICY_TEXT)
    return {"size": size, "crop": crop, "image_format": image_format, "manual_sample_count": manual}


def _upload_key(upload: Any) -> str:
    return getattr(upload, "file_id", None) or f"{upload.name}:{upload.size}"


def _discard_result(state: Any) -> None:
    """이전 결과의 썸네일 파일을 지우고, 세션과 공용 저장소에서 결과를 뺀다."""
    if state.get("result"):
        pipeline.delete_thumbnails(state["result"]["thumbnails"])
    state.pop("result", None)
    common.clear_latest_result()


def render_video_info(info: dict[str, Any]) -> None:
    st.subheader("영상 정보")
    duration = f"{info['duration_seconds']:.2f}초" if info["frame_count"] else "알 수 없음"
    st.table({
        "항목": ["파일명", "크기", "길이", "해상도", "FPS", "전체 프레임 수"],
        "값": [info["filename"], f"{info['file_size_mb']:.2f} MB", duration,
               info["resolution"], f"{info['fps']:.2f}", str(info["frame_count"] or "알 수 없음")],
    })


def render_sampling_summary(result: dict[str, Any]) -> None:
    total = result["info"]["frame_count"]
    processed = len(result["thumbnails"])
    st.markdown(f"**샘플 {result['sample_count']}장 선택, {processed}장 처리** "
                f"(전체 {total or '?'}프레임) · {result['reason']}")
    st.page_link(common.RESOURCE_PAGE, label="처리 중 자원 사용과 근거를 자원 모니터링 페이지에서 보기", icon="📊")


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
    st.download_button("썸네일 전체 다운로드 (zip)", data=pipeline.zip_thumbnails(thumbnails),
                       file_name="thumbnails.zip", mime="application/zip")

    if st.button("AI로 추천 프레임·문구 분석"):
        try:
            # Analyze the exact thumbnails currently shown on screen. Re-reading
            # the video here could produce different frame positions from the
            # already-rendered candidate list.
            frames = []
            for item in thumbnails:
                frame = cv2.imread(str(item["path"]))
                if frame is None:
                    raise ValueError(f"썸네일을 읽을 수 없습니다: {item['path']}")
                frames.append({
                    "index": item["index"],
                    "timestamp_seconds": item["timestamp_seconds"],
                    "frame": frame,
                })
            with st.spinner("AI가 썸네일 후보를 분석하는 중..."):
                analysis = ai_service.analyze_candidates(frames)
            result["ai_analysis"] = analysis
        except Exception as error:
            st.error(f"AI 분석에 실패했습니다: {error}")

    analysis = result.get("ai_analysis")
    if analysis:
        st.success(f"AI 추천 프레임: {analysis['best_index']}번")
        frame_options = {
            f"프레임 {item['index']} · {item['timestamp_seconds']:.2f}초": item["index"]
            for item in thumbnails
        }
        frame_labels = list(frame_options)
        recommended_label = next(
            (label for label, index in frame_options.items()
             if index == analysis["best_index"]),
            frame_labels[0],
        )
        selected_frame_label = st.selectbox(
            "사용할 프레임 선택", frame_labels,
            index=frame_labels.index(recommended_label),
        )
        selected_frame_index = frame_options[selected_frame_label]
        suggestions = analysis["suggestions"]
        if suggestions:
            selected_text = st.selectbox("사용할 문구 선택", suggestions)
            if st.button("선택한 문구로 최종 썸네일 만들기"):
                selected = next((item for item in thumbnails
                                 if item["index"] == selected_frame_index), None)
                if selected is None:
                    st.error("추천 프레임을 현재 후보 목록에서 찾을 수 없습니다.")
                else:
                    from PIL import Image
                    with Image.open(selected["path"]) as image:
                        final_image = add_text_overlay(image.convert("RGB"), selected_text)
                    final_path = save_thumbnail(final_image, result["options"]["image_format"])
                    result["final_thumbnail_path"] = str(final_path)
                    st.success("최종 썸네일을 만들었습니다.")
        if result.get("final_thumbnail_path"):
            st.image(result["final_thumbnail_path"], caption="최종 썸네일")
            with open(result["final_thumbnail_path"], "rb") as stream:
                st.download_button("최종 썸네일 다운로드", stream.read(),
                                   file_name="final_thumbnail.jpg", mime="image/jpeg")


def render() -> None:
    st.title("썸네일 생성")
    st.caption("영상을 올리면 시스템 자원 상태에 맞춰 샘플 프레임 수를 정하고 썸네일을 만듭니다.")
    options = sidebar_options()
    state = st.session_state

    upload = st.file_uploader("영상 업로드", type=common.VIDEO_EXTENSIONS, key="video_uploader")
    if upload is None and not state.get("video_path"):
        st.info("영상 파일(mp4, mov, avi, mkv, webm)을 업로드하세요.")
        return

    key = _upload_key(upload) if upload is not None else state.get("upload_key")
    if upload is not None and state.get("upload_key") != key:
        _discard_result(state)
        state.pop("info", None)
        state.pop("video_path", None)
        file_manager.cleanup_temp_files()
        state["upload_key"] = key
        try:
            with st.spinner("영상을 임시 저장하고 정보를 읽는 중..."):
                state["video_path"] = pipeline.save_upload(upload, upload.name)
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
                result = pipeline.run_pipeline(state["video_path"], **options)
        except (VideoServiceError, OSError, ValueError) as error:
            st.error(f"처리에 실패했습니다: {error}")
            return
        _discard_result(state)
        state["result"] = result
        common.publish_result(result)

    result = state.get("result")
    if result is None:
        return
    if result["options"] != options:
        st.info("옵션이 바뀌었습니다. '썸네일 생성'을 다시 누르면 새 설정으로 처리합니다.")
    render_sampling_summary(result)
    render_thumbnails(result)


render()
