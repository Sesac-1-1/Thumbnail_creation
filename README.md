# 자원 인식 영상 썸네일 생성기

영상을 올리면 **컴퓨터의 CPU·RAM 상태에 맞춰 뽑을 프레임 수를 정하고**, 처리 중 자원 변화를 기록하면서 썸네일을 만드는 Streamlit 앱입니다.

```
영상 업로드 → 자원 측정 → 샘플 수 결정 → 프레임 추출 → 썸네일 생성·저장 → 결과 + 자원 보고서
```

## 실행 방법

Python 3.10 이상이 필요합니다.

```bash
git clone https://github.com/Sesac-1-1/Thumbnail_creation.git
cd Thumbnail_creation
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
streamlit run app.py
```

브라우저가 열리면 영상(mp4, mov, avi, mkv, webm, 최대 1GB)을 올리고 **썸네일 생성**을 누릅니다.

## 화면 구성

앱은 두 페이지로 나뉘며 사이드바에서 전환합니다. **두 페이지를 서로 다른 브라우저 창에 동시에 띄울 수 있습니다.**

| 페이지 | 주소 | 내용 |
|---|---|---|
| 썸네일 생성 | `http://localhost:8501/` | 업로드, 옵션(샘플 수·크기·크롭·형식), 영상 정보, 썸네일 그리드, zip 다운로드 |
| 자원 모니터링 | `http://localhost:8501/resource` | 현재 CPU/RAM/프로세스/디스크(2초 자동 갱신), 최근 2분 추이 그래프, "지금 처리하면 N장" 정책 미리보기, 최근 처리 보고서 |

왼쪽 창에서 썸네일 생성을 누르면 오른쪽 창의 그래프에 CPU 봉우리가 실시간으로 나타나고, 처리가 끝나면 그 구간을 0.5초 간격으로 확대한 보고서 차트가 뜹니다. 자원 페이지의 **⏸ 일시정지** 버튼으로 화면을 멈추고 설명할 수 있습니다.

## 자원 기반 샘플 수 결정

처리 직전에 시스템 CPU와 RAM 사용률을 재고, 둘 중 높은 쪽으로 판단합니다.

| CPU 또는 RAM | 샘플 수 |
|---|---|
| 80% 이상 | 5장 |
| 60% 이상 | 10장 |
| 그 외 | 20장 |

여기에 두 가지 상한을 더 둡니다. 영상의 전체 프레임 수를 넘지 않고, 뽑은 프레임(가로 × 세로 × 3바이트)이 가용 RAM의 25% 안에 들어가야 합니다. 결과는 항상 1~20장입니다. 사이드바에서 자동 결정을 끄면 1~20장을 직접 지정할 수 있고, 자원 측정이 실패하면 기본값 10장으로 진행하며 화면에 사유를 표시합니다.

## 프로젝트 구조

```
app.py                 진입점 (페이지 라우팅, 서버 시작·종료 시 파일 정리)
pipeline.py            처리 오케스트레이션 (Streamlit 의존 없음)
views/
  thumbnail.py         썸네일 생성 페이지
  resource.py          자원 모니터링 페이지
  common.py            페이지 공용: 창 간 결과 공유 저장소, 상수
services/
  video_service.py     영상 메타데이터, 균등 간격 프레임 추출 (OpenCV)
  thumbnail_service.py BGR→RGB 변환, 리사이즈·중앙 크롭, JPEG/PNG 저장 (Pillow)
  file_manager.py      temp/·output/thumbnails/ 경로와 파일 수명 관리
  resource_service.py  CPU/RAM/디스크 측정, 샘플 수 정책, 처리 중 백그라운드 샘플링 (psutil)
tests/                 unittest 39개, 실제 영상 파일 없이 실행
```

의존 방향은 `views → pipeline → services` 한 방향입니다. `services/`의 모듈은 Streamlit을 전혀 모르고, 서로 간에는 `thumbnail → file_manager`, `resource → file_manager`(경로 상수만) 두 곳만 의존합니다. 그래서 UI를 바꾸거나 모듈을 따로 테스트하기 쉽습니다.

## 팀원과 담당

| 담당 | GitHub | 모듈 |
|---|---|---|
| hanjyoon01 | @hanjyoon01 | `video_service`, 자원 모듈 명세(`resource_service.md`) |
| 박찬솔 | @Restitutor1008 | `thumbnail_service`, `file_manager` |
| 김진홍 | @ghdghd8813 | `resource_service`, 앱 통합(`app.py`, `pipeline.py`, `views/`) |

## 기술 스택과 선택 이유

| 기술 | 역할 | 선택 이유 |
|---|---|---|
| Streamlit | 웹 UI | 파이썬만으로 업로더·표·차트를 만들 수 있어 하루 일정에 맞음. `st.navigation`(두 페이지), `st.fragment`(2초 자동 갱신), `st.cache_resource`(창 간 결과 공유)를 사용 |
| OpenCV | 영상 열기, 프레임 추출 | 샘플 위치로 건너뛰어 읽는 탐색 기능 덕에 10분짜리 735MB 영상도 20장만 읽어 수 초에 처리 |
| NumPy | 프레임 공통 형식 | 모듈 간 인터페이스를 "uint8 BGR ndarray"로 고정해 서로의 내부를 몰라도 연결 가능 |
| Pillow | 리사이즈, 크롭, 인코딩 | 비율 유지 여백(`pad`)과 중앙 크롭(`fit`), JPEG 품질·PNG 압축 옵션이 정리되어 있음 |
| psutil | 자원 측정 | 운영체제 차이를 감추고 시스템·프로세스 CPU/RAM/디스크를 한 API로 읽음 |
| unittest + AppTest | 테스트 | 추가 설치 없이 psutil·업로더를 mock하고, 브라우저 없이 페이지 전환·버튼 클릭까지 검증 |

## 테스트

```bash
python -m unittest discover -s tests -v
```

39개 테스트가 약 5초 안에 끝납니다. 합성 MJPEG 영상을 만들어 쓰므로 실제 영상 파일이 필요 없고, psutil·업로더·종료 훅은 mock으로 대체해 어느 컴퓨터에서든 같은 결과가 나옵니다.

## 파일이 저장되는 곳과 정리 규칙

| 종류 | 위치 | 정리 시점 |
|---|---|---|
| 올린 영상 (임시) | `temp/` | 새 영상 업로드, 앱 종료(Ctrl+C), 다음 앱 시작 |
| 썸네일 | `output/thumbnails/` | 다시 생성, 결과 지우기, 앱 종료, 다음 앱 시작 |

파일은 앱이 도는 동안만 존재합니다. 결과를 보관하려면 화면의 **썸네일 전체 다운로드 (zip)** 를 사용하세요. 두 폴더는 `.gitignore`에 포함되어 있습니다.

## 실측으로 확인해 반영한 것

- **macOS는 CPU 카운터를 약 1초에 한 번 갱신**해 `psutil.cpu_percent(interval=0.1)`이 약 40% 확률로 정확히 0.0을 반환합니다. 0.0이 나오면 0.1초씩 최대 1.1초까지 창을 늘려 읽습니다.
- **psutil 7은 non-blocking CPU 상태를 스레드별로 관리**하므로 백그라운드 샘플러는 자기 스레드 안에서 첫 읽기를 합니다.
- **업로드 파일은 Streamlit 버퍼에 한 번, `getvalue()` 복사로 또 한 번** 메모리에 올라갑니다. 디스크로 흘려 쓰는 방식으로 바꿔 735MB 영상의 메모리 사용을 절반으로 줄였습니다.
- **Streamlit 서버는 SIGINT/SIGTERM에서 `atexit`를 실행**하므로 종료 시 임시 파일 정리가 가능합니다.

## 개발 규칙

- main에는 PR로만 병합하고, 병합 전에 검토 코멘트를 남깁니다.
- 다른 팀원의 담당 모듈은 합의 후 수정합니다.
- 브랜치 이름은 `feat/<모듈>` 형식을 따릅니다.
