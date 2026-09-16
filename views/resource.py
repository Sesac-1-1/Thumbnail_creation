"""페이지 2 · 자원 모니터링: 현재 시스템 상태(자동 갱신), 샘플 수 정책 미리보기, 최근 처리 보고서.

다른 브라우저 창에 띄워 두면 썸네일 생성 페이지에서 처리하는 동안의 변화가 실시간으로 보인다.
"""
from collections import deque
from datetime import datetime
import time
from typing import Any

import pandas as pd
import streamlit as st

from services import resource_service as rs
from views import common

REFRESH_SECONDS = 2
PAUSE_LABEL = "⏸ 일시정지"
RESUME_LABEL = "▶ 재생"
HISTORY_POINTS = 60  # 2초 간격 × 60점 = 최근 2분
HISTORY_COLUMNS = ("cpu_percent", "memory_percent", "process_cpu_percent", "process_memory_mb")


def record_history(state: Any, now: dict[str, Any]) -> deque:
    """이번 갱신의 측정값을 세션 기록에 추가한다. 창(세션)마다 따로 쌓인다."""
    history = state.get("resource_history")
    if history is None:
        history = state["resource_history"] = deque(maxlen=HISTORY_POINTS)
    history.append({"time": datetime.now(), **{key: now[key] for key in HISTORY_COLUMNS}})
    return history


def render_history(history: deque, published_at: float | None) -> None:
    """최근 2분 추이. 점이 둘 이상 모여야 선이 된다."""
    st.subheader("최근 2분 추이")
    if len(history) < 2:
        st.caption("추이 수집 중... 다음 갱신부터 그래프가 그려집니다.")
        return
    frame = pd.DataFrame(list(history)).rename(columns=common.TIMELINE_LABELS)
    left, right = st.columns(2)
    with left:
        st.caption("CPU / RAM 사용률 (%)")
        st.line_chart(frame, x="time",
                      y=[common.TIMELINE_LABELS["cpu_percent"], common.TIMELINE_LABELS["memory_percent"],
                         common.TIMELINE_LABELS["process_cpu_percent"]],
                      x_label="시각", y_label="%")
    with right:
        st.caption("프로세스 메모리 (MB)")
        st.line_chart(frame, x="time", y=common.TIMELINE_LABELS["process_memory_mb"],
                      x_label="시각", y_label="MB")
    span = (history[-1]["time"] - history[0]["time"]).total_seconds()
    note = f"{len(history)}점 · 최근 {span:.0f}초"
    if published_at:
        note += f" · 마지막 처리 {time.strftime('%H:%M:%S', time.localtime(published_at))}"
    st.caption(note)


def render_live(video_info: dict[str, Any] | None, published_at: float | None) -> None:
    try:
        now = rs.get_system_resource()
        disk = rs.get_disk_resource()
    except rs.ResourceServiceError as error:
        st.warning(f"자원을 측정할 수 없습니다: {error}")
        return
    history = record_history(st.session_state, now)
    row = st.columns(4)
    row[0].metric("시스템 CPU", f"{now['cpu_percent']:.1f}%")
    row[1].metric("시스템 RAM", f"{now['memory_percent']:.1f}%",
                  help=f"사용 {now['memory_used_mb']:,.0f} MB · 가용 {now['memory_available_mb']:,.0f} MB")
    row[2].metric("프로세스 CPU", f"{now['process_cpu_percent']:.1f}%")
    row[3].metric("프로세스 메모리", f"{now['process_memory_mb']:,.1f} MB")
    row = st.columns(4)
    row[0].metric("디스크 여유", f"{disk['disk_free_mb'] / 1024:,.1f} GB")
    row[1].metric("temp/ 사용량", f"{disk['temp_dir_mb']:.1f} MB")
    row[2].metric("썸네일 사용량", f"{disk['thumbnail_dir_mb']:.2f} MB")
    policy_only = rs.choose_sample_count(now["cpu_percent"], now["memory_percent"])
    if video_info:
        with_video = rs.choose_sample_count(now["cpu_percent"], now["memory_percent"],
                                            video_info=video_info,
                                            memory_available_mb=now["memory_available_mb"])
        row[3].metric("지금 처리하면", f"{with_video}장",
                      help=f"최근 영상({video_info['resolution']}) 기준. 자원 정책만으로는 {policy_only}장")
    else:
        row[3].metric("지금 처리하면", f"{policy_only}장", help=common.SAMPLE_POLICY_TEXT)
    st.caption("규칙: " + common.SAMPLE_POLICY_TEXT)
    render_history(history, published_at)


def render_report(result: dict[str, Any], published_at: float | None) -> None:
    report = result["report"]
    info = result["info"]
    st.subheader("최근 처리 보고서")
    when = time.strftime("%H:%M:%S", time.localtime(published_at)) if published_at else "-"
    st.caption(f"{result['filename']} · {info['resolution']} · {when} 처리")
    total = info["frame_count"]
    processed = len(result["thumbnails"])
    row = st.columns(4)
    row[0].metric("선택된 샘플 수", result["sample_count"])
    row[1].metric("실제 처리 프레임", f"{processed} / {total or '?'}")
    row[2].metric("처리 비율", f"{processed / total:.2%}" if total else "알 수 없음")
    row[3].metric("처리 시간", f"{report['elapsed_seconds']:.2f}초")
    st.caption("근거: " + result["reason"])
    if report.get("error"):
        st.warning(f"자원 측정을 완료하지 못했습니다: {report['error']}")
        return
    if report.get("sampling_error"):
        st.warning(f"처리 중 샘플링이 중단되었습니다: {report['sampling_error']}")

    row = st.columns(4)
    row[0].metric("시스템 CPU (후)", f"{report['cpu_after']:.1f}%",
                  delta=f"{report['cpu_after'] - report['cpu_before']:+.1f}%p (전 {report['cpu_before']:.1f}%)")
    row[1].metric("시스템 RAM 사용 (후)", f"{report['memory_after_mb']:,.0f} MB",
                  delta=f"{report['memory_delta_mb']:+,.1f} MB")
    row[2].metric("프로세스 메모리 (후)", f"{report['process_memory_after_mb']:,.1f} MB",
                  delta=f"{report['process_memory_delta_mb']:+,.1f} MB")
    row[3].metric("처리 중 피크 / 평균 CPU", f"{report['peak_cpu_percent']:.1f}% / {report['avg_cpu_percent']:.1f}%")

    timeline = pd.DataFrame(report["timeline"]).rename(columns=common.TIMELINE_LABELS)
    if len(timeline) >= 2:
        left, right = st.columns(2)
        with left:
            st.caption("처리 중 CPU / RAM 사용률 (%)")
            st.line_chart(timeline, x="elapsed_seconds",
                          y=[common.TIMELINE_LABELS["cpu_percent"], common.TIMELINE_LABELS["memory_percent"],
                             common.TIMELINE_LABELS["process_cpu_percent"]],
                          x_label="경과 시간 (초)", y_label="%")
        with right:
            st.caption("처리 중 프로세스 메모리 (MB)")
            st.line_chart(timeline, x="elapsed_seconds", y=common.TIMELINE_LABELS["process_memory_mb"],
                          x_label="경과 시간 (초)", y_label="MB")
    with st.expander("디스크 사용량 전후"):
        before, after = report["disk_before"], report["disk_after"]
        st.table({
            "항목": ["여유 공간 (MB)", "temp/ 사용량 (MB)", "output/thumbnails/ 사용량 (MB)"],
            "처리 전": [f"{before['disk_free_mb']:,.1f}", f"{before['temp_dir_mb']:.2f}", f"{before['thumbnail_dir_mb']:.2f}"],
            "처리 후": [f"{after['disk_free_mb']:,.1f}", f"{after['temp_dir_mb']:.2f}", f"{after['thumbnail_dir_mb']:.2f}"],
        })


def render_status(paused: bool) -> None:
    """갱신 상태와 마지막 측정 시각. 조각 안에 있어 갱신마다 시각이 바뀐다."""
    measured = time.strftime("%H:%M:%S")
    if paused:
        st.caption(f"⏸ 정지됨 · 마지막 측정 {measured} · '{RESUME_LABEL}'을 누르면 이어서 갱신합니다")
    else:
        st.caption(f"● 실시간 · {REFRESH_SECONDS}초마다 갱신 · 마지막 측정 {measured}")


def _toggle_pause() -> None:
    """on_click 콜백은 화면을 다시 그리기 전에 실행되므로 버튼 라벨이 곧바로 새 상태를 반영한다."""
    st.session_state["monitor_paused"] = not st.session_state.get("monitor_paused", False)


def render() -> None:
    st.title("자원 모니터링")
    st.caption("현재 시스템 상태와 샘플 수 정책, 그리고 썸네일 생성 페이지에서 마지막으로 처리한 결과의 "
               "자원 사용을 보여줍니다. 다른 창에 띄워 두면 처리 중 변화가 실시간으로 보입니다.")
    paused = bool(st.session_state.get("monitor_paused", False))
    header, control = st.columns([5, 1])
    with control:
        # 버튼은 상태를 갖지 않으므로 세션에 저장한다. 누르면 전체가 다시 실행되어 조각의 갱신 주기가 바뀐다.
        st.button(RESUME_LABEL if paused else PAUSE_LABEL, key="monitor_pause", on_click=_toggle_pause)
    with header:
        st.subheader("현재 시스템 상태")

    @st.fragment(run_every=None if paused else REFRESH_SECONDS)
    def live_dashboard() -> None:
        latest, published_at = common.latest_result()
        render_status(paused)
        render_live(latest["info"] if latest else None, published_at)
        st.divider()
        if latest is None:
            st.info("아직 처리 기록이 없습니다. 썸네일 생성 페이지에서 영상을 처리하면 여기에 보고서가 나타납니다.")
            st.page_link(common.THUMBNAIL_PAGE, label="썸네일 생성 페이지로", icon="🎬")
        else:
            render_report(latest, published_at)

    live_dashboard()


render()
