"""Text / subtitle / image overlay layer rendered with Pillow.

All overlay items are composited into one RGBA image sequence (only frames that change are
written) plus an ffmpeg concat list, so the final render needs a single overlay pass and
does not depend on ffmpeg's drawtext / libass.
"""
from __future__ import annotations

import hashlib
import math
import re
import shutil
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from common import find_font

STYLES = {
    # size is a fraction of the frame's short side
    "caption":     {"size": 0.058, "color": "#FFFFFF", "stroke_color": "#000000", "stroke": 0.09, "pos": "caption", "weight": "bold"},
    "title":       {"size": 0.11, "color": "#FFFFFF", "stroke_color": "#000000", "stroke": 0.06, "pos": "center", "shadow": True, "anim": "pop"},
    "subtitle":    {"size": 0.05, "color": "#FFFFFF", "stroke_color": "#000000", "stroke": 0.06, "pos": "center_low", "anim": "fade"},
    "highlight":   {"size": 0.085, "color": "#FFE600", "stroke_color": "#000000", "stroke": 0.1, "pos": "center", "anim": "pop", "rotate": -3},
    "lower_third": {"size": 0.045, "color": "#FFFFFF", "box": "#000000B3", "pos": "lower_left", "anim": "slide_up", "align": "left"},
    "label":       {"size": 0.04, "color": "#111111", "box": "#FFFFFFE6", "pos": "top_left", "anim": "fade", "align": "left"},
    "tag":         {"size": 0.05, "color": "#FFFFFF", "box": "#FF2D55", "pos": "top", "anim": "pop"},
    "quote":       {"size": 0.065, "color": "#FFFFFF", "stroke_color": "#000000", "stroke": 0.04, "pos": "center", "anim": "typewriter", "shadow": True},
}

ANCHORS = {
    "center": (0.5, 0.5), "top": (0.5, 0.12), "bottom": (0.5, 0.88), "left": (0.18, 0.5), "right": (0.82, 0.5),
    "top_left": (0.06, 0.07), "top_right": (0.94, 0.07), "bottom_left": (0.06, 0.93), "bottom_right": (0.94, 0.93),
    "lower_left": (0.06, 0.8), "center_low": (0.5, 0.64), "center_high": (0.5, 0.33),
}


def _rgba(c, default="#FFFFFF"):
    c = c or default
    if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{8}", c):
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5, 7))
    r, g, b, *a = ImageColor.getrgb(c)
    return (r, g, b, a[0] if a else 255)


def _is_cjk(ch):
    o = ord(ch)
    return 0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0xAC00 <= o <= 0xD7AF or 0xFF00 <= o <= 0xFFEF


def _tokens(text):
    toks, buf = [], ""
    for ch in text:
        if _is_cjk(ch) or ch == " ":
            if buf:
                toks.append(buf)
                buf = ""
            toks.append(ch)
        else:
            buf += ch
    if buf:
        toks.append(buf)
    return toks


def _wrap(text, font, max_w, draw):
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for tk in _tokens(para):
            trial = cur + tk
            if cur and draw.textlength(trial, font=font) > max_w:
                lines.append(cur.rstrip())
                cur = tk.lstrip()
            else:
                cur = trial
        lines.append(cur.rstrip())
    return lines


@lru_cache(maxsize=64)
def _font(path, size):
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()


def render_text_block(item: dict, W: int, H: int, font_path: str | None, reveal: float = 1.0) -> Image.Image:
    st = {**STYLES.get(item.get("style", "caption"), STYLES["caption"]), **item}
    short = min(W, H)
    size = st["size"]
    size = int(size * short) if size <= 1 else int(size)
    font = _font(st.get("font") or font_path, max(size, 8))
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    max_w = int(W * float(st.get("max_width", 0.86)))
    text = str(st["text"])
    lines = _wrap(text, font, max_w, tmp)
    if reveal < 1.0:  # typewriter: reveal a prefix
        total = sum(len(l) for l in lines)
        keep = int(round(total * reveal))
        out = []
        for l in lines:
            out.append(l[:max(0, keep)])
            keep -= len(l)
        lines = out
    stroke = int(size * float(st.get("stroke", 0))) if st.get("stroke_color") else 0
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * float(st.get("line_spacing", 1.12)))
    full_lines = _wrap(text, font, max_w, tmp)
    tw = max([tmp.textlength(l, font=font) for l in full_lines] + [1])
    pad = int(size * 0.35) if st.get("box") else 0
    bw, bh = int(tw + 2 * pad + 2 * stroke + 8), int(lh * len(full_lines) + 2 * pad + 2 * stroke + 8)
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if st.get("box"):
        d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=int(size * 0.25), fill=_rgba(st["box"]))
    align = st.get("align", "center")
    hl_words = st.get("highlight") or []
    for li, line in enumerate(lines):
        lw = d.textlength(full_lines[li], font=font) if li < len(full_lines) else 0
        x = pad + stroke + 4 + ((tw - lw) / 2 if align == "center" else (tw - lw) if align == "right" else 0)
        y = pad + stroke + 4 + li * lh
        if st.get("shadow"):
            sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(sh).text((x + size * 0.05, y + size * 0.07), line, font=font, fill=(0, 0, 0, 170),
                                    stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
            img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(size * 0.06)))
            d = ImageDraw.Draw(img)
        if hl_words:  # colour specific words/characters
            cx = x
            segs = re.split("(" + "|".join(re.escape(w) for w in hl_words) + ")", line)
            for sgm in segs:
                if not sgm:
                    continue
                col = _rgba(st.get("highlight_color", "#FFE600")) if sgm in hl_words else _rgba(st.get("color"))
                d.text((cx, y), sgm, font=font, fill=col, stroke_width=stroke,
                       stroke_fill=_rgba(st.get("stroke_color", "#000")))
                cx += d.textlength(sgm, font=font)
        else:
            d.text((x, y), line, font=font, fill=_rgba(st.get("color")), stroke_width=stroke,
                   stroke_fill=_rgba(st.get("stroke_color", "#000000")))
    if st.get("rotate"):
        img = img.rotate(float(st["rotate"]), resample=Image.BICUBIC, expand=True)
    return img


def _anchor(item, W, H, w, h, vertical):
    pos = item.get("pos") or STYLES.get(item.get("style", "caption"), {}).get("pos", "center")
    if pos == "caption":
        ax, ay = 0.5, (0.74 if vertical else 0.86)
    elif isinstance(pos, (list, tuple)):
        ax, ay = float(pos[0]), float(pos[1])
    else:
        ax, ay = ANCHORS.get(pos, (0.5, 0.5))
    # anchor semantics: left/right edges for side anchors, centre otherwise
    x = ax * W - w * (0 if ax < 0.15 else 1 if ax > 0.85 else 0.5)
    y = ay * H - h * (0 if ay < 0.1 else 1 if ay > 0.9 else 0.5)
    return int(x), int(y)


def _ease_out_back(p):
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


def _anim_state(item, t):
    """Return (alpha, scale, dy_fraction, reveal) for item at absolute time t."""
    st = {**STYLES.get(item.get("style", "caption"), {}), **item}
    anim = st.get("anim", "none")
    s, e = float(item["start"]), float(item["end"])
    ain = float(st.get("anim_in", 0.28))
    aout = float(st.get("anim_out", 0.18))
    p_in = min(max((t - s) / ain, 0), 1)
    p_out = min(max((e - t) / aout, 0), 1)
    alpha, scale, dy, reveal = 1.0, 1.0, 0.0, 1.0
    if anim in ("fade", "slide_up", "pop", "typewriter"):
        alpha = min(p_in if anim != "typewriter" else 1.0, p_out)
    if anim == "pop":
        scale = 0.55 + 0.45 * _ease_out_back(p_in)
        alpha = min(1.0, p_in * 2.5, p_out)
    if anim == "slide_up":
        dy = (1 - (1 - (1 - p_in) ** 3)) * 0.04
    if anim == "typewriter":
        tw = min(float(st.get("type_time", 0.06 * len(str(item["text"])))), (e - s) * 0.6)
        reveal = min(max((t - s) / max(tw, 0.01), 0), 1)
    return alpha, scale, dy, reveal


def _anim_breaks(item, fps):
    st = {**STYLES.get(item.get("style", "caption"), {}), **item}
    anim = st.get("anim", "none")
    s, e = float(item["start"]), float(item["end"])
    pts = {s, e}
    if anim == "none":
        return pts
    step = 1.0 / fps
    ain, aout = float(st.get("anim_in", 0.28)), float(st.get("anim_out", 0.18))
    if anim == "typewriter":
        ain = min(float(st.get("type_time", 0.06 * len(str(item["text"])))), (e - s) * 0.6)
        step = max(step, ain / max(len(str(item["text"])), 1))
    pts |= {round(min(s + ain, e), 4), round(max(e - aout, s), 4)}  # exact end of in/out animation
    k = 1
    while k * step < ain and s + k * step < e:
        pts.add(round(s + k * step, 4))
        k += 1
    k = 1
    while k * step < aout and e - k * step > s:
        pts.add(round(e - k * step, 4))
        k += 1
    return pts


def _place_image(item, W, H):
    im = Image.open(item["path"]).convert("RGBA")
    wf = float(item.get("width", 0.2))
    w = int(W * wf) if wf <= 1 else int(wf)
    h = int(im.height * w / im.width)
    im = im.resize((max(w, 1), max(h, 1)), Image.LANCZOS)
    if item.get("opacity", 1) < 1:
        a = im.getchannel("A").point(lambda v: int(v * float(item["opacity"])))
        im.putalpha(a)
    return im


def build_layer(texts, images, W, H, fps, total, out_dir: Path, font=None, scale=1.0):
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    font_path = find_font(font)
    vertical = H > W
    items = []
    for t in texts:
        if not str(t.get("text", "")).strip():
            continue
        it = dict(t)
        it["start"] = max(0.0, float(it.get("start", 0)))
        it["end"] = min(total, float(it.get("end", total)))
        if it["end"] - it["start"] < 0.05:
            continue
        if isinstance(it.get("size"), (int, float)) and it["size"] > 1:
            it["size"] = it["size"] * scale
        items.append(("text", it))
    img_cache = {}
    for im in images:
        it = dict(im)
        it["start"] = max(0.0, float(it.get("start", 0)))
        it["end"] = min(total, float(it.get("end") if it.get("end") is not None else total))
        it.setdefault("anim", "fade")
        img_cache[id(it)] = _place_image(it, W, H)
        items.append(("image", it))

    breaks = {0.0, total}
    for _, it in items:
        breaks |= _anim_breaks(it, fps)
    breaks = sorted(b for b in breaks if 0 <= b <= total)

    static_cache = {}
    entries, last_hash, frame_no = [], None, 0
    for a, b in zip(breaks[:-1], breaks[1:]):
        if b - a < 1e-4:
            continue
        tm = a + min((b - a) / 2, 0.5 / fps)
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sig = []
        for kind, it in items:
            if not (it["start"] <= tm < it["end"]):
                continue
            alpha, sc, dy, reveal = _anim_state(it, tm)
            if alpha <= 0.01:
                continue
            if kind == "text":
                key = (id(it), round(reveal, 3))
                if key not in static_cache:
                    static_cache[key] = render_text_block(it, W, H, font_path, reveal)
                blk = static_cache[key]
            else:
                blk = img_cache[id(it)]
            bw, bh = blk.size
            x, y = _anchor(it, W, H, bw, bh, vertical)
            if abs(sc - 1) > 0.01:
                nb = blk.resize((max(1, int(bw * sc)), max(1, int(bh * sc))), Image.BICUBIC)
                x += (bw - nb.width) // 2
                y += (bh - nb.height) // 2
                blk = nb
            if alpha < 0.999:
                blk = blk.copy()
                blk.putalpha(blk.getchannel("A").point(lambda v, al=alpha: int(v * al)))
            canvas.alpha_composite(blk, (max(0, x), max(0, int(y + dy * H))))
            sig.append((id(it), round(alpha, 2), round(sc, 2), round(dy, 3), round(reveal, 3)))
        h = hashlib.md5(repr(sig).encode()).hexdigest()
        if entries and h == last_hash:
            entries[-1][1] += b - a
            continue
        fp = out_dir / f"f{frame_no:05d}.png"
        canvas.save(fp, compress_level=1)
        frame_no += 1
        entries.append([fp, b - a])
        last_hash = h
    lst = out_dir / "list.txt"
    lines = []
    for fp, d in entries:
        lines += [f"file '{fp.as_posix()}'", f"duration {d:.4f}"]
    lines.append(f"file '{entries[-1][0].as_posix()}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst
