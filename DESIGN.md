# 확정 설계

기획서(`SPEC.md`)를 구현하기 위한 확정 설계입니다.
여러 세션에 걸쳐 나눠 만들기 때문에, **스텝 사이의 데이터 계약**을 여기에 고정합니다.
구현 세션은 이 문서의 JSON 포맷과 함수 시그니처를 그대로 지키세요.

---

## 확정된 선택

| 항목 | 선택 | 이유 |
|---|---|---|
| STT | `faster-whisper`, 모델 `large-v3` | pip 한 줄, 맥/윈도우 동일, `word_timestamps` 지원 |
| 컷 렌더 | **재인코딩 필수**, libx264 CRF 23 | 아래 "왜 스트림 카피가 불가능한가" 참조 |
| 병합 렌더 | 스펙 같으면 스트림 카피 (concat demuxer) | 클립 경계는 원래 키프레임이라 정확 |
| 테스트 | ffmpeg로 만든 합성 클립 | 실제 촬영분은 로컬에서 최종 확인 |
| 의존성 | `faster-whisper`, `PyYAML` + ffmpeg 바이너리. 그 외 없음 | FFmpeg 래퍼 라이브러리 설치 금지 (기획서 9번) |

---

## 파일 구조

파이프라인 스텝과 1:1로 맞춘 평평한 모듈. 클래스·상속·의존성 주입 없이 함수만.

```
run.py          CLI 파싱 + 스텝 호출 순서 + 검수 게이트 input() 대기
ff.py           ffprobe/ffmpeg subprocess 호출 헬퍼 (모든 모듈이 공유)
collect.py      Step 0~1    스캔·필터·creation_time 정렬·제외 사유 수집
merge.py        Step 1.8    concat 또는 재인코딩
transcribe.py   Step 2      faster-whisper -> work/transcript.json (캐시)
cuts.py         Step 3      무음·필러 -> work/cutlist.json
preview.py      Step 4      썸네일 추출 + work/preview.html 생성
timeline.py     Step 5 핵심  keep 구간 산출 + 원본시각->출력시각 매핑
subtitles.py    Step 5      timeline.py를 써서 srt 생성
render.py       Step 7      keep 구간 렌더 + 검증
config.yaml
glossary.txt
```

`timeline.py`가 별도인 이유: **렌더와 자막이 반드시 같은 계산을 써야** 타임코드가 어긋나지 않습니다.
한 곳에 두고 양쪽이 import 합니다.

검수 게이트(1.5 / 4 / 6)는 별도 모듈을 만들지 않고 `run.py`에서 출력 + `input()`으로 처리합니다.

---

## 왜 컷 렌더에 스트림 카피가 불가능한가

기획서 Step 7은 "가능하면 스트림 카피"라고 되어 있지만, 컷 렌더에서는 쓸 수 없습니다.

스트림 카피는 **키프레임 경계에서만** 정확히 자릅니다. 키프레임 간격은 보통 1~2초입니다.
0.5초 무음을 자르려 하면 컷 지점이 키프레임까지 밀리거나 앞부분이 깨집니다.
그러면 **실제 영상 길이 ≠ 계산한 길이**가 되고, Step 5에서 계산한 srt 타임코드가 전부 어긋납니다.
기획서가 가장 경계하는 실패가 바로 이것이므로, 컷 렌더는 재인코딩으로 고정합니다.

**병합(Step 1.8)은 그대로 스트림 카피를 씁니다.** 클립 경계는 원래 키프레임이라 정확합니다.

---

## 무음 판정 — 신호 2개 교차검증

Whisper 단어 갭만으로 자르면, **Whisper가 놓친 발화가 통째로 잘려나갑니다.** 복구 불가능한 손실입니다.

그래서 두 신호가 **모두** 무음이라고 할 때만 컷 후보로 올립니다.

1. `ffmpeg -af silencedetect=n={silence_db}dB:d={silence_min}` → 실제 오디오 무음 구간
2. transcript의 단어 사이 갭 → 발화가 없는 구간

교집합만 후보. 여기에 기획서 Step 3의 조건을 추가로 적용합니다.

- 앞뒤가 모두 발화 구간일 것 (영상 맨 앞/뒤 무음은 인트로/아웃트로이므로 제외)
- 길이가 `keep_silent_over`(10초) 미만일 것 — 넘으면 B-roll로 보고 자동 제외
- 앞뒤로 `padding`(0.1초) 남기기
- `--protect` 구간과 겹치면 제외

---

## 데이터 계약

### `work/sources.json` (collect.py 생성)

```json
{
  "dir": "D:/촬영/2026",
  "sort": "creation_time",
  "included": [
    {
      "path": "D:/촬영/2026/160217_001.mp4",
      "creation_time": "2026-02-17T14:02:11",
      "time_source": "metadata",
      "duration": 134.2,
      "width": 1920, "height": 1080, "fps": 30.0,
      "vcodec": "h264", "acodec": "aac"
    }
  ],
  "excluded": [
    {"path": "...", "reason": "too_short",  "detail": "1.2초"},
    {"path": "...", "reason": "no_audio",   "detail": "오디오 트랙 없음"},
    {"path": "...", "reason": "extension",  "detail": ".heic"},
    {"path": "...", "reason": "date_filter","detail": "2026-02-16"}
  ]
}
```

- `time_source`는 `"metadata"`(ffprobe `format_tags=creation_time`) 또는 `"mtime"`(폴백).
  `"mtime"`이 하나라도 있으면 Step 1.5에서 반드시 경고를 띄웁니다.
- `excluded`는 **버리지 않고 전부 남깁니다.** 제외 사유를 사용자에게 보여줘야 하기 때문입니다
  (기획서 9번 "에러를 조용히 삼키지 않기").

### `work/transcript.json` (transcribe.py 생성)

faster-whisper 출력을 아래 형태로 정규화해서 저장합니다.

```json
{
  "language": "ko",
  "duration": 1458.2,
  "segments": [
    {
      "start": 12.34, "end": 15.80, "text": "오늘은 여기 왔습니다",
      "words": [
        {"start": 12.34, "end": 12.61, "word": "오늘은"},
        {"start": 12.70, "end": 13.05, "word": "여기"}
      ]
    }
  ]
}
```

이 파일이 있으면 재실행 시 전사를 건너뜁니다(캐시). `--retranscribe`로 강제 재실행.

### `work/cutlist.json` (cuts.py 생성, preview.py가 갱신)

```json
{
  "source": "work/merged.mp4",
  "duration": 1458.2,
  "cuts": [
    {"id": 1, "start": 131.2, "end": 132.4, "reason": "silence",
     "enabled": true,  "auto_excluded": false},
    {"id": 2, "start": 340.0, "end": 358.0, "reason": "silence",
     "enabled": false, "auto_excluded": true, "note": "10초 초과 — B-roll 추정"},
    {"id": 3, "start": 362.1, "end": 362.8, "reason": "filler",
     "enabled": true,  "auto_excluded": false, "word": "어"}
  ]
}
```

- `enabled`가 **실제로 자를지**를 결정하는 유일한 필드입니다.
- `preview.html`은 이 `enabled`만 바꿔서 다시 저장합니다. 다른 필드는 건드리지 않습니다.
- `auto_excluded: true`는 자동 제외된 항목. preview에서 회색으로 표시하되, 체크하면 다시 살아납니다.

---

## Step 5 — 타임코드 재계산 (핵심)

기획서가 지목한 가장 어려운 부분입니다. 접근을 바꿔서 **어긋날 수 없게** 만듭니다.

### 원칙: 컷이 아니라 keep이 진실의 원천

컷 구간을 빼면서 오프셋을 누적하는 방식은 경계 조건에서 미묘하게 어긋납니다.
대신 **유지 구간(keep) 리스트를 먼저 확정**하고, 렌더와 자막이 **둘 다 그 리스트만** 씁니다.
같은 입력에서 같은 함수로 계산하므로 어긋날 여지가 구조적으로 없습니다.

### `timeline.py`

```python
def build_keeps(duration, cuts):
    """enabled=true인 cut만 모아 정렬 -> 겹치거나 맞닿은 것 병합
    -> 그 사이의 유지 구간 리스트 반환.
    반환: [{"src_start":0.0, "src_end":131.2, "out_start":0.0}, ...]
    길이 0인 구간은 버린다. out_start는 앞선 keep들의 길이 누적합."""

def out_duration(keeps):
    """출력 영상의 총 길이 = 모든 keep 길이의 합"""

def map_time(t, keeps):
    """원본 시각 t -> 출력 시각. t가 잘려나간 구간이면 None."""

def map_clamped(t, keeps, side):
    """t가 잘린 구간에 있을 때 경계로 밀어낸다.
    side='start' -> 다음 keep의 시작으로 당김
    side='end'   -> 이전 keep의 끝으로 밈
    양쪽 다 없으면 None (자막 전체가 잘린 경우)"""
```

`build_keeps`에서 **컷 병합을 먼저 하는 것이 중요합니다.** 무음 컷과 필러 컷이 겹치면
(예: "어" 뒤에 바로 무음) 병합하지 않고 각각 빼면 길이가 중복으로 차감됩니다.

### 자막 생성 순서를 뒤집는다

기존 발상은 `transcript segment → srt → 타임코드 보정`인데, 이러면 두 가지 버그가 남습니다.
- 잘려나간 필러("어")가 **자막 텍스트에는 그대로 남습니다.**
- 보정을 빠뜨린 경로가 하나라도 있으면 밀립니다.

그래서 **단어 단위에서 먼저 거르고, 출력 타임라인에서 문장을 다시 만듭니다.**

```
1. transcript의 모든 word를 펼친다
2. keep 안에 없는 word는 버린다            <- 잘린 말·필러가 텍스트에서도 자동으로 사라짐
3. 남은 word마다 map_time()으로 출력 시각 부여   <- 처음부터 출력 기준이라 "밀림"이 발생 불가능
4. 원본 segment 경계를 기준으로 word를 다시 묶는다
5. 20자*2줄=40자를 넘으면 word 경계에서 분할
6. glossary.txt로 치환
7. 겹침·최소노출 규칙 적용
8. srt로 출력 (UTF-8, BOM 없음)
```

컷을 가로지르는 자막은 **분할하지 않고 이어붙입니다.** 컷 대상이 무음/필러이므로 말은 이어집니다.
분할하면 한 문장이 두 자막으로 쪼개져 캡컷에서 오히려 지저분해집니다.

### 겹침 규칙 (우선순위 주의)

- 인접 자막이 겹치면 앞 자막의 끝을 뒤 자막의 시작에 맞춥니다.
- 최소 노출 1초는 **다음 자막 시작을 넘지 않는 선에서만** 보장합니다.
- **겹침 금지가 최소 노출보다 우선입니다.** 순서를 반대로 하면 캡컷에서 자막이 겹쳐 뜹니다.

### 검증 (Step 7에서 실행)

- 마지막 자막 end ≤ `out_duration(keeps)`
- 모든 자막 start < end
- 인접 자막 겹침 없음
- `out_duration(keeps)`와 실제 `final.mp4` 길이 차이 ≤ 2초

하나라도 실패하면 어긋난 자막 번호를 표시하고 중단합니다.

---

## Step 7 — 렌더 방식

keep 구간마다 파트 파일을 만들고 concat 합니다.

```
keep마다:  ffmpeg -ss {src_start} -to {src_end} -i work/merged.mp4 \
             -c:v libx264 -crf 23 -preset medium -c:a aac -b:a 192k \
             work/parts/{i:04d}.mp4
전체:      concat demuxer로 파트들을 스트림 카피 결합 -> output/final.mp4
```

- 재인코딩이므로 `-ss`가 프레임 정확합니다.
- 파트들은 인코딩 파라미터가 동일하므로 concat 시 스트림 카피가 안전합니다.
- 파트 파일이 남으므로 **실패한 지점부터 재시작할 수 있습니다** (기획서 6번 요구).
- 이미 있는 파트는 다시 인코딩하지 않습니다.

단일 `select`/`aselect` 필터로 한 번에 처리하는 방법도 있지만, keep이 수십 개면 필터 문자열이
수천 자가 되고 실패 시 재시작이 불가능해서 쓰지 않습니다.

---

## 원본 보호 (타협 없음)

- 원본은 ffmpeg의 `-i` 입력으로만 사용합니다. 원본 경로에 쓰는 명령은 만들지 않습니다.
- Python에서 원본을 열 때는 읽기 모드만 씁니다.
- `work/`와 `output/`이 촬영 폴더 안이나 그 하위이면 **시작 전에 거부**합니다.
- `output/`에 같은 이름이 있으면 덮어쓰기 전에 확인받습니다.

---

## 크로스플랫폼

- 경로는 전부 `pathlib.Path`. 문자열 결합 금지.
- ffmpeg/ffprobe 경로는 `config.yaml`에서 읽고, 없으면 PATH에서 찾습니다.
- 최초 실행 시 ffmpeg·ffprobe 존재를 확인하고, 없으면 OS별 설치 안내를 출력하고 중단합니다.
- srt는 UTF-8로 쓰되 **BOM 없이** 씁니다 (캡컷 임포트 호환).

---

## 구현 순서

기획서 8번을 따릅니다. 한 번에 다 만들지 않습니다.

| 순서 | 범위 | 확인할 것 |
|---|---|---|
| 1 | Step 0~1.5 (`collect.py`, `run.py` 뼈대, `ff.py`) | 파일이 제대로 걸러지고 촬영시각 순서가 맞는지 |
| 2 | Step 1.8~2 (`merge.py`, `transcribe.py`) | 병합 타임코드와 전사가 맞는지 |
| 3 | Step 3~4 (`cuts.py`, `preview.py`) | 짧은 클립 2~3개로 컷 후보가 말이 되는지 |
| 4 | Step 5 (`timeline.py`, `subtitles.py`) | 설계는 확정됨. 구현만 하면 됨 |
| 5 | 캡컷 임포트 테스트 | **타임코드 밀림 여부를 눈으로 확인** |
| 6 | Step 6~7 (`render.py`, 검수 게이트) | 검증 4종이 실제로 잡아내는지 |

---

## 미해결 — 구현 중 확인 필요

**Whisper가 필러를 전사에 포함하지 않을 수 있습니다.**
Whisper는 "어", "음" 같은 필러를 자동으로 생략하는 경향이 있습니다.
그러면 Step 3의 필러 제거가 잡을 대상 자체가 없습니다.
실제 한국어 음성으로 확인해봐야 알 수 있으므로, 3번 순서에서 점검합니다.
만약 전사에 안 잡히면 필러 제거 기능은 조용히 아무것도 안 하게 두고
(무음 컷은 정상 동작), 사용자에게 그 사실을 알립니다. 억지로 다른 방법을 만들지 않습니다.
