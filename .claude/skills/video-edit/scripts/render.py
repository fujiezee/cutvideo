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
    "trailer": "colorbalance=rs=-0.05:bs=0.06:rh=0.06:bh=-0.05,eq=contrast=1.12:saturation=0.9:gamma=0.97",
    "vhs": "curves=preset=vintage,eq=saturation=1.3:contrast=0.9:brightness=0.03,chromashift=cbh=5:crh=-5,"
           "gblur=sigma=0.7,noise=alls=16:allf=t",
    "silent": "hue=s=0,eq=contrast=1.4:brightness=0.03:gamma=0.88,noise=alls=20:allf=t,vignette=PI/3.2",
    "cyberpunk": "colorbalance=rs=0.08:gs=-0.12:bs=0.28:rm=0.06:gm=-0.06:bm=0.12:rh=0.16:gh=-0.06:bh=0.12,"
                 "eq=contrast=1.2:saturation=1.5:gamma=0.9",
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
    # crop (fill); focus may be offsets (0..1 of the overflow) or pixel expressions of t
    if isinstance(fx, str):
        return (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H}:{fx}:{fy}"), False
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
        elif t == "flicker":  # old projector brightness flicker
            a = float(e.get("amount", 0.05))
            vf.append(f"eq=brightness='{a}*sin(t*61)*sin(t*17+1)':eval=frame")
        elif t == "echo":  # ghost trails
            vf.append("lagfun=decay=0.92")
        elif t == "speed_lines":
            pass
    return vf, af


# ------------------------------------------------------------------ clip rendering


def auto_focus(clip: dict, W: int, H: int, ctx: dict) -> list[float]:
    """Crop focus for fit=crop from the largest face in the clip (3 frames sampled);
    falls back to the centre. Returns crop offsets as fractions of the overflow."""
    import cv2
    from intro import _largest_face
    info = ctx["probe"](clip["path"])
    a = float(clip.get("in", 0))
    b = float(clip["out"]) if clip.get("out") is not None else a + float(clip.get("dur", 2))
    cap = cv2.VideoCapture(clip["path"])
    faces = []
    for f in (0.25, 0.5, 0.75):
        cap.set(cv2.CAP_PROP_POS_MSEC, (a + (b - a) * f) * 1000)
        ok, fr = cap.read()
        if not ok:
            continue
        k = 640 / fr.shape[1]
        small = cv2.resize(fr, (640, int(fr.shape[0] * k)))
        fc = _largest_face(small)
        if fc is not None:
            x, y, w, h = fc
            faces.append(((x + w / 2) / small.shape[1], (y + h / 2) / small.shape[0], w * h))
    cap.release()
    if not faces:
        log(f"focus auto: no face in {Path(clip['path']).name} {a:.1f}-{b:.1f}, centre crop")
        return [0.5, 0.5]
    tot = sum(f[2] for f in faces)
    cx = sum(f[0] * f[2] for f in faces) / tot
    cy = sum(f[1] * f[2] for f in faces) / tot
    sw, sh = info["width"], info["height"]
    k = max(W / sw, H / sh)
    iw, ih = sw * k, sh * k

    def frac(c, full, out, headroom=0.0):
        if full - out < 1:
            return 0.5
        return min(1.0, max(0.0, (c * full - out / 2 + headroom * out) / (full - out)))
    # faces sit a little above centre in a good vertical frame
    return [round(frac(cx, iw, W), 3), round(frac(cy, ih, H, 0.12), 3)]


def prepare_narration(edl: dict, work: Path):
    """Voice-over: synthesise every narration line (tts.py), and add its captions.

    "narration": {"voice": "Tingting", "rate": 190, "volume": 1.0, "captions": true,
                  "items": [{"text": "...", "clip": 2, "start": 0.2} | {"text": "...", "at": 5.0}]}
    Lines are mixed like dialogue: music tracks with `duck` dip under them."""
    nar = edl.get("narration")
    if not nar or not nar.get("items"):
        return
    # {keyword} marks words to highlight in the captions; braces are not spoken
    marked = set()
    for it in nar["items"]:
        marked.update(m for m in re.findall(r"\{([^{}]+)\}", it["text"]))
        it["text"] = re.sub(r"[{}]", "", it["text"])
    from tts import caption_chunks, load_config, synth
    job = work.parent if (work.parent / "tts.json").exists() else work.parent.parent  # auto/ EDLs
    cfg = load_config(job if (job / "tts.json").exists() else work.parent)
    if isinstance(nar.get("provider"), dict):  # per-EDL override, e.g. {"provider": "openai", ...}
        cfg = {**cfg, **nar["provider"]} if nar["provider"].get("provider") in (None, cfg.get("provider")) \
            else dict(nar["provider"])
    sub = edl.setdefault("subtitles", {"enabled": True, "from": "manual"})
    if sub.get("enabled") is False and nar.get("captions", True):
        sub.update({"enabled": True, "from": "manual"})
    sub.setdefault("style", nar.get("caption_style", "explainer"))
    if nar.get("highlight", True):
        sub["highlight"] = sorted(set(sub.get("highlight", [])) | marked | auto_keywords(edl, nar), key=len,
                                  reverse=True)
    for it in nar["items"]:
        path, dur = synth(it["text"], work / "tts", it.get("voice", nar.get("voice")),
                          it.get("rate", nar.get("rate")), cfg)
        it["_path"], it["_dur"] = str(path), dur
        if nar.get("captions", True):
            t0 = float(it.get("start", it.get("at", 0)))
            short = sub.get("style") in ("explainer", "boxed")  # punchier, shorter captions
            for text, a, b in caption_chunks(it["text"], dur, max_cjk=12 if short else 16,
                                             max_latin=32 if short else 42, protect=sub.get("highlight", [])):
                if short:  # explainer captions: no commas or full stops, a wide space instead
                    text = re.sub(r"\s*[，、；：]\s*", "\u3000", text).rstrip("。．.").strip("\u3000 ")
                cap = {"text": text, "start": round(t0 + a, 3), "end": round(t0 + b, 3)}
                if "clip" in it:
                    cap["clip"] = it["clip"]
                sub.setdefault("items", []).append(cap)


def auto_keywords(edl: dict, nar: dict) -> set[str]:
    """Words worth colouring in narration captions: names of recognised people (people.json
    next to the analysis) and numbers with their unit (四十年, 40 years)."""
    words = set()
    an = edl.get("analysis")
    if an:
        pj = Path(an).parent / "people.json"
        if not pj.is_absolute():
            pj = Path(edl.get("_base", ".")) / pj
        if pj.exists():
            for p in load_json(pj).get("people", []):
                for part in re.split(r"\s{2,}|·", p.get("name") or ""):
                    part = part.strip()
                    if part and not part.startswith("人物"):
                        words.add(part)
                        if part.startswith("老") and len(part) > 2:
                            words.add(part[1:])
    text = " ".join(it["text"] for it in nar["items"])
    words |= set(re.findall(r"[零一二两三四五六七八九十百千万\d]+(?:年|岁|天|个|次|小时|分钟|年后|年前)", text))
    words |= set(re.findall(r"\b\d+(?:\s?(?:years?|days?|hours?|minutes?|%))?", text))
    return {w for w in words if w and w in text}


def snap_intros_to_pauses(edl: dict, analysis: dict | None, reach: float = 0.8, min_gap: float = 0.12):
    """Move a freeze-frame intro out of the middle of a word: the audio pauses during the
    freeze, so freezing mid-speech chops a word. Uses transcript word timings; `"snap": false`
    on the intro keeps the given `at`."""
    if not analysis:
        return
    by_path = {v["path"]: v for v in analysis.get("videos", [])}
    for c in edl["clips"]:
        spec = next((e for e in c.get("effects", []) if e.get("type") == "intro"), None)
        v = by_path.get(c.get("path"))
        if not spec or spec.get("snap") is False or not v or not v.get("transcript"):
            continue
        a = float(c.get("in", 0))
        b = float(c["out"]) if c.get("out") is not None else a + float(c.get("dur", 2))
        sp = float(c.get("speed", 1.0))
        words = sorted((w["start"], w["end"]) for s_ in v["transcript"]["segments"] for w in s_.get("words", [])
                       if w["end"] > a and w["start"] < b)
        at_src = a + float(spec.get("at", 0.5)) * sp
        busy = any(ws - 0.05 <= at_src <= we + 0.02 for ws, we in words)
        if not busy:
            continue
        # candidate freeze points: inside pauses between words (and before the first / after the last)
        edges = [a] + [x for w in words for x in w] + [b]
        cands = []
        for g0, g1 in zip(edges[0::2], edges[1::2]):
            if g1 - g0 >= min_gap:
                cands.append(min(g0 + 0.06, (g0 + g1) / 2))
        cands = [t for t in cands if abs(t - at_src) <= reach and a + 0.1 <= t <= b - 0.1]
        if not cands:
            log(f"intro '{spec.get('name', '')}': freeze at {spec.get('at')}s falls inside speech; no pause nearby")
            continue
        new = min(cands, key=lambda t: abs(t - at_src))
        old = spec.get("at", 0.5)
        spec["at"] = round((new - a) / sp, 3)
        log(f"intro '{spec.get('name', '')}': freeze moved {old}s -> {spec['at']}s (out of a word)")


def intro_of(clip: dict, fps: float):
    """(spec, at, hold) of the clip's freeze-frame intro, hold rounded to whole frames; or None."""
    spec = next((e for e in clip.get("effects", []) if e.get("type") == "intro"), None)
    if not spec:
        return None
    return spec, float(spec.get("at", 0.5)), max(1, round(float(spec.get("hold", 1.8)) * fps)) / fps


AUDIO_KEYS = {"volume", "mute", "audio_lead", "audio_tail", "duck_music", "transition", "audio_filter", "voice"}
# speech clean-up presets ("voice" on a clip, or "audio": {"voice": ...} for the whole edit)
VOICE = {
    "denoise": "highpass=f=70,afftdn=nf=-30:tn=1",
    "clean": "highpass=f=80,afftdn=nf=-25:tn=1,deesser=i=0.4,"
             "acompressor=threshold=0.1:ratio=3:attack=5:release=80:makeup=1.5",
    "broadcast": "highpass=f=90,afftdn=nf=-25:tn=1,equalizer=f=3000:t=q:w=1.2:g=3,deesser=i=0.5,"
                 "acompressor=threshold=0.08:ratio=4:attack=3:release=60:makeup=2",
}


def render_clip(i: int, clip: dict, ctx: dict) -> dict:
    W, H, fps = ctx["W"], ctx["H"], ctx["fps"]
    kind = clip.get("type", "video")
    speed = float(clip.get("speed", 1.0))
    effects = list(clip.get("effects", []))
    names = {e.get("type") for e in effects}
    # colour clips (cards, black) stay neutral unless the clip asks for a grade
    grade = clip.get("grade", ctx["grade"] if clip.get("type", "video") != "color" else "none")
    fit = clip.get("fit", ctx["fit"])
    focus = clip.get("focus", [0.5, 0.5])
    vclip = {k: v for k, v in clip.items() if k not in AUDIO_KEYS}  # audio tweaks must not re-encode video
    iv = 0
    if any(e.get("type") == "intro" for e in effects):
        import intro as _intro
        iv = f"{_intro.VERSION}-{_intro.backend()}"  # re-render when the cut-out backend changes
    if any(e.get("type") == "text_behind" for e in effects):
        import behind as _bh
        iv = f"{iv}-behind{_bh.VERSION}"
    if clip.get("focus") == "track":
        import reframe as _rf
        iv = f"{iv}-track{_rf.VERSION}"
    key = hashlib.sha1(json.dumps({"c": vclip, "W": W, "H": H, "fps": fps, "g": grade, "fit": fit,
                                   "q": ctx["clip_q"], "v": 6, "iv": iv}, sort_keys=True).encode()).hexdigest()[:12]
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

    audio = None
    if has_audio:
        audio = render_clip_audio(i, clip, ctx, info, a, b, speed, dur, names, freeze)

    if out.exists():
        return {"path": str(out), "duration": probe(out)["duration"], "cached": True, "audio": audio}

    if focus == "track" and (kind != "video" or fit != "crop" or "reverse" in names):
        focus = "auto"
    if focus == "track":
        from reframe import track_crop
        info_ = ctx["probe"](clip["path"])
        a_ = float(clip.get("in", 0))
        b_ = float(clip["out"]) if clip.get("out") is not None else a_ + float(clip.get("dur", 2))
        hint = clip.get("track_hint")
        hint = {"left": 0.25, "center": 0.5, "right": 0.75}.get(hint, hint)
        tr = track_crop(clip["path"], a_, b_, speed, W, H, info_["width"], info_["height"],
                        hint=float(hint) if hint is not None else None)
        if tr:
            focus = [tr[0], tr[1]]
            log(f"focus track {Path(clip['path']).name} {a_:.1f}-{b_:.1f}: {tr[2]}")
        else:
            focus = "auto"
    if focus == "auto":
        focus = auto_focus(clip, W, H, ctx) if kind == "video" else [0.5, 0.5]

    vf = []
    if "reverse" in names:
        vf.append("reverse")
    if speed != 1.0:
        vf.append(f"setpts=PTS/{speed}")
    geom = names & {"zoom_in", "zoom_out", "ken_burns", "punch", "shake"}
    if fit == "pad" and kind != "color" and geom:
        # letterboxed: move/zoom the picture inside the frame, keep the bars static
        si = ctx["probe"](clip["path"])
        k = min(W / si["width"], H / si["height"])
        cw, ch = int(si["width"] * k) // 2 * 2, int(si["height"] * k) // 2 * 2
        vf.append(f"scale={cw}:{ch}:flags=lanczos")
        vf.append(f"fps={fps}")
        efx_v, _ = effect_filters(effects, cw, ch, dur, fps)
        vf += efx_v
        vf.append(f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={ctx['bg']}")
    else:
        fchain, _ = fit_chain(fit, W, H, focus, ctx["bg"])
        vf.append(fchain)
        vf.append(f"fps={fps}")
        efx_v, _ = effect_filters(effects, W, H, dur, fps)
        vf += efx_v
    adj = clip.get("adjust") or {}
    if adj:  # per-clip correction before the shared grade (match shots to each other)
        if adj.get("temperature"):
            vf.append(f"colortemperature=temperature={int(adj['temperature'])}:mix={adj.get('temperature_mix', 0.5)}")
        eq = {k: adj[k] for k in ("brightness", "contrast", "saturation", "gamma") if k in adj}
        if eq:
            vf.append("eq=" + ":".join(f"{k}={v}" for k, v in eq.items()))
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
    # exact frame count (= round(length * fps)) so planned and rendered timelines match
    vf.append(f"tpad=stop_mode=clone:stop_duration={3 / fps:.4f}")
    vf.append("setsar=1,format=yuv420p")
    nframes = max(1, round(total * fps))
    enc = ["-c:v", "libx264", "-preset", ctx["clip_preset"], "-crf", str(ctx["clip_q"]), "-g", str(int(fps))]
    tmp = out.with_name(out.stem + ".part.mp4")
    ffmpeg([*inputs, "-filter_complex", f"[0:v]{','.join(vf)}[v]", "-map", "[v]", "-an",
            "-frames:v", str(nframes), *enc, str(tmp)])
    for tb in [e for e in effects if e.get("type") == "text_behind"]:
        from behind import apply_text_behind
        apply_text_behind(tmp, tb, fps, ctx["clip_dir"] / f"behind{i:03d}", enc, ctx.get("font"))
    intro = intro_of(clip, fps)
    if intro:
        from intro import apply_intro
        apply_intro(tmp, intro[0], fps, ctx["clip_dir"] / f"intro{i:03d}", enc, ctx.get("font"))
    tmp.replace(out)
    return {"path": str(out), "duration": probe(out)["duration"], "cached": False, "audio": audio}


def render_clip_audio(i, clip, ctx, info, a, b, speed, dur, names, freeze) -> dict:
    """Clip audio as its own WAV, optionally extended for J-cuts (audio_lead) and L-cuts (audio_tail).

    Returns {"path", "lead", "duration"}; `lead` = output seconds the audio starts before the picture.
    """
    reverse = "reverse" in names
    lead = 0.0 if reverse else max(0.0, float(clip.get("audio_lead", 0)))
    tail = 0.0 if (reverse or freeze) else max(0.0, float(clip.get("audio_tail", 0)))
    lead_src = min(lead * speed, a)
    tail_src = min(tail * speed, max(info["duration"] - b, 0))
    lead_out, tail_out = lead_src / speed, tail_src / speed
    fz = float(freeze.get("duration", 0.6)) if freeze else 0.0
    akey = hashlib.sha1(json.dumps({"p": clip["path"], "a": a, "b": b, "s": speed, "v": clip.get("volume", 1.0),
                                    "l": lead_src, "t": tail_src, "r": reverse, "fz": fz,
                                    "fx": [e for e in clip.get("effects", []) if e.get("type") in ("fade_in", "fade_out")],
                                    "intro": intro_of(clip, ctx["fps"]), "af": clip.get("audio_filter"), "voice": clip.get("voice", ctx.get("voice")), "ver": 2},
                                   sort_keys=True).encode()).hexdigest()[:12]
    aout = ctx["clip_dir"] / f"a{i:03d}_{akey}.wav"
    intro = intro_of(clip, ctx["fps"])
    gap = intro[2] if intro else 0.0
    length = lead_out + dur + tail_out + fz + gap
    if not aout.exists():
        af = []
        if reverse:
            af.append("areverse")
        if speed != 1.0:
            af += atempo_chain(speed)
        voice = clip.get("voice", ctx.get("voice"))
        if voice:
            if voice not in VOICE:
                raise SystemExit(f"unknown voice preset {voice!r}; options: {', '.join(VOICE)}")
            af.append(VOICE[voice])
        af.append(f"volume={float(clip.get('volume', 1.0))}")
        if clip.get("audio_filter"):
            af.append(clip["audio_filter"])
        for e in clip.get("effects", []):
            if e.get("type") == "fade_in":
                d = float(e.get("duration", 0.5))
                af.append(f"afade=t=in:st={lead_out:.3f}:d={d}")
            elif e.get("type") == "fade_out":
                d = float(e.get("duration", 0.6))
                af.append(f"afade=t=out:st={max(lead_out + dur - d, 0):.3f}:d={d}")
        af.append("afade=t=in:d=0.03")
        af.append(f"afade=t=out:st={max(lead_out + dur + tail_out - 0.04, 0):.3f}:d=0.04")
        if fz:
            af.append(f"apad=pad_dur={fz}")
        af.append("aresample=48000,aformat=channel_layouts=stereo")
        if intro:  # audio pauses while the picture is frozen
            T = lead_out + intro[1]
            graph = (f"[0:a]{','.join(af)},asplit[x][y];[x]atrim=end={T:.4f},afade=t=out:st={max(T - 0.04, 0):.4f}:d=0.04[p1];"
                     f"[y]atrim=start={T:.4f},asetpts=PTS-STARTPTS,afade=t=in:d=0.06[p3];"
                     f"anullsrc=r=48000:cl=stereo,atrim=duration={gap:.4f}[p2];[p1][p2][p3]concat=n=3:v=0:a=1[ao]")
            args = ["-filter_complex", graph, "-map", "[ao]"]
        else:
            args = ["-af", ",".join(af)]
        ffmpeg(["-ss", f"{a - lead_src:.3f}", "-t", f"{(b - a) + lead_src + tail_src:.3f}", "-i", clip["path"],
                "-vn", *args, "-t", f"{length:.3f}", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                str(aout)])
    return {"path": str(aout), "lead": round(lead_out, 3), "duration": round(length, 3)}


# ------------------------------------------------------------------ beat sync


def best_beat_shift(path: str, beats: list[float]) -> float:
    """Shift (s, -0.3..0.3) that maximises onset strength at the beats: corrects off-beat or
    drifting phase from the beat tracker."""
    import numpy as np
    from common import decode_audio_mono
    if len(beats) < 4:
        return 0.0
    y = decode_audio_mono(path, 22050)
    hop = 220  # 10 ms
    n = len(y) // hop
    r = np.sqrt((y[:n * hop].reshape(n, hop) ** 2).mean(1))
    onset = np.maximum(0, np.diff(r, prepend=r[0]))
    b = np.array(beats)
    best, best_s = -1.0, 0.0
    for sh in np.arange(-0.3, 0.301, 0.01):
        idx = np.clip(((b + sh) * 100).astype(int), 0, n - 1)
        v = onset[idx].mean()
        if v > best:
            best, best_s = v, float(sh)
    return round(best_s, 3)


def apply_beat_sync(edl: dict, analysis: dict | None, base: Path):
    """Move clip out-points so that visible cuts land on beats of a music track.

    beat_sync: {"music": 0, "every": 1, "clips": [first, last], "offset_beats": 0, "shift": 0.0}
    Only clips in the inclusive index range are changed (default: all clips); clips with
    "snap": false keep their length (e.g. dialogue). Colour/image clips change `duration`.
    `shift` (seconds) nudges the whole grid, e.g. -0.25 at 120 BPM when the detector locked
    onto off-beats.
    With an xfade the visible switch is the middle of the transition, so that point is snapped.
    """
    bs = edl.get("beat_sync")
    if not bs or not edl.get("music"):
        return
    m = edl["music"][int(bs.get("music", 0))]
    mpath = m["path"]
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
    every = max(1, int(bs.get("every", 1)))
    shift = int(bs.get("offset_beats", 0))
    clips = edl["clips"]
    lo, hi = bs.get("clips", [0, len(clips) - 1])
    hi = len(clips) + hi if hi < 0 else hi
    if bs.get("shift", 0) == "auto":  # phase that puts the grid on real onsets in the synced span
        starts_ = plan_starts(clips)
        t0 = starts_[lo] - start + offset
        t1 = starts_[hi] + _clen(clips[hi]) - start + offset
        nudge = best_beat_shift(mpath, [bt for bt in beats if t0 - 1 <= bt <= t1 + 1])
        log(f"beat_sync: auto shift {nudge:+.3f}s over song {t0:.1f}-{t1:.1f}s")
    else:
        nudge = float(bs.get("shift", 0))
    grid = [start + bt + nudge - offset for bt in beats if bt + nudge >= offset]
    grid = grid[shift::every]

    tdur, length = _tdur, _clen

    acc, moved = 0.0, 0
    for k, c in enumerate(clips):
        if k:
            acc -= tdur(c)
        L = length(c)
        d_next = tdur(clips[k + 1]) if k + 1 < len(clips) else 0.0
        if lo <= k <= hi and c.get("snap", True):
            switch = acc + L - d_next / 2
            cands = [g for g in grid if g >= acc + 0.35 + d_next / 2]
            if cands:
                target = min(cands, key=lambda g: abs(g - switch))
                newL = target + d_next / 2 - acc
                if c.get("type", "video") == "video":
                    sp = float(c.get("speed", 1.0))
                    a = float(c.get("in", 0))
                    fz = L - (float(c["out"]) - a if c.get("out") is not None else float(c.get("dur", 2))) / sp
                    c["out"] = round(a + (newL - fz) * sp, 3)
                    c.pop("dur", None)
                else:
                    c["duration"] = round(newL, 3)
                L = newL
                moved += 1
        acc += L
    log(f"beat_sync: {moved} cut(s) snapped to beats")


def _tdur(c):
    tr = c.get("transition") or {}
    return float(tr.get("duration", 0.5)) if tr.get("type", "cut") != "cut" else 0.0


PLAN_FPS = [30.0]  # set from output.fps in main(); rendered clips last a whole number of frames


def _clen(c, quantize=True):
    L = _clen_raw(c)
    return max(1, round(L * PLAN_FPS[0])) / PLAN_FPS[0] if quantize else L


def _clen_raw(c):
    if c.get("type", "video") != "video":
        return float(c.get("duration", 2 if c.get("type") == "color" else 3))
    a = float(c.get("in", 0))
    b = float(c["out"]) if c.get("out") is not None else a + float(c.get("dur", 2))
    fz = sum(float(e.get("duration", 0.6)) for e in c.get("effects", []) if e.get("type") == "freeze")
    it = intro_of(c, PLAN_FPS[0])
    return (b - a) / float(c.get("speed", 1.0)) + fz + (it[2] if it else 0.0)


def plan_starts(clips: list[dict]) -> list[float]:
    """Predicted output start time of every clip (before rendering)."""
    acc, out = 0.0, []
    for k, c in enumerate(clips):
        if k:
            acc -= _tdur(c)
        out.append(acc)
        acc += _clen(c)
    return out


def apply_pins(edl: dict):
    """Clips with "start_at" (output s) or "start_at_music" (seconds in music[0] / music[i] via
    "music": i) start exactly there: the previous clip is lengthened or shortened to fit."""
    clips = edl["clips"]
    for k, c in enumerate(clips):
        if k == 0 or ("start_at" not in c and "start_at_music" not in c):
            continue
        if "start_at" in c:
            target = float(c["start_at"])
        else:
            m = edl["music"][int(c.get("pin_music", 0))]
            target = float(m.get("start", 0)) + float(c["start_at_music"]) - float(m.get("offset", 0))
        delta = target - plan_starts(clips)[k]
        prev = clips[k - 1]
        newL = _clen_raw(prev) + delta
        if newL < 0.2:
            raise SystemExit(f"pin on clip {k}: previous clip would be {newL:.2f}s; make earlier clips shorter")
        if prev.get("type", "video") == "video":
            sp = float(prev.get("speed", 1.0))
            fz = _clen_raw(prev) - (float(prev["out"]) - float(prev.get("in", 0)) if prev.get("out") is not None
                                else float(prev.get("dur", 2))) / sp
            prev["out"] = round(float(prev.get("in", 0)) + (newL - fz) * sp, 3)
            prev.pop("dur", None)
        else:
            prev["duration"] = round(newL, 3)
        log(f"pin: clip {k} at {target:.2f}s (clip {k - 1} {'+' if delta >= 0 else ''}{delta:.2f}s)")


# ------------------------------------------------------------------ subtitles from transcript


PUNCT = "，。！？、；：,.!?;:…“”\"'（）()《》【】"


def is_cjk(ch: str) -> bool:
    return any(lo <= ord(ch) <= hi for lo, hi in ((0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
                                                    (0xF900, 0xFAFF), (0xAC00, 0xD7AF)))


def text_len(s: str) -> float:
    return sum(1 if is_cjk(ch) else 0.55 for ch in s)


def src_to_out(seg: dict, t: float) -> float:
    """Map a source-video time inside a clip to output time (speed and intro freeze aware)."""
    rel = (t - seg["src_in"]) / seg["speed"]
    if seg.get("intro_at") is not None and rel >= seg["intro_at"]:
        rel += seg["intro_hold"]
    return seg["out_start"] + rel


def anchor_to_clip(item: dict, timeline: list[dict]) -> dict | None:
    """Resolve a text/caption whose timing is given relative to a clip.

    {"clip": i, "start": 0.5, "end": 2.0}          -> seconds from that clip's start (end defaults to clip end)
    {"clip": i, "src_start": 316.2, "src_end": 320} -> source-video seconds inside that clip (follows speed)
    `clip` may be negative (-1 = last clip). Items without `clip` are returned unchanged.
    """
    if "until_clip" in item:  # end where that clip becomes visible
        seg_u = timeline[int(item["until_clip"])]
        item = {**{k: v for k, v in item.items() if k != "until_clip"}, "end": seg_u.get("vis_start", seg_u["out_start"])}
        if "clip" in item:
            item["end"] -= timeline[int(item["clip"])]["out_start"]  # relative to the anchor clip
    if "clip" not in item:
        return item
    try:
        seg = timeline[int(item["clip"])]
    except IndexError:
        raise SystemExit(f"text/caption refers to clip {item['clip']} but the EDL has {len(timeline)} clips")
    it = {k: v for k, v in item.items() if k not in ("clip", "src_start", "src_end")}
    a = seg["out_start"]
    va, vb = seg.get("vis_start", a), seg.get("vis_end", seg["out_end"])
    if "src_start" in item:
        sp, src_in = seg["speed"], seg["src_in"]
        it["start"] = src_to_out(seg, float(item["src_start"]))
        it["end"] = src_to_out(seg, float(item.get("src_end", seg["src_out"])))
        it["start"], it["end"] = max(it["start"], va), min(it["end"], vb)  # not across transitions
    else:
        it["start"] = a + float(item.get("start", 0))
        it["end"] = a + float(item["end"]) if "end" in item else vb
    return it if it["end"] - it["start"] > 0.05 else None


SUB_PASS = ("size", "color", "stroke_color", "stroke", "pos", "box", "font", "highlight", "highlight_color",
            "line_colors", "line_spacing", "max_width", "anim", "karaoke", "karaoke_color", "karaoke_box",
            "karaoke_dim", "weight", "tracking", "shadow")


def _even_words(it: dict) -> list[dict]:
    """Spread a caption's tokens evenly over its duration (karaoke without word timings)."""
    from overlay import _tokens
    toks = [t for t in _tokens(it["text"].split("\n")[0])]
    if not toks:
        return []
    n_real = sum(1 for t in toks if t.strip()) or 1
    step = (it["end"] - it["start"]) / n_real
    out, pos, k = [], 0, 0
    for t in toks:
        if t.strip():
            out.append({"s": pos, "e": pos + len(t), "t0": it["start"] + k * step, "t1": it["start"] + (k + 1) * step})
            k += 1
        pos += len(t)
    return out


def _merge_short(items: list[dict], limit: float, min_dur: float = 0.45) -> list[dict]:
    """Merge captions shorter than `min_dur` into a touching neighbour (e.g. a word stranded
    by a cut) when the merged text still fits in `limit` (text_len units)."""
    def join(p, n):
        sep = "" if (p["text"] and is_cjk(p["text"][-1])) else " "
        off = len(p["text"]) + len(sep)
        m = {**p, "text": p["text"] + sep + n["text"], "end": n["end"]}
        if "words" in p or "words" in n:
            m["words"] = list(p.get("words", [])) + [{**w, "s": w["s"] + off, "e": w["e"] + off}
                                                      for w in n.get("words", [])]
        return m

    out = []
    for it in items:
        if out and "\n" not in it["text"] + out[-1]["text"] and out[-1].get("style") == it.get("style") \
                and it["start"] - out[-1]["end"] < 0.12 \
                and (it["end"] - it["start"] < min_dur or out[-1]["end"] - out[-1]["start"] < min_dur) \
                and text_len(out[-1]["text"] + it["text"]) <= limit:
            out[-1] = join(out[-1], it)
        else:
            out.append(it)
    return out


def build_subtitles(edl: dict, timeline: list[dict], analysis: dict | None) -> list[dict]:
    """Captions on the output timeline. With `karaoke`, each caption carries per-word timings
    (`words`: char ranges + times) so the overlay can highlight the word being spoken."""
    sub = edl.get("subtitles") or {}
    if not sub or sub.get("enabled") is False:
        return []
    shared = {k: sub[k] for k in SUB_PASS if k in sub}
    items = []
    for it in sub.get("items", []):  # manual captions (output time, or anchored to a clip)
        it = anchor_to_clip(it, timeline)
        if it:
            it = {**shared, **it, "style": it.get("style", sub.get("style", "caption"))}
            if it.get("karaoke") and "words" not in it:
                it["words"] = _even_words(it)
            items.append(it)
    if not analysis or sub.get("from", "transcript") != "transcript":
        return items
    max_chars = float(sub.get("max_chars", 14))
    strip = sub.get("strip_punct", True)
    by_path = {v["path"]: v for v in analysis.get("videos", [])}
    replace = sub.get("replace", {})

    def clean(wt: str) -> str:
        for k, val in replace.items():
            wt = wt.replace(k, val)
        if strip:
            core = wt.strip()
            core = core.strip(PUNCT)
            core = re.sub(r"[，。、；：,;:]+", " ", core)
            wt = (" " if wt[:1] == " " else "") + core
        return wt

    for seg in timeline:
        c = seg["clip"]
        v = by_path.get(c.get("path"))
        if not v or not v.get("transcript") or c.get("mute") or "reverse" in {e.get("type") for e in c.get("effects", [])}:
            continue
        a, b = seg["src_in"], seg["src_out"]
        words = [w for s_ in v["transcript"]["segments"] for w in (s_.get("words") or
                 [{"start": s_["start"], "end": s_["end"], "word": s_["text"]}])]
        words = [w for w in words if w["end"] > a + 0.05 and w["start"] < b - 0.05]
        chunk: list[tuple[str, float, float]] = []

        def flush():
            nonlocal chunk
            parts, wl, pos = [], [], 0
            for wt, ws, we in chunk:
                wt = clean(wt)
                if not parts:
                    wt = wt.lstrip()
                if not wt.strip():
                    continue
                lead = len(wt) - len(wt.lstrip())
                wl.append({"s": pos + lead, "e": pos + len(wt.rstrip()),
                           "t0": round(src_to_out(seg, max(ws, a)), 3), "t1": round(src_to_out(seg, min(we, b)), 3)})
                parts.append(wt)
                pos += len(wt)
            txt = "".join(parts).rstrip()
            if txt:
                t0 = src_to_out(seg, max(chunk[0][1], a))
                t1 = min(src_to_out(seg, min(chunk[-1][2], b)), seg["out_end"])
                if t1 - t0 > 0.15:
                    item = {"text": txt, "start": round(t0, 3), "end": round(t1, 3),
                            "style": sub.get("style", "caption"), **shared}
                    if sub.get("karaoke"):
                        item["words"] = wl
                    items.append(item)
            chunk = []

        for w in words:
            wt = w["word"]
            cur = "".join(x[0] for x in chunk)
            if chunk and (w["start"] - chunk[-1][2] > 0.6 or text_len(cur + wt) > max_chars):
                flush()
            chunk.append((wt, w["start"], w["end"]))
            if wt.strip() and wt.strip()[-1] in "。！？.!?":
                flush()
        if chunk:
            flush()
    # captions pause while a freeze-frame intro is on screen
    holds = [(t["out_start"] + t["intro_at"], t["out_start"] + t["intro_at"] + t["intro_hold"])
             for t in timeline if t.get("intro_at") is not None]
    def part(it, keep, start, end):
        """Caption limited to the words in `keep` (word-timed captions), else the full text."""
        if not it.get("words"):
            return {**it, "start": start, "end": end}
        if not keep:
            return None
        s0, e0 = keep[0]["s"], keep[-1]["e"]
        return {**it, "start": start, "end": end, "text": it["text"][s0:e0],
                "words": [{**w, "s": w["s"] - s0, "e": w["e"] - s0} for w in keep]}

    for h0, h1 in holds:
        split = []
        for it in items:
            if it["start"] < h1 and it["end"] > h0:
                ws = it.get("words") or []
                if it["start"] < h0 - 0.15:
                    p_ = part(it, [w for w in ws if w["t0"] < h0], it["start"], h0)
                    if p_:
                        split.append(p_)
                if it["end"] > h1 + 0.15:
                    p_ = part(it, [w for w in ws if w["t0"] >= h0], h1, it["end"])
                    if p_:
                        split.append(p_)
            else:
                split.append(it)
        items = split
    items.sort(key=lambda x: x["start"])
    items = _merge_short(items, float(sub.get("max_chars", 14)) * 1.35)
    # keep captions from overlapping each other
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
    ap.add_argument("--plan", action="store_true", help="print the planned timeline (after beat sync/pins) and exit")
    ap.add_argument("--formats", default=None,
                    help="render the edit for several aspect ratios, e.g. 16:9,9:16,1:1 (see derive_format)")
    ap.add_argument("--qa", action="store_true", help="after rendering, check loudness, peaks, silences and whether "
                    "every caption is still intelligible in the mix (re-transcribes the output)")
    args = ap.parse_args()

    edl_path = Path(args.edl).resolve()
    if args.formats:
        flags = [f for f, on in (("--draft", args.draft), ("--no-review", args.no_review), ("--qa", args.qa)) if on]
        render_formats(edl_path, [f.strip() for f in args.formats.split(",") if f.strip()], flags)
        return
    base = edl_path.parent
    edl = load_json(edl_path)
    analysis = None
    if edl.get("analysis"):
        ap_ = Path(edl["analysis"])
        analysis = load_json(ap_ if ap_.is_absolute() else base / ap_)

    o = edl.get("output", {})
    W, H, fps = int(o.get("width", 1920)), int(o.get("height", 1080)), float(o.get("fps", 30))
    PLAN_FPS[0] = fps
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

    ctx = {"W": W, "H": H, "fps": fps, "grade": edl.get("grade", "none"), "fit": o.get("fit", "crop"), "font": o.get("font"),
           "bg": o.get("bg", "black"), "clip_dir": clip_dir, "probe": cprobe,
           "voice": edl.get("audio", {}).get("voice"),
           "clip_q": 26 if args.draft else 16, "clip_preset": "ultrafast" if args.draft else "veryfast"}

    for c in edl["clips"]:
        if c.get("type", "video") == "video":
            c["path"] = resolve_src(c.get("src") or c.get("path"), analysis, base)
        elif c.get("type") == "image":
            c["path"] = resolve_src(c["path"], None, base)
    for m in edl.get("music", []):
        m["path"] = resolve_src(m["path"], analysis, base)
    for snd in edl.get("sounds", []):
        if not snd.get("sfx"):
            snd["path"] = resolve_src(snd.get("src") or snd["path"], analysis, base)

    from brand import apply_brand
    apply_brand(edl, base)
    edl["_base"] = str(base)
    prepare_narration(edl, work)
    snap_intros_to_pauses(edl, analysis)
    apply_beat_sync(edl, analysis, base)
    apply_pins(edl)
    save_json(edl, work / "resolved_edl.json")  # after beat sync + pins: the exact in/out points rendered
    if args.plan:
        for k, (c, t0) in enumerate(zip(edl["clips"], plan_starts(edl["clips"]))):
            src = f"{c.get('in', 0):.2f}-{c.get('out', 0):.2f}" if c.get("type", "video") == "video" else c.get("type")
            print(f"{k:3d}  {t0:7.2f}  len {_clen(c):5.2f}  {src}")
        return

    # 1) clips
    rendered = []
    for i, c in enumerate(edl["clips"]):
        r = render_clip(i, c, ctx)
        log(f"clip {i + 1}/{len(edl['clips'])} {'(cached)' if r['cached'] else ''} {r['duration']:.2f}s")
        rendered.append(r)

    # 2) join graph (video only) + timeline
    n = len(rendered)
    inputs, parts, timeline = [], [], []
    for r in rendered:
        inputs += ["-i", r["path"]]
    for i in range(n):  # common timebase so concat and xfade can be mixed freely
        parts.append(f"[{i}:v]fps={fps},settb=AVTB,setpts=PTS-STARTPTS[s{i}]")
    acc = 0.0
    cur_v = "[s0]"
    trans_in = [0.0] * n  # xfade duration into each clip (0 = cut)
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
                trans_in[i] = d
                parts.append(f"{cur_v}[s{i}]xfade=transition={ttype}:duration={d:.3f}:offset={start:.3f}[v{i}]")
            else:
                start = acc
                parts.append(f"{cur_v}[s{i}]concat=n=2:v=1:a=0[v{i}]")
            cur_v = f"[v{i}]"
            acc = start + r["duration"]
        sp = float(c.get("speed", 1.0))
        src_in = float(c.get("in", 0))
        timeline.append({"clip": c, "out_start": round(start, 3), "out_end": round(start + r["duration"], 3),
                         "src_in": src_in, "speed": sp})
        it = intro_of(c, fps)
        hold = it[2] if it else 0.0
        timeline[-1].update({"src_out": src_in + (r["duration"] - hold) * sp,
                             "intro_at": it[1] if it else None, "intro_hold": hold})
    total = acc
    # visible span of each clip: transitions are split at their midpoint
    for i, t in enumerate(timeline):
        prev = timeline[i - 1] if i else None
        nxt = timeline[i + 1] if i + 1 < len(timeline) else None
        t["vis_start"] = round((prev["out_end"] + t["out_start"]) / 2, 3) if prev and prev["out_end"] > t["out_start"] \
            else t["out_start"]
        t["vis_end"] = round((t["out_end"] + nxt["out_start"]) / 2, 3) if nxt and t["out_end"] > nxt["out_start"] \
            else t["out_end"]

    # 3) overlay layer (texts + subtitles + images)
    subs = build_subtitles(edl, timeline, analysis)
    texts = [t for t in (anchor_to_clip(x, timeline) for x in edl.get("texts", [])) if t] + subs
    images = []
    for im in edl.get("images", []):
        images.append({**im, "path": resolve_src(im["path"], None, base)} if im.get("path") else dict(im))
    scale = W / int(o.get("width", W))
    layer_list = None
    if texts or images:
        layer_list = ovl.build_layer(texts, images, W, H, fps, total, work / "layer", font=o.get("font"),
                                     scale=scale)
    vin = sum(1 for x in inputs if x == "-i")
    # three.js / alpha video overlays: above the picture, below the text layer
    for k, ov in enumerate(edl.get("overlays", [])):
        if "clip" in ov:
            seg = timeline[int(ov["clip"])]
            t0 = seg["out_start"] + float(ov.get("start", 0))
            t1 = seg["out_start"] + float(ov["end"]) if "end" in ov else seg.get("vis_end", seg["out_end"])
        else:
            t0, t1 = float(ov.get("start", 0)), float(ov.get("end", total))
        t1 = min(t1, total)
        if t1 - t0 < 0.05:
            continue
        if ov.get("fx"):
            from fx3d import render_fx
            src = render_fx(ov["fx"], ov.get("params", {}), W, H, fps, t1 - t0, work / "fx")
        else:
            src = resolve_src(ov["path"], None, base)
        inputs += ["-i", str(src)]
        op = float(ov.get("opacity", 1.0))
        fade = float(ov.get("fade", 0.3))
        chain = (f"[{vin}:v]format=rgba,fps={fps},trim=duration={t1 - t0:.3f},"
                 f"fade=t=out:st={max(t1 - t0 - fade, 0):.3f}:d={fade}:alpha=1,"
                 + (f"colorchannelmixer=aa={op}," if op < 1 else "")
                 + f"setpts=PTS-STARTPTS+{t0:.3f}/TB[ovl{k}]")
        parts.append(chain)
        parts.append(f"{cur_v}[ovl{k}]overlay=0:0:eof_action=pass:format=auto[vov{k}]")
        cur_v = f"[vov{k}]"
        vin += 1
    if layer_list:
        inputs += ["-f", "concat", "-safe", "0", "-i", str(layer_list)]
        parts.append(f"[{vin}:v]format=rgba,setpts=PTS-STARTPTS[lay]")
        parts.append(f"{cur_v}[lay]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[vout]")
        vin += 1
    else:
        parts.append(f"{cur_v}format=yuv420p[vout]")

    # 4) audio: clip audio placed on the timeline (J/L cuts), music, sound effects
    vin = sum(1 for x in inputs if x == "-i")
    base_vol = float(edl.get("audio", {}).get("clip_volume", 1.0))
    # side-chain only when some music track actually ducks (else the split output dangles)
    ducking = any(m.get("duck") for m in edl.get("music", []))
    flagged = [i for i, c in enumerate(edl["clips"]) if c.get("duck_music")] if ducking else []
    clip_tracks, side_tracks = [], []
    for i, (c, r, t) in enumerate(zip(edl["clips"], rendered, timeline)):
        au = r.get("audio")
        if not au:
            continue
        at = t["out_start"] - au["lead"]
        nxt_d = trans_in[i + 1] if i + 1 < n else 0.0
        has_tail = float(c.get("audio_tail", 0)) > 0
        fin = trans_in[i] if (trans_in[i] and au["lead"] == 0) else 0.03
        fout = nxt_d if (nxt_d and not has_tail) else (0.15 if has_tail else 0.03)
        inputs += ["-i", au["path"]]
        chain = [f"afade=t=in:d={fin:.3f}", f"afade=t=out:st={max(au['duration'] - fout, 0):.3f}:d={fout:.3f}"]
        if at < 0:
            chain.insert(0, f"atrim=start={-at:.3f},asetpts=PTS-STARTPTS")
            at = 0.0
        ms = int(round(at * 1000))
        chain.append(f"adelay={ms}|{ms}")
        if flagged and i in flagged:
            parts.append(f"[{vin}:a]{','.join(chain)},asplit=2[ca{i}][cs{i}]")
            side_tracks.append(f"[cs{i}]")
        else:
            parts.append(f"[{vin}:a]{','.join(chain)}[ca{i}]")
        clip_tracks.append(f"[ca{i}]")
        vin += 1

    for k, it in enumerate((edl.get("narration") or {}).get("items", [])):
        at = (timeline[int(it["clip"])]["out_start"] + float(it.get("start", 0))) if "clip" in it \
            else float(it.get("at", 0))
        if at >= total:
            continue
        ms = int(round(at * 1000))
        vol = float(it.get("volume", edl["narration"].get("volume", 1.0)))
        inputs += ["-i", it["_path"]]
        chain = f"[{vin}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},adelay={ms}|{ms}"
        if ducking:
            parts.append(f"{chain},asplit=2[na{k}][ns{k}]")
            side_tracks.append(f"[ns{k}]")
        else:
            parts.append(f"{chain}[na{k}]")
        clip_tracks.append(f"[na{k}]")
        vin += 1

    def bus(tracks, label):
        if not tracks:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={total:.3f}[{label}]")
        elif len(tracks) == 1:
            parts.append(f"{tracks[0]}apad,atrim=duration={total:.3f}[{label}]")
        else:
            parts.append(f"{''.join(tracks)}amix=inputs={len(tracks)}:duration=longest:normalize=0:dropout_transition=0,"
                         f"apad,atrim=duration={total:.3f}[{label}]")

    bus(clip_tracks, "cbus0")
    music = edl.get("music", [])
    nduck = sum(1 for m in music if m.get("duck"))
    if nduck and side_tracks:
        bus(side_tracks, "sidebus")
        parts.append(f"[cbus0]volume={base_vol}[cbus]")
        parts.append(f"[sidebus]asplit={nduck}" + "".join(f"[aside{j}]" for j in range(nduck)) if nduck > 1
                     else "[sidebus]anull[aside0]")
    elif nduck:
        parts.append(f"[cbus0]volume={base_vol},asplit={nduck + 1}[cbus]" + "".join(f"[aside{j}]" for j in range(nduck)))
    else:
        parts.append(f"[cbus0]volume={base_vol}[cbus]")
    mix, dj = ["[cbus]"], 0
    for k, m in enumerate(music):
        inputs += ["-stream_loop", "-1" if m.get("loop", True) else "0", "-i", m["path"]]
        mstart = float(m.get("start", 0))
        mend = float(m["end"]) if m.get("end") is not None else total
        mlen = max(0.1, mend - mstart)
        fi, fo = float(m.get("fade_in", 0.5)), float(m.get("fade_out", 2.0))
        ms = int(mstart * 1000)
        chain = (f"[{vin}:a]atrim=start={float(m.get('offset', 0)):.3f}:duration={mlen:.3f},asetpts=PTS-STARTPTS,"
                 f"aresample=48000,aformat=channel_layouts=stereo,volume={float(m.get('volume', 0.3))},"
                 f"afade=t=in:d={fi},afade=t=out:st={max(mlen - fo, 0):.3f}:d={fo},"
                 f"{(m['filter'] + ',') if m.get('filter') else ''}adelay={ms}|{ms}")
        if m.get("duck"):
            parts.append(chain + f"[mraw{k}]")
            parts.append(f"[mraw{k}][aside{dj}]sidechaincompress=threshold={m.get('duck_threshold', 0.03)}:"
                         f"ratio={m.get('duck_ratio', 8)}:attack=20:release={m.get('duck_release', 400)}[m{k}]")
            dj += 1
        else:
            parts.append(chain + f"[m{k}]")
        mix.append(f"[m{k}]")
        vin += 1
    from sfx import sfx_file
    auto = []
    for i, (c, t) in enumerate(zip(edl["clips"], timeline)):
        it = intro_of(c, fps)
        if it and it[0].get("sfx", "hit"):
            auto.append({"sfx": it[0].get("sfx", "hit"), "at": t["out_start"] + it[1], "volume": it[0].get("sfx_volume", 0.7)})
            auto.append({"sfx": "whoosh", "at": t["out_start"] + it[1] + 0.02, "volume": 0.35,
                         "params": {"duration": 0.45}})
    for k, snd in enumerate(list(edl.get("sounds", [])) + auto):
        if "clip" in snd:
            at = timeline[int(snd["clip"])]["out_start"] + float(snd.get("start", 0))
        else:
            at = float(snd.get("at", snd.get("start", 0)))
        if snd.get("sfx"):
            path, a_in, a_len = sfx_file(snd["sfx"], work / "sfx", **snd.get("params", {})), 0.0, None
        else:
            path = snd["path"]
            a_in = float(snd.get("in", 0))
            a_len = float(snd["out"]) - a_in if snd.get("out") is not None else None
        inputs += ["-ss", f"{a_in:.3f}", *(["-t", f"{a_len:.3f}"] if a_len else []), "-i", str(path)]
        chain = [f"volume={float(snd.get('volume', 1.0))}", "aresample=48000", "aformat=channel_layouts=stereo"]
        if snd.get("fade_in"):
            chain.append(f"afade=t=in:d={snd['fade_in']}")
        if snd.get("fade_out") and a_len:
            chain.append(f"afade=t=out:st={max(a_len - float(snd['fade_out']), 0):.3f}:d={snd['fade_out']}")
        if at < 0:
            chain.insert(0, f"atrim=start={-at:.3f},asetpts=PTS-STARTPTS")
            at = 0.0
        ms = int(round(at * 1000))
        chain.append(f"adelay={ms}|{ms}")
        parts.append(f"[{vin}:a]{','.join(chain)}[snd{k}]")
        mix.append(f"[snd{k}]")
        vin += 1
    if len(mix) > 1:
        parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0:dropout_transition=0[amix]")
    else:
        parts.append("[cbus]anull[amix]")
    alast = "[amix]"
    lufs = float(o.get("loudness", -14))
    parts.append(f"{alast}loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000[aout]")

    fg_path = work / "graph.txt"
    fg_path.write_text(";\n".join(parts), encoding="utf-8")
    # cap the bitrate: grain/noise looks (vhs, silent, film, grain) barely compress and would
    # otherwise reach 70-90 Mbps at crf 18 (hundreds of MB for a 40 s clip)
    px = W * H * fps
    cap = o.get("maxrate") or (f"{max(4, round(px / (1920 * 1080 * 30) * 10))}M")
    rate = ["-maxrate", str(cap), "-bufsize", f"{int(str(cap).rstrip('Mk')) * 2}M"] if str(cap).endswith("M") \
        else ["-maxrate", str(cap), "-bufsize", str(cap)]
    enc = (["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"] if args.draft else
           ["-c:v", "libx264", "-preset", o.get("preset", "medium"), "-crf", str(o.get("crf", 18)), *rate])
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
    if args.qa:
        from qa import qa as run_qa
        lang = None
        if analysis:
            langs = [v["transcript"]["language"] for v in analysis.get("videos", []) if v.get("transcript")]
            lang = langs[0] if langs else None
        report["qa"] = run_qa(str(out_path), str(work / "subtitles.json"), lang=lang)["summary"]
    if not args.no_review:
        from preview import review_sheet
        report["review"] = review_sheet(str(out_path), [t["out_start"] for t in timeline], work / "review")
    print(json.dumps(report, ensure_ascii=False, indent=1))


def _measure(path: Path):
    from common import run, FFMPEG
    p = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
            check=False)
    m = re.findall(r"I:\s+(-?[0-9.]+) LUFS", p.stderr)
    pk = re.findall(r"Peak:\s+(-?[0-9.]+) dBFS", p.stderr)
    return (float(m[-1]) if m else None), (float(pk[-1]) if pk else -99.0)


def fix_loudness(path: Path, target: float, max_peak: float = -1.0):
    """Single-pass loudnorm can land a few LU off, and AAC adds 1-3 dB of overshoot on hard
    transients (hits, booms). Correct the level and limit, then re-measure the encoded file and
    tighten the limiter until the true peak is at or below `max_peak` (audio-only passes)."""
    measured, peak = _measure(path)
    if measured is None or measured < -60:
        return
    gain = target - measured if abs(target - measured) >= 1.0 else 0.0
    if gain == 0.0 and peak <= max_peak:
        return
    src = path.with_name(path.stem + ".src" + path.suffix)
    path.replace(src)
    limit = 0.7
    try:
        for attempt in range(4):
            tmp = path.with_name(path.stem + ".loud" + path.suffix)
            ffmpeg(["-i", str(src), "-map", "0", "-c:v", "copy", "-af",
                    f"volume={gain:.2f}dB,alimiter=limit={limit:.3f}:level=false:attack=3:release=80",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)])
            m2, p2 = _measure(tmp)
            tmp.replace(path)
            if p2 <= max_peak:
                break
            limit *= 10 ** ((max_peak - p2 - 0.3) / 20)  # pull the limiter down by the overshoot
        log(f"loudness {measured:.1f} LUFS / peak {peak:.1f} dBFS -> {m2:.1f} LUFS / {p2:.1f} dBFS"
            f" ({attempt + 1} pass{'es' if attempt else ''})")
    finally:
        if path.exists():
            src.unlink(missing_ok=True)
        else:
            src.replace(path)


FORMATS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350),
           "4:3": (1440, 1080)}
CAPTION_POS = {"16:9": [0.5, 0.88], "9:16": [0.5, 0.72], "1:1": [0.5, 0.84], "4:5": [0.5, 0.8], "4:3": [0.5, 0.86]}
CAPTION_CHARS = {"16:9": 22, "9:16": 13, "1:1": 16, "4:5": 15, "4:3": 20}


def _deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def derive_format(edl: dict, fmt: str, src_vertical: bool) -> dict:
    """The same edit for another aspect ratio: tracked crops when the target is narrower than
    the footage, blurred fill when it is wider, captions placed and wrapped for the format,
    person boxes re-detected. `edl["formats"][fmt]` overrides anything (deep merge)."""
    import copy
    W, H = FORMATS[fmt]
    v = copy.deepcopy(edl)
    v.pop("formats", None)
    o = v.setdefault("output", {})
    same = (int(o.get("width", 0)), int(o.get("height", 0))) == (W, H)
    tag = fmt.replace(":", "x")
    p = Path(o.get("path", "output.mp4"))
    stem = re.sub(r"_(16x9|9x16|1x1|4x5|4x3)$", "", p.stem)
    o["path"] = str(p.with_name(f"{stem}_{tag}{p.suffix}"))
    if not same:
        o["width"], o["height"] = W, H
        narrower = W / H < (9 / 16 if src_vertical else 16 / 9) - 0.01
        o["fit"] = "crop" if (narrower or fmt == "1:1") else ("blur" if src_vertical else o.get("fit", "crop"))
        for c in v["clips"]:
            c.pop("fit", None)
            if o["fit"] == "crop" and c.get("type", "video") == "video":
                c["focus"] = "track"
            for e in c.get("effects", []):
                if e.get("type") == "intro":
                    e.pop("box", None)  # output-frame coordinates of the old format
        sub = v.get("subtitles")
        if sub:
            sub["pos"] = CAPTION_POS[fmt]
            sub["max_chars"] = CAPTION_CHARS[fmt]
            for it in sub.get("items", []):
                if "pos" in it:
                    it["pos"] = CAPTION_POS[fmt]
    return _deep_merge(v, (edl.get("formats") or {}).get(fmt, {}))


def render_formats(edl_path: Path, formats: list[str], passthrough: list[str]):
    import subprocess
    edl = load_json(edl_path)
    an = edl.get("analysis")
    src_vertical = False
    if an:
        ap_ = Path(an) if Path(an).is_absolute() else edl_path.parent / an
        vids = load_json(ap_).get("videos", [])
        src_vertical = bool(vids) and vids[0].get("height", 0) > vids[0].get("width", 1)
    outs = []
    for fmt in formats:
        if fmt not in FORMATS:
            raise SystemExit(f"unknown format {fmt}; options: {', '.join(FORMATS)}")
        var = derive_format(edl, fmt, src_vertical)
        vp = edl_path.with_name(f".{edl_path.stem}.{fmt.replace(':', 'x')}.json")
        save_json(var, vp)
        log(f"== {fmt}: {var['output']['path']}")
        r = subprocess.run([sys.executable, __file__, str(vp), *passthrough])
        if r.returncode:
            raise SystemExit(f"{fmt} failed")
        outs.append(var["output"]["path"])
    print(json.dumps({"formats": dict(zip(formats, outs))}, indent=1))


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
