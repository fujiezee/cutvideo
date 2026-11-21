#!/usr/bin/env python3
"""Cover images (thumbnails): the strongest face frames, cropped per platform format, with
a 3D title. Writes JOB/covers/cover_<fmt>_<n>.jpg and a sheet to pick from.

Usage: cover.py JOB/work/analysis.json --title "钢铁之泪" [--subtitle "..."]
                [--formats 16:9,9:16,3:4,1:1] [--n 3] [--exclude 588-708] [--at 321.6 ...]
Candidates: one per recognised person (people.json, largest face) first, then the
largest-face close-ups; `--at` forces source times (first video).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import find_font, load_json, log  # noqa: E402

SIZES = {"16:9": (1280, 720), "9:16": (1080, 1920), "3:4": (1080, 1440), "1:1": (1080, 1080),
         "4:5": (1080, 1350)}


def candidates(an: dict, work: Path, n: int, exclude=(), at=None) -> list[dict]:
    """[{vid, t, face:[x,y,w,h] fractions or None, who}] best first."""
    if at:
        v = an["videos"][0]
        return [{"vid": v["id"], "t": float(t), "face": None, "who": ""} for t in at][:n]
    from people import load_people
    out = []
    ppl = load_people(work)  # main characters first: named, then by number of shots
    ppl = sorted(ppl, key=lambda p: (not p.get("name"), ppl.index(p)))
    for p in ppl:
        # a medium close-up reads best on a cover (extreme close-ups leave no room for a title)
        mid = [a for a in p["appearances"] if 0.015 <= a["area"] <= 0.10]
        a = max(mid or p["appearances"], key=lambda a: a["area"] if mid else -a["area"])
        if any(lo <= a["t"] < hi for _, lo, hi in exclude):
            continue
        out.append({"vid": a["vid"], "t": a["t"], "face": a["box"], "who": p.get("name") or p["id"]})
    if len(out) < n:  # no people.json, or few people: largest-face close-ups from the style features
        from styles import features
        f = features(an, work, exclude)
        for s in sorted(f["closeups"] or f["all"], key=lambda s: -s.get("face_area", s.get("score", 0))):
            t = (s["start"] + s["end"]) / 2
            if all(abs(t - c["t"]) > 20 for c in out):
                out.append({"vid": s["vid"], "t": t, "face": None, "who": ""})
            if len(out) >= n:
                break
    return out[:n]


def grab(path: str, t: float):
    import cv2
    from PIL import Image
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, fr = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"cannot read a frame at {t:.2f}s of {path}")
    return Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))


def find_face(img):
    """Largest face as fractions [x, y, w, h] (YuNet when available, else Haar)."""
    import numpy as np
    try:
        from people import Faces
        found = Faces()(np.asarray(img)[:, :, ::-1].copy())
        if found:
            return max(found, key=lambda f: f[0][2] * f[0][3])[0]
    except Exception:  # models unavailable offline
        pass
    return None


def crop_to(img, W, H, face):
    """Fill W x H; keep the face at about a third from the top and centred horizontally."""
    from PIL import Image
    sw, sh = img.size
    sc = max(W / sw, H / sh)
    big = img.resize((int(sw * sc + 0.5), int(sh * sc + 0.5)), Image.LANCZOS)
    if face:
        cx, cy = (face[0] + face[2] / 2) * big.width, (face[1] + face[3] / 2) * big.height
    else:
        cx, cy = big.width / 2, big.height / 2
    x0 = int(min(max(cx - W / 2, 0), big.width - W))
    y0 = int(min(max(cy - H * 0.36, 0), big.height - H))
    return big.crop((x0, y0, x0 + W, y0 + H)), face


def letterbox_fill(frame, W, H, face):
    """Very wide source in a tall format: the frame across the width (slightly zoomed on the
    face), a blurred copy filling the rest; returns (image, free_area_top_fraction)."""
    from PIL import ImageFilter
    bg, _ = crop_to(frame, W, H, face)
    bg = bg.filter(ImageFilter.GaussianBlur(W * 0.03)).point(lambda v: int(v * 0.55))
    fw = W
    fh = int(fw * 1.0 / 1.35)  # moderately wide window, cropped around the face
    win, _ = crop_to(frame, fw, fh, face)
    y = int(H * 0.16)
    bg.paste(win, (0, y))
    return bg, (y + fh) / H


def compose(frame, fmt, title, subtitle, face, font):
    from PIL import Image, ImageDraw, ImageEnhance
    from overlay import render_text_block
    W, H = SIZES[fmt]
    sw, sh = frame.size
    free_from = None
    if (sw / sh) / (W / H) > 2.0:  # e.g. 2.4:1 film into 9:16
        img, free_from = letterbox_fill(frame, W, H, face)
    else:
        img, _ = crop_to(frame, W, H, face)
    img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.15)).enhance(1.08).convert("RGBA")
    # where is the face in the crop? put the title on the other half (usually the bottom)
    f2 = None if free_from else find_face(img.convert("RGB"))
    face_low = f2 is not None and f2[1] + f2[3] / 2 > 0.55
    shade = Image.new("L", (1, 256))
    for y in range(256):
        a = max(0.0, (y / 255 - 0.45) / 0.55) if not face_low else max(0.0, (0.55 - y / 255) / 0.55)
        shade.putpixel((0, y), int(200 * a ** 1.4))
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    dark.putalpha(shade.resize((W, H)))
    img = Image.alpha_composite(img, dark)
    vertical = H > W
    size = (0.15 if vertical else 0.14) if len(title) <= 6 else (0.10 if vertical else 0.10)
    t_img = render_text_block({"text": title, "style": "text3d", "size": size, "max_width": 0.9}, W, H, font)
    if free_from:
        y_t = int(H * (free_from + 0.05))
    else:
        y_t = int(H * (0.06 if face_low else 0.68))
    img.alpha_composite(t_img, ((W - t_img.width) // 2, min(y_t, H - t_img.height - int(H * 0.06))))
    if subtitle:
        s_img = render_text_block({"text": subtitle, "style": "card", "size": 0.05 if vertical else 0.045,
                                   "tracking": 0.2, "max_width": 0.9}, W, H, font)
        ys = min(y_t + t_img.height + int(H * 0.01), H - s_img.height - int(H * 0.03))
        img.alpha_composite(s_img, ((W - s_img.width) // 2, ys))
    ImageDraw.Draw(img)
    return img.convert("RGB")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("analysis")
    ap.add_argument("--title", required=True)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--formats", default="16:9,9:16,3:4,1:1")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--at", nargs="*", type=float, default=None)
    ap.add_argument("--font", default=None)
    a = ap.parse_args()
    an_path = Path(a.analysis).resolve()
    an = load_json(an_path)
    work = an_path.parent
    out_dir = work.parent / "covers"
    out_dir.mkdir(exist_ok=True)
    exclude = []
    for e in a.exclude:
        vid, _, rng = e.rpartition(":")
        lo, hi = rng.split("-")
        exclude.append((vid or None, float(lo), float(hi)))
    paths = {v["id"]: v["path"] for v in an["videos"]}
    font = a.font or find_font(bold=True)
    fmts = [f.strip() for f in a.formats.split(",") if f.strip()]
    cands = candidates(an, work, a.n, exclude, a.at)
    from PIL import Image
    made = []
    for k, c in enumerate(cands):
        frame = grab(paths[c["vid"]], c["t"])
        face = c["face"] or find_face(frame)
        for fmt in fmts:
            img = compose(frame, fmt, a.title, a.subtitle, face, font)
            p = out_dir / f"cover_{fmt.replace(':', 'x')}_{k + 1}.jpg"
            img.save(p, quality=92)
            made.append((k, fmt, p))
        log(f"cover {k + 1}: {c['vid']} {c['t']:.2f}s {c['who']}")
    # sheet: one row per candidate, formats side by side at equal height
    h = 360
    rows = []
    for k in range(len(cands)):
        ims = [Image.open(p) for kk, _, p in made if kk == k]
        for im in ims:
            im.thumbnail((10000, h))
        row = Image.new("RGB", (sum(i.width for i in ims) + 10 * len(ims), h), (24, 24, 24))
        x = 0
        for im in ims:
            row.paste(im, (x, 0))
            x += im.width + 10
        rows.append(row)
    sheet = Image.new("RGB", (max(r.width for r in rows), (h + 10) * len(rows)), (24, 24, 24))
    for i, r in enumerate(rows):
        sheet.paste(r, (0, i * (h + 10)))
    sheet.save(out_dir / "sheet.jpg", quality=85)
    print(out_dir / "sheet.jpg")
    for _, fmt, p in made:
        print(p)


if __name__ == "__main__":
    main()
