# `resource_service.py` 작업 명세

## 담당 목적

영상 처리 전후의 시스템 자원과 처리 시간을 측정한다.
측정 결과를 이용해 `video_service.extract_frames()`에 전달할 샘플 프레임 수를 결정한다.

## 담당 범위

- CPU 사용률 측정
- RAM 사용률 및 사용량 측정
- 처리 시간 측정
- 처리 전후 자원 차이 계산
- 시스템 상태에 따른 샘플 프레임 수 결정

영상 열기, 메타데이터 분석, 프레임 추출은 `video_service.py`의 책임이다.
이미지 리사이즈와 파일 저장은 `thumbnail_service.py`의 책임이다.

## 권장 함수

```python
def get_system_resource() -> dict:
    """현재 CPU와 RAM 사용량을 반환한다."""
```

권장 반환 형식:

```python
{
    "cpu_percent": 31.2,
    "memory_percent": 48.5,
    "memory_used_mb": 7820.4,
    "memory_available_mb": 8320.1,
}
```

```python
def choose_sample_count(cpu_percent: float, memory_percent: float) -> int:
    """현재 자원 상태에 맞는 샘플 프레임 수를 결정한다."""
```

기본 정책:

```text
CPU 또는 RAM 80% 이상 → 5개
CPU 또는 RAM 60% 이상 → 10개
그 외                 → 20개
```

```python
def start_measurement() -> dict:
    """처리 시작 시각과 시작 자원 상태를 기록한다."""
```

```python
def finish_measurement(start: dict) -> dict:
    """처리 종료 시각과 종료 자원 상태를 기록하고 결과를 반환한다."""
```

권장 반환 형식:

```python
{
    "elapsed_seconds": 1.7,
    "cpu_before": 31.0,
    "cpu_after": 37.0,
    "memory_before_mb": 7820.4,
    "memory_after_mb": 7882.4,
    "memory_delta_mb": 62.0,
}
```

## 구현 확장 (`feat/resource`)

명세의 함수명과 반환 키는 그대로 유지하고, 다음을 더했다.

- `get_system_resource()`는 `process_cpu_percent`(현재 프로세스, 코어 수로 정규화)와
  `process_memory_mb`(RSS)를 함께 반환한다.
- `get_disk_resource()`는 `disk_free_mb`, `disk_percent`, `temp_dir_mb`, `thumbnail_dir_mb`를 반환한다.
- `choose_sample_count(cpu, mem, *, video_info=None, memory_available_mb=None)`:
  `video_service.get_video_info()`가 반환한 dict를 넘기면 프레임 1장 메모리(`width × height × 3`)와
  가용 RAM의 25%로 상한을 두고, `frame_count`도 넘지 않는다. 결과는 항상 1~20이다.
- `start_measurement(sample_interval=0.5)`는 백그라운드 스레드로 처리 중 CPU/RAM을 기록한다.
  `finish_measurement()` 결과에 `timeline`(차트용 리스트), `peak_cpu_percent`, `avg_cpu_percent`,
  `peak_memory_percent`, `peak_process_memory_mb`, `process_memory_before_mb/after_mb/delta_mb`,
  `disk_before/after`, `sampling_error`가 추가된다. `sample_interval=None`이면 전후 스냅샷만 남긴다.
- `measure()` 컨텍스트 매니저는 위 두 함수를 `finally`로 감싼다. 측정이 실패해도 본문은 실행되고
  `report["error"]`에 이유가 남는다. Streamlit에서는 이 형태를 권장한다.
- 측정 실패는 `ResourceServiceError`로 던진다. 호출 측은 이를 잡아 `DEFAULT_SAMPLE_COUNT`(10)로 진행한다.
- `start_measurement()`가 반환한 dict는 스레드를 담고 있으므로 `st.session_state`에 넣지 않는다.
  `finish_measurement()` 결과는 순수 데이터라 저장·표시해도 안전하다.
- macOS 커널은 CPU 카운터를 약 1초에 한 번 갱신해 `psutil.cpu_percent(interval=0.1)`이 약 40% 확률로
  정확히 0.0을 반환한다(실측 50회: 첫 읽기 성공 31회, 나머지는 최대 1.07초 뒤 값이 나옴). 그래서 0.1초 창에서
  0.0이 나오면 0.1초씩 최대 1.1초까지 창을 연장한다. 틱이 누적되므로 첫 비영 값이 전체 창의 실제 비율이다.
  Linux/Windows는 첫 읽기에서 바로 값이 나온다. 샘플러 간격을 0.5초 미만으로 줄이면 타임라인에 0.0이 섞일 수 있다.

## `app.py` 연결 예시

```python
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

info = get_video_info(video_path)
try:
    before = get_system_resource()
    sample_count = choose_sample_count(
        before["cpu_percent"],
        before["memory_percent"],
        video_info=info,
        memory_available_mb=before["memory_available_mb"],
    )
except ResourceServiceError as error:
    st.warning(f"자원 측정 실패: {error}")
    sample_count = DEFAULT_SAMPLE_COUNT

with measure() as report:
    frames = extract_frames(video_path, sample_count=sample_count)

paths = [save_thumbnail(create_thumbnail(item["frame"])) for item in frames]
st.line_chart(report["timeline"], x="elapsed_seconds", y=["cpu_percent", "memory_percent"])
```

화면에는 다음 값을 표시하면 된다.

- 처리 전 CPU / RAM
- 처리 후 CPU / RAM
- 처리 시간
- 처리 중 CPU / RAM 피크와 평균, 시간축 차트
- 선택된 샘플 수와 그 근거(자원 상태, 해상도, 가용 메모리)
- 전체 프레임 수 대비 실제 처리 프레임 수
- 디스크 여유 공간과 temp / output 사용량

## 구현 시 주의사항

- `psutil.cpu_percent(interval=0.1)`처럼 짧은 측정 간격을 사용한다.
- 퍼센트 값은 소수 둘째 자리 정도로 반올림한다.
- CPU나 RAM 값이 0~100 범위를 벗어나면 안 된다.
- `sample_count`는 최소 1 이상이어야 한다.
- `resource_service`는 OpenCV나 Pillow를 import하지 않는다.
- 시스템 자원 측정 실패 시에도 영상 처리 전체가 중단되지 않도록 기본값 또는 명확한 예외 처리를 제공한다.
- 실제 CPU 점유율은 순간값이므로, 발표 화면에서는 처리 전후 값과 처리 시간을 함께 보여준다.

## 최소 완료 기준

- [x] `psutil`로 CPU 사용률을 읽는다.
- [x] RAM 사용률과 사용 가능 메모리를 읽는다.
- [x] 자원 상태에 따라 5 / 10 / 20개의 샘플 수를 반환한다.
- [x] 처리 시간을 측정한다.
- [x] 처리 전후 CPU·RAM 결과를 반환한다.
- [ ] `app.py`에서 `video_service.extract_frames()`와 연결된다.

## 테스트 기준

다음 케이스를 테스트한다.

- CPU와 RAM이 낮으면 20 반환
- CPU가 60% 이상이면 10 반환
- RAM이 80% 이상이면 5 반환
- 처리 시간이 0 이상으로 반환됨
- CPU와 RAM 결과가 숫자로 반환됨
- CPU 또는 RAM이 높은 경우에도 함수가 정상 종료됨
- 영상 메타데이터·가용 메모리에 따라 샘플 수 상한이 적용됨
- 백그라운드 샘플러가 타임라인을 수집하고 예외를 `sampling_error`로 보고함
- `measure()`가 본문 예외 시에도 스레드를 정리하고, 측정 실패 시에도 본문을 실행함
- 모듈 import 시 OpenCV / Pillow가 로드되지 않음

테스트 파일: `tests/test_resource_service.py` (psutil은 mock, 실제 영상 불필요)
