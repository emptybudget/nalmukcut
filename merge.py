"""Step 1.8: 병합. 승인된 순서대로 클립을 이어붙여 work/merged.mp4 를 만든다.

파일이 1개면 병합을 생략하고 원본 파일을 그대로 가리킨다 (복사하지 않는다).
스펙이 같으면 스트림 카피(빠름), 사용자가 통일을 선택했으면 재인코딩한다.

원본은 여기서도 ffmpeg의 -i 입력으로만 쓴다. 원본 경로에 쓰는 코드는 없다.
"""

import json
from pathlib import Path

import collect
import ff

# 캐시(merge.json)와 재인코딩 결과에 공통으로 담는 정보.
INFO_KEYS = ("duration", "width", "height", "fps", "vcodec", "acodec")


def _concat_line(path):
    """concat demuxer용 한 줄. Windows 역슬래시는 concat 구문의 이스케이프 문자와
    충돌하므로 슬래시로 바꾸고, 경로에 작은따옴표가 있으면 이스케이프한다."""
    escaped = Path(path).resolve().as_posix().replace("'", r"'\''")
    return f"file '{escaped}'"


def _concat_stream_copy(ffmpeg, entries, work_dir):
    """스펙이 동일한 클립을 재인코딩 없이 그대로 이어붙인다."""
    list_file = work_dir / "concat_list.txt"
    list_file.write_text("\n".join(_concat_line(e["path"]) for e in entries) + "\n",
                         encoding="utf-8")
    out = work_dir / "merged.mp4"
    ff.run([ffmpeg, "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(out)])
    return out


def _concat_reencode(ffmpeg, entries, work_dir, config):
    """해상도·fps·코덱이 다른 클립을 첫 클립 기준으로 맞춰 재인코딩한다."""
    target = entries[0]
    w, h, fps = target["width"], target["height"], target["fps"]

    cmd = [ffmpeg, "-y"]
    filters = []
    for i, e in enumerate(entries):
        cmd += ["-i", e["path"]]
        filters.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v{i}];"
            f"[{i}:a]aresample=async=1[a{i}]"
        )
    joins = "".join(f"[v{i}][a{i}]" for i in range(len(entries)))
    filter_complex = ";".join(filters) + f";{joins}concat=n={len(entries)}:v=1:a=1[v][a]"

    out = work_dir / "merged.mp4"
    cmd += ["-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-crf", config["crf"], "-preset", config["preset"],
            "-c:a", "aac", "-b:a", config["audio_bitrate"], str(out)]
    ff.run(cmd)
    return out


def run(included, work_dir, tools, unify, config, force=False):
    """병합을 실행하고 work/merge.json 을 남긴다.

    이미 merge.json이 있으면 재실행하지 않고 재사용한다(캐시, transcript.json과 동일 방식).
    다시 병합하려면 force=True(=--remerge) 를 쓰거나 merge.json과 결과물을 지운다.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    info_path = work_dir / "merge.json"
    if info_path.exists() and not force:
        info = json.loads(info_path.read_text(encoding="utf-8"))
        print(f"이미 병합됨 (재사용): {info['path']}")
        print("다시 병합하려면 --remerge 를 쓰세요.")
        return info

    if len(included) == 1:
        # 복사하지 않는다 — 원본을 그대로 가리킨다.
        src = included[0]
        info = {"path": str(Path(src["path"]).resolve()), "single": True,
                "unify": False, **{k: src[k] for k in INFO_KEYS}}
    else:
        if unify:
            out = _concat_reencode(tools["ffmpeg"], included, work_dir, config)
        else:
            out = _concat_stream_copy(tools["ffmpeg"], included, work_dir)
        # 계산값이 아니라 실제 결과물을 다시 재서 기록한다 (스트림 카피/재인코딩
        # 과정에서 합계와 미세하게 달라질 수 있음 — 나중 단계의 타임코드 검증이
        # 이 값을 기준으로 삼으므로 실측이어야 한다).
        probed = collect.describe(tools["ffprobe"], out)
        info = {"path": str(out), "single": False, "unify": unify,
                **{k: probed[k] for k in INFO_KEYS}}

    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info
