"""Step 0~1 검증.  python test_collect.py

ffprobe 없이 돌아가도록 ff.probe를 가짜로 바꿔서 collect.py의 판단만 확인한다.
가짜 JSON은 실제 ffprobe 출력 형태를 그대로 따랐다.
"""

import os
import time
from datetime import datetime
from pathlib import Path

import collect
import ff
import run

CONFIG = {"extensions": ["mp4", "mov"], "min_duration": 3.0, "recursive": False}


def probe_json(*, creation=None, duration=5.0, w=1920, h=1080,
               rate="30/1", audio=True, video=True):
    """실제 ffprobe -show_format -show_streams JSON 형태."""
    streams = []
    if video:
        streams.append({"codec_type": "video", "codec_name": "h264",
                        "width": w, "height": h, "avg_frame_rate": rate})
    if audio:
        streams.append({"codec_type": "audio", "codec_name": "aac"})
    fmt = {"duration": str(duration)}
    if creation:
        fmt["tags"] = {"creation_time": creation}
    return {"streams": streams, "format": fmt}


def check(label, got, want):
    assert got == want, f"{label}: {got!r} != {want!r}"
    print(f"  ok  {label}")


def test_shot_at_converts_utc_to_local():
    # ffprobe가 주는 실제 문자열 형태 (마이크로초 + Z)
    dt, src = collect._shot_at(
        probe_json(creation="2026-02-17T05:02:11.000000Z"), Path("x"))
    check("촬영 시각 출처", src, "metadata")
    # UTC 05:02 를 로컬로 옮겼는지 (KST면 14:02)
    offset = datetime.now().astimezone().utcoffset()
    check("UTC->로컬 변환", dt.hour, (5 + int(offset.total_seconds() // 3600)) % 24)


def test_shot_at_falls_back_to_mtime(tmp):
    f = tmp / "notag.mp4"
    f.write_bytes(b"x")
    os.utime(f, (1_700_000_000, 1_700_000_000))
    dt, src = collect._shot_at(probe_json(), f)
    check("메타데이터 없으면 폴백", src, "mtime")
    check("폴백 값", dt, datetime.fromtimestamp(1_700_000_000))


def test_sorts_by_shot_time_not_filename():
    # 카메라 파일명은 순환한다: DSC_9999 다음이 DSC_0001.
    # 파일명순으로 정렬하면 순서가 뒤집힌다.
    entries = [
        {"path": "DSC_0001.mp4", "creation_time": "2026-02-17T16:44:02"},
        {"path": "DSC_9999.mp4", "creation_time": "2026-02-17T14:02:11"},
    ]
    collect.sort_entries(entries)
    check("촬영 시각순 정렬", [e["path"] for e in entries],
          ["DSC_9999.mp4", "DSC_0001.mp4"])


def test_classify():
    def entry(**kw):
        base = {"vcodec": "h264", "acodec": "aac", "duration": 10.0,
                "creation_time": "2026-02-17T14:02:11"}
        return {**base, **kw}

    check("정상", collect._classify(entry(), CONFIG, None), None)
    check("영상 없음", collect._classify(entry(vcodec=None), CONFIG, None)[0], "no_video")
    check("오디오 없음", collect._classify(entry(acodec=None), CONFIG, None)[0], "no_audio")
    check("짧은 클립", collect._classify(entry(duration=1.2), CONFIG, None)[0], "too_short")
    check("경계값 3.0초는 통과", collect._classify(entry(duration=3.0), CONFIG, None), None)
    check("촬영일 불일치",
          collect._classify(entry(), CONFIG, "2026-02-18")[0], "date_filter")
    check("촬영일 일치", collect._classify(entry(), CONFIG, "2026-02-17"), None)


def test_scan(tmp, monkey):
    """폴더 스캔 전체. 확장자·길이·오디오·정렬을 한 번에 본다."""
    files = {
        "DSC_0001.mp4": probe_json(creation="2026-02-16T21:30:00.000000Z"),  # 늦게 찍힘? 아님
        "DSC_9999.mp4": probe_json(creation="2026-02-16T20:00:00.000000Z"),
        "LIVE_0003.mov": probe_json(creation="2026-02-16T20:30:00.000000Z", duration=1.2),
        "NOAUD_0004.mp4": probe_json(creation="2026-02-16T20:40:00.000000Z", audio=False),
        "IMG_5000.heic": None,
        "IMG_5001.jpg": None,
    }
    for name in files:
        (tmp / name).write_bytes(b"x")

    monkey(ff, "find_tools", lambda cfg: {"ffprobe": "ffprobe", "ffmpeg": "ffmpeg"})
    monkey(ff, "probe", lambda exe, path: files[Path(path).name])

    included, excluded = collect.scan(tmp, CONFIG)
    check("포함된 파일", [Path(e["path"]).name for e in included],
          ["DSC_9999.mp4", "DSC_0001.mp4"])  # 파일명 역순 = 촬영 시각순

    by_reason = {Path(x["path"]).name: x["reason"] for x in excluded}
    check("Live Photo 제외", by_reason["LIVE_0003.mov"], "too_short")
    check("무음 파일 제외", by_reason["NOAUD_0004.mp4"], "no_audio")
    check("heic 제외", by_reason["IMG_5000.heic"], "extension")
    check("jpg 제외", by_reason["IMG_5001.jpg"], "extension")
    check("제외 사유가 전부 남는다", len(excluded), 4)

    included, _ = collect.scan(tmp, CONFIG, name_filter="DSC_*")
    check("파일명 패턴", len(included), 2)


def test_date_filter_uses_local_date(tmp, monkey):
    """UTC 21:30 은 KST 로 다음날 06:30. 로컬 날짜로 걸러야 촬영일과 맞는다."""
    if not hasattr(time, "tzset"):
        print("  --  로컬 날짜 테스트 건너뜀 (Windows)")
        return
    os.environ["TZ"] = "Asia/Seoul"
    time.tzset()
    try:
        (tmp / "morning.mp4").write_bytes(b"x")
        monkey(ff, "find_tools", lambda cfg: {"ffprobe": "ffprobe"})
        monkey(ff, "probe",
               lambda exe, path: probe_json(creation="2026-02-16T21:30:00.000000Z"))
        included, _ = collect.scan(tmp, CONFIG, date="2026-02-17")
        check("UTC 전날 파일도 로컬 촬영일로 잡힌다", len(included), 1)
        included, _ = collect.scan(tmp, CONFIG, date="2026-02-16")
        check("UTC 날짜로는 안 잡힌다", len(included), 0)
    finally:
        del os.environ["TZ"]
        time.tzset()


def test_spec_mismatch():
    def e(w, h, fps=30.0, vcodec="h264", acodec="aac"):
        return {"width": w, "height": h, "fps": fps, "vcodec": vcodec, "acodec": acodec}

    check("전부 같으면 없음",
          collect.spec_mismatch([e(1920, 1080), e(1920, 1080)]), [])
    check("4K 섞임",
          collect.spec_mismatch([e(1920, 1080), e(3840, 2160)]), [1])
    check("fps 다름",
          collect.spec_mismatch([e(1920, 1080), e(1920, 1080, fps=59.94)]), [1])
    check("오디오 코덱 다름 (스트림 카피 시 깨질 수 있음)",
          collect.spec_mismatch([e(1920, 1080), e(1920, 1080, acodec="pcm_s16le")]), [1])
    check("빈 목록", collect.spec_mismatch([]), [])


def test_run_helpers():
    check("시:분:초", run.hms(3661), "1:01:01")
    check("분:초", run.hms(74), "01:14")
    check("번호 선택", run._pick(["3", "1"], 5), [2, 0])
    check("범위 밖은 거부", run._pick(["9"], 5), None)
    check("숫자 아니면 거부", run._pick(["a"], 5), None)


def test_guard_paths(tmp, monkey):
    """work/ 이 촬영 폴더 안이면 거부해야 한다 (원본 보호)."""
    monkey(run, "WORK", tmp / "work")
    monkey(run, "OUTPUT", tmp / "output")
    try:
        run.guard_paths(tmp)
    except SystemExit:
        print("  ok  촬영 폴더 안에서 실행하면 거부")
    else:
        raise AssertionError("촬영 폴더 안인데 통과했다")

    monkey(run, "WORK", tmp / "work")
    run.guard_paths(tmp / "elsewhere")
    print("  ok  촬영 폴더 밖이면 통과")


def main():
    import shutil
    import tempfile

    saved = []

    def monkey(module, name, value):
        saved.append((module, name, getattr(module, name)))
        setattr(module, name, value)

    tests = [
        (test_shot_at_converts_utc_to_local, 0),
        (test_shot_at_falls_back_to_mtime, 1),
        (test_sorts_by_shot_time_not_filename, 0),
        (test_classify, 0),
        (test_scan, 2),
        (test_date_filter_uses_local_date, 2),
        (test_spec_mismatch, 0),
        (test_run_helpers, 0),
        (test_guard_paths, 2),
    ]
    for fn, arity in tests:
        print(f"\n{fn.__name__}")
        tmp = Path(tempfile.mkdtemp())
        try:
            fn(*[tmp, monkey][:arity])
        finally:
            while saved:
                module, name, value = saved.pop()
                setattr(module, name, value)
            shutil.rmtree(tmp, ignore_errors=True)
    print("\n전부 통과")


if __name__ == "__main__":
    main()
