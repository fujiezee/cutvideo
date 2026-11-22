#!/usr/bin/env python3
"""Caption style gallery: every caption style on the same frame, to choose from.

Usage: captions.py VIDEO --at 9.5 [--text "四十年后，{机器人}占领了这座城市"] [--out sheet.jpg]
       captions.py --list
`{word}` marks the highlighted word. Styles shown: the caption designs in overlay.STYLES.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import find_font  # noqa: E402
from overlay import STYLES, _anchor, render_text_block  # noqa: E402

CAPTION_STYLES = {
    "caption": "plain dialogue subtitles",
    "explainer": "narration: big bold, keywords coloured",
    "variety": "variety-show 综艺: gradient fill, double outline, tilt",
    "bigword": "vertical video: big words, lower centre",
    "boxed": "translucent bar with an accent stripe",
    "soft": "white rounded box, dark text (vlog)",
    "news": "news bar, left aligned",
    "cinema": "film: light serif, wide spacing",
    "neon_caption": "neon glow (night, tech)",
}


def frame_at(video: str, t: float):
    import cv2
    from PIL import Image
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, fr = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"no frame at {t}s")
    return Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)).convert("RGBA")


def gallery(bg, text: str, out: Path, styles=None, tile_w=640):
    from PIL import Image, ImageDraw
    hl = re.findall(r"\{([^{}]+)\}", text)
    plain = re.sub(r"[{}]", "", text)
    styles = styles or list(CAPTION_STYLES)
    W, H = bg.size
    font = find_font()
    tiles = []
    for name in styles:
        item = {"text": plain, "style": name, "highlight": hl}
        if name in ("explainer", "variety", "bigword", "boxed", "soft", "news"):
            item["text"] = re.sub(r"\s*[，、；：]\s*", "　", plain).rstrip("。")
        img = bg.copy()
        b = render_text_block(item, W, H, font)
        x, y = _anchor({**STYLES.get(name, {}), **item}, W, H, b.width, b.height, H > W)
        img.alpha_composite(b, (x, y))
        t = img.convert("RGB")
        t.thumbnail((tile_w, tile_w))
        ImageDraw.Draw(t).rectangle([0, 0, 260, 22], fill=(0, 0, 0))
        ImageDraw.Draw(t).text((6, 5), f"{name}", fill=(255, 214, 10))
        tiles.append(t)
    cols = 3
    th = tiles[0].height
    sheet = Image.new("RGB", (cols * tile_w + (cols - 1) * 8, ((len(tiles) + cols - 1) // cols) * (th + 8)), (20, 20, 20))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * (tile_w + 8), (i // cols) * (th + 8)))
    sheet.save(out, quality=88)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?")
    ap.add_argument("--at", type=float, default=None)
    ap.add_argument("--text", default="四十年后，{机器人}占领了这座城市")
    ap.add_argument("--styles", default=None, help="comma list (default: all caption designs)")
    ap.add_argument("--out", default="caption_styles.jpg")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list or not a.video:
        for k, v in CAPTION_STYLES.items():
            print(f"{k:14s} {v}")
        return
    bg = frame_at(a.video, a.at if a.at is not None else 1.0)
    out = gallery(bg, a.text, Path(a.out), a.styles.split(",") if a.styles else None)
    print(out)


if __name__ == "__main__":
    main()
