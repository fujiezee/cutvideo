#!/usr/bin/env python3
"""Who is in the footage: face detection (YuNet) + face embeddings (SFace), clustered into
people. Writes WORK/people.json and a sheet WORK/people.jpg (one face per person) so the
editor can name them; names survive a re-scan.

Usage:
  people.py WORK/analysis.json [--exclude 588-708]          scan and cluster
  people.py WORK/analysis.json --name P1=Thom P2="Celia:robot engineer"   set name[:role]

Models (OpenCV Zoo, ~38 MB) download once to ~/.cache/video-edit/models.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import fmt_ts, load_json, log, save_json  # noqa: E402

VERSION = 1
MODELS = {
    "yunet": ("face_detection_yunet_2023mar.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
              "face_detection_yunet_2023mar.onnx"),
    "sface": ("face_recognition_sface_2021dec.onnx",
              "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/"
              "face_recognition_sface_2021dec.onnx"),
}
SAME = 0.40      # cosine similarity for "same person" between cluster centres (SFace suggests 0.363)
SCAN_W = 960     # frames are scanned at this width


def model(name: str) -> str:
    d = Path.home() / ".cache" / "video-edit" / "models"
    d.mkdir(parents=True, exist_ok=True)
    fn, url = MODELS[name]
    p = d / fn
    if not p.exists() or p.stat().st_size < 1000:
        log(f"people: downloading {fn}")
        urllib.request.urlretrieve(url, p)
    return str(p)


class Faces:
    def __init__(self):
        import cv2
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(model("yunet"), "", (320, 320), 0.8, 0.3, 50)
        self.rec = cv2.FaceRecognizerSF.create(model("sface"), "")

    def __call__(self, bgr):
        """[(box_xywh_fraction, score, feature128)] for faces at least 4 % of the frame height."""
        h, w = bgr.shape[:2]
        s = SCAN_W / w
        img = self.cv2.resize(bgr, (SCAN_W, int(round(h * s))))
        self.det.setInputSize((img.shape[1], img.shape[0]))
        _, found = self.det.detect(img)
        out = []
        for f in (found if found is not None else []):
            x, y, fw, fh, score = f[0], f[1], f[2], f[3], f[14]
            if fh < 0.04 * img.shape[0]:
                continue
            crop = self.rec.alignCrop(img, f)
            feat = self.rec.feature(crop).flatten()
            feat = feat / (np.linalg.norm(feat) + 1e-9)
            out.append(([x / img.shape[1], y / img.shape[0], fw / img.shape[1], fh / img.shape[0]],
                        float(score), feat))
        return out


def _excluded(vid, t, exclude):
    return any((v in (None, vid)) and a <= t < b for v, a, b in exclude)


def scan(an: dict, exclude=()) -> list[dict]:
    """Face detections: 3 frames per shot (shots under 0.5 s skipped)."""
    import cv2
    fx = Faces()
    dets = []
    for v in an.get("videos", []):
        cap = cv2.VideoCapture(v["path"])
        log(f"people: scanning {len(v['scenes'])} shots of {v['id']}")
        for si, sc in enumerate(v["scenes"]):
            d = sc["end"] - sc["start"]
            if d < 0.5 or _excluded(v["id"], (sc["start"] + sc["end"]) / 2, exclude):
                continue
            for q in (0.25, 0.5, 0.75):
                t = sc["start"] + q * d
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                ok, fr = cap.read()
                if not ok:
                    continue
                for box, score, feat in fx(fr):
                    box = [round(float(b), 4) for b in box]
                    dets.append({"vid": v["id"], "shot": si, "t": round(float(t), 3), "box": box,
                                 "area": round(box[2] * box[3], 5), "score": round(float(score), 3), "feat": feat})
        cap.release()
    return dets


def cluster(dets: list[dict], same=SAME) -> list[list[int]]:
    """Average-linkage agglomerative clustering on cosine similarity of the embeddings."""
    if not dets:
        return []
    X = np.stack([d["feat"] for d in dets])
    groups = [[i] for i in range(len(dets))]
    cent = X.copy()
    while len(groups) > 1:
        C = cent / np.linalg.norm(cent, axis=1, keepdims=True)
        S = C @ C.T
        np.fill_diagonal(S, -1)
        i, j = np.unravel_index(np.argmax(S), S.shape)
        if S[i, j] < same:
            break
        i, j = min(i, j), max(i, j)
        groups[i] += groups[j]
        cent[i] = X[groups[i]].mean(axis=0)
        del groups[j]
        cent = np.delete(cent, j, axis=0)
    return groups


def name_candidates(an: dict) -> list[str]:
    """Capitalised words used mid-sentence at least twice (names people call each other)."""
    common = {"I", "I'm", "I'll", "I've", "OK", "Okay", "God", "Oh", "Hey", "Yes", "No", "Mr", "Mrs"}
    count: dict[str, int] = {}
    for v in an.get("videos", []):
        for s in (v.get("transcript") or {}).get("segments", []):
            words = re.findall(r"[A-Za-z][A-Za-z'\-]+", s["text"])
            for k, w in enumerate(words):
                if k and w[0].isupper() and w not in common and not w.isupper():
                    count[w] = count.get(w, 0) + 1
    return [w for w, n in sorted(count.items(), key=lambda x: -x[1]) if n >= 2][:12]


def build_people(an: dict, dets: list[dict], groups: list[list[int]], previous: dict | None,
                 min_shots: int = 2) -> list[dict]:
    durs = {(v["id"], i): sc["end"] - sc["start"] for v in an["videos"] for i, sc in enumerate(v["scenes"])}
    prev = [(p, np.array(p["centroid"])) for p in (previous or {}).get("people", []) if p.get("centroid")]
    people = []
    for g in groups:
        shots = {}
        for i in g:
            d = dets[i]
            k = (d["vid"], d["shot"])
            if k not in shots or d["area"] * d["score"] > shots[k]["area"] * shots[k]["score"]:
                shots[k] = d
        if len(shots) < min_shots:
            continue
        c = np.mean([dets[i]["feat"] for i in g], axis=0)
        c = c / np.linalg.norm(c)
        best = max(shots.values(), key=lambda d: d["area"] * d["score"])
        p = {"name": "", "role": "", "shots": len(shots),
             "screen_time": round(sum(durs[k] for k in shots), 1),
             "appearances": sorted(({k: d[k] for k in ("vid", "shot", "t", "box", "area")} for d in shots.values()),
                                   key=lambda a: (a["vid"], a["t"])),
             "best": {k: best[k] for k in ("vid", "shot", "t", "box", "area")},
             "centroid": [round(float(x), 5) for x in c]}
        if prev:  # keep the names given before
            q, sim = max(((q, float(qc @ c)) for q, qc in prev), key=lambda x: x[1])
            if sim >= 0.5:
                p["name"], p["role"] = q.get("name", ""), q.get("role", "")
        people.append(p)
    people.sort(key=lambda p: (-p["shots"], -p["screen_time"]))
    for k, p in enumerate(people):
        p["id"] = f"P{k + 1}"
    return people


def sheet(an: dict, people: list[dict], out: Path, tile=200):
    import cv2
    from PIL import Image, ImageDraw
    paths = {v["id"]: v["path"] for v in an["videos"]}
    cols = min(6, max(1, len(people)))
    rows = (len(people) + cols - 1) // cols
    img = Image.new("RGB", (cols * tile, rows * (tile + 34)), (18, 18, 18))
    d = ImageDraw.Draw(img)
    caps = {}
    for k, p in enumerate(people):
        b = p["best"]
        cap = caps.setdefault(b["vid"], cv2.VideoCapture(paths[b["vid"]]))
        cap.set(cv2.CAP_PROP_POS_MSEC, b["t"] * 1000)
        ok, fr = cap.read()
        if not ok:
            continue
        H, W = fr.shape[:2]
        x, y, w, h = b["box"]
        cx, cy, r = (x + w / 2) * W, (y + h / 2) * H, max(w * W, h * H) * 0.9
        crop = fr[int(max(0, cy - r)):int(min(H, cy + r)), int(max(0, cx - r)):int(min(W, cx + r))]
        face = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)).resize((tile, tile))
        X, Y = (k % cols) * tile, (k // cols) * (tile + 34)
        img.paste(face, (X, Y))
        label = f"{p['id']} {p['name']}".strip() + f"  {p['shots']} shots"
        d.text((X + 6, Y + tile + 4), label, fill=(255, 214, 10))
        d.text((X + 6, Y + tile + 18), f"best {b['vid']} {fmt_ts(b['t'])}", fill=(170, 170, 170))
    for c in caps.values():
        c.release()
    img.save(out, quality=88)


def load_people(work: Path) -> list[dict]:
    p = Path(work) / "people.json"
    return load_json(p).get("people", []) if p.exists() else []


def person_box(face_box, pad=1.6):
    """A person box (fractions of the source frame) around a face box: shoulders and chest."""
    x, y, w, h = face_box
    return [max(0.0, x - pad * w), max(0.0, y - 0.6 * h), min(1.0, x + w + pad * w), 1.0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("analysis")
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--name", nargs="*", default=None, help="P1=Name or P1=Name:role")
    ap.add_argument("--min-shots", type=int, default=2)
    a = ap.parse_args()
    an_path = Path(a.analysis).resolve()
    work = an_path.parent
    out = work / "people.json"
    if a.name is not None:
        data = load_json(out)
        by = {p["id"]: p for p in data["people"]}
        for item in a.name:
            pid, _, val = item.partition("=")
            name, _, role = val.partition(":")
            by[pid]["name"], by[pid]["role"] = name.strip(), role.strip()
        save_json(data, out)
        sheet(load_json(an_path), data["people"], work / "people.jpg")
        for p in data["people"]:
            print(f"{p['id']:4s} {p['name'] or '-':16s} {p['role'] or '':20s} {p['shots']} shots")
        return
    an = load_json(an_path)
    exclude = []
    for e in a.exclude:
        vid, _, rng = e.rpartition(":")
        lo, hi = rng.split("-")
        exclude.append((vid or None, float(lo), float(hi)))
    try:
        previous = load_json(out) if out.exists() else None
    except ValueError:
        previous = None
    dets = scan(an, exclude)
    groups = cluster(dets)
    people = build_people(an, dets, groups, previous, a.min_shots)
    data = {"version": VERSION, "people": people, "name_candidates": name_candidates(an),
            "faces": len(dets)}
    save_json(data, out)
    sheet(an, people, work / "people.jpg")
    print(out)
    print(f"{len(dets)} faces -> {len(people)} people (seen in >= {a.min_shots} shots)")
    for p in people:
        print(f"{p['id']:4s} {p['name'] or '-':16s} {p['shots']:3d} shots  {p['screen_time']:6.1f}s  "
              f"best {p['best']['vid']} {fmt_ts(p['best']['t'])}")
    if data["name_candidates"]:
        print("names heard in the dialogue:", ", ".join(data["name_candidates"]))


if __name__ == "__main__":
    main()
