#!/usr/bin/env python3
"""Generate a starting EDL from analysis.json. Claude should then refine it by hand.

Modes:
  jumpcut   talking-head / vlog: keep speech, drop silences, alternate punch-ins, subtitles
  montage   highlight / mixed cut: best-scoring shots, beat-synced to music if given
Usage:
  draft_edl.py WORK/analysis.json --mode montage --out WORK/edl.json
               [--format 9:16|16:9|1:1|4:5|source] [--duration 30] [--music song.mp3]
               [--title "..."] [--grade cinematic]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, save_json  # noqa: E402

FORMATS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}


def out_size(fmt: str, v0: dict):
    if fmt in FORMATS:
        return FORMATS[fmt]
    return (1080, 1920) if v0["height"] > v0["width"] else (1920, 1080)


FILLERS = {"um", "uh", "umm", "uhm", "uhh", "erm", "er", "ah", "hmm", "mm", "mhm",
           "嗯", "呃", "额", "唔", "啊", "嗯嗯"}


def is_filler(word: str) -> bool:
    w = word.strip().strip(".,!?;:，。！？、…").lower()
    return w in FILLERS


def speech_spans_without_fillers(words: list[dict], pad=0.08, gap=0.35) -> list[list[float]]:
    """Spans of consecutive real words; a filler word or a pause longer than `gap` splits a
    span, and the filler itself is left out."""
    spans, cur = [], None
    for w in words:
        if is_filler(w["word"]):
            if cur:
                spans.append(cur)
            cur = None
            continue
        if cur and w["start"] - cur[1] <= gap:
            cur[1] = w["end"]
        else:
            if cur:
                spans.append(cur)
            cur = [w["start"], w["end"]]
    if cur:
        spans.append(cur)
    return [[max(0.0, a - pad), b + pad] for a, b in spans]


def jumpcut(an: dict, pad=0.12, min_gap=0.3, min_len=0.45, drop_fillers=False):
    clips = []
    for v in an["videos"]:
        spans = v["audio"].get("speech_spans") or []
        if not spans and not v.get("transcript"):
            continue  # no speech: B-roll, leave it for Claude to place as cutaways
        if drop_fillers and v.get("transcript"):  # word level: cut "um"/"嗯" out of the line
            words = [w for s in v["transcript"]["segments"] for w in s.get("words", [])]
            spans = speech_spans_without_fillers(words)
            pad = 0.0
        elif v.get("transcript") and v["transcript"]["segments"]:  # prefer ASR timings
            spans = [[s["start"], s["end"]] for s in v["transcript"]["segments"]]
        merged = []
        for a, b in spans:
            a, b = max(0, a - pad), min(v["duration"], b + pad)
            if merged and a - merged[-1][1] < min_gap:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        for k, (a, b) in enumerate(m for m in merged if m[1] - m[0] >= min_len):
            c = {"src": v["id"], "in": round(a, 2), "out": round(b, 2)}
            if k % 3 == 1:
                c["effects"] = [{"type": "punch", "scale": 1.12}]
            clips.append(c)
    return clips


def montage(an: dict, target: float, beat: float | None):
    shots = []
    for v in an["videos"]:
        for s in v["scenes"]:
            if s["end"] - s["start"] >= 0.8:
                shots.append((s["score"], v["id"], s, v))
    shots.sort(key=lambda x: -x[0])
    seg_len = (beat * 2 if beat and beat * 2 >= 1.0 else beat * 4 if beat else 2.2)
    chosen, total = [], 0.0
    for score, vid, s, v in shots:
        if total >= target:
            break
        L = min(seg_len, s["end"] - s["start"])
        mid = (s["start"] + s["end"]) / 2
        a = max(s["start"], mid - L / 2)
        chosen.append((vid, s["start"], {"src": vid, "in": round(a, 2), "out": round(a + L, 2)}))
        total += L
    chosen.sort(key=lambda x: (x[0], x[1]))  # keep story order by source & time
    clips = [c for _, _, c in chosen]
    for k, c in enumerate(clips):
        if k and k % 4 == 0:
            c["transition"] = {"type": "fade", "duration": 0.3}
        if k % 5 == 2:
            c["effects"] = [{"type": "zoom_in", "amount": 0.1}]
    if clips:
        clips[0].setdefault("effects", []).append({"type": "fade_in", "duration": 0.4})
        clips[-1].setdefault("effects", []).append({"type": "fade_out", "duration": 0.8})
    return clips


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("analysis")
    ap.add_argument("--mode", choices=["jumpcut", "montage"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--format", default="source")
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--music", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--grade", default="natural")
    ap.add_argument("--drop-fillers", action="store_true",
                    help="jumpcut: cut filler words (um/uh/嗯/呃) out; analyse with --verbatim so they are transcribed")
    ap.add_argument("--voice", default="clean", help="speech clean-up preset for jumpcut: clean|denoise|broadcast|none")
    a = ap.parse_args()

    an = load_json(a.analysis)
    v0 = an["videos"][0]
    W, H = out_size(a.format, v0)
    out = Path(a.out).resolve()
    rel_an = Path(a.analysis).resolve()
    edl = {
        "analysis": str(rel_an),
        "output": {"path": str(out.parent / "output.mp4"), "width": W, "height": H, "fps": 30,
                   "fit": "blur" if (W < H) != (v0["width"] < v0["height"]) else "crop"},
        "grade": a.grade,
        "clips": [],
        "texts": [],
        "music": [],
    }
    beat = None
    if a.music:
        mus = next((m for m in an.get("music", []) if Path(m["path"]).name == Path(a.music).name), None)
        beat = mus["beat_interval"] if mus else None
        edl["music"].append({"path": str(Path(a.music).resolve()), "volume": 0.35 if a.mode == "jumpcut" else 0.9,
                             "duck": a.mode == "jumpcut", "fade_in": 0.5, "fade_out": 2.0})
    if a.mode == "jumpcut":
        edl["clips"] = jumpcut(an, drop_fillers=a.drop_fillers)
        if a.voice != "none":
            edl["audio"] = {"voice": a.voice}
        edl["subtitles"] = {"enabled": True, "style": "caption", "max_chars": 14 if H > W else 22}
    else:
        edl["clips"] = montage(an, a.duration, beat)
        if a.music:
            edl["beat_sync"] = {"music": 0, "every": 1}
            edl["audio"] = {"clip_volume": 0.25}
    if a.title:
        edl["texts"].append({"text": a.title, "start": 0.3, "end": 2.8, "style": "title"})
    save_json(edl, out)
    print(out)


if __name__ == "__main__":
    main()
