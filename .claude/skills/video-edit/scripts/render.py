#!/usr/bin/env python3
"""Render an EDL (edit decision list, JSON) into a finished video with ffmpeg.

Pipeline: each clip -> normalised segment (trim, speed, fit to frame, effects, grade)
          -> joined with cuts / xfade transitions
          -> text / subtitle / image layer (rendered with Pillow, one overlay pass)
          -> audio mix (clip audio + music with ducking) -> loudness normalisation.

Usage:
  render.py EDL.json [--draft] [--no-review]
    --draft      half resolution, fast encode (for quick iteration)
    --no-review  skip the review contact sheet of the output
See reference/edl.md for the EDL format.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import fmt_ts, ffmpeg, load_json, log, probe, save_json  # noqa: E402
import overlay as ovl  # noqa: E402

XFADE = {
    "fade", "fadeblack", "fadewhite", "dissolve", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown", "smoothleft", "smoothright", "smoothup",
    "smoothdown", "circleopen", "circleclose", "circlecrop", "rectcrop", "radial", "zoomin",
    "pixelize", "hblur", "distance", "squeezeh", "squeezev", "coverleft", "coverright",
    "revealleft", "revealright", "diagtl", "diagbr", "hlslice", "hrslice", "vuslice", "vdslice",
    "fadegrays", "horzopen", "horzclose", "vertopen", "vertclose",
}
# friendly aliases
XFADE_ALIAS = {"crossfade": "fade", "black": "fadeblack", "white": "fadewhite", "flash": "fadewhite",
               "zoom": "zoomin", "slide": "slideleft", "wipe": "wipeleft", "blur": "hblur", "whip": "smoothleft"}

GRADES = {
    "none": "",
    "natural": "eq=contrast=1.04:saturation=1.06",
    "vivid": "eq=contrast=1.08:saturation=1.35:gamma=0.98,unsharp=5:5:0.4",
    "cinematic": "colorbalance=rs=-0.06:bs=0.08:rh=0.07:bh=-0.07,eq=contrast=1.1:saturation=0.88:gamma=0.97,vignette=PI/5",
    "teal_orange": "colorbalance=rs=-0.1:gs=0.02:bs=0.12:rh=0.1:gh=0.02:bh=-0.1,eq=contrast=1.08:saturation=1.1",
    "warm": "colortemperature=temperature=4800:mix=0.6,eq=saturation=1.08",
    "cool": "colortemperature=temperature=8200:mix=0.6,eq=contrast=1.04",
    "moody": "eq=contrast=1.18:brightness=-0.04:saturation=0.75,colorbalance=bs=0.05:bm=0.03,vignette=PI/4",
    "vintage": "curves=preset=vintage,eq=saturation=0.85,noise=alls=8:allf=t,vignette=PI/4",
    "film": "curves=preset=medium_contrast,colorbalance=rs=0.03:bs=-0.03:rh=0.04,noise=alls=6:allf=t",
    "bw": "hue=s=0,eq=contrast=1.15",
    "fade": "curves=all='0/0.08 0.5/0.5 1/0.94',eq=saturation=0.8",
    "bright": "eq=brightness=0.05:contrast=1.04:saturation=1.1:gamma=1.06",
}


# ------------------------------------------------------------------ helpers


def resolve_src(src: str, analysis: dict | None, base: Path) -> str:
    if analysis and re.fullmatch(r"v\d+", src or ""):
        for v in analysis.get("videos", []):
            if v["id"] == src:
                return v["path"]
        raise SystemExit(f"unknown source id {src}")
    p = Path(src).expanduser()
    if not p.is_absolute():
        p = (base / p)
    return str(p.resolve())


def atempo_chain(speed: float) -> list[str]:
    out, s = [], speed
    while s > 2.0:
        out.append("atempo=2.0")
        s /= 2
    while s < 0.5:
        out.append("atempo=0.5")
        s /= 0.5
    out.append(f"atempo={s:.5f}")
    return out


def fit_chain(mode: str, W: int, H: int, focus=(0.5, 0.5), bg="black") -> tuple[str, bool]:
    """Return (filter, needs_split) mapping any input to W x H."""
    fx, fy = focus
    if mode == "pad":
        return (f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={bg}"), False
    if mode == "blur":
        return (f"split[bg][fg];[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"gblur=sigma=28,eq=brightness=-0.08:saturation=0.9[bgb];"
                f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"), True
    # crop (fill)
    return (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={W}:{H}:(iw-{W})*{fx}:(ih-{H})*{fy}"), False


def effect_filters(effects: list[dict], W: int, H: int, dur: float, fps: float) -> tuple[list[str], list[str]]:
    """Video filters applied after fit (frame already W x H) and extra audio filters."""
    vf, af = [], []
    for e in effects:
        t = e.get("type")
        if t in ("zoom_in", "zoom_out", "ken_burns"):
            a = float(e.get("amount", 0.12))
            p = f"(t/{max(dur, 0.1):.3f})"
            z = f"(1+{a}*{p})" if t != "zoom_out" else f"(1+{a}*(1-{p}))"
            fx, fy = e.get("focus", [0.5, 0.5])
            vf.append(f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame:flags=bicubic,"
                      f"crop={W}:{H}:(iw-{W})*{fx}:(ih-{H})*{fy}")
        elif t == "punch":
            s = float(e.get("scale", 1.18))
            fx, fy = e.get("focus", [0.5, 0.5])
            sw, sh = int(W * s) // 2 * 2, int(H * s) // 2 * 2
            st = e.get("start")
            if st is None:
                vf.append(f"scale={sw}:{sh},crop={W}:{H}:(iw-{W})*{fx}:(ih-{H})*{fy}")
            else:  # punch only for part of the clip
                en = e.get("end", dur)
                vf.append(f"split[pa][pb];[pb]scale={sw}:{sh},crop={W}:{H}:(iw-{W})*{fx}:(ih-{H})*{fy}[pz];"
                          f"[pa][pz]overlay=0:0:enable='between(t,{st},{en})'")
        elif t == "shake":
            amt = float(e.get("amount", 0.03))
            st, en = e.get("start", 0), e.get("end", dur)
            sw, sh = int(W * (1 + 2 * amt)) // 2 * 2, int(H * (1 + 2 * amt)) // 2 * 2
            cond = f"between(t,{st},{en})"
            vf.append(f"scale={sw}:{sh},crop={W}:{H}:"
                      f"x='(iw-{W})/2+if({cond},(iw-{W})/2*0.9*sin(t*43),0)':"
                      f"y='(ih-{H})/2+if({cond},(ih-{H})/2*0.9*cos(t*37),0)'")
        elif t == "flash":
            vf.append(f"fade=t=in:st=0:d={float(e.get('duration', 0.25))}:color=white")
        elif t == "fade_in":
            d = float(e.get("duration", 0.5))
            vf.append(f"fade=t=in:st=0:d={d}")
            af.append(f"afade=t=in:st=0:d={d}")
        elif t == "fade_out":
            d = float(e.get("duration", 0.6))
            vf.append(f"fade=t=out:st={max(dur - d, 0):.3f}:d={d}")
            af.append(f"afade=t=out:st={max(dur - d, 0):.3f}:d={d}")
        elif t == "bw":
            vf.append("hue=s=0")
        elif t == "blur":
            vf.append(f"gblur=sigma={e.get('amount', 12)}")
        elif t == "vignette":
            vf.append("vignette=PI/4")
        elif t == "mirror":
            vf.append("hflip")
        elif t == "glitch":
            d = float(e.get("duration", 0.35))
            vf.append(f"rgbashift=rh=14:bh=-14:gv=4:enable='lt(t,{d})',"
                      f"noise=alls=40:allf=t:enable='lt(t,{d})'")
        elif t == "rgb_split":
            vf.append("rgbashift=rh=6:bh=-6")
        elif t == "grain":
            vf.append(f"noise=alls={e.get('amount', 10)}:allf=t")
        elif t == "sharpen":
            vf.append("unsharp=5:5:0.8")
        elif t == "echo":  # ghost trails
            vf.append("lagfun=decay=0.92")
        elif t == "speed_lines":
            pass
    return vf, af


# ------------------------------------------------------------------ clip rendering


def render_clip(i: int, clip: dict, ctx: dict) -> dict:
    W, H, fps = ctx["W"], ctx["H"], ctx["fps"]
    kind = clip.get("type", "video")
    speed = float(clip.get("speed", 1.0))
    effects = list(clip.get("effects", []))
    names = {e.get("type") for e in effects}
    grade = clip.get("grade", ctx["grade"])
    fit = clip.get("fit", ctx["fit"])
    focus = clip.get("focus", [0.5, 0.5])

    key = hashlib.sha1(json.dumps({"c": clip, "W": W, "H": H, "fps": fps, "g": grade, "fit": fit,
                                   "q": ctx["clip_q"], "v": 3}, sort_keys=True).encode()).hexdigest()[:12]
    out = ctx["clip_dir"] / f"c{i:03d}_{key}.mp4"

    inputs, src_dur, has_audio = [], 0.0, False
    if kind == "color":
        src_dur = float(clip.get("duration", 2))
        inputs = ["-f", "lavfi", "-t", f"{src_dur}", "-i",
                  f"color=c={clip.get('color', 'black')}:s={W}x{H}:r={fps}"]
    elif kind == "image":
        src_dur = float(clip.get("duration", 3))
        inputs = ["-loop", "1", "-framerate", str(fps), "-t", f"{src_dur}", "-i", clip["path"]]
    else:
        info = ctx["probe"](clip["path"])
        a = max(0.0, float(clip.get("in", 0)))
        b = float(clip["out"]) if clip.get("out") is not None else (
            a + float(clip["dur"]) if clip.get("dur") is not None else info["duration"])
        b = min(b, info["duration"])
        src_dur = max(b - a, 1.0 / fps)
        has_audio = info.get("has_audio", False) and not clip.get("mute")
        inputs = ["-ss", f"{a:.3f}", "-t", f"{src_dur:.3f}", "-i", clip["path"]]

    dur = src_dur / speed
    freeze = next((e for e in effects if e.get("type") == "freeze"), None)
    total = dur + (float(freeze.get("duration", 0.6)) if freeze else 0)

    if out.exists():
        return {"path": str(out), "duration": probe(out)["duration"], "cached": True}

    vf = []
    if "reverse" in names:
        vf.append("reverse")
    if speed != 1.0:
        vf.append(f"setpts=PTS/{speed}")
    fchain, _ = fit_chain(fit, W, H, focus, ctx["bg"])
    vf.append(fchain)
    vf.append(f"fps={fps}")
    efx_v, efx_a = effect_filters(effects, W, H, dur, fps)
    vf += efx_v
    if grade and grade != "none":
        if str(grade).startswith("lut:"):
            vf.append(f"lut3d='{grade[4:]}'")
        else:
            g = GRADES.get(grade)
            if g is None:
                raise SystemExit(f"unknown grade '{grade}'. options: {', '.join(GRADES)} or lut:/path.cube")
            if g:
                vf.append(g)
    if freeze:
        vf.append(f"tpad=stop_mode=clone:stop_duration={float(freeze.get('duration', 0.6))}")
    vf.append("setsar=1,format=yuv420p")
    vgraph = f"[0:v]{','.join(vf)}[v]"

    if has_audio:
        af = []
        if "reverse" in names:
            af.append("areverse")
        if speed != 1.0:
            af += atempo_chain(speed)
        af.append(f"volume={float(clip.get('volume', 1.0))}")
        af += efx_a
        af.append("afade=t=in:d=0.02")
        af.append(f"afade=t=out:st={max(dur - 0.03, 0):.3f}:d=0.03")
        if freeze:
            af.append(f"apad=pad_dur={float(freeze.get('duration', 0.6))}")
        af.append("aresample=48000,aformat=channel_layouts=stereo")
        agraph = f"[0:a]{','.join(af)}[a]"
        amap = "[a]"
        extra_in = []
    else:
        agraph = None
        extra_in = ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        amap = "1:a"

    fg = vgraph + (";" + agraph if agraph else "")
    ffmpeg([*inputs, *extra_in, "-filter_complex", fg, "-map", "[v]", "-map", amap,
            "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", ctx["clip_preset"], "-crf", str(ctx["clip_q"]),
            "-g", str(int(fps)), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)])
    return {"path": str(out), "duration": probe(out)["duration"], "cached": False}


# ------------------------------------------------------------------ beat sync


def apply_beat_sync(edl: dict, analysis: dict | None, base: Path):
    bs = edl.get("beat_sync")
    if not bs or not edl.get("music"):
        return
    mi = int(bs.get("music", 0))
    m = edl["music"][mi]
    mpath = resolve_src(m["path"], None, base)
    beats = None
    for mm in (analysis or {}).get("music", []):
        if mm["path"] == mpath:
            beats = mm["beats"]
    if beats is None:
        from analyze import music_beats
        beats = music_beats(mpath).get("beats", [])
    if not beats:
        log("beat_sync: no beats found, skipping")
        return
    start, offset = float(m.get("start", 0)), float(m.get("offset", 0))
    grid = [start + b - offset for b in beats if b >= offset]
    every = max(1, int(bs.get("every", 1)))
    grid = grid[::every] if every > 1 else grid
    acc = 0.0
    for k, c in enumerate(edl["clips"]):
        if c.get("type", "video") != "video":
            acc += float(c.get("duration", 2))
            continue
        sp = float(c.get("speed", 1.0))
        a = float(c.get("in", 0))
        b = float(c["out"]) if c.get("out") is not None else a + float(c.get("dur", 2))
        nxt = edl["clips"][k + 1] if k + 1 < len(edl["clips"]) else None
        d_next = float((nxt or {}).get("transition", {}).get("duration", 0)) if nxt and \
            (nxt.get("transition") or {}).get("type", "cut") != "cut" else 0.0
        want_end = acc + (b - a) / sp
        cands = [g for g in grid if g >= acc + 0.35]
        if not cands:
            break
        target = min(cands, key=lambda g: abs(g - want_end))
        # with a transition the visual switch is mid-transition -> extend by d/2
        new_len = target - acc + d_next / 2
        c["in"], c["out"] = a, round(a + new_len * sp, 3)
        c.pop("dur", None)
        acc = target - d_next / 2


# ------------------------------------------------------------------ subtitles from transcript


PUNCT = "，。！？、；：,.!?;:…“”\"'（）()《》【】"


def is_cjk(ch: str) -> bool:
    return any(lo <= ord(ch) <= hi for lo, hi in ((0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
                                                    (0xF900, 0xFAFF), (0xAC00, 0xD7AF)))


def text_len(s: str) -> float:
    return sum(1 if is_cjk(ch) else 0.55 for ch in s)


def build_subtitles(edl: dict, timeline: list[dict], analysis: dict | None) -> list[dict]:
    sub = edl.get("subtitles") or {}
    if not sub or sub.get("enabled") is False:
        return []
    items = []
    for it in sub.get("items", []):  # manual captions on output timeline
        items.append({**it, "style": it.get("style", sub.get("style", "caption"))})
    if not analysis or sub.get("from", "transcript") != "transcript":
        return items
    max_chars = float(sub.get("max_chars", 14))
    strip = sub.get("strip_punct", True)
    by_path = {v["path"]: v for v in analysis.get("videos", [])}
    replace = sub.get("replace", {})
    for seg in timeline:
        c = seg["clip"]
        v = by_path.get(c.get("path"))
        if not v or not v.get("transcript") or c.get("mute") or "reverse" in {e.get("type") for e in c.get("effects", [])}:
            continue
        a, b, sp = seg["src_in"], seg["src_out"], seg["speed"]
        words = [w for s in v["transcript"]["segments"] for w in (s.get("words") or
                 [{"start": s["start"], "end": s["end"], "word": s["text"]}])]
        words = [w for w in words if w["end"] > a + 0.05 and w["start"] < b - 0.05]
        chunk, cs, ce = "", None, None

        def flush():
            nonlocal chunk, cs, ce
            txt = chunk.strip()
            for k, val in replace.items():
                txt = txt.replace(k, val)
            if strip:
                txt = txt.strip(PUNCT + " ")
                txt = re.sub(r"[，。、；：,;:]+", " ", txt).strip()
            if txt:
                t0 = seg["out_start"] + (max(cs, a) - a) / sp
                t1 = seg["out_start"] + (min(ce, b) - a) / sp
                t1 = min(t1, seg["out_end"])
                if t1 - t0 > 0.15:
                    items.append({"text": txt, "start": round(t0, 3), "end": round(t1, 3),
                                  "style": sub.get("style", "caption"), **{k: sub[k] for k in
                                  ("size", "color", "stroke_color", "pos", "box", "font", "highlight") if k in sub}})
            chunk, cs, ce = "", None, None

        for w in words:
            wt = w["word"]
            if cs is not None and (w["start"] - ce > 0.6 or text_len(chunk + wt) > max_chars):
                flush()
            if cs is None:
                cs = w["start"]
            chunk += wt
            ce = w["end"]
            if wt.strip() and wt.strip()[-1] in "。！？.!?":
                flush()
        if cs is not None:
            flush()
    # keep captions from overlapping each other
    items.sort(key=lambda x: x["start"])
    for p, n in zip(items, items[1:]):
        if p.get("style") == n.get("style") and p["end"] > n["start"]:
            p["end"] = n["start"]
    return items


# ------------------------------------------------------------------ main


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edl")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--no-review", action="store_true")
    args = ap.parse_args()

    edl_path = Path(args.edl).resolve()
    base = edl_path.parent
    edl = load_json(edl_path)
    analysis = None
    if edl.get("analysis"):
        ap_ = Path(edl["analysis"])
        analysis = load_json(ap_ if ap_.is_absolute() else base / ap_)

    o = edl.get("output", {})
    W, H, fps = int(o.get("width", 1920)), int(o.get("height", 1080)), float(o.get("fps", 30))
    out_path = Path(o.get("path", "output.mp4"))
    out_path = out_path if out_path.is_absolute() else base / out_path
    if args.draft:
        W, H = W // 4 * 2, H // 4 * 2
        out_path = out_path.with_name(out_path.stem + "_draft" + out_path.suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    work = base / ".render"
    clip_dir = work / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    probes = {}

    def cprobe(p):
        if p not in probes:
            probes[p] = probe(p)
        return probes[p]

    ctx = {"W": W, "H": H, "fps": fps, "grade": edl.get("grade", "none"), "fit": o.get("fit", "crop"),
           "bg": o.get("bg", "black"), "clip_dir": clip_dir, "probe": cprobe,
           "clip_q": 26 if args.draft else 16, "clip_preset": "ultrafast" if args.draft else "veryfast"}

    for c in edl["clips"]:
        if c.get("type", "video") == "video":
            c["path"] = resolve_src(c.get("src") or c.get("path"), analysis, base)
        elif c.get("type") == "image":
            c["path"] = resolve_src(c["path"], None, base)
    for m in edl.get("music", []):
        m["path"] = resolve_src(m["path"], None, base)

    apply_beat_sync(edl, analysis, base)

    # 1) clips
    rendered = []
    for i, c in enumerate(edl["clips"]):
        r = render_clip(i, c, ctx)
        log(f"clip {i + 1}/{len(edl['clips'])} {'(cached)' if r['cached'] else ''} {r['duration']:.2f}s")
        rendered.append(r)

    # 2) join graph + timeline
    n = len(rendered)
    inputs, parts, timeline = [], [], []
    for r in rendered:
        inputs += ["-i", r["path"]]
    for i in range(n):  # common timebase so concat and xfade can be mixed freely
        parts.append(f"[{i}:v]fps={fps},settb=AVTB,setpts=PTS-STARTPTS[s{i}]")
        parts.append(f"[{i}:a]asetpts=PTS-STARTPTS[t{i}]")
    acc = 0.0
    cur_v, cur_a = "[s0]", "[t0]"
    for i, (c, r) in enumerate(zip(edl["clips"], rendered)):
        tr = c.get("transition") or {}
        ttype = XFADE_ALIAS.get(tr.get("type", "cut"), tr.get("type", "cut"))
        d = float(tr.get("duration", 0.5))
        if i == 0:
            start = 0.0
            acc = r["duration"]
        else:
            prev_dur = rendered[i - 1]["duration"]
            if ttype != "cut":
                if ttype not in XFADE:
                    raise SystemExit(f"unknown transition '{ttype}'. options: cut, {', '.join(sorted(XFADE))}")
                d = max(0.04, min(d, prev_dur * 0.9, r["duration"] * 0.9))
                start = acc - d
                parts.append(f"{cur_v}[s{i}]xfade=transition={ttype}:duration={d:.3f}:offset={start:.3f}[v{i}]")
                parts.append(f"{cur_a}[t{i}]acrossfade=d={d:.3f}:c1=tri:c2=tri[a{i}]")
            else:
                start = acc
                parts.append(f"{cur_v}{cur_a}[s{i}][t{i}]concat=n=2:v=1:a=1[v{i}][a{i}]")
            cur_v, cur_a = f"[v{i}]", f"[a{i}]"
            acc = start + r["duration"]
        sp = float(c.get("speed", 1.0))
        src_in = float(c.get("in", 0))
        timeline.append({"clip": c, "out_start": round(start, 3), "out_end": round(start + r["duration"], 3),
                         "src_in": src_in, "src_out": src_in + (r["duration"] * sp), "speed": sp})
    total = acc

    # 3) overlay layer (texts + subtitles + images)
    subs = build_subtitles(edl, timeline, analysis)
    texts = list(edl.get("texts", [])) + subs
    images = []
    for im in edl.get("images", []):
        images.append({**im, "path": resolve_src(im["path"], None, base)})
    scale = W / int(o.get("width", W))
    layer_list = None
    if texts or images:
        layer_list = ovl.build_layer(texts, images, W, H, fps, total, work / "layer", font=o.get("font"),
                                     scale=scale)
    vin = len(rendered)
    if layer_list:
        inputs += ["-f", "concat", "-safe", "0", "-i", str(layer_list)]
        parts.append(f"[{vin}:v]format=rgba,setpts=PTS-STARTPTS[lay]")
        parts.append(f"{cur_v}[lay]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[vout]")
        vin += 1
    else:
        parts.append(f"{cur_v}format=yuv420p[vout]")

    # 4) audio mix
    base_vol = float(edl.get("audio", {}).get("clip_volume", 1.0))
    music = edl.get("music", [])
    if music:
        nduck = sum(1 for m in music if m.get("duck"))
        parts.append(f"{cur_a}volume={base_vol},asplit={nduck + 1}[abase]" +
                     "".join(f"[aside{j}]" for j in range(nduck)) if nduck
                     else f"{cur_a}volume={base_vol}[abase]")
        mix, dj = ["[abase]"], 0
        for k, m in enumerate(music):
            inputs += ["-stream_loop", "-1" if m.get("loop", True) else "0", "-i", m["path"]]
            mstart = float(m.get("start", 0))
            mend = float(m.get("end", total))
            mlen = max(0.1, mend - mstart)
            fi, fo = float(m.get("fade_in", 0.5)), float(m.get("fade_out", 2.0))
            chain = (f"[{vin}:a]atrim=start={float(m.get('offset', 0)):.3f}:duration={mlen:.3f},asetpts=PTS-STARTPTS,"
                     f"aresample=48000,aformat=channel_layouts=stereo,volume={float(m.get('volume', 0.3))},"
                     f"afade=t=in:d={fi},afade=t=out:st={max(mlen - fo, 0):.3f}:d={fo},"
                     f"adelay={int(mstart * 1000)}|{int(mstart * 1000)}")
            if m.get("duck"):
                parts.append(chain + f"[mraw{k}]")
                parts.append(f"[mraw{k}][aside{dj}]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[m{k}]")
                dj += 1
            else:
                parts.append(chain + f"[m{k}]")
            mix.append(f"[m{k}]")
            vin += 1
        parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[amix]")
        alast = "[amix]"
    else:
        parts.append(f"{cur_a}volume={base_vol}[amix]")
        alast = "[amix]"
    lufs = float(o.get("loudness", -14))
    parts.append(f"{alast}loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000[aout]")

    fg_path = work / "graph.txt"
    fg_path.write_text(";\n".join(parts), encoding="utf-8")
    enc = (["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"] if args.draft else
           ["-c:v", "libx264", "-preset", o.get("preset", "medium"), "-crf", str(o.get("crf", 18))])
    log(f"final render -> {out_path} ({fmt_ts(total)})")
    ffmpeg([*inputs, "-filter_complex_script", str(fg_path), "-map", "[vout]", "-map", "[aout]",
            "-t", f"{total:.3f}", *enc, "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out_path)])

    fix_loudness(out_path, lufs)
    report = {"output": str(out_path), "duration": round(total, 3), "size": [W, H],
              "timeline": [{"i": i, "src": Path(t["clip"].get("path", t["clip"].get("type", ""))).name,
                            "out": [t["out_start"], t["out_end"]],
                            "src_range": [round(t["src_in"], 2), round(t["src_out"], 2)]}
                           for i, t in enumerate(timeline)],
              "captions": len(subs)}
    save_json(report, work / "last_render.json")
    save_json(subs, work / "subtitles.json")
    if subs:
        write_srt(subs, out_path.with_suffix(".srt"))
    if not args.no_review:
        from preview import review_sheet
        report["review"] = review_sheet(str(out_path), [t["out_start"] for t in timeline], work / "review")
    print(json.dumps(report, ensure_ascii=False, indent=1))


def fix_loudness(path: Path, target: float):
    """Single-pass loudnorm can land a few LU off; measure and correct with a cheap audio-only pass."""
    from common import run, FFMPEG
    p = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
            check=False)
    m = re.findall(r"I:\s+(-?[0-9.]+) LUFS", p.stderr)
    if not m:
        return
    measured = float(m[-1])
    gain = target - measured
    if abs(gain) < 1.0 or measured < -60:
        return
    tmp = path.with_name(path.stem + ".loud" + path.suffix)
    ffmpeg(["-i", str(path), "-map", "0", "-c:v", "copy", "-af",
            f"volume={gain:.2f}dB,alimiter=limit=0.84:level=false", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(tmp)])
    tmp.replace(path)
    log(f"loudness {measured:.1f} -> ~{target:.0f} LUFS")


def write_srt(subs: list[dict], path: Path):
    def ts(t):
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    lines = []
    for k, s in enumerate(subs, 1):
        lines += [str(k), f"{ts(s['start'])} --> {ts(s['end'])}", s["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
