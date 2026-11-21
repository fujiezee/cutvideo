"""Text behind a person ("人物遮挡文字"): big text placed between the background and the
person, so the person stands in front of it.

Clip effect:
  {"type": "text_behind", "text": "AMSTERDAM", "start": 0.3, "end": 3.5,
   "style": "text3d" | any text style, "size": 0.24, "pos": [0.5, 0.42], "anim": "fade"}

Every frame in [start, end] (clip seconds) gets a person mask (rembg u2net_human_seg,
temporally smoothed; OpenCV GrabCut is far too slow per frame, so rembg is required).
Works best on static or slow shots: the text does not follow camera moves.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from common import ffmpeg, find_font, log

VERSION = 1
_S = {}


def _mask(img: Image.Image) -> np.ndarray:
    from rembg import new_session, remove
    if "s" not in _S:
        _S["s"] = new_session("u2net_human_seg")
    w = 512
    small = img.resize((w, max(1, int(img.height * w / img.width))))
    m = remove(small, session=_S["s"], only_mask=True).convert("L").resize(img.size, Image.BILINEAR)
    return np.asarray(m, dtype=np.float32) / 255.0


def apply_text_behind(clip_mp4: Path, spec: dict, fps: float, work: Path, enc: list, font=None) -> None:
    try:
        import rembg  # noqa: F401
    except ImportError:
        raise SystemExit("text_behind needs rembg (pip3 install --user 'rembg[cpu]')")
    import overlay as ovl
    from common import probe
    work = Path(work)
    if work.exists():
        shutil.rmtree(work)
    (work / "in").mkdir(parents=True)
    (work / "out").mkdir()
    dur = probe(clip_mp4)["duration"]
    s = max(0.0, float(spec.get("start", 0.0)))
    e = min(dur, float(spec.get("end", dur)))
    f0, f1 = round(s * fps), round(e * fps)
    if f1 - f0 < 2:
        return
    ffmpeg(["-i", str(clip_mp4), "-vf", f"select='between(n\\,{f0}\\,{f1 - 1})'", "-vsync", "0",
            str(work / "in" / "f%05d.png")])
    frames = sorted((work / "in").glob("f*.png"))
    W, H = Image.open(frames[0]).size
    item = {"style": "text3d", "anim": "fade", "size": 0.24, "pos": [0.5, 0.42], **spec,
            "start": s, "end": e}
    item.pop("type", None)
    font_path = find_font(font)
    block = ovl.render_text_block(item, W, H, font_path)
    vertical = H > W
    prev = None
    for k, fp in enumerate(frames):
        t = s + k / fps
        img = Image.open(fp).convert("RGB")
        m = _mask(img)
        m = m if prev is None else 0.6 * m + 0.4 * prev  # less edge flicker between frames
        prev = m
        alpha, sc, dy, _rev, ry, rx = ovl._anim_state(item, t)
        blk = block
        if abs(ry) > 0.05 or abs(rx) > 0.05:
            blk = ovl._perspective(blk, ry, rx)
        if abs(sc - 1) > 0.0005:
            blk = blk.resize((max(1, int(blk.width * sc)), max(1, int(blk.height * sc))), Image.BICUBIC)
        if alpha < 0.999:
            blk = blk.copy()
            blk.putalpha(blk.getchannel("A").point(lambda v, al=alpha: int(v * al)))
        bx, by = ovl._anchor(item, W, H, block.width, block.height, vertical)
        bx += (block.width - blk.width) // 2
        by += (block.height - blk.height) // 2 + int(dy * H)
        canvas = img.convert("RGBA")
        canvas.alpha_composite(blk, (max(0, bx), max(0, by)))
        person = img.convert("RGBA")
        person.putalpha(Image.fromarray((m * 255).astype("uint8")))
        canvas.alpha_composite(person)
        canvas.convert("RGB").save(work / "out" / f"f{k:05d}.png", compress_level=1)
    tmp = clip_mp4.with_name(clip_mp4.stem + ".behind.mp4")
    total = round(dur * fps)
    parts, graph = [], [f"[1:v]fps={fps},format=yuv420p,setsar=1[p2]"]
    if f0 > 0:
        graph.append(f"[0:v]trim=end_frame={f0},setpts=PTS-STARTPTS[p1]")
        parts.append("[p1]")
    parts.append("[p2]")
    if f0 + len(frames) < total:
        graph.append(f"[0:v]trim=start_frame={f0 + len(frames)},setpts=PTS-STARTPTS[p3]")
        parts.append("[p3]")
    graph.append(f"{''.join(parts)}concat=n={len(parts)}:v=1:a=0[v]")
    ffmpeg(["-i", str(clip_mp4), "-framerate", str(fps), "-i", str(work / "out" / "f%05d.png"),
            "-filter_complex", ";".join(graph), "-map", "[v]", "-an", *enc, str(tmp)])
    tmp.replace(clip_mp4)
    shutil.rmtree(work, ignore_errors=True)
    log(f"text behind person '{spec.get('text', '')}': {len(frames)} frames")
