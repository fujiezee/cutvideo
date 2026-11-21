"""Freeze-frame character intro ("定格人物介绍").

At a chosen moment the clip freezes; the background turns monochrome/dark/tinted, the person
stays in colour with an outline and glow, an accent panel and the name + role slide in, then
the clip continues. Rendered with Pillow; person cut-out via rembg when installed, otherwise
OpenCV GrabCut seeded with a box around the person.

Clip effect spec:
  {"type": "intro", "at": 1.2, "hold": 1.8, "name": "BARLEY", "role": "Mad scientist",
   "color": "#FF3B30", "side": "auto", "bg": "bw", "box": [0.35, 0.05, 0.8, 1.0],
   "cutout": true, "index": "01", "sfx": "hit"}
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont

from common import ffmpeg, find_font, load_font, log

VERSION = 4  # bump when the look changes so cached clips re-render


# ------------------------------------------------------------------ cut-out


def cutout_mask(img: Image.Image, box=None) -> Image.Image | None:
    """Return an 'L' mask of the main person (255 = person), or None when no usable cut-out
    exists (no face and no box, e.g. an extreme close-up that fills the frame)."""
    import cv2

    W, H = img.size
    k = min(1.0, 640 / W)
    small = np.array(img.resize((int(W * k), int(H * k)))).astype("uint8")[:, :, ::-1].copy()
    h, w = small.shape[:2]
    face = _largest_face(small, box)
    if backend() == "rembg":
        m = _rembg_mask(img, box, face, k)
        if m is not None:
            return m
    if box is None:  # derive a person box from the face
        if face is None:
            return None
        else:
            fx, fy, fw, fh = face
            box = [max(0, (fx - 1.3 * fw) / w), max(0, (fy - 0.6 * fh) / h), min(1, (fx + 2.3 * fw) / w), 1.0]
    x0, y0, x1, y1 = [int(round(v)) for v in (box[0] * w, box[1] * h, box[2] * w, box[3] * h)]
    mask = np.full((h, w), cv2.GC_BGD, np.uint8)
    mask[y0:y1, x0:x1] = cv2.GC_PR_BGD
    # inner region leans foreground; edges of the box that are not frame edges are background
    bw_, bh_ = x1 - x0, y1 - y0
    ix0, ix1 = x0 + int(bw_ * 0.15), x1 - int(bw_ * 0.15)
    iy0 = y0 + int(bh_ * 0.08)
    mask[iy0:y1, ix0:ix1] = cv2.GC_PR_FGD
    band = max(2, int(min(bw_, bh_) * 0.03))
    if y0 > 1:
        mask[y0:y0 + band, x0:x1] = cv2.GC_BGD
    if x0 > 1:
        mask[y0:y1, x0:x0 + band] = cv2.GC_BGD
    if x1 < w - 1:
        mask[y0:y1, x1 - band:x1] = cv2.GC_BGD
    if face is not None:  # the face (shrunk) is certainly the person, and so is the neck below it
        fx, fy, fw, fh = face
        mask[fy + fh // 6: fy + fh - fh // 8, fx + fw // 6: fx + fw - fw // 6] = cv2.GC_FGD
        mask[fy + fh: min(h, fy + int(fh * 1.6)), fx + fw // 3: fx + fw - fw // 3] = cv2.GC_FGD
    else:  # no face found: a central core of the box
        cx0, cx1 = x0 + int(bw_ * 0.4), x1 - int(bw_ * 0.4)
        mask[y0 + int(bh_ * 0.3): y0 + int(bh_ * 0.7), cx0:cx1] = cv2.GC_FGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(small, mask, None, bgd, fgd, 8, cv2.GC_INIT_WITH_MASK)
    m = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype("uint8")
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    # keep the component(s) connected to the face / core, close small holes
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:
        if face is not None:
            fx, fy, fw, fh = face
            ids = set(np.unique(lab[fy + fh // 3: fy + 2 * fh // 3, fx + fw // 3: fx + 2 * fw // 3])) - {0}
        else:
            ids = set()
        if not ids:
            ids = {1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))}
        m = np.where(np.isin(lab, list(ids)), 255, 0).astype("uint8")
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    m = cv2.resize(m, (W, H), interpolation=cv2.INTER_LINEAR)
    return Image.fromarray(m).filter(ImageFilter.GaussianBlur(1.5))


_REMBG = {}


def backend() -> str:
    """'rembg' when installed (better edges), otherwise 'grabcut'."""
    try:
        import rembg  # noqa: F401
        return "rembg"
    except Exception:
        return "grabcut"


def _rembg_mask(img, box, face, k):
    """rembg person mask limited to the box and to the person whose face was found."""
    import cv2
    try:
        from rembg import new_session, remove
        if "s" not in _REMBG:
            _REMBG["s"] = new_session("u2net_human_seg")
        m = np.asarray(remove(img, session=_REMBG["s"], only_mask=True).convert("L")).copy()
    except Exception as e:
        log(f"rembg failed ({e}); using GrabCut")
        return None
    H, W = m.shape
    if box is not None:  # other people outside the box are not the subject
        x0, y0, x1, y1 = int(box[0] * W), int(box[1] * H), int(box[2] * W), int(box[3] * H)
        keep = np.zeros_like(m)
        keep[y0:y1, x0:x1] = 1
        m = m * keep
    binm = (m > 127).astype("uint8")
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binm)
    if n > 1:
        ids = set()
        if face is not None:  # face found on the downscaled frame -> scale back
            fx, fy, fw, fh = [int(v / k) for v in face]
            ids = set(np.unique(lab[fy + fh // 3: fy + 2 * fh // 3, fx + fw // 3: fx + 2 * fw // 3])) - {0}
        if not ids:
            ids = {1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))}
        # keep soft edges of the chosen component(s)
        sel = np.isin(lab, list(ids)).astype("uint8")
        sel = cv2.dilate(sel, np.ones((5, 5), np.uint8))
        m = m * sel
    if m.max() < 128:
        return None
    return Image.fromarray(m.astype("uint8"))


def _largest_face(bgr, box=None):
    """Largest frontal/profile face (x, y, w, h) using OpenCV's bundled Haar cascades, or None.
    With a box (fractions), only faces whose centre lies inside it count."""
    import cv2
    gray = cv2.equalizeHist(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    found = []
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + name)
        if casc.empty():
            continue
        for flip in (False, True) if "profile" in name else (False,):
            g = cv2.flip(gray, 1) if flip else gray
            for (x, y, fw, fh) in casc.detectMultiScale(g, 1.1, 5, minSize=(gray.shape[0] // 8,) * 2):
                found.append((gray.shape[1] - x - fw if flip else x, y, fw, fh))
    if box is not None:
        H_, W_ = gray.shape
        found = [f for f in found if box[0] * W_ <= f[0] + f[2] / 2 <= box[2] * W_
                 and box[1] * H_ <= f[1] + f[3] / 2 <= box[3] * H_]
    return max(found, key=lambda f: f[2] * f[3]) if found else None


# ------------------------------------------------------------------ drawing helpers


def _ease(p):
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def _rgb(c):
    return ImageColor.getrgb(c)[:3]


def _font(size, bold=True, path=None):
    return load_font(find_font(path, bold=bold), size)


def _stylize_bg(img: Image.Image, mode: str, color) -> Image.Image:
    g = img.convert("L").convert("RGB")
    if mode == "dark":
        out = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), 0.6)
    elif mode == "tint":
        tint = Image.new("RGB", img.size, color)
        out = ImageChops.multiply(g, tint)
        out = Image.blend(out, g, 0.35)
    elif mode == "blur":
        out = Image.blend(img.filter(ImageFilter.GaussianBlur(14)), Image.new("RGB", img.size, (0, 0, 0)), 0.45)
    else:  # bw
        out = Image.blend(g, Image.new("RGB", img.size, (0, 0, 0)), 0.45)
    return out


def _subject_layer(img, mask, color, short):
    """Person in colour with white outline and accent glow, as RGBA."""
    stroke_px = max(3, int(short * 0.006))
    dil = mask.filter(ImageFilter.MaxFilter(stroke_px * 2 + 1))
    glow = dil.filter(ImageFilter.MaxFilter(stroke_px * 2 + 1)).filter(ImageFilter.GaussianBlur(short * 0.02))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    layer.paste(Image.new("RGBA", img.size, (*color, 255)), (0, 0), glow.point(lambda v: int(v * 0.85)))
    layer.paste(Image.new("RGBA", img.size, (255, 255, 255, 255)), (0, 0), dil)
    person = img.convert("RGBA")
    person.putalpha(mask)
    layer.alpha_composite(person)
    return layer


def render_intro_frames(frame: Image.Image, spec: dict, fps: float, out_dir: Path, font_path=None) -> int:
    """Write intro frames f00000.png... into out_dir; returns the frame count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    W, H = frame.size
    short = min(W, H)
    hold = float(spec.get("hold", 1.8))
    color = _rgb(spec.get("color", "#FF3B30"))
    use_cut = spec.get("cutout", True)
    mask = cutout_mask(frame, spec.get("box")) if use_cut else None
    if mask is not None:  # keep the cut-out inside the picture (letterbox / pillarbox bars)
        lum = np.asarray(frame.convert("L"), dtype=float)
        rows = np.where(lum.mean(axis=1) > 4)[0]
        cols = np.where(lum.mean(axis=0) > 4)[0]
        if len(rows) and len(cols):
            clip = np.zeros_like(lum, dtype=np.uint8)
            clip[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1] = 255
            mask = ImageChops.multiply(mask, Image.fromarray(clip))
        cover = np.asarray(mask, dtype=float).mean() / 255
        if not 0.08 <= cover <= 0.85:
            log(f"intro: cut-out covers {cover:.0%} of the frame, using the no-cut-out look")
            mask = None
    elif use_cut:
        log("intro: no person found for a cut-out (give a 'box'), using the no-cut-out look")
    if mask is None:  # keep the frame in colour, darken edges so the panel reads
        vig = Image.radial_gradient("L").resize(frame.size).point(lambda v: int(v * 0.75))
        bg = Image.composite(Image.new("RGB", frame.size, (0, 0, 0)), frame, vig)
        bg = Image.blend(bg, Image.new("RGB", frame.size, (0, 0, 0)), 0.15)
    else:
        bg = _stylize_bg(frame, spec.get("bg", "bw"), color)
    subj = _subject_layer(frame, mask, color, short) if mask is not None else None

    # which side does the text go?
    side = spec.get("side", "auto")
    if side == "auto":
        if mask is not None:
            a = np.asarray(mask, dtype=float)
            cx = (a.sum(axis=0) * np.arange(W)).sum() / max(a.sum(), 1) / W
        else:
            b = spec.get("box") or [0.25, 0, 0.75, 1]
            cx = (b[0] + b[2]) / 2
        side = "left" if cx > 0.5 else "right"
    vertical = H > W

    name_f = _font(int(short * (0.1 if vertical else 0.085)), True, font_path)
    role_f = _font(int(short * 0.036), False, font_path)
    idx_f = _font(int(short * 0.03), True, font_path)
    name, role, index = str(spec.get("name", "")), str(spec.get("role", "")), str(spec.get("index", ""))
    d0 = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    nw = d0.textlength(name, font=name_f)
    rw = d0.textlength(role, font=role_f) if role else 0
    tw = max(nw, rw)
    band_y = int(H * (0.62 if vertical else 0.5))
    nh = name_f.getbbox("Ag国")[3]
    panel_h = int(nh * 1.0 + (role_f.size * 1.9 if role else 0) + short * 0.06)
    margin = int(W * 0.06)
    panel_w = int(tw + margin * 2.2)
    slant = int(panel_h * 0.35)

    n = max(1, round(hold * fps))
    for i in range(n):
        t = i / fps
        canvas = Image.blend(frame, bg, _ease(t / 0.12)).convert("RGBA")
        if subj is not None:
            s = 1 + 0.03 * t / hold
            if abs(s - 1) > 1e-3:
                sw, sh = int(W * s), int(H * s)
                big = subj.resize((sw, sh), Image.BICUBIC)
                canvas.alpha_composite(big, (-(sw - W) // 2, -(sh - H) // 2))
            else:
                canvas.alpha_composite(subj)
        # accent panel (parallelogram) sliding in from the text side
        p = _ease(t / 0.25)
        top, bot = band_y - panel_h // 2, band_y + panel_h // 2
        if side == "left":
            x1 = int(-panel_w + p * panel_w) + panel_w
            poly = [(x1 - panel_w - slant, top), (x1 + slant, top), (x1, bot), (x1 - panel_w - slant, bot)]
        else:
            x0 = int(W + panel_w - p * panel_w) - panel_w
            poly = [(x0, top), (x0 + panel_w + slant, top), (x0 + panel_w + slant, bot), (x0 - slant, bot)]
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        od.polygon(poly, fill=(*color, 235))
        # name slides with a short delay, role and index fade in
        pn = _ease((t - 0.08) / 0.25)
        pr = min(max((t - 0.22) / 0.2, 0), 1)
        tx = (margin if side == "left" else W - margin - tw)
        shift = (1 - pn) * (-W * 0.25 if side == "left" else W * 0.25)
        ny = top + int(short * 0.025)
        if index:
            od.text((tx + shift, ny - idx_f.size * 1.4), index, font=idx_f, fill=(255, 255, 255, int(255 * pr)))
        od.text((tx + shift + short * 0.004, ny + short * 0.005), name, font=name_f, fill=(0, 0, 0, int(110 * pn)))
        od.text((tx + shift, ny), name, font=name_f, fill=(255, 255, 255, int(255 * pn)))
        if role:
            od.text((tx, ny + nh + role_f.size * 0.45), role, font=role_f, fill=(255, 255, 255, int(235 * pr)))
        canvas.alpha_composite(ov)
        if t < 0.1:  # flash on the freeze
            canvas = Image.blend(canvas, Image.new("RGBA", (W, H), (255, 255, 255, 255)), 0.7 * (1 - t / 0.1))
        canvas.convert("RGB").save(out_dir / f"f{i:05d}.png", compress_level=1)
    return n


def apply_intro(clip_mp4: Path, spec: dict, fps: float, work: Path, enc: list, font_path=None) -> float:
    """Splice a freeze-frame intro into a rendered (video-only) clip. Returns the added seconds."""
    at = float(spec.get("at", 0.5))
    hold = float(spec.get("hold", 1.8))
    work = Path(work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    still = work / "still.png"
    ffmpeg(["-ss", f"{at:.3f}", "-i", str(clip_mp4), "-frames:v", "1", str(still)])
    frame = Image.open(still).convert("RGB")
    n = render_intro_frames(frame, spec, fps, work / "frames", font_path)
    tmp = clip_mp4.with_name(clip_mp4.stem + ".intro.mp4")
    a_frames = round(at * fps)
    ffmpeg(["-i", str(clip_mp4), "-framerate", str(fps), "-i", str(work / "frames" / "f%05d.png"),
            "-filter_complex",
            f"[0:v]split[a][b];[a]trim=end_frame={a_frames},setpts=PTS-STARTPTS[p1];"
            f"[b]trim=start_frame={a_frames},setpts=PTS-STARTPTS[p3];"
            f"[1:v]fps={fps},format=yuv420p,setsar=1[p2];[p1][p2][p3]concat=n=3:v=1:a=0[v]",
            "-map", "[v]", "-an", *enc, str(tmp)])
    tmp.replace(clip_mp4)
    shutil.rmtree(work, ignore_errors=True)
    log(f"intro '{spec.get('name', '')}' at {at:.2f}s, hold {n / fps:.2f}s")
    return n / fps
