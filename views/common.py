"""두 페이지가 공유하는 Streamlit 조각: 서버 수명주기, 최근 결과 저장소, 화면 상수."""
import atexit
import threading
import time
from typing import Any

import streamlit as st

import pipeline

THUMBNAIL_PAGE = "views/thumbnail.py"
RESOURCE_PAGE = "views/resource.py"
VIDEO_EXTENSIONS = ["mp4", "mov", "avi", "mkv", "webm"]
SIZE_PRESETS = {"320 × 180": (320, 180), "640 × 360": (640, 360), "160 × 90": (160, 90)}
IMAGE_FORMATS = ["JPEG", "PNG"]
TIMELINE_LABELS = {
    "cpu_percent": "시스템 CPU %",
    "memory_percent": "시스템 RAM %",
    "process_cpu_percent": "프로세스 CPU %",
    "process_memory_mb": "프로세스 메모리 MB",
}
SAMPLE_POLICY_TEXT = "CPU 또는 RAM 80% 이상 → 5장, 60% 이상 → 10장, 그 외 20장 (해상도·가용 메모리로 상한 보정)"


@st.cache_resource(show_spinner=False)
def server_lifecycle() -> bool:
    """streamlit run 프로세스당 한 번만 실행된다 (브라우저 세션·새로고침과 무관).

    지난 실행이 비정상 종료로 남긴 파일을 지우고, 이번 프로세스가 정상 종료될 때
    (Ctrl+C, kill) 같은 정리를 하도록 atexit에 등록한다.
    """
    pipeline.cleanup_all_outputs()
    atexit.register(pipeline.cleanup_at_exit)
    return True


@st.cache_resource(show_spinner=False)
def _latest_store() -> dict[str, Any]:
    """서버 프로세스 전체가 공유하는 '최근 처리 결과' 저장소.

    브라우저 창(세션)이 달라도 같은 결과를 본다. 그래서 썸네일 페이지와 자원 페이지를
    다른 창에 동시에 띄울 수 있다. 결과 하나만 보관하는 단일 사용자 데모용 구조다.
    """
    return {"lock": threading.Lock(), "result": None, "published_at": None}


def publish_result(result: dict[str, Any]) -> None:
    store = _latest_store()
    with store["lock"]:
        store["result"] = result
        store["published_at"] = time.time()


def latest_result() -> tuple[dict[str, Any] | None, float | None]:
    store = _latest_store()
    with store["lock"]:
        return store["result"], store["published_at"]


def clear_latest_result() -> None:
    store = _latest_store()
    with store["lock"]:
        store["result"] = None
        store["published_at"] = None
