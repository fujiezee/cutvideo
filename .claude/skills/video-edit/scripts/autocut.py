#!/usr/bin/env python3
"""First drafts of several styles from one analysis, in one go.

For each requested style a recipe picks shots from the footage features (styles.py),
writes an EDL, renders it with QA, and everything is summarised in a report and a
side-by-side contact sheet. These are drafts: Claude reviews them and refines the EDLs.

Usage:
  autocut.py JOB/work/analysis.json --styles trailer,beatmix,vhs [--music song.mp3]
             [--title "Name"] [--credit "attribution"] [--exclude 588-708] [--draft] [--no-render]
  --styles top3 | recommended | all | comma list (see RECIPES)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ffmpeg, fmt_ts, load_json, log, save_json  # noqa: E402
from styles import catalogue, features  # noqa: E402
from brand import load_brand  # noqa: E402
from tts import load_config as load_tts_config  # noqa: E402

TAPE = "highpass=f=180,lowpass=f=4500"
GRAMOPHONE = "highpass=f=250,lowpass=f=3200"


# ------------------------------------------------------------------ shot picking


class Picker:
    """Chooses non-overlapping pieces of shots; every piece lies inside one detected shot
    (with a margin), so no clip crosses a cut and no flash frame of a neighbour appears."""

    def __init__(self, margin: float = 0.15):
        self.used: list[tuple[str, float, float]] = []
        self.margin = margin

    def free(self, s, a, b):
        return all(v != s["vid"] or b <= ua or a >= ub for v, ua, ub in self.used)

    def piece(self, s, length, where="mid"):
        lo, hi = s["start"] + self.margin, s["end"] - self.margin
        if hi - lo < min(length, 0.4):
            return None
        length = min(length, hi - lo)
        want = {"start": lo, "end": hi - length}.get(where, (lo + hi - length) / 2)
        # free gaps of this shot (a long take can give several pieces), nearest to `want`
        busy = sorted((ua, ub) for v, ua, ub in self.used if v == s["vid"] and ub > lo and ua < hi)
        gaps, cur = [], lo
        for ua, ub in busy:
            if ua - cur >= length:
                gaps.append((cur, ua))
            cur = max(cur, ub + 0.05)
        if hi - cur >= length:
            gaps.append((cur, hi))
        if not gaps:
            return None
        a = min((min(max(want, g0), g1 - length) for g0, g1 in gaps), key=lambda x: abs(x - want))
        a, b = round(a, 3), round(a + length, 3)
        self.used.append((s["vid"], a, b))
        return a, b

    def take(self, shots, k, length, key="score", min_gap=6.0, chrono=True, where="mid", fallback=None):
        """k pieces from the best shots by `key`, at least `min_gap` s apart in the source;
        when `shots` cannot supply k, the rest comes from `fallback` (e.g. all shots)."""
        out = []
        ranked = sorted(shots, key=lambda s: -s.get(key, 0))
        for gap in (min_gap, 0.0):  # short footage: relax the spacing rather than give up
            for s in ranked:
                if len(out) >= k:
                    break
                if any(o["vid"] == s["vid"] and abs(o["start"] - s["start"]) < gap for o, _ in out):
                    continue
                p = self.piece(s, length, where)
                if p:
                    out.append((s, p))
        if len(out) < k and fallback:
            out += self.take([s for s in fallback if all(o is not s for o, _ in out)], k - len(out), length,
                             key=key, min_gap=min_gap, chrono=False, where=where)
        if chrono:
            out.sort(key=lambda x: (x[0]["vid"], x[0]["start"]))
        return out


_END = tuple(".!?。！？…")


def line_quality(l) -> float:
    """Quotable lines first: a whole sentence (capital or CJK start, final punctuation),
    4-12 words, not starting mid-thought ("and", "but", ...)."""
    t = l["text"].strip()
    words = t.split() if t.isascii() else list(t)
    n = len(words) if t.isascii() else len(t) / 2
    q = 0.0
    q += 1.0 if t[:1].isupper() or not t[:1].isascii() else 0.0
    q += 1.0 if t.endswith(_END) else 0.0
    q -= 1.0 if t.isascii() and words and words[0].lower().strip(",") in ("and", "but", "or", "so", "because") else 0.0
    q += 1.0 if 4 <= n <= 12 else 0.0
    return q + min(n, 12) / 24


def clip(s, p, **x):
    return {"src": s["vid"], "in": p[0], "out": p[1], **x}


def end_card(credit, dur=2.4):
    c = {"type": "color", "color": "#000000", "duration": dur, "snap": False,
         "transition": {"type": "fadeblack", "duration": 0.3}}
    t = ({"text": credit, "clip": -1, "start": 0.3, "end": dur, "style": "subtitle", "size": 0.026,
          "color": "#BBBBBB", "pos": [0.5, 0.5], "max_width": 0.9} if credit else None)
    return c, t


def music_block(music, volume=0.85, duck=False, filt=None, offset=0.0):
    if not music:
        return []
    m = {"path": music, "offset": offset, "volume": volume, "loop": False, "fade_in": 0.5, "fade_out": 2.0}
    if duck:
        m["duck"] = True
    if filt:
        m["filter"] = filt
    return [m]


# ------------------------------------------------------------------ music


def find_music(an: dict, job: Path, min_len=25.0) -> str | None:
    """Longest loud stretch with no transcribed words (score, credits music), extracted to
    JOB/audio/auto_music.wav. None when the footage has no such stretch."""
    best = None
    for v in an.get("videos", []):
        words = sorted((w["start"], w["end"]) for s in (v.get("transcript") or {}).get("segments", [])
                       for w in s.get("words", []))
        edges = [0.0] + [x for w in words for x in w] + [v["duration"]]
        for g0, g1 in zip(edges[0::2], edges[1::2]):
            g0, g1 = g0 + 0.5, g1 - 0.5
            if g1 - g0 < min_len:
                continue
            sc = [s for s in v["scenes"] if s["start"] < g1 and s["end"] > g0]
            dur = sum(min(s["end"], g1) - max(s["start"], g0) for s in sc) or 1
            loud = sum(s.get("loudness_db", -90) * (min(s["end"], g1) - max(s["start"], g0)) for s in sc) / dur
            if loud > -30 and (best is None or g1 - g0 > best[2] - best[1]):
                best = (v["path"], g0, g1)
    if not best:
        return None
    out = job / "audio" / "auto_music.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        ffmpeg(["-ss", f"{best[1]:.2f}", "-to", f"{best[2]:.2f}", "-i", best[0], "-vn", "-ac", "2", "-ar", "48000",
                str(out)])
    log(f"music: using {fmt_ts(best[2] - best[1])} of the soundtrack ({fmt_ts(best[1])}-{fmt_ts(best[2])}) "
        f"-> {out.name}")
    return str(out)


# ------------------------------------------------------------------ recipes
# Each returns (edl_without_paths, notes). Paths are filled in by build().


def r_beatmix(f, P, o):
    picks = P.take(f["action"], 22, 0.55, key="motion", min_gap=4, fallback=f["all"])
    if len(picks) < 8:
        return None, ["not enough usable shots"]
    clips = []
    for k, (s, p) in enumerate(picks):
        fx = []
        if k == 0:
            fx = [{"type": "zoom_in", "amount": 0.1}]
        elif k % 6 == 5:
            fx = [{"type": "flash", "duration": 0.12}]
        elif k % 8 == 3:
            fx = [{"type": "shake", "amount": 0.025, "end": 0.35}]
        clips.append(clip(s, p, focus="auto", volume=0.25, effects=fx))
    last = len(clips) - 1
    card, credit = end_card(o["credit"])
    clips.append(card)
    edl = {"output": {"width": 1080, "height": 1920, "fps": 30, "fit": "crop"}, "grade": "vivid", "clips": clips,
           "music": music_block(o["music"], 0.95),
           "sounds": [{"sfx": "hit", "clip": 0, "start": 0.0, "volume": 0.6}],
           "texts": [{"text": o["title"], "clip": 0, "start": 0.05, "end": 1.6, "style": "highlight",
                      "size": 0.08, "pos": [0.5, 0.3]}] + ([credit] if credit else [])}
    notes = []
    if o["music"]:
        edl["beat_sync"] = {"music": 0, "every": 1, "shift": "auto", "clips": [0, last]}
    else:
        notes.append("no music: cuts are not beat-synced; give a music track for a real beat mix")
    return edl, notes


def r_trailer(f, P, o):
    est = P.take(f["establishing"] or f["bright_day"], 1, 3.5, key="sharpness")
    lines = sorted(f["lines"], key=lambda l: -line_quality(l))
    chosen = []
    for l in sorted(lines[:12], key=lambda l: l["start"]):
        if len(chosen) < 3 and all(abs(l["start"] - c["start"]) > 20 for c in chosen):
            chosen.append(l)
    action = P.take(f["action"], 14, 0.9, key="motion", min_gap=5, fallback=f["all"])
    if len(action) < 6:
        return None, ["not enough action shots"]
    clips, texts = [], []
    if est:
        s, p = est[0]
        clips.append(clip(s, p, volume=0.3, snap=False, effects=[{"type": "zoom_in", "amount": 0.08}]))
    a_iter = iter(action)
    for l in chosen:
        P.used.append((l["vid"], l["start"] - 0.15, l["end"] + 0.25))
        clips.append({"src": l["vid"], "in": round(l["start"] - 0.15, 3), "out": round(l["end"] + 0.25, 3),
                      "duck_music": True, "snap": False, "volume": 1.3})
        for _ in range(2):
            n = next(a_iter, None)
            if n:
                clips.append(clip(n[0], n[1], volume=0.35))
    first_sync = len(clips)
    for s, p in a_iter:
        clips.append(clip(s, p, volume=0.35, effects=[{"type": "flash", "duration": 0.1}] if len(clips) % 4 == 0 else []))
    last_sync = len(clips) - 1
    clips.append({"type": "color", "color": "#000000", "duration": 3.2, "snap": False,
                  "transition": {"type": "fadeblack", "duration": 0.3}})
    texts.append({"text": o["title"], "clip": len(clips) - 1, "start": 0.3, "end": 3.0, "style": "card", "size": 0.09})
    card, credit = end_card(o["credit"])
    clips.append(card)
    if credit:
        texts.append(credit)
    edl = {"output": {"width": 1920, "height": 1080, "fps": 24, "fit": "crop"}, "grade": "trailer", "clips": clips,
           "music": music_block(o["music"], 0.8, duck=True),
           "subtitles": {"enabled": bool(chosen), "from": "transcript"},
           "sounds": [{"sfx": "boom", "clip": first_sync, "start": 0.0, "volume": 0.5}], "texts": texts}
    notes = [f"{len(chosen)} dialogue lines: check they make a story, reorder or swap in plan.md"]
    if o["music"]:
        edl["beat_sync"] = {"music": 0, "every": 1, "shift": "auto", "clips": [first_sync, last_sync]}
    else:
        notes.append("no music: a trailer needs a score")
    return edl, notes


def _pad_box(box, sw, sh, W, H):
    """Source-frame fractions -> output-frame fractions for `fit: pad`."""
    sc = min(W / sw, H / sh)
    ox, oy = (W - sw * sc) / 2, (H - sh * sc) / 2
    return [round((ox + box[0] * sw * sc) / W, 4), round((oy + box[1] * sh * sc) / H, 4),
            round((ox + box[2] * sw * sc) / W, 4), round((oy + box[3] * sh * sc) / H, 4)]


def r_cast_people(f, P, o, W=1920, H=1080):
    """One intro per recognised person, frozen on a frame where their face was found."""
    from people import person_box
    dims = {v["id"]: (v["width"], v["height"]) for v in o["videos"]}
    by_vid_shot = {}
    for s in f["all"]:
        by_vid_shot.setdefault(s["vid"], []).append(s)
    clips, named = [{"type": "color", "color": "#000000", "duration": 2.0}], 0
    for p in f["people"][:6]:
        best = None
        for a in sorted(p["appearances"], key=lambda a: -a["area"]):
            s = next((s for s in by_vid_shot.get(a["vid"], []) if s["start"] <= a["t"] < s["end"]), None)
            if not s or s["end"] - s["start"] < 2.0 or s.get("faces", 1) > 2:
                continue
            a0 = max(s["start"] + 0.15, a["t"] - 1.0)
            b0 = min(s["end"] - 0.15, a0 + 3.4)
            if b0 - a0 < 1.8 or not P.free(s, a0, b0):
                continue
            best = (s, a, round(a0, 3), round(b0, 3))
            break
        if not best:
            continue
        s, a, a0, b0 = best
        P.used.append((s["vid"], a0, b0))
        k = len(clips)
        named += bool(p["name"])
        sw, sh = dims[s["vid"]]
        clips.append({"src": s["vid"], "in": a0, "out": b0, "volume": 0.6,
                      "effects": [{"type": "intro", "at": round(a["t"] - a0, 3), "hold": 1.6,
                                   "color": o["palette"][(k - 1) % len(o["palette"])],
                                   "name": p["name"] or f"人物 {k:02d}", "role": p["role"] or "CHARACTER",
                                   "index": f"{k:02d}", "bg": "bw",
                                   "box": _pad_box(person_box(a["box"]), sw, sh, W, H)}]})
    if len(clips) < 3:
        return None, ["fewer than two recognised people with a usable shot"]
    clips[1]["transition"] = {"type": "fadeblack", "duration": 0.4}
    card, credit = end_card(o["credit"], 2.6)
    clips.append(card)
    edl = {"output": {"width": W, "height": H, "fps": 24, "fit": "pad"}, "grade": "trailer", "clips": clips,
           "music": music_block(o["music"], 0.7),
           "sounds": [{"sfx": "hit", "clip": 0, "start": 0.3, "volume": 0.5}],
           "texts": [{"text": o["title"], "clip": 0, "start": 0.3, "end": 1.9, "style": "card"}]
           + ([credit] if credit else [])}
    n = len(clips) - 2
    notes = [f"{n} recognised people, one intro each"]
    if named < n:
        notes.append("unnamed people show placeholders: people.py --name P1=Name:role, then rerun")
    return edl, notes


def r_cast(f, P, o):
    if f.get("people"):
        edl, notes = r_cast_people(f, P, o)
        if edl:
            return edl, notes
    long_ = lambda lst: [s for s in lst if s["dur"] >= 2.4]  # noqa: E731  (room for the freeze at 1.0 s)
    picks = P.take(long_(f["closeups"]), 5, 3.4, key="face_area", min_gap=60, fallback=long_(f["faces_any"]))
    if len(picks) < 2:
        return None, ["fewer than two clear single-person shots"]
    clips = [{"type": "color", "color": "#000000", "duration": 2.0}]
    for k, (s, p) in enumerate(picks):
        clips.append(clip(s, p, volume=0.6, effects=[{"type": "intro", "at": 1.0, "hold": 1.6,
                                                      "name": f"人物 {k + 1:02d}", "role": "CHARACTER",
                                                      "index": f"{k + 1:02d}", "bg": "bw"}]))
    clips[1]["transition"] = {"type": "fadeblack", "duration": 0.4}
    card, credit = end_card(o["credit"], 2.6)
    clips.append(card)
    edl = {"output": {"width": 1920, "height": 1080, "fps": 24, "fit": "pad"}, "grade": "trailer", "clips": clips,
           "music": music_block(o["music"], 0.7),
           "sounds": [{"sfx": "hit", "clip": 0, "start": 0.3, "volume": 0.5}],
           "texts": [{"text": o["title"], "clip": 0, "start": 0.3, "end": 1.9, "style": "card"}]
           + ([credit] if credit else [])}
    return edl, ["names are placeholders (人物 01...): fill in names and roles from the transcript",
                 "the same person may appear twice: check the frames"]


def r_karaoke(f, P, o):
    ls = sorted(f["lines"], key=lambda l: (l["vid"], l["start"]))
    best, at = 0, 0
    for i, l in enumerate(ls):
        n = sum(1 for m in ls[i:] if m["vid"] == l["vid"] and m["start"] - l["start"] <= 40)
        if n > best:
            best, at = n, i
    if best < 3:
        return None, ["not enough dialogue for a dialogue short"]
    win = [l for l in ls[at:at + best]][:9]
    clips = []
    for l in win:
        a, b = round(l["start"] - 0.12, 3), round(l["end"] + 0.18, 3)
        if clips and clips[-1]["src"] == l["vid"] and a - clips[-1]["out"] < 0.5:
            clips[-1]["out"] = b
        else:
            clips.append({"src": l["vid"], "in": a, "out": b, "focus": "track", "duck_music": True})
        P.used.append((l["vid"], a, b))
    card, credit = end_card(o["credit"])
    clips.append(card)
    edl = {"output": {"width": 1080, "height": 1920, "fps": 24, "fit": "crop"}, "grade": "natural", "clips": clips,
           "music": music_block(o["music"], 0.35, duck=True),
           "subtitles": {"enabled": True, "from": "transcript", "karaoke": "word", "karaoke_box": "#FFE600",
                         "size": 0.064, "pos": [0.5, 0.74], "max_chars": 13, "strip_punct": False, "stroke": 0.08},
           "texts": [credit] if credit else []}
    return edl, [f"dialogue from {fmt_ts(win[0]['start'])} to {fmt_ts(win[-1]['end'])}: check it is one scene"]


def r_vhs(f, P, o):
    pool = [s for s in f["bright_day"] if s["faces"]] or f["faces_any"]
    picks = P.take(pool, 9, 3.2, key="score", min_gap=10, fallback=f["bright_day"] + f["all"])
    if len(picks) < 4:
        return None, ["not enough daylight / people shots"]
    clips = [clip(s, p, focus="auto", volume=1.2, audio_filter=TAPE, duck_music=True,
                  effects=[{"type": "glitch", "duration": 0.18}] if k and k % 3 == 0 else [])
             for k, (s, p) in enumerate(picks)]
    clips.append({"type": "color", "color": "#1C3FAA", "duration": 2.0,
                  "transition": {"type": "fadegrays", "duration": 0.2}})
    blue = len(clips) - 1
    card, credit = end_card(o["credit"])
    clips.append(card)
    edl = {"output": {"width": 1440, "height": 1080, "fps": 30, "fit": "crop"}, "grade": "vhs", "clips": clips,
           "music": music_block(o["music"], 0.45, duck=True, filt=TAPE),
           "images": [{"preset": "scanlines", "opacity": 0.22}],
           "subtitles": {"enabled": True, "from": "transcript", "font": "mono", "size": 0.042, "pos": [0.5, 0.78],
                         "strip_punct": False, "max_chars": 22},
           "texts": [{"text": "PLAY ▶", "start": 0.0, "end": 3.0, "style": "stamp", "pos": [0.05, 0.06]},
                     {"text": "SP", "start": 0.0, "until_clip": blue, "style": "stamp", "pos": [0.95, 0.06],
                      "align": "right"},
                     {"text": "■ STOP", "clip": blue, "start": 0.2, "end": 2.0, "style": "stamp", "size": 0.07,
                      "pos": "center"}] + ([credit] if credit else [])}
    return edl, []


def r_silent(f, P, o):
    close = P.take(f["closeups"], 4, 2.6, key="face_area", min_gap=15, fallback=f["faces_any"])
    act = P.take(f["action"], 4, 2.2, key="motion", min_gap=10, fallback=f["all"])
    lines = sorted(f["lines"], key=lambda l: -line_quality(l))[:2]
    if len(close) + len(act) < 4:
        return None, ["not enough close-ups / action"]
    FILM = {"focus": "auto", "mute": True, "speed": 1.15, "effects": [{"type": "flicker", "amount": 0.05}]}
    clips, texts = [], []

    def title(text, sec=2.6, size=0.06, tr=None):
        c = {"type": "color", "color": "#0B0B0B", "duration": sec, "grade": "silent"}
        if tr:
            c["transition"] = tr
        clips.append(c)
        texts.append({"text": text, "clip": len(clips) - 1, "start": 0.25, "end": sec - 0.2, "style": "intertitle",
                      "size": size})

    title(o["title"], 3.0, 0.075)
    shots = [x for pair in zip(close, act) for x in pair] + close[len(act):] + act[len(close):]
    shots.sort(key=lambda x: x[0]["start"])
    third = max(1, len(shots) // 3)
    for k, (s, p) in enumerate(shots):
        if k and k % third == 0 and lines:
            title(f"「{lines.pop(0)['text'].strip()}」", 2.6, 0.055)
        clips.append(clip(s, p, **FILM, **({"transition": {"type": "circleopen", "duration": 0.8}} if k == 0 else {})))
    title("THE END", 3.0, 0.075, tr={"type": "circleclose", "duration": 0.9})
    if o["credit"]:
        texts.append({"text": o["credit"], "clip": len(clips) - 1, "start": 0.8, "end": 2.9, "style": "subtitle",
                      "font": "serif", "size": 0.024, "color": "#9A9A9A", "pos": [0.5, 0.86]})
    edl = {"output": {"width": 1440, "height": 1080, "fps": 24, "fit": "crop"}, "grade": "silent", "clips": clips,
           "music": music_block(o["music"], 1.6, filt=GRAMOPHONE), "texts": texts}
    notes = ["intertitles quote the source language: translate them if the audience needs it"]
    if not o["music"]:
        notes.append("no music: a silent film needs a score")
    return edl, notes


def r_cyberpunk(f, P, o):
    picks = P.take(f["dark_neon"], 14, 1.3, key="colorfulness", min_gap=4, fallback=f["action"] + f["all"])
    if len(picks) < 6:
        return None, ["not enough dark / colourful shots"]
    clips = []
    for k, (s, p) in enumerate(picks):
        fx = [{"type": "glitch", "duration": 0.22}] if k % 3 == 1 else ([{"type": "rgb_split"}] if k % 4 == 2 else [])
        clips.append(clip(s, p, focus="auto", volume=0.35, effects=fx))
    last = len(clips) - 1
    card, credit = end_card(o["credit"])
    clips.append(card)
    edl = {"output": {"width": 1920, "height": 1080, "fps": 30, "fit": "crop"}, "grade": "cyberpunk", "clips": clips,
           "music": music_block(o["music"], 0.9),
           "sounds": [{"sfx": "glitch", "clip": i, "start": 0.0, "volume": 0.35}
                      for i, c in enumerate(clips) if any(e.get("type") == "glitch" for e in c.get("effects", []))],
           "texts": [{"text": o["title"].upper(), "clip": 0, "start": 0.2, "end": 1.9, "style": "neon", "size": 0.09}]
           + ([credit] if credit else [])}
    if o["music"]:
        edl["beat_sync"] = {"music": 0, "every": 2, "shift": "auto", "clips": [0, last]}
    return edl, [] if len(f["dark_neon"]) >= 6 else ["few dark/neon shots: the look will feel forced"]


def _ts(x: str) -> float:
    parts = [float(p) for p in x.strip().split(":")]
    return sum(v * 60 ** k for k, v in enumerate(reversed(parts)))


def parse_script_line(raw: str):
    """'text | 0:25-0:45 @P2' -> ('text', {"range": (25.0, 45.0), "people": ["P2"]}).
    After '|': an optional source range and/or @person references (people.json id or name)."""
    text, sep, spec = raw.rpartition("|")
    if not sep:
        return raw.strip(), None
    out = {"range": None, "people": []}
    for tok in spec.split():
        if tok.startswith("@"):
            out["people"].append(tok[1:])
        elif "-" in tok:
            try:
                a, b = tok.split("-")
                out["range"] = (_ts(a), _ts(b))
            except ValueError:
                return raw.strip(), None
        else:
            return raw.strip(), None
    return text.strip(), out


def r_recap(f, P, o):
    """Narrated recap: one script line per story beat, shots from the matching part of the
    film in story order, each group exactly as long as its spoken line."""
    script = o.get("script") or []
    if not script:
        return None, ["needs a narration script: one sentence per line in story order, optionally "
                      "'| m:ss-m:ss' for the source range of that beat; pass --script"]
    from tts import synth
    shots = sorted(f["all"], key=lambda s: (s["vid"], s["start"]))
    if len(shots) < len(script):
        return None, ["fewer shots than script lines"]
    n = len(script)
    clips, items = [{"type": "color", "color": "#000000", "duration": 1.6, "snap": False}], []
    texts = [{"text": o["title"], "clip": 0, "start": 0.2, "end": 1.5, "style": "card"}]
    for i, raw in enumerate(script):
        line, rng = parse_script_line(raw)
        _, dur = synth(line, o["tts_dir"], o.get("voice"), o.get("rate"), o["tts_config"])
        if rng:  # the editor said where this beat is in the source and/or who should be seen
            part = shots
            if rng["range"]:
                a_, b_ = rng["range"]
                part = [s for s in part if s["end"] > a_ and s["start"] < b_]
            if rng["people"]:
                who = [p for p in f.get("people", []) if p["id"] in rng["people"]
                       or any(r and r in p.get("name", "") for r in rng["people"])]
                seen = {(a["vid"], a["t"]) for p in who for a in p["appearances"]}
                part = [s for s in part if any(v == s["vid"] and s["start"] <= t < s["end"] for v, t in seen)]
            part = part or shots[:1]
        else:
            part = shots[len(shots) * i // n: max(len(shots) * (i + 1) // n, len(shots) * i // n + 1)]
        need, group = dur + 0.9, []  # line starts 0.3 s in, plus transition overlap
        for s in sorted(part, key=lambda s: -s["score"]):
            if need <= 0.3:
                break
            p = P.piece(s, min(2.6, max(1.2, need)))
            if p:
                group.append((s, p))
                need -= p[1] - p[0]
        if need > 0.3:  # stretch the last piece or borrow from the neighbours
            for s in sorted(shots, key=lambda s: abs(s["start"] - part[-1]["start"])):
                if need <= 0.3:
                    break
                p = P.piece(s, min(2.6, max(1.2, need)))
                if p:
                    group.append((s, p))
                    need -= p[1] - p[0]
        group.sort(key=lambda x: (x[0]["vid"], x[0]["start"]))
        first = len(clips)
        for k, (s, p) in enumerate(group):
            c = clip(s, p, volume=0.2, snap=False)
            if k == 0 and i:
                c["transition"] = {"type": "fade", "duration": 0.25}
            clips.append(c)
        items.append({"text": line, "clip": first, "start": 0.3})
    card, credit = end_card(o["credit"])
    clips.append(card)
    if credit:
        texts.append(credit)
    edl = {"output": {"width": 1920, "height": 1080, "fps": 24, "fit": "crop"}, "grade": "trailer", "clips": clips,
           "music": music_block(o["music"], 0.4, duck=True),  # dense narration needs a quiet bed
           "narration": {"items": items, "volume": 1.25, "emphasis_style": o.get("emphasis_style") or None, **({"voice": o["voice"]} if o.get("voice") else {}),
                         **({"rate": o["rate"]} if o.get("rate") else {})},
           "subtitles": {"enabled": True, "from": "manual", "style": o.get("caption_style", "explainer")},
           "texts": texts}
    return edl, [f"{n} narration lines; check each line's shots illustrate it, swap shots in the EDL if not"]


RECIPES = {"trailer": r_trailer, "beatmix": r_beatmix, "cast": r_cast, "karaoke": r_karaoke, "vhs": r_vhs,
           "silent": r_silent, "cyberpunk": r_cyberpunk,
           "recap": r_recap}
ASPECT = {(1920, 1080): "16x9", (1080, 1920): "9x16", (1440, 1080): "4x3", (1080, 1080): "1x1"}


# ------------------------------------------------------------------ driver


def choose(spec: str, ranked: list[dict]) -> list[str]:
    auto = [s["key"] for s in ranked if s["key"] in RECIPES]
    if spec == "all":
        return auto
    if spec == "recommended":
        return [s["key"] for s in ranked if s["key"] in RECIPES and s["score"] >= 60]
    if spec.startswith("top"):
        return auto[:int(spec[3:] or 3)]
    keys = [k.strip() for k in spec.split(",") if k.strip()]
    bad = [k for k in keys if k not in RECIPES]
    if bad:
        raise SystemExit(f"no recipe for {bad}; available: {', '.join(RECIPES)}")
    return keys


def build(style, f, an_path: Path, auto_dir: Path, o) -> tuple[Path | None, list[str]]:
    edl, notes = RECIPES[style](f, Picker(), o)
    if edl is None:
        return None, notes
    out = edl["output"]
    tag = ASPECT.get((out["width"], out["height"]), f"{out['width']}x{out['height']}")
    if o.get("brand", True):
        edl["brand"] = True

    def rel(p):
        return os.path.relpath(Path(p).resolve(), auto_dir.resolve())

    edl = {"analysis": rel(an_path), **edl}
    out["path"] = f"../out/auto_{style}_{tag}.mp4"
    for m in edl.get("music", []):
        m["path"] = rel(m["path"])
    p = auto_dir / f"edl_{style}.json"
    save_json(edl, p)
    return p, notes


def render(edl_path: Path, draft: bool) -> dict:
    cmd = [sys.executable, str(Path(__file__).parent / "render.py"), str(edl_path), "--no-review", "--qa"]
    if draft:
        cmd.append("--draft")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        return {"error": (r.stderr or "")[-800:]}
    try:
        rep = json.loads(r.stdout[r.stdout.index("{"):])
    except ValueError:
        return {"error": "no report"}
    return rep


def compare_sheet(rows: list[tuple[str, str]], out: Path, n=6, h=200):
    import cv2
    from PIL import Image, ImageDraw
    strips = []
    for name, video in rows:
        cap = cv2.VideoCapture(video)
        dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / max(cap.get(cv2.CAP_PROP_FPS), 1)
        tiles = []
        for k in range(n):
            cap.set(cv2.CAP_PROP_POS_MSEC, dur * (k + 0.5) / n * 1000)
            ok, fr = cap.read()
            if not ok:
                continue
            im = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            im.thumbnail((10000, h))
            tiles.append(im)
        cap.release()
        w = sum(t.width for t in tiles) + 160
        row = Image.new("RGB", (w, h), (18, 18, 18))
        ImageDraw.Draw(row).text((10, h // 2 - 6), name, fill=(255, 214, 10))
        x = 160
        for t in tiles:
            row.paste(t, (x, 0))
            x += t.width
        strips.append(row)
    if not strips:
        return None
    W = max(s.width for s in strips)
    sheet = Image.new("RGB", (W, h * len(strips) + 4 * len(strips)), (0, 0, 0))
    for i, s in enumerate(strips):
        sheet.paste(s, (0, i * (h + 4)))
    sheet.save(out, quality=85)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("analysis")
    ap.add_argument("--styles", default="recommended")
    ap.add_argument("--music", default=None, help="music file; default: the footage's own longest music passage")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--title", default=None)
    ap.add_argument("--credit", default="", help="attribution line for the end card")
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--script", default=None, help="recap narration: text file, one sentence per line")
    ap.add_argument("--voice", default=None, help="tts voice (tts.py --voices)")
    ap.add_argument("--rate", type=int, default=None, help="tts speaking rate (words per minute)")
    ap.add_argument("--caption-style", default="explainer", help="narration captions (captions.py --list)")
    ap.add_argument("--emphasis-style", default="variety",
                    help="captions with a {marked} word switch to this style ('' to keep one style)")
    ap.add_argument("--no-brand", action="store_true", help="no product signature on the end card")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()

    an_path = Path(a.analysis).resolve()
    job = an_path.parent.parent
    an = load_json(an_path)
    exclude = []
    for e in a.exclude:
        vid, _, rng = e.rpartition(":")
        lo, hi = rng.split("-")
        exclude.append((vid or None, float(lo), float(hi)))
    f = features(an, an_path.parent, exclude)
    ranked = catalogue(f)
    styles = choose(a.styles, ranked)
    music = None if a.no_music else (str(Path(a.music).resolve()) if a.music else find_music(an, job))
    title = a.title or Path(an["videos"][0]["path"]).stem.replace("_", " ")
    auto_dir = job / "auto"
    script = ([ln.strip() for ln in Path(a.script).read_text(encoding="utf-8").splitlines() if ln.strip()]
              if a.script else [])
    o = {"music": music, "title": title, "credit": a.credit, "videos": an["videos"], "script": script,
         "voice": a.voice, "rate": a.rate, "tts_dir": auto_dir / ".render" / "tts", "brand": not a.no_brand, "caption_style": a.caption_style, "emphasis_style": a.emphasis_style,
         "palette": load_brand(job)["colors"], "tts_config": load_tts_config(job)}
    auto_dir.mkdir(exist_ok=True)
    (job / "out").mkdir(exist_ok=True)
    score = {s["key"]: s["score"] for s in ranked}

    results = []
    for st in styles:
        log(f"== {st}")
        p, notes = build(st, f, an_path, auto_dir, o)
        res = {"style": st, "score": score.get(st), "edl": str(p) if p else None, "notes": notes}
        if p and not a.no_render:
            rep = render(p, a.draft)
            if "error" in rep:
                res["error"] = rep["error"]
                log(f"{st}: render failed")
            else:
                res["video"] = rep.get("output")
                res["duration"] = rep.get("duration")
                res["qa"] = rep.get("qa")
        results.append(res)

    md = ["# Auto drafts", "", f"Music: {Path(music).name if music else 'none'} · title: {title}", "",
          "| Style | Score | Video | Length | QA | Notes |", "|---|---|---|---|---|---|"]
    for r in results:
        q = r.get("qa") or {}
        lines_ = (f"{q.get('captions_clear')}/{q.get('captions_checked')} lines clear" if q.get("captions_checked")
                  else "no captions")
        qa = (f"{q.get('integrated_lufs')} LUFS, {q.get('true_peak_dbfs')} dBTP, {lines_}"
              + (f"; {'; '.join(q['issues'])}" if q.get("issues") else "")) if q else ("render failed" if r.get("error") else "-")
        vid = Path(r["video"]).name if r.get("video") else ("skipped" if not r["edl"] else "-")
        md.append(f"| {r['style']} | {r['score']} | {vid} | {fmt_ts(r['duration']) if r.get('duration') else '-'} | "
                  f"{qa} | {'; '.join(r['notes']) or '-'} |")
    rows = [(r["style"], r["video"]) for r in results if r.get("video")]
    sheet = compare_sheet(rows, auto_dir / "compare.jpg") if rows else None
    if sheet:
        md += ["", f"Contact sheet: {sheet}"]
    (auto_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    save_json({"styles": results, "music": music, "title": title}, auto_dir / "report.json")
    print(auto_dir / "report.md")
    print("\n".join(md))


if __name__ == "__main__":
    main()
