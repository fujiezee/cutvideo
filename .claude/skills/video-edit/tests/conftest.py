"""Shared fixtures: a small synthetic clip (four 3 s shots with different pictures and a
tone), analysed once per test session. Everything renders at low resolution in draft mode
so the whole suite stays fast."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
# hermetic: never use the developer's own TTS provider (network, cost) in tests
os.environ["CUTVIDEO_TTS_USER_CONFIG"] = "/nonexistent/tts.json"
os.environ.pop("CUTVIDEO_TTS_CONFIG", None)
sys.path.insert(0, str(SCRIPTS))


def run(*args, cwd=None, check=True):
    r = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(f"{args[:3]} failed:\n{r.stderr[-2000:]}")
    return r


def probe(path):
    r = run("ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
            "-of", "json", path)
    d = json.loads(r.stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    return {"w": v["width"], "h": v["height"], "dur": float(d["format"]["duration"]),
            "audio": any(s["codec_type"] == "audio" for s in d["streams"])}


def render(edl: dict, job: Path, name: str, *flags) -> dict:
    p = job / f"{name}.json"
    p.write_text(json.dumps(edl), encoding="utf-8")
    r = run(sys.executable, SCRIPTS / "render.py", p, "--no-review", *flags, cwd=job)
    return json.loads(r.stdout[r.stdout.index("{"):])


@pytest.fixture(scope="session")
def job(tmp_path_factory) -> Path:
    """Job folder with src/clip.mp4 (640x360, 24 s, eight 3 s shots) and work/analysis.json."""
    j = tmp_path_factory.mktemp("job")
    (j / "src").mkdir()
    src = j / "src" / "clip.mp4"
    pics = ["testsrc2=size=640x360:rate=24", "color=c=0x2050C0:size=640x360:rate=24",
            "mandelbrot=size=640x360:rate=24", "color=c=0xE0C040:size=640x360:rate=24",
            "rgbtestsrc=size=640x360:rate=24", "color=c=0xF0F0F0:size=640x360:rate=24",
            "smptebars=size=640x360:rate=24", "color=c=0x10A060:size=640x360:rate=24"]
    args = []
    for k, pic in enumerate(pics):
        args += ["-f", "lavfi", "-t", "3", "-i", pic]
    n = len(pics)
    args += ["-f", "lavfi", "-t", str(3 * n), "-i", "sine=frequency=330:sample_rate=48000"]
    graph = "".join(f"[{k}:v]format=yuv420p,setsar=1[v{k}];" for k in range(n)) + \
        "".join(f"[v{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0[v];[{n}:a]volume=0.3[a]"
    run("ffmpeg", "-v", "error", "-y", *args, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", src)
    run(sys.executable, SCRIPTS / "analyze.py", src, "--out", j / "work", "--no-transcribe")
    return j


@pytest.fixture(scope="session")
def analysis(job) -> dict:
    return json.loads((job / "work" / "analysis.json").read_text())
