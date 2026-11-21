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

from common import find_font, load_font

STYLES = {
    # size is a fraction of the frame's short side
    "caption":     {"size": 0.058, "color": "#FFFFFF", "stroke_color": "#000000", "stroke": 0.09, "pos": "caption", "weight": "bold"},
    "title":       {"weight": "bold", "size": 0.11, "color": "#FFFFFF", "stroke_color": "#000000", "stroke": 0.06, "pos": "center", "shadow": True, "anim": "pop"},
    "subtitle":    {"size": 0.05, "color": "#FFFFFF", "stroke_color": "#000000", "stroke": 0.06, "pos": "center_low", "anim": "fade"},
    "highlight":   {"weight": "bold", "size": 0.085, "color": "#FFE600", "stroke_color": "#000000", "stroke": 0.1, "pos": "center", "anim": "pop", "rotate": -3},
    "lower_third": {"size": 0.045, "color": "#FFFFFF", "box": "#000000B3", "pos": "lower_left", "anim": "slide_up", "align": "left"},
    "label":       {"size": 0.04, "color": "#111111", "box": "#FFFFFFE6", "pos": "top_left", "anim": "fade", "align": "left"},
    "tag":         {"weight": "bold", "size": 0.05, "color": "#FFFFFF", "box": "#FF2D55", "pos": "top", "anim": "pop"},
    "card":        {"weight": "bold", "size": 0.062, "color": "#FFFFFF", "pos": "center", "anim": "zoom", "tracking": 0.14, "anim_in": 0.45, "anim_out": 0.35},
    "intertitle":  {"font": "serif", "size": 0.06, "color": "#EDEDED", "border": "#D8D8D8", "pos": "center",
                    "anim": "fade", "line_spacing": 1.45, "anim_in": 0.4, "anim_out": 0.3},
    "neon":        {"weight": "bold", "size": 0.085, "color": "#F0FFFF", "glow": "#00E5FF", "pos": "center",
                    "anim": "pop"},
    "stamp":       {"font": "mono", "size": 0.042, "color": "#FFFFFF", "shadow": True, "pos": "bottom_left",
                    "align": "left"},
    "text3d":      {"weight": "bold", "size": 0.13, "color": "#FFFFFF", "gradient": ["#FFFFFF", "#FFD36B"],
                    "depth": 0.16, "extrude_color": "#8A5A12", "extrude_angle": 60, "pos": "center",
                    "anim": "flip_in", "anim_in": 0.7, "anim_out": 0.3, "tracking": 0.04},
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


def _line_w(draw, line, font, track=0.0):
    return draw.textlength(line, font=font) + track * max(len(line) - 1, 0)


def _draw_line(d, x, y, line, font, colors, stroke, stroke_fill, track, boxes=None):
    """Draw one line; per-character colours and letter spacing supported."""
    if boxes and any(boxes):
        cx = x
        for ch, col, bx in zip(line, colors, boxes):
            d.text((cx, y), ch, font=font, fill=col, stroke_width=0 if bx else stroke, stroke_fill=stroke_fill)
            cx += d.textlength(ch, font=font) + track
        return
    if not track and len(set(colors)) <= 1:
        d.text((x, y), line, font=font, fill=colors[0] if colors else (255, 255, 255, 255),
               stroke_width=stroke, stroke_fill=stroke_fill)
        return
    cx = x
    for ch, col in zip(line, colors):
        d.text((cx, y), ch, font=font, fill=col, stroke_width=stroke, stroke_fill=stroke_fill)
        cx += d.textlength(ch, font=font) + track


def _wrap(text, font, max_w, draw, track=0.0):
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for tk in _tokens(para):
            trial = cur + tk
            if cur and _line_w(draw, trial, font, track) > max_w:
                lines.append(cur.rstrip())
                cur = tk.lstrip()
            else:
                cur = trial
        lines.append(cur.rstrip())
    return lines


@lru_cache(maxsize=64)
def _font(spec, size):
    return load_font(spec, size)


def _karaoke_maps(st: dict, text: str, kara: int):
    """Per-character colour and box flags for the word-by-word highlight state `kara`
    (index of the word being spoken; -1 = none yet)."""
    n = len(text)
    base = _rgba(st.get("color"))
    dim = _rgba(st["karaoke_dim"]) if st.get("karaoke_dim") else base
    hl = _rgba(st.get("karaoke_color", "#FFE600"))
    mode = st.get("karaoke", "word")
    cols, box = [dim] * n, [False] * n
    for j, w in enumerate(st.get("words", [])):
        for c in range(w["s"], min(w["e"], n)):
            if j < kara:
                cols[c] = hl if mode == "fill" else base
            elif j == kara:
                cols[c] = _rgba("#111111") if st.get("karaoke_box") else hl
                box[c] = bool(st.get("karaoke_box"))
    return cols, box


def _map_chars(text: str, lines: list[str]):
    """Index into `text` of every character of the wrapped `lines` (wrapping drops spaces)."""
    out, p = [], 0
    for line in lines:
        idx = []
        for ch in line:
            while p < len(text) and text[p] != ch:
                p += 1
            idx.append(min(p, len(text) - 1))
            p += 1
        out.append(idx)
    return out


def render_text_block(item: dict, W: int, H: int, font_path: str | None, reveal: float = 1.0,
                      kara: int | None = None) -> Image.Image:
    st = {**STYLES.get(item.get("style", "caption"), STYLES["caption"]), **item}
    short = min(W, H)
    size = st["size"]
    size = int(size * short) if size <= 1 else int(size)
    bold = st.get("weight") == "bold"
    spec = find_font(st["font"], bold=bold) if st.get("font") else (find_font(None, bold=True) if bold else font_path)
    font = _font(spec, max(size, 8))
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    max_w = int(W * float(st.get("max_width", 0.86)))
    text = str(st["text"])
    lines = _wrap(text, font, max_w, tmp, float(st.get("tracking", 0)) * size)
    if reveal < 1.0:  # typewriter: reveal a prefix
        total = sum(len(l) for l in lines)
        keep = int(round(total * reveal))
        out = []
        for l in lines:
            out.append(l[:max(0, keep)])
            keep -= len(l)
        lines = out
    stroke = int(size * float(st.get("stroke", 0))) if st.get("stroke_color") else 0
    track = float(st.get("tracking", 0)) * size  # extra letter spacing in px
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * float(st.get("line_spacing", 1.12)))
    full_lines = _wrap(text, font, max_w, tmp, track)
    tw = max([_line_w(tmp, l, font, track) for l in full_lines] + [1])
    pad = int(size * 0.35) if st.get("box") else int(size * 0.9) if st.get("border") else \
        int(size * 0.6) if st.get("glow") else 0
    bw, bh = int(tw + 2 * pad + 2 * stroke + 8), int(lh * len(full_lines) + 2 * pad + 2 * stroke + 8)
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if st.get("box"):
        d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=int(size * 0.25), fill=_rgba(st["box"]))
    if st.get("border"):  # double rule frame (silent-film intertitle)
        lw_ = max(2, int(size * 0.05))
        d.rectangle([lw_, lw_, bw - 1 - lw_, bh - 1 - lw_], outline=_rgba(st["border"]), width=lw_)
        g = int(size * 0.22)
        d.rectangle([g, g, bw - 1 - g, bh - 1 - g], outline=_rgba(st["border"]), width=max(1, lw_ // 2))
    align = st.get("align", "center")
    hl_words = st.get("highlight") or []
    line_colors = st.get("line_colors") or []
    stroke_fill = _rgba(st.get("stroke_color", "#000000"))
    kmaps = _karaoke_maps(st, text, kara) if (kara is not None and st.get("words")) else None
    cmap = _map_chars(text, full_lines) if kmaps else None
    for li, line in enumerate(lines):
        lw = _line_w(d, full_lines[li], font, track) if li < len(full_lines) else 0
        x = pad + stroke + 4 + ((tw - lw) / 2 if align == "center" else (tw - lw) if align == "right" else 0)
        y = pad + stroke + 4 + li * lh
        base = _rgba(line_colors[li] if li < len(line_colors) else st.get("color"))
        hl = _rgba(st.get("highlight_color", "#FFE600"))
        colors = [base] * len(line)
        for w in hl_words:  # colour specific words/characters
            for m in re.finditer(re.escape(w), line):
                colors[m.start():m.end()] = [hl] * (m.end() - m.start())
        boxes = None
        if kmaps and li < len(cmap):
            colors = [kmaps[0][k] for k in cmap[li][:len(line)]]
            boxes = [kmaps[1][k] for k in cmap[li][:len(line)]]
            if any(boxes):  # rounded box behind the word being spoken
                cx, x_on = x, None
                for ch, bx in zip(line + " ", boxes + [False]):
                    if bx and x_on is None:
                        x_on = cx
                    if not bx and x_on is not None:
                        padb = size * 0.12
                        d.rounded_rectangle([x_on - padb, y - padb * 0.4, cx - track + padb, y + lh * 0.92],
                                            radius=int(size * 0.18), fill=_rgba(st.get("karaoke_box")))
                        x_on = None
                    cx += d.textlength(ch, font=font) + track
        if st.get("glow"):  # neon glow: blurred coloured copies behind the text
            gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
            gc = _rgba(st["glow"])
            _draw_line(ImageDraw.Draw(gl), x, y, line, font, [gc] * len(line), int(size * 0.06), gc, track)
            img = Image.alpha_composite(img, gl.filter(ImageFilter.GaussianBlur(size * 0.28)))
            img = Image.alpha_composite(img, gl.filter(ImageFilter.GaussianBlur(size * 0.1)))
            d = ImageDraw.Draw(img)
        if st.get("shadow"):
            sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
            _draw_line(ImageDraw.Draw(sh), x + size * 0.05, y + size * 0.07, line, font, [(0, 0, 0, 170)] * len(line),
                       stroke, (0, 0, 0, 170), track)
            img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(size * 0.06)))
            d = ImageDraw.Draw(img)
        _draw_line(d, x, y, line, font, colors, stroke, stroke_fill, track, boxes)
    if st.get("gradient"):  # face filled with a vertical gradient (e.g. white -> gold)
        c0, c1 = _rgba(st["gradient"][0]), _rgba(st["gradient"][1])
        grad = Image.new("RGBA", img.size)
        gd = ImageDraw.Draw(grad)
        for yy in range(img.height):
            t_ = yy / max(img.height - 1, 1)
            gd.line([(0, yy), (img.width, yy)], fill=tuple(int(c0[i] + (c1[i] - c0[i]) * t_) for i in range(4)))
        grad.putalpha(img.getchannel("A"))
        img = grad
    if st.get("depth"):  # extruded 3D letters: shaded copies stacked behind the face
        img = _extrude(img, size, st)
    if st.get("rotate"):
        img = img.rotate(float(st["rotate"]), resample=Image.BICUBIC, expand=True)
    return img


def _extrude(face: Image.Image, size: int, st: dict) -> Image.Image:
    depth = max(2, int(size * float(st["depth"])))
    ang = math.radians(float(st.get("extrude_angle", 55)))  # direction the sides go (screen space)
    dx, dy = math.cos(ang), math.sin(ang)
    side = _rgba(st.get("extrude_color", "#3A3A3A"))
    W_, H_ = face.width + int(abs(dx) * depth) + 2, face.height + int(abs(dy) * depth) + 2
    ox, oy = (int(-dx * depth) if dx < 0 else 0), (int(-dy * depth) if dy < 0 else 0)
    out = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
    mask = face.getchannel("A")
    for k in range(depth, 0, -1):  # far to near; nearer slices lighter (simple lighting)
        lum = 0.55 + 0.45 * (1 - k / depth)
        col = tuple(int(c * lum) for c in side[:3]) + (255,)
        layer = Image.new("RGBA", face.size, col)
        layer.putalpha(mask)
        out.alpha_composite(layer, (ox + int(dx * k), oy + int(dy * k)))
    if st.get("shadow3d", True):  # soft contact shadow under the block
        sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
        sm = Image.new("RGBA", face.size, (0, 0, 0, 120))
        sm.putalpha(mask.point(lambda v: v * 120 // 255))
        sh.alpha_composite(sm, (ox + int(dx * depth * 1.4), oy + int(dy * depth * 1.4)))
        out = Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(size * 0.08)), out)
    out.alpha_composite(face, (ox, oy))
    return out


def _perspective(img: Image.Image, ry: float = 0.0, rx: float = 0.0, focal: float = 2.2) -> Image.Image:
    """Rotate a flat layer around its vertical (ry) / horizontal (rx) axis, degrees, with
    perspective; returns a new RGBA image (bounding box of the projected quad)."""
    if abs(ry) < 0.05 and abs(rx) < 0.05:
        return img
    import numpy as np
    w, h = img.size
    F = focal * max(w, h)
    a, b = math.radians(ry), math.radians(rx)
    pts = []
    for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        x1, z1 = x * math.cos(a), x * math.sin(a)          # around Y
        y1, z2 = y * math.cos(b) - z1 * math.sin(b), y * math.sin(b) + z1 * math.cos(b)  # around X
        k = F / (F + z2)
        pts.append((x1 * k, y1 * k))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    minx, miny = min(xs), min(ys)
    ow, oh = int(math.ceil(max(xs) - minx)) + 2, int(math.ceil(max(ys) - miny)) + 2
    dst = [(px - minx + 1, py - miny + 1) for px, py in pts]
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    A, B = [], []
    for (X, Y), (u, v) in zip(dst, src):  # solve output->input homography
        A += [[X, Y, 1, 0, 0, 0, -u * X, -u * Y], [0, 0, 0, X, Y, 1, -v * X, -v * Y]]
        B += [u, v]
    coef = np.linalg.solve(np.array(A, float), np.array(B, float))
    return img.transform((max(ow, 1), max(oh, 1)), Image.PERSPECTIVE, tuple(coef), Image.BICUBIC)


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
    """Return (alpha, scale, dy_fraction, reveal, rot_y, rot_x) for item at absolute time t."""
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
    if anim == "zoom":  # slow push-in for trailer cards
        alpha = min(p_in, p_out)
        scale = 1.0 + float(st.get("zoom", 0.08)) * min(max((t - s) / max(e - s, 0.01), 0), 1)
    if anim == "typewriter":
        tw = min(float(st.get("type_time", 0.06 * len(str(item["text"])))), (e - s) * 0.6)
        reveal = min(max((t - s) / max(tw, 0.01), 0), 1)
    ry, rx = float(st.get("rotate_y", 0)), float(st.get("rotate_x", 0))
    if anim == "flip_in":  # swings in around the vertical axis, settles with a slight overshoot
        q = _ease_out_back(p_in)
        ry += (1 - q) * float(st.get("flip", 80))
        alpha = min(1.0, p_in * 3, p_out)
        scale = 0.85 + 0.15 * min(q, 1.0)
    if anim == "swing":  # slow 3D sway for the whole duration
        ry += float(st.get("swing", 12)) * math.sin(2 * math.pi * (t - s) / float(st.get("period", 4.0)))
        alpha = min(p_in, p_out)
    return alpha, scale, dy, reveal, ry, rx


def _kara_index(item, t):
    words = item.get("words") or []
    if not item.get("karaoke") or not words:
        return None
    k = -1
    for j, w in enumerate(words):
        if w["t0"] <= t:
            k = j
    if k >= 0 and item.get("karaoke") == "word" and t >= words[k]["t1"] and k == len(words) - 1:
        k = len(words)  # last word finished
    return k


def _anim_breaks(item, fps):
    st = {**STYLES.get(item.get("style", "caption"), {}), **item}
    anim = st.get("anim", "none")
    s, e = float(item["start"]), float(item["end"])
    pts = {s, e}
    if item.get("karaoke"):
        for w in item.get("words") or []:
            if s < w["t0"] < e:
                pts.add(round(w["t0"], 4))
            if s < w["t1"] < e:
                pts.add(round(w["t1"], 4))
    if anim == "none":
        return pts
    step = 1.0 / fps
    if anim in ("zoom", "swing"):  # continuous motion: one frame per output frame
        k = 1
        while s + k * step < e:
            pts.add(round(s + k * step, 4))
            k += 1
        return pts
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
    if item.get("preset") == "scanlines":  # CRT / VHS scan lines over the whole frame
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        a = int(255 * float(item.get("opacity", 0.22)))
        gap = max(2, int(H / 360))
        for yy in range(0, H, gap * 2):
            d.rectangle([0, yy, W, yy + gap - 1], fill=(0, 0, 0, a))
        return im
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
        if it.get("preset"):
            it["pos"] = [0.0, 0.0]
            it["opacity"] = 1.0  # already applied inside the preset
        img_cache[id(it)] = _place_image({**im, **({"opacity": im.get("opacity", 0.22)} if im.get("preset") else {})}, W, H)
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
            alpha, sc, dy, reveal, ry, rx = _anim_state(it, tm)
            if alpha <= 0.01:
                continue
            ki = _kara_index(it, tm) if kind == "text" else None
            if kind == "text":
                key = (id(it), round(reveal, 3), ki)
                if key not in static_cache:
                    static_cache[key] = render_text_block(it, W, H, font_path, reveal, ki)
                blk = static_cache[key]
            else:
                blk = img_cache[id(it)]
            if abs(ry) > 0.05 or abs(rx) > 0.05:
                c0 = blk.size
                blk = _perspective(blk, ry, rx)
                bw, bh = c0  # anchor on the unrotated size so the block turns in place
                x, y = _anchor(it, W, H, bw, bh, vertical)
                x += (bw - blk.width) // 2
                y += (bh - blk.height) // 2
            else:
                bw, bh = blk.size
                x, y = _anchor(it, W, H, bw, bh, vertical)
            bw, bh = blk.size
            if abs(sc - 1) > 0.0005:
                nb = blk.resize((max(1, int(bw * sc)), max(1, int(bh * sc))), Image.BICUBIC)
                x += (bw - nb.width) // 2
                y += (bh - nb.height) // 2
                blk = nb
            if alpha < 0.999:
                blk = blk.copy()
                blk.putalpha(blk.getchannel("A").point(lambda v, al=alpha: int(v * al)))
            canvas.alpha_composite(blk, (max(0, x), max(0, int(y + dy * H))))
            sig.append((id(it), round(alpha, 3), round(sc, 4), round(dy, 4), round(reveal, 3), ki, round(ry, 2), round(rx, 2)))
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
