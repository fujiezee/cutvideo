"""Shared helpers for the Tears of Steel example edits (run from the job folder)."""
import json
from pathlib import Path

ATTRIBUTION = "Tears of Steel (2012) · Blender Foundation · CC BY 3.0 · mango.blender.org"


def v(a, b, **extra):
    """A clip of the film (source id v1) from a to b seconds."""
    return {"src": "v1", "in": a, "out": b, **extra}


def end_card(duration=2.8, transition="fadeblack", **text_style):
    """Black end card with the CC BY attribution (the licence requires it)."""
    clip = {"type": "color", "color": "#000000", "duration": duration, "snap": False}
    if transition:
        clip["transition"] = {"type": transition, "duration": 0.3}
    text = {"text": ATTRIBUTION, "clip": -1, "start": 0.3, "end": duration, "style": "subtitle",
            "size": 0.026, "color": "#BBBBBB", "pos": [0.5, 0.5], "max_width": 0.9, **text_style}
    return clip, text


def check_shots(clips, analysis="work/analysis.json", tol=0.06, edge=0.4):
    """Warn when a film cut falls just inside a clip's start or end: a flash frame of the
    neighbouring shot. Cuts in the middle of a clip are the film's own and are fine."""
    scenes = json.load(open(analysis))["videos"][0]["scenes"]
    cuts = [s["start"] for s in scenes if s.get("hard_cut")]
    bad = 0
    for i, c in enumerate(clips):
        if c.get("type", "video") != "video":
            continue
        inside = [x for x in cuts if c["in"] + tol < x < c["out"] - tol
                  and (x - c["in"] < edge or c["out"] - x < edge)]
        if inside:
            bad += 1
            print(f"  clip {i}: {c['in']}-{c['out']} crosses a cut at {inside}")
    return bad


def write(edl, name):
    Path(name).write_text(json.dumps(edl, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {name}: {len(edl['clips'])} clips")
