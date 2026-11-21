#!/usr/bin/env python3
"""Audio QA for a rendered video: the checks a human would do by listening.

- loudness (integrated LUFS, loudness range) and true peak
- dialogue intelligibility: the output is transcribed again and every expected caption is
  compared with what the recogniser hears at that time; lines it cannot recover are likely
  masked by music/SFX or too quiet
- long unexpected silences

Usage: qa.py OUTPUT.mp4 [--subs .render/subtitles.json] [--model small] [--lang en]
Writes <render dir>/qa.json and prints a short report.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import FFMPEG, decode_audio_mono, fmt_ts, log, run, save_json  # noqa: E402


def _cjk(ch):
    o = ord(ch)
    return 0x3040 <= o <= 0x30FF or 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0xAC00 <= o <= 0xD7AF


_NUM = {str(i): w for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
_NUM.update({"一": "one", "二": "two", "三": "three", "四": "four", "五": "five", "六": "six", "七": "seven",
             "八": "eight", "九": "nine", "十": "ten", "两": "two"})


def tokens(text: str) -> list[str]:
    """Words for Latin scripts, single characters for CJK; lower case, no punctuation."""
    out, buf = [], ""
    for ch in text.lower():
        if _cjk(ch):
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        elif ch.isalnum() or ch == "'":
            buf += ch
        else:
            if buf:
                out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    # "4" / "four" / "四" are the same word for the comparison
    return [_NUM.get(t.strip("'"), t.strip("'")) for t in out if t.strip("'")]


def recall(expected: list[str], heard: list[str]) -> float:
    if not expected:
        return 1.0
    pool = list(heard)
    hit = 0
    for e in expected:
        best, bi = 0.0, -1
        for i, h in enumerate(pool):
            r = 1.0 if e == h else difflib.SequenceMatcher(None, e, h).ratio()
            if r > best:
                best, bi = r, i
        if best >= 0.8:
            hit += 1
            pool.pop(bi)
    return hit / len(expected)


def loudness(path: str) -> dict:
    p = run([FFMPEG, "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
            check=False)
    def last(pat):
        m = re.findall(pat, p.stderr)
        return float(m[-1]) if m else None
    return {"integrated_lufs": last(r"I:\s+(-?[0-9.]+) LUFS"), "lra": last(r"LRA:\s+(-?[0-9.]+) LU"),
            "true_peak_dbfs": last(r"Peak:\s+(-?[0-9.]+) dBFS")}


def silences(path: str, min_len=1.5) -> list[list[float]]:
    p = run([FFMPEG, "-hide_banner", "-nostats", "-i", path, "-vn", "-af", f"silencedetect=n=-50dB:d={min_len}",
             "-f", "null", "-"], check=False)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[0-9.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[0-9.]+)", p.stderr)]
    return [[round(a, 2), round(b, 2)] for a, b in zip(starts, ends + [None] * (len(starts) - len(ends))) if b]


def intelligibility(path: str, subs: list[dict], model_name: str, lang: str | None) -> list[dict]:
    from faster_whisper import WhisperModel
    audio = decode_audio_mono(path, 16000)
    model = WhisperModel(model_name, device="auto", compute_type="int8")
    # no VAD: speech under music must still be attempted
    segs, _ = model.transcribe(audio, language=lang, vad_filter=False, word_timestamps=True)
    words = [(w.start, w.end, w.word) for s in segs for w in (s.words or [])]
    res = []
    for c in subs:
        a, b = float(c["start"]) - 0.4, float(c["end"]) + 0.4
        heard = tokens(" ".join(w for s, e, w in words if e > a and s < b))
        lines = [ln for ln in str(c["text"]).split("\n") if ln.strip()]
        # bilingual captions: score against the line in the spoken language (best match)
        scores = [(recall(tokens(ln), heard), ln) for ln in lines] or [(1.0, "")]
        r, ln = max(scores)
        res.append({"start": round(c["start"], 2), "end": round(c["end"], 2), "text": ln, "recall": round(r, 2),
                    "heard": " ".join(heard)})
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--subs", default=None, help="captions JSON (default: .render/subtitles.json next to the EDL)")
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    report = qa(a.video, a.subs, a.model, a.lang, a.out)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=1))


def qa(video, subs_path=None, model="small", lang=None, out=None) -> dict:
    video = str(Path(video).resolve())
    subs = []
    if subs_path and Path(subs_path).exists():
        subs = json.loads(Path(subs_path).read_text(encoding="utf-8"))
    log("qa: loudness / peaks / silences")
    loud = loudness(video)
    sil = silences(video)
    lines = []
    if subs:
        log(f"qa: re-transcribing the output to check {len(subs)} caption(s)")
        try:
            lines = intelligibility(video, subs, model, lang)
        except ImportError:
            log("qa: faster-whisper not installed, skipping intelligibility")
    unclear = [x for x in lines if x["recall"] < 0.6]
    issues = []
    if loud["integrated_lufs"] is not None and abs(loud["integrated_lufs"] + 14) > 2:
        issues.append(f"loudness {loud['integrated_lufs']} LUFS (target about -14)")
    if loud["true_peak_dbfs"] is not None and loud["true_peak_dbfs"] > -1.0:
        issues.append(f"true peak {loud['true_peak_dbfs']} dBFS (keep at or below -1)")
    for x in unclear:
        issues.append(f"{fmt_ts(x['start'])} hard to hear: \"{x['text']}\" (recognised {x['recall']:.0%})")
    for s0, s1 in sil:
        issues.append(f"{fmt_ts(s0)}-{fmt_ts(s1)} silence ({s1 - s0:.1f} s): intended?")
    summary = {"video": Path(video).name, **loud,
               "captions_checked": len(lines),
               "captions_clear": sum(1 for x in lines if x["recall"] >= 0.6),
               "issues": issues}
    report = {"summary": summary, "captions": lines, "silences": sil}
    if not out:
        out = str(Path(subs_path).parent / "qa.json") if subs_path else str(Path(video).with_suffix(".qa.json"))
    save_json(report, out)
    return report


if __name__ == "__main__":
    main()
