# Codex 프롬프트 — Thumbnail + File Manager

# Role

당신은 팀 프로젝트에서 **Thumbnail Processing(썸네일 처리)** 과 **File Management(파일 관리)** 모듈만 담당하는 Python 애플리케이션 엔지니어입니다.

Video Processing 모듈이나 Resource Management 모듈은 구현하지 마십시오.

이 프로젝트는 내일 다른 팀원의 모듈과 통합될 예정이므로 다음을 우선합니다.

- 모듈성
- 명확한 인터페이스
- 낮은 결합도
- 예측 가능한 입력/출력
- 쉬운 통합
- 유지보수성

---

# Goal

다음 두 개의 독립적인 Python 모듈을 구현하십시오.

1. **Thumbnail Service**
   - 이미 추출된 비디오 프레임을 입력으로 받는다.
   - 프레임을 썸네일로 변환한다.
   - 필요에 따라 resize/crop 한다.
   - 썸네일을 이미지 파일로 저장한다.

2. **File Manager**
   - 임시 디렉터리와 출력 디렉터리를 관리한다.
   - 썸네일 파일 저장을 지원한다.
   - 안전한 파일 경로를 생성한다.
   - 임시 파일을 정리한다.
   - 파일 관련 로직을 이미지 처리 로직과 분리한다.

이 두 모듈은 독립적으로 테스트 가능해야 하며, 이후 다음 모듈과 쉽게 통합될 수 있어야 합니다.

- 팀원 A의 `Resource Management`
- 팀원 B의 `Video Processing`
- 최종 `Streamlit` 애플리케이션

---

# Context

전체 애플리케이션 개념은 다음과 같습니다.

```text
사용자가 영상 업로드
        ↓
Video Processing
        ↓
Frame Extraction
        ↓
Thumbnail Processing
        ↓
File Management
        ↓
Thumbnail Result
```

다른 팀원이 구현할 영역:

```text
Video Processing
- OpenCV 영상 로딩
- 영상 메타데이터 처리
- 프레임 추출
```

다른 팀원이 구현할 영역:

```text
Resource Management
- psutil
- CPU 모니터링
- 메모리 모니터링
- 워크로드/자원 판단
```

내 담당 영역:

```text
Extracted Frame
      ↓
Thumbnail Service
      ↓
File Manager
      ↓
Saved Thumbnail
```

Video 또는 Resource 모듈이 이미 존재한다고 가정하지 마십시오.

나중에 단순한 함수 호출만으로 연결할 수 있도록 설계하십시오.

---

# Canonical Identifier Registry — SSOT

다음 식별자를 한 번만 정의하고 구현 전체에서 일관되게 재사용하십시오.

```text
PROJECT_ROOT
TEMP_DIR
OUTPUT_DIR
THUMBNAIL_DIR

thumbnail_service.py
file_manager.py

create_thumbnail
save_thumbnail
generate_thumbnail_path
ensure_directories
cleanup_temp_files
```

권장 프로젝트 구조:

```text
project/
│
├── services/
│   ├── thumbnail_service.py
│   └── file_manager.py
│
├── temp/
│
└── output/
    └── thumbnails/
```

이 식별자들은 **Single Source of Truth(SSOT)** 입니다.

기술적으로 꼭 필요한 경우가 아니라면 동일한 개념에 대해 다른 이름을 새로 만들지 마십시오.

---

# Target

내 담당 영역에 필요한 파일만 생성하십시오.

주요 대상 파일:

```text
services/thumbnail_service.py
services/file_manager.py
```

독립 테스트가 필요하다면 다음 파일도 추가할 수 있습니다.

```text
tests/test_thumbnail_service.py
tests/test_file_manager.py
```

다음 파일은 생성하거나 수정하지 마십시오.

```text
video_service.py
resource_service.py
app.py
Streamlit UI
```

단, 최소한의 mock 또는 test stub가 반드시 필요한 경우에는 예외로 합니다.

Mock이 필요한 경우 테스트 전용 코드임을 명확히 표시하십시오.

---

# Task

## 1. File Manager

다음 파일을 생성하십시오.

```text
services/file_manager.py
```

담당 기능:

### A. 디렉터리 생성

다음을 구현하십시오.

```python
ensure_directories()
```

필요한 디렉터리가 존재하도록 보장해야 합니다.

필수 디렉터리:

```text
temp/
output/
output/thumbnails/
```

이미 디렉터리가 존재하는 경우에도 안전하게 동작해야 합니다.

---

### B. 썸네일 경로 생성

다음을 구현하십시오.

```python
generate_thumbnail_path(...)
```

유효하고 충돌 가능성이 낮은 썸네일 출력 경로를 생성해야 합니다.

권장 파일명 예시:

```text
thumbnail_001.jpg
thumbnail_002.jpg
thumbnail_003.jpg
```

기술적으로 더 적절한 경우 다른 deterministic naming strategy를 사용할 수 있습니다.

가능하면 함수는 `pathlib.Path`를 반환하십시오.

---

### C. 임시 파일 정리

다음을 구현하십시오.

```python
cleanup_temp_files()
```

프로젝트에서 생성한 임시 파일을 삭제해야 합니다.

안전 요구사항:

- 프로젝트의 임시 디렉터리 외부 파일을 절대 삭제하지 않는다.
- 출력 썸네일 파일은 삭제하지 않는다.
- 임시 디렉터리가 없어도 오류 없이 처리한다.
- 디렉터리가 이미 비어 있어도 실패하지 않는다.

---

### D. File Manager 책임 분리 규칙

`file_manager.py`에서는 다음을 수행하지 마십시오.

- OpenCV 프레임 추출
- Pillow 이미지 변환
- 자원 모니터링
- Streamlit 렌더링

이 모듈은 파일 시스템 관련 작업만 담당합니다.

---

# Task 2. Thumbnail Service

다음 파일을 생성하십시오.

```text
services/thumbnail_service.py
```

다음을 사용하십시오.

```python
Pillow
```

또한 OpenCV에서 생성된 프레임과 호환되어야 합니다.

예상 입력 형식:

```python
numpy.ndarray
```

OpenCV 기본 채널 순서:

```text
BGR
```

---

## A. 프레임 변환

다음과 같은 프레임을 입력으로 받습니다.

```python
frame: numpy.ndarray
```

변환 과정:

```text
OpenCV BGR ndarray
        ↓
RGB
        ↓
PIL.Image.Image
```

여기서는 비디오 파일을 직접 읽지 마십시오.

---

## B. 썸네일 생성

다음을 구현하십시오.

```python
create_thumbnail(...)
```

권장 개념 인터페이스:

```python
create_thumbnail(
    frame,
    size=(320, 180),
    crop=False
)
```

기술적으로 필요하면 인터페이스를 개선할 수 있으나, 이후 통합이 쉽도록 단순한 구조를 유지하십시오.

요구사항:

- 입력: 이미 추출된 프레임
- 출력: `PIL.Image.Image`
- 기본 썸네일 비율은 일반적인 영상 썸네일에 적합해야 한다.
- 기본 크기:

```text
320 x 180
```

- 합리적인 이미지 품질을 유지한다.
- 가능하면 불필요한 메모리 복사를 줄인다.

최소한 다음 기능을 지원하십시오.

```text
resize
```

선택적으로 `crop=True`일 때 다음 기능을 지원할 수 있습니다.

```text
center crop
```

---

## C. 썸네일 저장

다음을 구현하십시오.

```python
save_thumbnail(...)
```

처리 흐름:

```text
PIL Image
   ↓
File Manager output path
   ↓
JPEG 또는 PNG 파일
   ↓
저장된 경로 반환
```

기본 권장 형식:

```text
JPEG
```

JPEG 저장 시 적절한 품질 설정을 적용할 수 있습니다.

Thumbnail Service 내부에 파일 경로를 하드코딩하지 마십시오.

반드시 File Manager의 기능을 사용하십시오.

---

# Task 3. Integration Contract 정의

모듈은 단순한 통합 인터페이스를 제공해야 합니다.

향후 사용 방식 예시:

```python
from services.thumbnail_service import create_thumbnail, save_thumbnail

frame = extracted_frame_from_video_module

thumbnail = create_thumbnail(frame)

saved_path = save_thumbnail(thumbnail)
```

실제 구현에는 추가 optional parameter가 포함될 수 있습니다.

하지만 다른 팀원이 즉시 이해할 수 있도록 주요 인터페이스는 단순하게 유지하십시오.

---

# Task 4. Error Handling

예측 가능한 오류를 명확하게 처리하십시오.

예:

```text
frame is None
frame is empty
invalid ndarray shape
unsupported channel format
invalid thumbnail size
directory creation failure
file save failure
```

명확한 Python 예외를 사용하십시오.

다음과 같은 패턴은 피하십시오.

```python
except Exception:
    pass
```

오류를 조용히 무시하지 마십시오.

오류 메시지는 다음을 알 수 있어야 합니다.

```text
무엇이 실패했는지
어느 단계에서 실패했는지
어떤 입력이 잘못되었는지
```

불필요한 시스템 세부정보는 노출하지 마십시오.

---

# Task 5. Lightweight Tests

내 담당 모듈에 대해 간단한 테스트 또는 독립 검증 루틴을 작성하십시오.

실제 비디오 파일은 필요하지 않아야 합니다.

다음과 같은 Dummy NumPy frame을 생성하여 테스트하십시오.

```python
np.zeros((1080, 1920, 3), dtype=np.uint8)
```

최소 테스트 항목:

```text
1. 디렉터리 생성
2. 정상 frame → PIL thumbnail
3. 결과 썸네일 크기
4. 썸네일 저장
5. 저장된 파일 존재 여부
6. 생성된 경로가 예상치 않게 충돌하지 않는지
7. cleanup_temp_files가 temp 디렉터리에만 영향을 주는지
8. 잘못된 frame이 정상적으로 거부되는지
```

필요한 경우 테스트 후 생성된 파일을 정리하십시오.

---

# Constraints

## Scope

다음 영역만 작업하십시오.

```text
Thumbnail Processing
File Management
```

다음은 구현하지 마십시오.

```text
video upload UI
Streamlit UI
video decoding
video metadata extraction
frame sampling algorithms
CPU monitoring
RAM monitoring
psutil logic
adaptive workload logic
```

이 영역은 다른 팀원의 담당입니다.

---

## Integration Safety

팀원의 Video 모듈 내부 구현 방식을 알 필요가 없어야 합니다.

허용되는 유일한 가정:

```text
Video 모듈이 추출된 frame을 NumPy ndarray 형태로 제공한다.
```

팀원 특정 클래스나 파일명에 강하게 의존하지 마십시오.

---

## Dependencies

표준 Python 모듈과 다음 패키지를 우선 사용하십시오.

```text
Pillow
NumPy
```

OpenCV는 BGR → RGB 변환을 위해 정말 필요한 경우에만 사용하십시오.

NumPy/Pillow만으로 안전하게 변환할 수 있다면 Thumbnail 모듈 내부의 OpenCV 의존성을 피하십시오.

불필요한 패키지는 추가하지 마십시오.

---

## Path Handling

가능하면 다음을 사용하십시오.

```python
pathlib.Path
```

Raw string 기반 경로 결합은 피하십시오.

다음 운영체제에 종속된 경로를 가정하지 마십시오.

```text
Windows-only paths
macOS-only paths
Linux-only paths
```

Cross-platform 경로 처리를 구현하십시오.

---

## Code Quality

코드는 다음 조건을 만족해야 합니다.

```text
짧고
읽기 쉽고
모듈화되어 있고
유지보수 가능하고
통합하기 쉬워야 한다.
```

다음을 사용하십시오.

```text
type hints
docstrings
small functions
clear names
```

불필요한 디자인 패턴이나 클래스 구조로 과도하게 설계하지 마십시오.

클래스가 분명한 기술적 장점을 제공하지 않는다면 단순 함수 기반 구현을 우선하십시오.

---

## Separation of Concerns

다음 경계를 엄격하게 유지하십시오.

```text
thumbnail_service.py
→ 이미지 처리

file_manager.py
→ 파일 시스템 관리
```

`thumbnail_service.py`에 경로 관리 로직을 중복 구현하지 마십시오.

`file_manager.py`에 이미지 처리 로직을 구현하지 마십시오.

---

# Output

구현 완료 후 다음 보고서를 제공하십시오.

## 1. 생성된 파일

예:

```text
services/
├── thumbnail_service.py
└── file_manager.py
```

테스트 파일을 생성했다면 포함하십시오.

---

## 2. 모듈별 책임

다음 형식의 간단한 표를 제공하십시오.

| Module | Responsibility | Input | Output |
|---|---|---|---|

---

## 3. Public Interface

최종 public function과 signature를 나열하십시오.

예:

```python
ensure_directories(...)
generate_thumbnail_path(...)
cleanup_temp_files(...)

create_thumbnail(...)
save_thumbnail(...)
```

---

## 4. Integration Example

향후 Video 모듈이 frame을 전달하는 최소 통합 예제를 보여주십시오.

Video 모듈 자체는 구현하지 마십시오.

---

## 5. Dependency Report

필요한 패키지를 나열하십시오.

예:

```text
Pillow
NumPy
```

OpenCV를 실제 사용한 경우에만 포함하십시오.

---

## 6. Validation Report

각 검증 항목에 대해 다음 형식으로 결과를 보고하십시오.

```text
PASS / FAIL
```

실제로 테스트하지 않은 항목을 PASS로 표시하지 마십시오.

---

# Validation

## Incremental Validation

각 논리적 구현 Chunk가 끝날 때마다 **Incremental Validation(점진적 검증)** 을 수행하십시오.

### Chunk 1 — File Manager

검증:

```text
디렉터리 생성이 정상 동작하는지
경로가 올바른지
경로 생성 방식이 안전한지
cleanup이 TEMP_DIR 내부에서만 수행되는지
```

명확한 문제가 있으면 수정한 후 다음 단계로 진행하십시오.

---

### Chunk 2 — Thumbnail Conversion

검증:

```text
NumPy frame 입력 가능
BGR → RGB 변환 정확성
PIL Image 반환
입력 검증 정상 동작
```

---

### Chunk 3 — Resize / Crop

검증:

```text
기본 출력 크기 = 320x180
비율 처리가 정상인지
crop 옵션이 예측 가능하게 동작하는지
```

---

### Chunk 4 — Saving

검증:

```text
File Manager가 경로 생성
썸네일 저장 성공
저장 파일 실제 존재
반환 경로와 실제 파일 경로 일치
```

---

### Chunk 5 — Error Handling

다음 잘못된 입력을 테스트하십시오.

```text
None frame
empty ndarray
wrong dimensions
invalid size
invalid save destination
```

오류가 명확하고 이해 가능하게 발생하는지 확인하십시오.

---

### Chunk 6 — Integration Contract

다음 흐름이 실제로 동작하는지 검증하십시오.

```text
NumPy frame
   ↓
create_thumbnail()
   ↓
PIL Image
   ↓
save_thumbnail()
   ↓
Path
```

---

# Cross-Chunk Consistency Check

모든 Chunk가 완료된 후 **Cross-Chunk Consistency Check(청크 간 일관성 검사)** 를 수행하십시오.

다음을 확인하십시오.

```text
Thumbnail Service가 자체 경로 로직을 중복 구현하지 않고 File Manager를 사용하는지

File Manager에 이미지 처리 책임이 들어가 있지 않은지

Video Processing 로직이 실수로 구현되지 않았는지

Resource Management 로직이 실수로 구현되지 않았는지

Public function 이름이 Canonical Identifier Registry와 일치하는지

디렉터리 이름이 Canonical Identifier Registry와 일치하는지

함수 signature가 구현, 테스트, 예제 사이에서 동일한지

import 경로가 실제 프로젝트 구조와 일치하는지

동일한 경로/개념을 나타내는 중복 상수가 없는지

각 모듈을 독립적으로 import할 수 있는지

Integration Example이 실제 구현 인터페이스와 일치하는지
```

불일치가 있으면 완료 선언 전에 수정하십시오.

---

# Repetition Prevention

**Canonical Identifier Registry를 Single Source of Truth(SSOT)** 로 사용하십시오.

규칙:

1. 정확한 식별자는 한 번만 정의한다.
2. 같은 값을 반복해서 직접 작성하지 말고 canonical 값을 재사용한다.
3. 기존 모듈/함수/경로 개념과 동일한 의미의 다른 이름을 새로 만들지 않는다.
4. 중복 문자열 리터럴을 최소화한다.
5. 재사용되는 경로/상수는 적절한 위치에서 중앙 관리한다.
6. 최종 단계에서 모든 참조가 registry와 일치하는지 비교한다.
7. 최종 검증 시 실수로 생긴 식별자 변형을 검색한다.

잘못된 예:

```text
thumbnail_output/
thumbnail_outputs/
thumb_output/
thumbnail_folder/
```

Canonical Identifier가 다음이라면:

```text
THUMBNAIL_DIR
```

위와 같은 변형을 만들지 마십시오.

마찬가지로 다음처럼 이름을 섞지 마십시오.

```text
make_thumbnail
build_thumbnail
generate_thumbnail
```

Canonical public identifier는 다음과 같습니다.

```text
create_thumbnail
```

---

# Final Requirement

이 작업은 하나의 독립된 팀 컴포넌트입니다.

전체 애플리케이션을 완성하려고 하지 마십시오.

최종 결과는 다음과 같은 독립적인 컴포넌트여야 합니다.

```text
Thumbnail + File Manager component
```

그리고 내일 다음 영역과 최소 수정으로 통합 가능해야 합니다.

```text
Video module
Resource Management module
Streamlit application
```
