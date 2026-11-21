#!/usr/bin/env python3
"""Review sheets of a rendered video so Claude can check the result visually.

Usage: preview.py VIDEO [--out DIR] [--every 2.0] [--at 1.5 3.2 ...]
Writes DIR/review_NN.jpg (labelled frames) and prints their paths.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import find_font, fmt_ts, load_font, probe  # noqa: E402


def review_sheet(video: str, marks: list[float] | None, out_dir, every: float | None = None,
                 cols: int = 4, rows: int = 4) -> list[str]:
    import cv2
    from PIL import Image, ImageDraw, ImageFont

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("review_*.jpg"):
        old.unlink()
    dur = probe(video)["duration"]
    if every is None:
        every = max(1.0, dur / 30)
    times = {round(k * every + every / 2, 2) for k in range(int(dur / every))}
    for m in marks or []:
        times.add(round(min(m + 0.25, dur - 0.05), 2))  # just after each cut / clip start
    times = sorted(t for t in times if 0 <= t < dur)
    cap = cv2.VideoCapture(video)
    frames = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, fr = cap.read()
        if ok:
            frames.append((t, fr))
    cap.release()
    if not frames:
        return []
    h0, w0 = frames[0][1].shape[:2]
    tw = 360 if w0 >= h0 else 240
    th = int(h0 * tw / w0)
    fp = find_font()
    font = load_font(fp, 18)
    paths, per = [], cols * rows if w0 >= h0 else (cols + 2) * rows
    c = cols if w0 >= h0 else cols + 2
    for pi in range(0, len(frames), per):
        chunk = frames[pi: pi + per]
        sheet = Image.new("RGB", (c * tw, math.ceil(len(chunk) / c) * th), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        for i, (t, fr) in enumerate(chunk):
            im = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)).resize((tw, th))
            x, y = (i % c) * tw, (i // c) * th
            sheet.paste(im, (x, y))
            lab = fmt_ts(t)
            d.rectangle([x, y, x + 8 + d.textlength(lab, font=font), y + 22], fill=(0, 0, 0))
            d.text((x + 4, y + 1), lab, fill=(0, 255, 140), font=font)
        p = out_dir / f"review_{pi // per:02d}.jpg"
        sheet.save(p, quality=80)
        paths.append(str(p))
    return paths


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default=None)
    ap.add_argument("--every", type=float, default=None)
    ap.add_argument("--at", type=float, nargs="*", default=[])
    a = ap.parse_args()
    out = a.out or str(Path(a.video).parent / "review")
    for p in review_sheet(a.video, a.at, out, a.every):
        print(p)
