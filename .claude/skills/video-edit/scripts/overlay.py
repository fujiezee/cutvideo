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
    font = _font(st.get("font") or (find_font(None, bold=True) if bold else font_path), max(size, 8))
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
    pad = int(size * 0.35) if st.get("box") else 0
    bw, bh = int(tw + 2 * pad + 2 * stroke + 8), int(lh * len(full_lines) + 2 * pad + 2 * stroke + 8)
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if st.get("box"):
        d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=int(size * 0.25), fill=_rgba(st["box"]))
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
        if st.get("shadow"):
            sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
            _draw_line(ImageDraw.Draw(sh), x + size * 0.05, y + size * 0.07, line, font, [(0, 0, 0, 170)] * len(line),
                       stroke, (0, 0, 0, 170), track)
            img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(size * 0.06)))
            d = ImageDraw.Draw(img)
        _draw_line(d, x, y, line, font, colors, stroke, stroke_fill, track, boxes)
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
    if anim == "zoom":  # slow push-in for trailer cards
        alpha = min(p_in, p_out)
        scale = 1.0 + float(st.get("zoom", 0.08)) * min(max((t - s) / max(e - s, 0.01), 0), 1)
    if anim == "typewriter":
        tw = min(float(st.get("type_time", 0.06 * len(str(item["text"])))), (e - s) * 0.6)
        reveal = min(max((t - s) / max(tw, 0.01), 0), 1)
    return alpha, scale, dy, reveal


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
    if anim == "zoom":  # continuous motion: one frame per output frame
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
            ki = _kara_index(it, tm) if kind == "text" else None
            if kind == "text":
                key = (id(it), round(reveal, 3), ki)
                if key not in static_cache:
                    static_cache[key] = render_text_block(it, W, H, font_path, reveal, ki)
                blk = static_cache[key]
            else:
                blk = img_cache[id(it)]
            bw, bh = blk.size
            x, y = _anchor(it, W, H, bw, bh, vertical)
            if abs(sc - 1) > 0.0005:
                nb = blk.resize((max(1, int(bw * sc)), max(1, int(bh * sc))), Image.BICUBIC)
                x += (bw - nb.width) // 2
                y += (bh - nb.height) // 2
                blk = nb
            if alpha < 0.999:
                blk = blk.copy()
                blk.putalpha(blk.getchannel("A").point(lambda v, al=alpha: int(v * al)))
            canvas.alpha_composite(blk, (max(0, x), max(0, int(y + dy * H))))
            sig.append((id(it), round(alpha, 3), round(sc, 4), round(dy, 4), round(reveal, 3), ki))
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
