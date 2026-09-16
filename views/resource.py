"""페이지 2 · 자원 모니터링: 현재 시스템 상태(자동 갱신), 샘플 수 정책 미리보기, 최근 처리 보고서.

다른 브라우저 창에 띄워 두면 썸네일 생성 페이지에서 처리하는 동안의 변화가 실시간으로 보인다.
"""
import time
from typing import Any

import pandas as pd
import streamlit as st

from services import resource_service as rs
from views import common

REFRESH_SECONDS = 2


def render_live(video_info: dict[str, Any] | None) -> None:
    try:
        now = rs.get_system_resource()
        disk = rs.get_disk_resource()
    except rs.ResourceServiceError as error:
        st.warning(f"자원을 측정할 수 없습니다: {error}")
        return
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
    st.caption(f"마지막 측정 {time.strftime('%H:%M:%S')} · 규칙: {common.SAMPLE_POLICY_TEXT}")


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


def render() -> None:
    st.title("자원 모니터링")
    st.caption("현재 시스템 상태와 샘플 수 정책, 그리고 썸네일 생성 페이지에서 마지막으로 처리한 결과의 "
               "자원 사용을 보여줍니다. 다른 창에 띄워 두면 처리 중 변화가 실시간으로 보입니다.")
    auto = st.toggle(f"{REFRESH_SECONDS}초마다 자동 갱신", value=True)

    @st.fragment(run_every=REFRESH_SECONDS if auto else None)
    def live_dashboard() -> None:
        latest, published_at = common.latest_result()
        st.subheader("현재 시스템 상태")
        render_live(latest["info"] if latest else None)
        st.divider()
        if latest is None:
            st.info("아직 처리 기록이 없습니다. 썸네일 생성 페이지에서 영상을 처리하면 여기에 보고서가 나타납니다.")
            st.page_link(common.THUMBNAIL_PAGE, label="썸네일 생성 페이지로", icon="🎬")
        else:
            render_report(latest, published_at)

    live_dashboard()


render()
