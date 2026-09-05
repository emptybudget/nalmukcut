"""영상 편집 자동화 도구.

  python run.py --dir "D:/촬영/2026" --date 2026-02-17 --merge
  python run.py --dir "D:/촬영/2026" --filter "160217*" --merge
  python run.py "D:/촬영/2026/160217_001.mp4"

--merge 없이 --dir 만 쓰면 파일 목록 확인(Step 0~1.5)까지만 하고 멈춘다.
단일 파일은 합칠 것이 없으므로 --merge 없이도 병합·전사까지 이어서 진행한다.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import yaml

import collect
import ff
import merge
import transcribe

WORK = Path("work")
OUTPUT = Path("output")

REASON = {
    "too_short": "최소 길이 미만",
    "no_audio": "오디오 트랙 없음",
    "no_video": "영상 트랙 없음",
    "date_filter": "촬영일 불일치",
    "name_filter": "파일명 패턴 불일치",
    "probe_failed": "읽기 실패",
    "manual": "사용자가 제외",
}

GATE_HELP = """  y        이 순서로 진행
  x 3 5    3번, 5번 파일 제외
  r 2      제외 목록의 2번 되살리기
  m 3 1    3번 파일을 1번 자리로 이동
  n        중단"""


def hms(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def guard_paths(source_dir):
    """work/·output/ 이 촬영 폴더 안이면 거부한다. 원본은 건드리지 않는다."""
    src = source_dir.resolve()
    for d in (WORK, OUTPUT):
        p = d.resolve()
        if p == src or src in p.parents:
            raise SystemExit(
                f"{d}/ 가 촬영 폴더({src}) 안에 있습니다.\n"
                "원본을 건드릴 위험이 있으니 촬영 폴더 밖에서 실행해주세요."
            )


def show(included, excluded, source, date):
    """Step 1.5 검수 게이트 화면."""
    mismatched = collect.spec_mismatch(included)
    where = f"\n{source} 에서 {len(included)}개 파일 찾음"
    print(where + (f" (촬영일 {date})" if date else ""))
    print("정렬 기준: 촬영 시각(메타데이터)\n")

    for i, e in enumerate(included, 1):
        clock = e["creation_time"][11:19]
        fallback = "*" if e["time_source"] == "mtime" else " "
        warn = "  ⚠️ 스펙 다름" if (i - 1) in mismatched else ""
        print(f"{i:3d}. {Path(e['path']).name:<26} {clock}{fallback} "
              f"{hms(e['duration']):>7}  {e['width']}x{e['height']} "
              f"{e['fps']:g}fps {e['vcodec']}{warn}")

    # 확장자 제외(사진 등)는 수백 개가 될 수 있어 개수만 보여준다.
    # 전체 목록은 work/sources.json 에 그대로 남는다.
    photos = [x for x in excluded if x["reason"] == "extension"]
    listed = [x for x in excluded if x["reason"] != "extension"]
    if listed:
        print(f"\n제외됨 ({len(listed)}개):")
        for i, x in enumerate(listed, 1):
            label = REASON.get(x["reason"], x["reason"])
            why = label if x["detail"] in (label, "") else f"{label}: {x['detail']}"
            print(f"{i:3d}. {Path(x['path']).name:<26} ← {why}")
    if photos:
        print(f"\n사진·기타 파일 {len(photos)}개는 확장자 단계에서 제외 "
              "(목록은 work/sources.json)")

    if any(e["time_source"] == "mtime" for e in included):
        print("\n⚠️ 촬영 시각 메타데이터가 없어 파일 수정일로 정렬한 파일이 있습니다(*).")
        print("   순서가 맞는지 특히 주의해서 확인하세요.")
    if mismatched:
        print("\n⚠️ 클립마다 해상도·fps·코덱이 다릅니다. 그냥 붙이면 깨집니다.")

    print(f"\n총 {hms(sum(e['duration'] for e in included))} → work/merged.mp4")
    return listed, mismatched


def _pick(args, count):
    """게이트 입력의 숫자 인자를 0-based 인덱스로. 범위를 벗어나면 None."""
    try:
        picked = [int(a) - 1 for a in args]
    except ValueError:
        return None
    return picked if all(0 <= i < count for i in picked) else None


def gate(included, excluded, source, date):
    """Step 1.5 — 승인받기 전에는 절대 다음으로 넘어가지 않는다."""
    while True:
        listed, mismatched = show(included, excluded, source, date)
        print("\n" + GATE_HELP)
        parts = input("\n어떻게 할까요? ").split()
        cmd, args = (parts[0].lower(), parts[1:]) if parts else ("", [])

        if cmd == "n":
            raise SystemExit("중단했습니다. 원본은 그대로입니다.")

        if cmd == "y":
            if not included:
                print("\n포함된 파일이 없습니다.\n")
                continue
            if mismatched:
                first = included[0]
                print(f"\n첫 클립 기준({first['width']}x{first['height']} "
                      f"{first['fps']:g}fps)으로 통일해서 재인코딩할까요?")
                if input("  y = 통일하고 진행 / 그 외 = 돌아가기: ").strip().lower() != "y":
                    continue
                return included, True
            return included, False

        if cmd == "x":
            picked = _pick(args, len(included))
            if picked is None:
                print("\n번호를 다시 확인해주세요.\n")
                continue
            for i in sorted(picked, reverse=True):
                excluded.append({**included.pop(i), "reason": "manual",
                                 "detail": "사용자가 제외"})

        elif cmd == "r":
            picked = _pick(args, len(listed))
            if picked is None:
                print("\n번호를 다시 확인해주세요.\n")
                continue
            for i in picked:
                entry = listed[i]
                if "duration" not in entry:
                    print(f"\n{Path(entry['path']).name} 은 되살릴 수 없습니다 "
                          f"({REASON.get(entry['reason'], entry['reason'])}).\n")
                    continue
                excluded.remove(entry)
                included.append({k: v for k, v in entry.items()
                                 if k not in ("reason", "detail")})
            collect.sort_entries(included)

        elif cmd == "m":
            picked = _pick(args, len(included))
            if picked is None or len(picked) != 2:
                print("\n'm 3 1' 처럼 옮길 번호와 갈 자리를 적어주세요.\n")
                continue
            included.insert(picked[1], included.pop(picked[0]))

        else:
            print("\n무슨 뜻인지 모르겠습니다.\n")


def save_sources(included, excluded, source, date, unify):
    WORK.mkdir(exist_ok=True)
    data = {
        "dir": str(source),
        "date": date,
        "sort": "creation_time",
        "unify": unify,
        "included": included,
        "excluded": excluded,
    }
    path = WORK / "sources.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="단일 영상 파일")
    ap.add_argument("--dir", help="촬영 폴더")
    ap.add_argument("--date", help="촬영일 필터 (YYYY-MM-DD)")
    ap.add_argument("--filter", help="파일명 glob 패턴 (예: '160217*')")
    ap.add_argument("--merge", action="store_true",
                    help="찾은 파일을 하나로 합치고 이어서 전사까지 진행")
    ap.add_argument("--remerge", action="store_true",
                    help="이미 병합된 결과(work/merge.json)가 있어도 다시 병합")
    ap.add_argument("--retranscribe", action="store_true",
                    help="이미 전사된 결과(work/transcript.json)가 있어도 다시 전사")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    if bool(args.file) == bool(args.dir):
        ap.error("영상 파일 경로 또는 --dir 중 하나만 지정해주세요")
    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            ap.error("--date 는 YYYY-MM-DD 형식이어야 합니다")

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    if args.file:
        source = Path(args.file)
        if not source.is_file():
            raise SystemExit(f"파일이 없습니다: {source}")
        guard_paths(source.parent)
        included, excluded = collect.one(source, config)
        # 단일 파일은 합칠 것이 없으니 --merge 없이도 바로 다음 단계로 진행한다.
        proceed = True
    else:
        source = Path(args.dir)
        if not source.is_dir():
            raise SystemExit(f"폴더가 없습니다: {source}")
        guard_paths(source)
        included, excluded = collect.scan(source, config, args.date, args.filter)
        proceed = args.merge

    included, unify = gate(included, excluded, source, args.date)
    path = save_sources(included, excluded, source, args.date, unify)
    print(f"\n{len(included)}개 파일 확정 → {path}")

    if not proceed:
        print("\n다음 단계(병합·전사·컷·자막)는 아직 구현 전입니다.")
        print("병합까지 이어서 하려면 --merge 를 추가하세요.")
        return

    tools = ff.find_tools(config)
    info = merge.run(included, WORK, tools, unify, config, force=args.remerge)
    label = "단일 파일 사용(병합 생략)" if info["single"] else "병합 완료"
    print(f"\n{label} → {info['path']}  ({hms(info['duration'])}, "
          f"{info['width']}x{info['height']} {info['fps']:g}fps)")

    transcript = transcribe.run(Path(info["path"]), WORK, config,
                                force=args.retranscribe)
    n_words = sum(len(s["words"]) for s in transcript["segments"])
    print(f"전사 완료: 세그먼트 {len(transcript['segments'])}개, "
          f"단어 {n_words}개, 총 {hms(transcript['duration'])}")

    print("\n다음 단계(컷 후보·미리보기·자막)는 아직 구현 전입니다.")


if __name__ == "__main__":
    main()
