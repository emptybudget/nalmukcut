"""Step 2: faster-whisper로 전사한다. 단어 단위 타임스탬프 필수.

work/transcript.json 이 있으면 재실행하지 않고 재사용한다(캐시).
전사가 실패해도 기존 transcript.json이 있으면 그걸 그대로 쓴다 (기획서 6번 실패 대비).
"""

import json


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize(segments, info):
    """faster-whisper 결과를 work/transcript.json 스키마로 정리."""
    return {
        "language": info.language,
        "duration": round(info.duration, 2),
        "segments": [
            {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
                "words": [
                    {"start": round(w.start, 2), "end": round(w.end, 2),
                     "word": w.word.strip()}
                    for w in (seg.words or [])
                ],
            }
            for seg in segments
        ],
    }


def run(video_path, work_dir, config, force=False):
    """전사 결과를 반환한다. work/transcript.json 을 캐시로 쓴다."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path = work_dir / "transcript.json"
    if out_path.exists() and not force:
        print(f"이미 전사됨 (재사용): {out_path}")
        print("다시 전사하려면 --retranscribe 를 쓰세요.")
        return _load(out_path)

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        if out_path.exists():
            print("faster-whisper 가 설치되어 있지 않아 기존 전사 결과를 재사용합니다.")
            return _load(out_path)
        raise SystemExit(
            "faster-whisper 가 설치되어 있지 않습니다.\n"
            "  pip install faster-whisper"
        )

    print(f"전사 중... ({config['whisper_model']}, 영상 길이에 따라 시간이 걸립니다)")
    try:
        model = WhisperModel(config["whisper_model"], device=config["whisper_device"],
                             compute_type=config.get("whisper_compute_type", "auto"))
        segments, info = model.transcribe(
            str(video_path), language=config["language"], word_timestamps=True)
        data = _normalize(list(segments), info)
    except Exception as e:
        if out_path.exists():
            print(f"전사 실패({e}). 기존 전사 결과를 재사용합니다.")
            return _load(out_path)
        raise RuntimeError(
            f"전사 실패: {e}\n"
            "네트워크(모델 최초 다운로드) 또는 메모리 부족일 수 있습니다."
        ) from e

    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
