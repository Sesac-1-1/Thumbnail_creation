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

## `app.py` 연결 예시

```python
from services.resource_service import (
    choose_sample_count,
    finish_measurement,
    get_system_resource,
    start_measurement,
)
from services.video_service import extract_frames

before = get_system_resource()
sample_count = choose_sample_count(
    before["cpu_percent"],
    before["memory_percent"],
)

measurement = start_measurement()
frames = extract_frames(video_path, sample_count=sample_count)
resource_result = finish_measurement(measurement)
```

화면에는 다음 값을 표시하면 된다.

- 처리 전 CPU / RAM
- 처리 후 CPU / RAM
- 처리 시간
- 선택된 샘플 수
- 전체 프레임 수 대비 실제 처리 프레임 수

## 구현 시 주의사항

- `psutil.cpu_percent(interval=0.1)`처럼 짧은 측정 간격을 사용한다.
- 퍼센트 값은 소수 둘째 자리 정도로 반올림한다.
- CPU나 RAM 값이 0~100 범위를 벗어나면 안 된다.
- `sample_count`는 최소 1 이상이어야 한다.
- `resource_service`는 OpenCV나 Pillow를 import하지 않는다.
- 시스템 자원 측정 실패 시에도 영상 처리 전체가 중단되지 않도록 기본값 또는 명확한 예외 처리를 제공한다.
- 실제 CPU 점유율은 순간값이므로, 발표 화면에서는 처리 전후 값과 처리 시간을 함께 보여준다.

## 최소 완료 기준

- [ ] `psutil`로 CPU 사용률을 읽는다.
- [ ] RAM 사용률과 사용 가능 메모리를 읽는다.
- [ ] 자원 상태에 따라 5 / 10 / 20개의 샘플 수를 반환한다.
- [ ] 처리 시간을 측정한다.
- [ ] 처리 전후 CPU·RAM 결과를 반환한다.
- [ ] `app.py`에서 `video_service.extract_frames()`와 연결된다.

## 테스트 기준

다음 케이스를 테스트한다.

- CPU와 RAM이 낮으면 20 반환
- CPU가 60% 이상이면 10 반환
- RAM이 80% 이상이면 5 반환
- 처리 시간이 0 이상으로 반환됨
- CPU와 RAM 결과가 숫자로 반환됨
- CPU 또는 RAM이 높은 경우에도 함수가 정상 종료됨
