"""ffmpeg / ffprobe 호출 헬퍼. 다른 모듈은 전부 여기를 거친다."""

import json
import shutil
import subprocess


def find_tools(config):
    """config에 적힌 실행 파일을 찾는다. 없으면 설치 안내 후 중단."""
    tools = {}
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(config.get(name, name))
        if not path:
            raise SystemExit(
                f"{name} 을(를) 찾을 수 없습니다.\n"
                "  macOS:   brew install ffmpeg\n"
                "  Windows: winget install Gyan.FFmpeg\n"
                f"이미 설치했다면 config.yaml 의 '{name}' 에 전체 경로를 적어주세요."
            )
        tools[name] = path
    return tools


def run(cmd):
    """실행하고 실패하면 stderr를 그대로 올린다 (조용히 삼키지 않는다)."""
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} 실패: {p.stderr.strip().splitlines()[-1:]}")
    return p.stdout


def probe(ffprobe, path):
    """ffprobe 결과를 dict로. 원본은 읽기만 한다."""
    out = run([ffprobe, "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", path])
    return json.loads(out)
