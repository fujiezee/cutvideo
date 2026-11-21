#!/usr/bin/env python3
"""Voice-over: text to speech, offline.

Backends: macOS `say` (built-in voices, e.g. Tingting for Mandarin, Samantha for English),
else `espeak-ng` / `espeak` on Linux. Lines are cached by text + voice + rate.

Usage: tts.py "text" --out line.wav [--voice Tingting] [--rate 190]
       tts.py --voices            list voices of the available backend
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ffmpeg, log, probe  # noqa: E402

DEFAULT_VOICE = {"say": {"zh": "Tingting", "en": "Samantha"}, "espeak": {"zh": "cmn", "en": "en-us"}}


def backend() -> str | None:
    if shutil.which("say"):
        return "say"
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return "espeak"
    return None


def lang_of(text: str) -> str:
    cjk = sum(1 for ch in text if "㐀" <= ch <= "鿿")
    return "zh" if cjk >= max(1, len(text.replace(" ", "")) * 0.3) else "en"


def voices() -> list[str]:
    b = backend()
    if b == "say":
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
        return [ln for ln in out.splitlines() if re.search(r"\b(zh_|en_)", ln)]
    if b == "espeak":
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        return subprocess.run([exe, "--voices"], capture_output=True, text=True).stdout.splitlines()
    return []


def synth(text: str, out_dir: Path, voice: str | None = None, rate: int | None = None) -> tuple[Path, float]:
    """Speak `text` to a 48 kHz mono wav in out_dir; returns (path, seconds)."""
    b = backend()
    if not b:
        raise SystemExit("no text-to-speech backend: macOS `say` or espeak-ng is needed")
    voice = voice or DEFAULT_VOICE[b][lang_of(text)]
    key = hashlib.sha1(f"{b}|{voice}|{rate}|{text}".encode()).hexdigest()[:14]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / f"vo_{key}.wav"
    if not wav.exists():
        raw = out_dir / f"vo_{key}.raw.{'aiff' if b == 'say' else 'wav'}"
        if b == "say":
            cmd = ["say", "-v", voice, "-o", str(raw)] + (["-r", str(rate)] if rate else []) + [text]
        else:
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            cmd = [exe, "-v", voice, "-w", str(raw)] + (["-s", str(rate)] if rate else []) + [text]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode or not raw.exists():
            raise SystemExit(f"tts failed for voice {voice}: {r.stderr.strip()[:300]}")
        # trim leading/trailing silence so lines sit exactly where they are placed
        ffmpeg(["-i", str(raw), "-af", "silenceremove=start_periods=1:start_threshold=-50dB,"
                "areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse",
                "-ar", "48000", "-ac", "1", str(wav)])
        raw.unlink(missing_ok=True)
        log(f"tts: {voice} \"{text[:30]}\"")
    return wav, probe(wav)["duration"]


def caption_chunks(text: str, dur: float, max_cjk=16, max_latin=42) -> list[tuple[str, float, float]]:
    """Split a spoken line into caption-sized pieces at punctuation, timed by length."""
    parts = [p.strip() for p in re.split(r"(?<=[，。！？；、,.!?;:])\s*", text) if p.strip()]
    chunks = []
    for p in parts:
        limit = max_cjk if lang_of(p) == "zh" else max_latin
        while len(p) > limit * 1.3:
            cut = p.rfind(" ", 0, limit) if lang_of(p) != "zh" else limit
            cut = cut if cut > 0 else limit
            chunks.append(p[:cut].strip())
            p = p[cut:].strip()
        if chunks and len(chunks[-1]) + len(p) <= limit:
            chunks[-1] += ("" if lang_of(p) == "zh" else " ") + p
        else:
            chunks.append(p)
    total = sum(len(c) for c in chunks) or 1
    out, t = [], 0.0
    for c in chunks:
        d = dur * len(c) / total
        out.append((c.rstrip("，、,;；"), round(t, 3), round(t + d, 3)))
        t += d
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?")
    ap.add_argument("--out", default="vo")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--rate", type=int, default=None)
    ap.add_argument("--voices", action="store_true")
    a = ap.parse_args()
    if a.voices:
        print("\n".join(voices()))
        return
    p, d = synth(a.text, Path(a.out), a.voice, a.rate)
    print(f"{p} {d:.2f}s")


if __name__ == "__main__":
    main()
