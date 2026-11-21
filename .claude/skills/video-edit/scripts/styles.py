#!/usr/bin/env python3
"""Which edits can this footage carry? A style menu from the analysis.

Reads analysis.json (and adds a per-shot face count, cached in faces.json), derives footage
features (dialogue density, quotable lines, action, faces, close-ups, static portraits, wide
establishing shots, dark/neon shots, music-like passages) and scores every style in the
catalogue with reasons, the material that supports it, a suggested format/length and the
gaps. Claude reviews the contact sheets and refines the menu; this is the evidence base.

Usage: styles.py WORK/analysis.json [--out WORK/styles.md]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import fmt_ts, load_json, log, save_json  # noqa: E402


# ------------------------------------------------------------------ features


def face_counts(video: dict, cache: Path) -> dict:
    """Per shot: number of faces and largest face area (fraction of frame), middle frame."""
    import cv2
    data = load_json(cache) if cache.exists() else {}
    key = video["path"]
    if key in data and len(data[key]) == len(video["scenes"]):
        return data[key]
    log(f"faces: scanning {len(video['scenes'])} shots")
    from intro import _largest_face  # noqa: F401  (same cascades)
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    prof = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
    cap = cv2.VideoCapture(video["path"])
    out = []
    for sc in video["scenes"]:
        cap.set(cv2.CAP_PROP_POS_MSEC, (sc["start"] + sc["end"]) / 2 * 1000)
        ok, fr = cap.read()
        if not ok:
            out.append({"n": 0, "area": 0.0})
            continue
        small = cv2.resize(fr, (640, int(fr.shape[0] * 640 / fr.shape[1])))
        g = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        mn = (g.shape[0] // 10,) * 2
        faces = list(casc.detectMultiScale(g, 1.1, 5, minSize=mn)) + list(prof.detectMultiScale(g, 1.1, 6, minSize=mn))
        # merge overlapping detections (frontal + profile on the same face)
        kept = []
        for (x, y, w, h) in sorted(faces, key=lambda f: -f[2] * f[3]):
            if all(abs((x + w / 2) - (kx + kw / 2)) > kw / 2 for kx, ky, kw, kh in kept):
                kept.append((x, y, w, h))
        area = max((w * h for x, y, w, h in kept), default=0) / (g.shape[0] * g.shape[1])
        out.append({"n": len(kept), "area": round(float(area), 4)})
    cap.release()
    data[key] = out
    save_json(data, cache)
    return out


def _excluded(vid, t, exclude):
    return any((v in (None, vid)) and a <= t < b for v, a, b in exclude)


def features(an: dict, work: Path, exclude=()) -> dict:
    vids = an.get("videos", [])
    shots, lines, speech = [], [], 0.0
    total = 0.0
    for v in vids:
        fc = face_counts(v, work / "faces.json")
        for sc, f in zip(v["scenes"], fc):
            if _excluded(v["id"], (sc["start"] + sc["end"]) / 2, exclude):
                continue
            shots.append({**sc, "vid": v["id"], "faces": f["n"], "face_area": f["area"],
                          "dur": sc["end"] - sc["start"]})
        total += v["duration"]
        speech += sum(b - a for a, b in v.get("audio", {}).get("speech_spans", []))
        for s in (v.get("transcript") or {}).get("segments", []):
            if _excluded(v["id"], s["start"], exclude):
                continue
            n = len(s["text"].split()) if s["text"].isascii() else len(s["text"]) // 2
            if 2 <= n <= 14 and s["end"] - s["start"] < 8:
                lines.append({"vid": v["id"], "start": s["start"], "end": s["end"], "text": s["text"]})
    speech_secs = {round(l["start"]) for l in lines}
    # spoken share from the transcript (energy-based speech spans also count music and effects)
    said = sum(s_["end"] - s_["start"] for v in vids for s_ in (v.get("transcript") or {}).get("segments", [])
               if s_["end"] - s_["start"] < 15)
    has_tr = any(v.get("transcript") for v in vids)
    music_like = sum(sc["dur"] for sc in shots if sc.get("loudness_db", -90) > -22 and sc["faces"] == 0
                     and round(sc["start"]) not in speech_secs)
    n = max(len(shots), 1)
    feat = {
        "duration": total, "shots": len(shots), "avg_shot": total / n,
        "speech_ratio": min(1.0, (said if has_tr else 0.5 * speech) / max(total, 1)),
        "lines": lines,
        "action": [s for s in shots if s.get("motion", 0) > 0.11 and s["dur"] >= 0.8],
        "faces_any": [s for s in shots if s["faces"] >= 1],
        "closeups": [s for s in shots if s["faces"] == 1 and s["face_area"] > 0.035],
        "two_shots": [s for s in shots if s["faces"] >= 2],
        "portraits_static": [s for s in shots if s["faces"] == 1 and 0.01 < s["face_area"] < 0.2
                             and s.get("motion", 1) < 0.06 and s["dur"] >= 2.0],
        "establishing": [s for s in shots if s["faces"] == 0 and s.get("motion", 1) < 0.05
                         and s["dur"] >= 2.5 and s.get("brightness", 0) > 0.25],
        "dark_neon": [s for s in shots if s.get("brightness", 1) < 0.42 and s.get("colorfulness", 0) > 0.38],
        "bright_day": [s for s in shots if s.get("brightness", 0) > 0.42],
        "music_like": music_like,
        "vertical": bool(vids) and vids[0]["height"] > vids[0]["width"],
        "credits_tail": total > 300,  # long films usually end with credits/score
    }
    return feat


# ------------------------------------------------------------------ catalogue


def _pick(lst, k=5, key="score"):
    return sorted(lst, key=lambda s: -s.get(key, 0))[:k]


def _shots(lst):
    return ", ".join(f"{s['vid']} {fmt_ts(s['start'])}" for s in sorted(lst, key=lambda s: s["start"]))


def _clip(x, lo, hi):
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def catalogue(f: dict) -> list[dict]:
    L, A, C = len(f["lines"]), len(f["action"]), len(f["closeups"])
    S = []

    def add(key, name, score, why, material, fmt, gaps):
        S.append({"key": key, "name": name, "score": int(round(100 * max(0, min(1, score)))), "why": why,
                  "material": material, "format": fmt, "gaps": gaps})

    music_ok = f["music_like"] >= 20
    music_gap = [] if music_ok else ["no clear music passage: needs a music track from the user"]

    add("trailer", "Trailer 预告片",
        0.35 * _clip(L, 4, 25) + 0.35 * _clip(A, 10, 50) + 0.2 * (1 if music_ok else 0.3) + 0.1 * _clip(f["duration"], 180, 900),
        [f"{L} quotable lines", f"{A} action shots", f"{f['music_like']:.0f}s of music-like audio"],
        "lines: " + "; ".join(f"\"{l['text'][:40]}\" ({fmt_ts(l['start'])})" for l in f["lines"][:4]),
        "16:9 (+9:16), 45-75 s", (["few spoken lines: story must be told with cards"] if L < 4 else []) + music_gap)

    add("beatmix", "Beat-sync mix 卡点混剪",
        0.6 * _clip(A, 15, 60) + 0.25 * (1 if music_ok else 0.3) + 0.15 * _clip(f["shots"], 30, 150),
        [f"{A} action shots", f"{f['shots']} shots in total"], "action: " + _shots(_pick(f["action"], 6)),
        "9:16, 15-30 s", music_gap + (["not enough action for one shot per beat"] if A < 10 else []))

    add("cast", "Cast intros 人物介绍",
        0.7 * _clip(C, 4, 20) + 0.3 * _clip(len(f["faces_any"]), 10, 60),
        [f"{C} single-person close/medium shots", f"{len(f['faces_any'])} shots with faces"],
        "freeze candidates: " + _shots(_pick(f["closeups"], 6, "face_area")),
        "16:9 or 9:16, 5-8 s per person", ["identify who is who from the sheets / transcript"])

    best_window, best_at = 0, None
    ls = sorted(f["lines"], key=lambda l: l["start"])
    for i, l in enumerate(ls):
        n_ = sum(1 for m in ls[i:] if m["vid"] == l["vid"] and m["start"] - l["start"] <= 60)
        if n_ > best_window:
            best_window, best_at = n_, l
    add("karaoke", "Word-highlight dialogue short 逐字字幕短片",
        0.7 * _clip(best_window, 3, 12) + 0.3 * _clip(C, 2, 10),
        [f"up to {best_window} lines within one minute", f"{C} close-ups for the speakers"],
        (f"densest dialogue minute starts {best_at['vid']} {fmt_ts(best_at['start'])}: \"{best_at['text'][:40]}\""
         if best_at else ""), "9:16, 20-45 s", ["needs a transcript"] if not L else [])

    add("talking", "Talking-head jump cut 口播精剪",
        0.5 * _clip(f["speech_ratio"], 0.3, 0.8) + 0.3 * _clip(f["avg_shot"], 6, 30)
        + 0.2 * _clip(len(f["closeups"]) / max(f["shots"], 1), 0.2, 0.6),
        [f"speech {f['speech_ratio']:.0%} of the runtime", f"average shot {f['avg_shot']:.1f}s"],
        "long takes with one speaker", "source aspect / 9:16, all content tightened",
        [] if f["speech_ratio"] > 0.4 else ["footage is not mostly one person talking"])

    add("vhs", "Retro VHS 复古录像带",
        0.45 * _clip(len(f["faces_any"]), 10, 60) + 0.35 * _clip(len(f["bright_day"]), 10, 60) + 0.2 * _clip(L, 2, 10),
        [f"{len(f['faces_any'])} people shots", f"{len(f['bright_day'])} daylight shots"],
        "everyday / people moments: " + _shots(_pick([s for s in f["bright_day"] if s["faces"]], 5)),
        "4:3, 30-45 s", [])

    add("silent", "Black-and-white silent film 黑白默片",
        0.4 * _clip(C, 4, 25) + 0.3 * _clip(A, 4, 30) + 0.3 * _clip(L, 3, 15),
        [f"{C} expressive close-ups", f"{A} action shots", f"{L} lines to turn into intertitles"],
        "close-ups: " + _shots(_pick(f["closeups"], 4, "face_area")) + "; action: " + _shots(_pick(f["action"], 4)),
        "4:3, 40-60 s", [])

    add("cyberpunk", "Cyberpunk glitch 赛博故障",
        0.6 * _clip(len(f["dark_neon"]), 6, 40) + 0.4 * _clip(A, 5, 30),
        [f"{len(f['dark_neon'])} dark, colourful shots", f"{A} action shots"],
        "neon/dark: " + _shots(_pick(f["dark_neon"], 6, "colorfulness")), "16:9, 25-40 s",
        ["footage is mostly bright/natural: the look will feel forced"] if len(f["dark_neon"]) < 4 else [])

    add("text_behind", "Text behind person 人物遮挡文字",
        _clip(len(f["portraits_static"]), 1, 10),
        [f"{len(f['portraits_static'])} static single-person shots (stable for per-frame cut-outs)"],
        "candidates: " + _shots(_pick(f["portraits_static"], 5, "face_area")), "any, 3-6 s per title",
        [] if f["portraits_static"] else ["no static single-person shots: the text would float"])

    add("title3d", "3D title opener 立体字片头",
        0.7 * _clip(len(f["establishing"]), 1, 10) + 0.3 * _clip(A, 0, 10),
        [f"{len(f['establishing'])} calm wide shots without people (title backgrounds)"],
        "backgrounds: " + _shots(_pick(f["establishing"], 5, "sharpness")), "16:9, 6-12 s",
        [] if f["establishing"] else ["no calm wide shots: put the title on black or a blurred shot"])

    add("fx3d", "3D particles / hologram overlay 粒子全息特效",
        0.6 * _clip(len(f["dark_neon"]) + len([s for s in f["establishing"] if s.get("brightness", 1) < 0.45]), 6, 45)
        + 0.4 * _clip(A, 2, 10),
        [f"{len(f['dark_neon'])} dark/colourful shots where light effects read well"],
        "dark shots: " + _shots(_pick(f["dark_neon"], 5)), "16:9, accents of 2-5 s", [])

    add("recap", "One-minute recap 一分钟看完",
        0.2 * _clip(f["duration"], 300, 1200) + 0.2 * _clip(L, 5, 20),
        ["needs a narrated voice-over over the story"], "", "16:9 or 9:16, 60-90 s",
        ["no voice-over (TTS) feature yet: possible with captions-only narration"])
    return sorted(S, key=lambda s: -s["score"])


def report(an: dict, f: dict, styles: list[dict]) -> str:
    md = ["# Style menu", "",
          f"Footage: {fmt_ts(f['duration'])}, {f['shots']} shots (avg {f['avg_shot']:.1f}s), speech "
          f"{f['speech_ratio']:.0%}, {len(f['lines'])} quotable lines, {len(f['action'])} action shots, "
          f"{len(f['closeups'])} close-ups, {len(f['two_shots'])} two-shots, {len(f['establishing'])} calm wide "
          f"shots, {len(f['dark_neon'])} dark/neon shots, {f['music_like']:.0f}s music-like audio.", ""]
    for band, lo, hi in (("Recommended", 60, 101), ("Possible", 40, 60), ("Not recommended", 0, 40)):
        group = [s for s in styles if lo <= s["score"] < hi]
        if not group:
            continue
        md += [f"## {band}", "", "| Score | Style | Why | Format | Gaps |", "|---|---|---|---|---|"]
        for s in group:
            md.append(f"| {s['score']} | {s['name']} | {'; '.join(s['why'])} | {s['format']} | "
                      f"{'; '.join(s['gaps']) or '-'} |")
        md.append("")
        for s in group:
            if s["material"]:
                md.append(f"- **{s['name']}**: {s['material']}")
        md.append("")
    return "\n".join(md)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("analysis")
    ap.add_argument("--out", default=None)
    ap.add_argument("--exclude", nargs="*", default=[],
                    help="time ranges to ignore (credits, slates, test footage): A-B or v2:A-B, seconds")
    a = ap.parse_args()
    exclude = []
    for e in a.exclude:
        vid, _, rng = e.rpartition(":")
        lo, hi = rng.split("-")
        exclude.append((vid or None, float(lo), float(hi)))
    ap_ = Path(a.analysis).resolve()
    an = load_json(ap_)
    f = features(an, ap_.parent, exclude)
    styles = catalogue(f)
    out = Path(a.out) if a.out else ap_.parent / "styles.md"
    out.write_text(report(an, f, styles), encoding="utf-8")
    save_json({"styles": styles}, out.with_suffix(".json"))
    print(out)
    for s in styles:
        print(f"{s['score']:3d}  {s['name']}")


if __name__ == "__main__":
    main()
