#!/usr/bin/env python3
"""Analyze source footage so Claude can "watch" it.

Produces in --out:
  analysis.json        machine-readable: metadata, scenes, per-scene scores, silences,
                       speech spans, transcript (with word timings), music beats
  summary.md           compact human/LLM-readable digest (read this first)
  sheets/<id>_NN.jpg   contact sheets: labelled frames per scene (open with Read)

Usage:
  analyze.py VIDEO [VIDEO ...] --out WORK [--music SONG.mp3] [--no-transcribe]
             [--model small] [--lang zh] [--scene-threshold 0.3]
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import FFMPEG, decode_audio_mono, fmt_ts, log, probe, run, save_json  # noqa: E402

# ---------------------------------------------------------------- scenes


def detect_scenes(path: str, duration: float, threshold: float, min_len: float = 0.6) -> list[dict]:
    p = run(
        [FFMPEG, "-hide_banner", "-nostats", "-i", path, "-an", "-sn",
         "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        check=False,
    )
    cuts = [float(m.group(1)) for m in re.finditer(r"pts_time:([0-9.]+)", p.stderr)]
    bounds = [0.0]
    for c in cuts:
        if c - bounds[-1] >= min_len and duration - c >= min_len * 0.5:
            bounds.append(c)
    bounds.append(duration)
    # split very long shots into ~8 s chunks so long takes still get several entries
    out = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        n = max(1, int((b - a) // 8))
        step = (b - a) / n
        for i in range(n):
            out.append({"start": round(a + i * step, 3), "end": round(a + (i + 1) * step, 3),
                        "hard_cut": i == 0 and a > 0})
    return out


def frame_stats(path: str, scenes: list[dict], fps: float):
    """Per-scene motion, brightness, sharpness, colourfulness from sampled frames."""
    import cv2

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return
    for sc in scenes:
        dur = sc["end"] - sc["start"]
        n = int(min(8, max(3, dur * 2)))
        ts = [sc["start"] + dur * (i + 0.5) / n for i in range(n)]
        prev, motion, bright, sharp, colour = None, [], [], [], []
        for t in ts:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            if not ok:
                continue
            small = cv2.resize(fr, (160, int(160 * fr.shape[0] / fr.shape[1])))
            g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype("float32")
            bright.append(g.mean() / 255)
            sharp.append(cv2.Laplacian(g, cv2.CV_32F).var())
            b, gg, r = [small[..., i].astype("float32") for i in range(3)]
            rg, yb = np.abs(r - gg), np.abs(0.5 * (r + gg) - b)
            colour.append((np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)) / 100)
            if prev is not None:
                motion.append(float(np.abs(g - prev).mean() / 255))
            prev = g
        sc["motion"] = round(float(np.mean(motion)) if motion else 0.0, 4)
        sc["brightness"] = round(float(np.mean(bright)) if bright else 0.0, 3)
        sc["sharpness"] = round(float(np.log1p(np.mean(sharp))) if sharp else 0.0, 2)
        sc["colorfulness"] = round(float(np.mean(colour)) if colour else 0.0, 3)
    cap.release()


# ---------------------------------------------------------------- audio


def audio_profile(path: str, scenes: list[dict]):
    sr = 16000
    y = decode_audio_mono(path, sr)
    if y.size == 0:
        return {"silences": [], "speech_spans": [], "rms_db_10hz": []}
    hop = sr // 10
    n = len(y) // hop
    rms = np.sqrt(np.mean(y[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    floor = np.percentile(db, 10)
    loud = np.percentile(db, 90)
    thr = max(floor + 0.35 * (loud - floor), -45.0) if loud - floor > 8 else floor - 1
    voiced = db > thr
    # silence spans >= 0.4 s
    silences, speech, i = [], [], 0
    while i < n:
        j = i
        while j < n and voiced[j] == voiced[i]:
            j += 1
        a, b = i / 10, j / 10
        if not voiced[i] and b - a >= 0.4:
            silences.append([round(a, 2), round(b, 2)])
        elif voiced[i]:
            speech.append([round(a, 2), round(b, 2)])
        i = j
    # merge speech spans separated by < 0.4 s
    merged = []
    for s in speech:
        if merged and s[0] - merged[-1][1] < 0.4:
            merged[-1][1] = s[1]
        else:
            merged.append(list(s))
    for sc in scenes:
        a, b = int(sc["start"] * 10), max(int(sc["end"] * 10), int(sc["start"] * 10) + 1)
        seg = db[a:b]
        sc["loudness_db"] = round(float(seg.mean()), 1) if seg.size else -90.0
    return {"silences": silences, "speech_spans": merged, "silence_threshold_db": round(float(thr), 1),
            "rms_db_10hz": [round(float(v), 1) for v in db[:: 5]]}  # 2 Hz curve to keep JSON small


def music_beats(path: str) -> dict:
    """Tempo + beat grid + energy sections using spectral flux (no librosa needed)."""
    sr, nfft, hop = 22050, 1024, 256
    y = decode_audio_mono(path, sr)
    if y.size < sr:
        return {}
    win = np.hanning(nfft).astype("float32")
    frames = 1 + (len(y) - nfft) // hop
    idx = np.arange(nfft)[None, :] + hop * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(y[idx] * win, axis=1))
    logspec = np.log1p(100 * spec)
    flux = np.maximum(0, np.diff(logspec, axis=0)).sum(axis=1)
    flux = np.concatenate([[0], flux])
    flux = (flux - flux.mean()) / (flux.std() + 1e-9)
    fr = sr / hop
    # tempo via autocorrelation in 70..180 BPM
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    lags = np.arange(len(ac))
    bpm_lags = (lags >= fr * 60 / 180) & (lags <= fr * 60 / 70)
    weights = np.exp(-0.5 * (np.log2((60 * fr / np.maximum(lags, 1)) / 120)) ** 2)  # prefer ~120
    score = np.where(bpm_lags, ac * weights, -np.inf)
    lag = int(np.argmax(score))
    if 1 <= lag < len(ac) - 1:  # parabolic refinement -> fractional lag
        y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
        den = y0 - 2 * y1 + y2
        lagf = lag + (0.5 * (y0 - y2) / den if den else 0.0)
    else:
        lagf = float(lag)
    bpm = 60 * fr / lagf
    # phase: offset maximising onset strength on the (fractional) grid
    nb = int((len(flux) - 1) // lagf)
    best, phase = -1e9, 0.0
    for off in np.arange(0, lagf, 0.5):
        pos = np.round(off + lagf * np.arange(nb)).astype(int)
        pos = pos[pos < len(flux)]
        sc = flux[pos].sum()
        if sc > best:
            best, phase = sc, off
    beats, rad = [], max(1, int(lagf // 10))
    k = 0
    while phase + k * lagf < len(flux):
        c = int(round(phase + k * lagf))
        a, b = max(0, c - rad), min(len(flux), c + rad + 1)
        # an onset raises flux most when it sits ~3/4 into the analysis window
        beats.append((a + int(np.argmax(flux[a:b]))) / fr + 0.75 * nfft / sr)
        k += 1
    # energy per 1 s and sections where energy jumps (drops)
    rms = np.sqrt(np.convolve(y ** 2, np.ones(sr) / sr, mode="valid")[::sr] + 1e-12)
    edb = 20 * np.log10(rms + 1e-9)
    drops = [i for i in range(2, len(edb)) if edb[i] - edb[i - 2: i].mean() > 6]
    strong = []
    if beats:
        bstr = np.array([flux[min(len(flux) - 1, int(b * fr))] for b in beats])
        strong = [round(beats[i], 3) for i in range(len(beats)) if bstr[i] > np.percentile(bstr, 75)]
    return {
        "path": str(Path(path).resolve()),
        "duration": round(len(y) / sr, 2),
        "bpm": round(float(bpm), 1),
        "beat_interval": round(60 / bpm, 4),
        "beats": [round(b, 3) for b in beats],
        "strong_beats": strong,
        "energy_db_1hz": [round(float(v), 1) for v in edb],
        "drops": drops,
    }


# ---------------------------------------------------------------- transcript


VERBATIM = {"en": "Umm, so, uh, I mean, like, you know, hmm... Okay, uh, let's go.",
            "zh": "嗯，那个，就是说，呃，我们今天，嗯，来聊一下。"}


def transcribe(path: str, model_name: str, lang: str | None, verbatim: bool = False) -> dict | None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("faster-whisper not installed; skipping transcript (pip install faster-whisper)")
        return None
    log(f"transcribing with faster-whisper/{model_name} (first run downloads the model)...")
    model = WhisperModel(model_name, device="auto", compute_type="int8")
    audio = decode_audio_mono(path, 16000)  # avoid PyAV version issues inside faster-whisper
    prompt = "以下是普通话的句子，使用简体中文。" if lang in ("zh", None) else None
    if verbatim:  # Whisper drops "um"/"嗯" unless the prompt shows them; needed to cut them out
        prompt = VERBATIM.get(lang or "en", VERBATIM["en"]) if lang != "zh" else prompt + VERBATIM["zh"]
    segs, info = model.transcribe(audio, language=lang, vad_filter=True, word_timestamps=True,
                                  initial_prompt=prompt)
    out = []
    for s in segs:
        out.append({
            "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
            "words": [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word} for w in (s.words or [])],
        })
    return {"language": info.language, "segments": out}


# ---------------------------------------------------------------- contact sheets


def contact_sheets(path: str, vid: str, scenes: list[dict], out_dir: Path, cols: int = 4, rows: int = 4) -> list[str]:
    import cv2
    from PIL import Image, ImageDraw, ImageFont

    from common import find_font

    cap = cv2.VideoCapture(path)
    tiles = []
    for si, sc in enumerate(scenes):
        dur = sc["end"] - sc["start"]
        k = 1 if dur < 3 else 2 if dur < 7 else 3
        for j in range(k):
            t = sc["start"] + dur * (j + 0.5) / k
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, fr = cap.read()
            if ok:
                tiles.append((si, t, fr))
    cap.release()
    if not tiles:
        return []
    tw = 384
    font_path = find_font()
    from common import load_font
    font = load_font(font_path, 20)
    paths = []
    per = cols * rows
    for pi in range(0, len(tiles), per):
        chunk = tiles[pi: pi + per]
        h0 = chunk[0][2].shape[0] * tw // chunk[0][2].shape[1]
        th = min(h0, 520)
        sheet = Image.new("RGB", (cols * tw, math.ceil(len(chunk) / cols) * th), (16, 16, 16))
        d = ImageDraw.Draw(sheet)
        for i, (si, t, fr) in enumerate(chunk):
            im = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            im.thumbnail((tw, th))
            x, y = (i % cols) * tw, (i // cols) * th
            sheet.paste(im, (x + (tw - im.width) // 2, y + (th - im.height) // 2))
            label = f"{vid} s{si} {fmt_ts(t)}"
            d.rectangle([x, y, x + 8 + d.textlength(label, font=font), y + 26], fill=(0, 0, 0))
            d.text((x + 4, y + 2), label, fill=(255, 220, 0), font=font)
        p = out_dir / f"{vid}_{pi // per:02d}.jpg"
        sheet.save(p, quality=82)
        paths.append(str(p))
    return paths


# ---------------------------------------------------------------- main


def scene_score(sc: dict) -> float:
    """Heuristic 'interestingness' 0..1: motion + colour + sharpness, penalise dark/blurred."""
    m = min(sc.get("motion", 0) / 0.08, 1)
    c = min(sc.get("colorfulness", 0) / 0.6, 1)
    s = min(max(sc.get("sharpness", 0) - 3, 0) / 4, 1)
    b = sc.get("brightness", 0.5)
    pen = 0.5 if b < 0.12 or b > 0.95 else 1.0
    return round(pen * (0.45 * m + 0.25 * c + 0.30 * s), 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--music", nargs="*", default=[])
    ap.add_argument("--no-transcribe", action="store_true")
    ap.add_argument("--model", default="small", help="faster-whisper model: tiny/base/small/medium/large-v3")
    ap.add_argument("--lang", default=None, help="force language code, e.g. zh / en")
    ap.add_argument("--scene-threshold", type=float, default=0.3)
    ap.add_argument("--verbatim", action="store_true",
                    help="transcribe filler words (um/uh/嗯/呃) too, so draft_edl --drop-fillers can cut them")
    args = ap.parse_args()

    out = Path(args.out)
    (out / "sheets").mkdir(parents=True, exist_ok=True)
    result = {"videos": [], "music": []}
    md = ["# Footage analysis", ""]

    for i, v in enumerate(args.videos):
        vid = f"v{i + 1}"
        log(f"[{vid}] probing {v}")
        info = probe(v)
        info["id"] = vid
        if not info.get("has_video"):
            log(f"[{vid}] no video stream, skipping")
            continue
        log(f"[{vid}] scenes...")
        scenes = detect_scenes(v, info["duration"], args.scene_threshold)
        log(f"[{vid}] {len(scenes)} shots; frame stats...")
        frame_stats(v, scenes, info["fps"])
        aud = audio_profile(v, scenes) if info.get("has_audio") else {"silences": [], "speech_spans": []}
        for sc in scenes:
            sc["score"] = scene_score(sc)
        tr = None
        speech_total = sum(b - a for a, b in aud.get("speech_spans", []))
        if info.get("has_audio") and not args.no_transcribe and speech_total > 2:
            try:
                tr = transcribe(v, args.model, args.lang, args.verbatim)
            except Exception as e:  # keep going without transcript
                log(f"[{vid}] transcription failed: {e}")
        log(f"[{vid}] contact sheets...")
        sheets = contact_sheets(v, vid, scenes, out / "sheets")
        entry = {**info, "scenes": scenes, "audio": aud, "transcript": tr, "sheets": sheets}
        result["videos"].append(entry)

        orient = "vertical" if info["height"] > info["width"] else "horizontal"
        md += [f"## {vid}: {Path(v).name}",
               f"- {info['width']}x{info['height']} ({orient}), {info['fps']} fps, {fmt_ts(info['duration'])}, "
               f"audio: {'yes' if info.get('has_audio') else 'no'}, speech ≈ {speech_total:.0f}s",
               f"- sheets: {', '.join(Path(s).name for s in sheets)}", "",
               "| shot | start | end | dur | motion | bright | sharp | color | loud dB | score |",
               "|---|---|---|---|---|---|---|---|---|---|"]
        for si, sc in enumerate(scenes):
            md.append(f"| s{si} | {sc['start']:.2f} | {sc['end']:.2f} | {sc['end'] - sc['start']:.1f} | "
                      f"{sc.get('motion', 0):.3f} | {sc.get('brightness', 0):.2f} | {sc.get('sharpness', 0):.1f} | "
                      f"{sc.get('colorfulness', 0):.2f} | {sc.get('loudness_db', -90):.0f} | {sc['score']:.2f} |")
        if aud.get("silences"):
            md += ["", f"Silences ≥0.4s ({len(aud['silences'])}): " +
                   ", ".join(f"{a:.1f}-{b:.1f}" for a, b in aud["silences"][:60])]
        if tr and tr["segments"]:
            md += ["", f"### Transcript ({tr['language']})"]
            md += [f"- [{s['start']:.2f}-{s['end']:.2f}] {s['text']}" for s in tr["segments"]]
        md.append("")

    for m in args.music:
        log(f"music: {m}")
        mb = music_beats(m)
        if mb:
            result["music"].append(mb)
            md += [f"## Music: {Path(m).name}",
                   f"- {fmt_ts(mb['duration'])}, ≈{mb['bpm']} BPM (beat every {mb['beat_interval']:.3f}s), "
                   f"{len(mb['beats'])} beats, energy jumps at: {mb['drops'][:20]} s",
                   f"- first beats: {mb['beats'][:16]}", ""]

    save_json(result, out / "analysis.json")
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    print(out / "summary.md")


if __name__ == "__main__":
    main()
