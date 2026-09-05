"""Step 1.8~2 검증.  python test_merge_transcribe.py

ffmpeg/ffprobe, faster-whisper 없이도 돌아가도록 ff.run/ff.probe와
faster_whisper 모듈을 가짜로 바꿔서 로직(캐싱·단일파일 스킵·정규화)만 확인한다.
실제 ffmpeg 명령어 문법은 별도로 실물 클립에 돌려 확인했다 (merge.py의
_concat_stream_copy / _concat_reencode 참고).
"""

import json
import sys
import types
from pathlib import Path

import ff
import merge
import transcribe

CONFIG = {"crf": 23, "preset": "medium", "audio_bitrate": "192k",
          "whisper_model": "large-v3", "whisper_device": "auto",
          "language": "ko"}


def check(label, got, want):
    assert got == want, f"{label}: {got!r} != {want!r}"
    print(f"  ok  {label}")


def entry(path, **kw):
    base = {"path": path, "duration": 5.0, "width": 1920, "height": 1080,
            "fps": 30.0, "vcodec": "h264", "acodec": "aac"}
    return {**base, **kw}


def test_merge_single_file_skips_encoding(tmp, monkey):
    """파일이 1개면 ffmpeg를 부르지 않고 원본을 그대로 가리킨다 (복사 없음)."""
    calls = []
    monkey(ff, "run", lambda cmd: calls.append(cmd))
    src = tmp / "only.mp4"
    src.write_bytes(b"x")

    info = merge.run([entry(str(src))], tmp / "work", {}, unify=False, config=CONFIG)
    check("ffmpeg 호출 없음", calls, [])
    check("single 플래그", info["single"], True)
    check("원본 경로 그대로", Path(info["path"]), src.resolve())
    check("복사본 안 만듦", (tmp / "work" / "merged.mp4").exists(), False)


def test_merge_uses_stream_copy_without_unify(tmp, monkey):
    calls = []
    monkey(ff, "run", lambda cmd: calls.append(cmd))
    monkey(ff, "probe", lambda exe, path: {
        "format": {"duration": "8.0"},
        "streams": [{"codec_type": "video", "codec_name": "h264",
                    "width": 1920, "height": 1080, "avg_frame_rate": "30/1"},
                   {"codec_type": "audio", "codec_name": "aac"}],
    })
    entries = [entry("a.mp4"), entry("b.mp4")]
    work = tmp / "work"
    work.mkdir()
    (work / "merged.mp4").write_bytes(b"")  # _concat_stream_copy가 만들 파일을 흉내

    info = merge.run(entries, work, {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"},
                     unify=False, config=CONFIG)
    check("스트림 카피 사용", "copy" in calls[0], True)
    check("재인코딩 옵션 없음", "libx264" in calls[0], False)
    check("merge.json 기록", json.loads((work / "merge.json").read_text())["path"],
          info["path"])


def test_merge_reencodes_when_unify(tmp, monkey):
    calls = []
    monkey(ff, "run", lambda cmd: calls.append(cmd))
    monkey(ff, "probe", lambda exe, path: {
        "format": {"duration": "8.0"},
        "streams": [{"codec_type": "video", "codec_name": "h264",
                    "width": 1920, "height": 1080, "avg_frame_rate": "30/1"},
                   {"codec_type": "audio", "codec_name": "aac"}],
    })
    entries = [entry("a.mp4"), entry("b.mp4", width=3840, height=2160)]
    work = tmp / "work"
    work.mkdir()
    (work / "merged.mp4").write_bytes(b"")

    merge.run(entries, work, {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"},
             unify=True, config=CONFIG)
    check("재인코딩 사용", "libx264" in calls[0], True)
    check("filter_complex 로 첫 클립 크기에 맞춤",
          any("scale=1920:1080" in c for c in calls[0]), True)


def test_merge_caches_by_default(tmp, monkey):
    """merge.json이 있으면 ffmpeg를 다시 부르지 않는다 (캐시)."""
    calls = []
    monkey(ff, "run", lambda cmd: calls.append(cmd))
    work = tmp / "work"
    work.mkdir()
    (work / "merge.json").write_text(json.dumps({"path": "work/merged.mp4",
                                                  "single": False, "duration": 8.0}))

    info = merge.run([entry("a.mp4"), entry("b.mp4")], work, {}, unify=False,
                     config=CONFIG)
    check("캐시 히트 시 ffmpeg 호출 없음", calls, [])
    check("캐시된 값 반환", info["duration"], 8.0)


def test_merge_force_rebuilds(tmp, monkey):
    """force=True(--remerge)면 캐시가 있어도 다시 만든다."""
    calls = []
    monkey(ff, "run", lambda cmd: calls.append(cmd))
    monkey(ff, "probe", lambda exe, path: {
        "format": {"duration": "8.0"},
        "streams": [{"codec_type": "video", "codec_name": "h264",
                    "width": 1920, "height": 1080, "avg_frame_rate": "30/1"},
                   {"codec_type": "audio", "codec_name": "aac"}],
    })
    work = tmp / "work"
    work.mkdir()
    (work / "merge.json").write_text(json.dumps({"path": "stale", "single": False}))
    (work / "merged.mp4").write_bytes(b"")

    merge.run([entry("a.mp4"), entry("b.mp4")],
             work, {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"},
             unify=False, config=CONFIG, force=True)
    check("force면 ffmpeg 다시 호출", len(calls) >= 1, True)


def _fake_faster_whisper():
    """faster_whisper 모듈을 흉내낸다. 실제 패키지 없이 transcribe.py를 테스트."""
    mod = types.ModuleType("faster_whisper")

    class Info:
        language = "ko"
        duration = 12.34

    class Word:
        def __init__(self, s, e, w):
            self.start, self.end, self.word = s, e, w

    class Segment:
        def __init__(self, s, e, text, words):
            self.start, self.end, self.text = s, e, text
            self.words = words

    class WhisperModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, path, **kw):
            check("word_timestamps 요청됨", kw.get("word_timestamps"), True)
            segs = [Segment(0.0, 1.5, " 안녕하세요 ",
                            [Word(0.0, 0.5, " 안녕"), Word(0.5, 1.5, "하세요 ")])]
            return iter(segs), Info()

    mod.WhisperModel = WhisperModel
    return mod


def test_transcribe_normalizes_and_caches(tmp, monkey):
    monkey(sys.modules, "faster_whisper", _fake_faster_whisper())
    work = tmp / "work"
    data = transcribe.run(tmp / "video.mp4", work, CONFIG)

    check("언어", data["language"], "ko")
    check("길이", data["duration"], 12.34)
    check("단어 앞뒤 공백 제거", data["segments"][0]["words"][0]["word"], "안녕")
    check("텍스트 공백 제거", data["segments"][0]["text"], "안녕하세요")
    check("캐시 파일 생성", (work / "transcript.json").exists(), True)

    # 캐시가 있으면 다시 부르지 않는다 - WhisperModel이 다시 생성되면 예외를 던지게 해서 확인
    def boom(*a, **k):
        raise AssertionError("캐시가 있는데 다시 전사했다")
    monkey(sys.modules["faster_whisper"], "WhisperModel", boom)
    data2 = transcribe.run(tmp / "video.mp4", work, CONFIG)
    check("캐시 재사용", data2["duration"], 12.34)


def test_transcribe_falls_back_to_cache_on_failure(tmp, monkey):
    """전사가 실패해도 기존 결과가 있으면 그걸 쓴다."""
    work = tmp / "work"
    work.mkdir()
    (work / "transcript.json").write_text(
        json.dumps({"language": "ko", "duration": 5.0, "segments": []}))

    mod = types.ModuleType("faster_whisper")

    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("모델 다운로드 실패")

    mod.WhisperModel = Broken
    monkey(sys.modules, "faster_whisper", mod)

    data = transcribe.run(tmp / "video.mp4", work, CONFIG, force=True)
    check("실패해도 캐시로 폴백", data["duration"], 5.0)


def test_transcribe_raises_clearly_without_cache_or_package(tmp, monkey):
    """캐시도 없고 faster-whisper도 없으면 조용히 넘어가지 않고 분명히 알린다."""
    if "faster_whisper" in sys.modules:
        monkey(sys.modules, "faster_whisper", sys.modules["faster_whisper"])
        del sys.modules["faster_whisper"]
    import builtins
    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name == "faster_whisper":
            raise ImportError("no module")
        return real_import(name, *a, **k)

    monkey(builtins, "__import__", blocked)
    try:
        transcribe.run(tmp / "video.mp4", tmp / "work", CONFIG)
    except SystemExit as e:
        check("설치 안내 포함", "pip install faster-whisper" in str(e), True)
    else:
        raise AssertionError("에러 없이 넘어갔다")


def main():
    import shutil
    import tempfile

    saved = []

    def monkey(obj, name, value):
        if isinstance(obj, dict):
            had = name in obj
            saved.append(("dict", obj, name, obj.get(name), had))
            obj[name] = value
        else:
            saved.append(("attr", obj, name, getattr(obj, name, None), True))
            setattr(obj, name, value)

    tests = [
        test_merge_single_file_skips_encoding,
        test_merge_uses_stream_copy_without_unify,
        test_merge_reencodes_when_unify,
        test_merge_caches_by_default,
        test_merge_force_rebuilds,
        test_transcribe_normalizes_and_caches,
        test_transcribe_falls_back_to_cache_on_failure,
        test_transcribe_raises_clearly_without_cache_or_package,
    ]
    for fn in tests:
        print(f"\n{fn.__name__}")
        tmp = Path(tempfile.mkdtemp())
        try:
            fn(tmp, monkey)
        finally:
            while saved:
                kind, obj, name, old, had = saved.pop()
                if kind == "dict":
                    if had:
                        obj[name] = old
                    else:
                        obj.pop(name, None)
                else:
                    setattr(obj, name, old)
            shutil.rmtree(tmp, ignore_errors=True)
    print("\n전부 통과")


if __name__ == "__main__":
    main()
