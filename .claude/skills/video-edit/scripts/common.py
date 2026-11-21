"""Shared helpers for the video-edit skill (ffmpeg wrappers, fonts, timing)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

FONT_CANDIDATES = [
    # macOS (CJK-capable first so Chinese/Japanese text renders)
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    # Windows
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/arialbd.ttf",
]


def log(*a):
    print("[video-edit]", *a, file=sys.stderr, flush=True)


def run(cmd: list[str], quiet: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    if not quiet:
        log(" ".join(str(c) for c in cmd))
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if check and p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-25:])
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{tail}")
    return p


def ffmpeg(args: list, quiet: bool = True) -> subprocess.CompletedProcess:
    return run([FFMPEG, "-hide_banner", "-y", "-loglevel", "error", *args], quiet=quiet)


def probe(path: str | Path) -> dict:
    p = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(p.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    info = {
        "path": str(Path(path).resolve()),
        "duration": float(data["format"].get("duration", 0) or 0),
        "has_video": v is not None,
        "has_audio": a is not None,
    }
    if v:
        num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den) if float(den) else 30.0
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        rot = int(v.get("tags", {}).get("rotate", rot) or rot)
        if abs(rot) % 180 == 90:
            w, h = h, w
        info.update({"width": w, "height": h, "fps": round(fps, 3), "vcodec": v.get("codec_name")})
        if not info["duration"]:
            info["duration"] = float(v.get("duration", 0) or 0)
    if a:
        info.update({"sample_rate": int(a.get("sample_rate", 48000)), "channels": int(a.get("channels", 2))})
    return info


BOLD_CANDIDATES = [  # "path#face-index"
    "/System/Library/Fonts/PingFang.ttc#8",
    "/System/Library/Fonts/Hiragino Sans GB.ttc#2",
    "/System/Library/Fonts/STHeiti Medium.ttc#1",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


SERIF_CANDIDATES = [  # intertitles, classic titles
    "/System/Library/Fonts/Supplemental/Songti.ttc#1",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
]
MONO_CANDIDATES = [  # camcorder / terminal style
    "/System/Library/Fonts/Menlo.ttc#1",
    "/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "C:/Windows/Fonts/consolab.ttf",
]


def find_font(preferred: str | None = None, bold: bool = False) -> str | None:
    """Return a font spec "path" or "path#index" (face inside a .ttc).
    `preferred` may be a path, "path#index", or an alias: "serif" / "mono"."""
    aliases = {"serif": SERIF_CANDIDATES, "mono": MONO_CANDIDATES}
    if preferred in aliases:
        lst = aliases[preferred] + FONT_CANDIDATES
    elif preferred and Path(preferred.partition("#")[0]).exists():
        return preferred
    else:
        lst = (BOLD_CANDIDATES if bold else []) + FONT_CANDIDATES
    for f in lst:
        path, _, idx = f.partition("#")
        if Path(path).exists():
            if idx:
                try:
                    from PIL import ImageFont
                    ImageFont.truetype(path, 10, index=int(idx))
                except Exception:
                    continue
            return f
    return None


def load_font(spec: str | None, size: int):
    from PIL import ImageFont
    if not spec:
        return ImageFont.load_default()
    path, _, idx = spec.partition("#")
    return ImageFont.truetype(path, size, index=int(idx or 0))


def fmt_ts(t: float) -> str:
    m, s = divmod(max(t, 0), 60)
    h, m = divmod(int(m), 60)
    return f"{h:d}:{m:02d}:{s:05.2f}" if h else f"{m:d}:{s:05.2f}"


def load_json(p: str | Path) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(obj, p: str | Path):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def decode_audio_mono(path: str | Path, sr: int = 16000):
    """Return mono float32 numpy samples at `sr` (empty array if no audio)."""
    import numpy as np

    p = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True,
    )
    if p.returncode != 0 or not p.stdout:
        return np.zeros(0, dtype="float32")
    return np.frombuffer(p.stdout, dtype="float32").copy()


def has_encoder(name: str) -> bool:
    p = run([FFMPEG, "-hide_banner", "-encoders"], check=False)
    return f" {name} " in p.stdout


def env_threads() -> str:
    return str(os.cpu_count() or 4)
