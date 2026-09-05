"""Step 0~1: 촬영 폴더에서 영상 파일을 골라 촬영 시각 순으로 정렬한다.

원본은 읽기 전용으로만 접근한다. 여기에 원본을 쓰거나 옮기는 코드는 없다.
"""

import fnmatch
from datetime import datetime

import ff


def _fps(stream):
    """avg_frame_rate '30000/1001' -> 29.97"""
    num, _, den = stream.get("avg_frame_rate", "0/0").partition("/")
    try:
        return round(int(num) / int(den), 2)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _shot_at(info, path):
    """촬영 시각을 로컬 시각으로 반환. -> (datetime, "metadata" | "mtime")

    ffprobe는 creation_time을 보통 UTC로 준다. 로컬로 바꿔야 --date 필터와
    표시 시각이 실제 촬영한 날짜·시각과 맞는다. (아침 촬영은 UTC 기준으로
    전날이 되어 --date가 통째로 빗나간다)
    """
    tags = [info.get("format", {}).get("tags", {})]
    tags += [s.get("tags", {}) for s in info.get("streams", [])]
    for t in tags:
        raw = t.get("creation_time")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.replace(tzinfo=None), "metadata"
    return datetime.fromtimestamp(path.stat().st_mtime), "mtime"


def describe(ffprobe, path):
    """한 파일의 길이·해상도·fps·코덱·촬영 시각."""
    info = ff.probe(ffprobe, path)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    shot_at, time_source = _shot_at(info, path)
    return {
        "path": str(path),
        "creation_time": shot_at.isoformat(sep="T", timespec="seconds"),
        "time_source": time_source,
        "duration": round(float(info["format"].get("duration", 0)), 2),
        "width": int(video["width"]) if video else 0,
        "height": int(video["height"]) if video else 0,
        "fps": _fps(video) if video else 0.0,
        "vcodec": video["codec_name"] if video else None,
        "acodec": audio["codec_name"] if audio else None,
    }


def sort_entries(included):
    """촬영 시각 오름차순. 파일명순은 쓰지 않는다 (카메라 파일명은 순환한다)."""
    included.sort(key=lambda e: e["creation_time"])


def _classify(entry, config, date):
    """제외 사유를 돌려준다. 포함이면 None."""
    if entry["vcodec"] is None:
        return "no_video", "영상 트랙 없음"
    if date and entry["creation_time"][:10] != date:
        return "date_filter", entry["creation_time"][:10]
    if entry["duration"] < config["min_duration"]:
        return "too_short", f"{entry['duration']:.1f}초"
    if entry["acodec"] is None:
        return "no_audio", "오디오 트랙 없음"
    return None


def scan(directory, config, date=None, name_filter=None):
    """폴더를 훑어 (포함, 제외) 목록을 만든다. 제외는 사유와 함께 전부 남긴다."""
    tools = ff.find_tools(config)
    exts = {"." + e.lstrip(".").lower() for e in config["extensions"]}
    pattern = "**/*" if config.get("recursive") else "*"

    included, excluded = [], []
    for path in sorted(directory.glob(pattern)):
        if not path.is_file():
            continue
        # 사진·기타 파일은 ffprobe를 돌리기 전에 확장자로 거른다.
        if path.suffix.lower() not in exts:
            excluded.append({"path": str(path), "reason": "extension",
                             "detail": path.suffix.lower() or "확장자 없음"})
            continue
        if name_filter and not fnmatch.fnmatch(path.name, name_filter):
            excluded.append({"path": str(path), "reason": "name_filter",
                             "detail": f"'{name_filter}' 와 불일치"})
            continue
        try:
            entry = describe(tools["ffprobe"], path)
        except (RuntimeError, KeyError) as e:
            excluded.append({"path": str(path), "reason": "probe_failed",
                             "detail": str(e)})
            continue

        found = _classify(entry, config, date)
        if found:
            excluded.append({**entry, "reason": found[0], "detail": found[1]})
        else:
            included.append(entry)

    sort_entries(included)
    return included, excluded


def one(path, config):
    """단일 파일. 폴더 스캔 없이 그 파일만 본다."""
    tools = ff.find_tools(config)
    entry = describe(tools["ffprobe"], path)
    found = _classify(entry, config, None)
    if found:
        return [], [{**entry, "reason": found[0], "detail": found[1]}]
    return [entry], []


def spec_mismatch(included):
    """해상도·fps·코덱이 첫 클립과 다른 항목의 인덱스. 그냥 붙이면 깨진다."""
    if not included:
        return []
    key = lambda e: (e["width"], e["height"], e["fps"], e["vcodec"])
    first = key(included[0])
    return [i for i, e in enumerate(included) if key(e) != first]
