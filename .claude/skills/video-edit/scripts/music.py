#!/usr/bin/env python3
"""Background music when the footage has none.

1. A music provider configured in music.json (an AI music model behind an OpenAI-compatible
   chat endpoint, e.g. the Duaer gateway) when one is set up and answers.
2. Otherwise, or when the provider fails, a built-in generator: an instrumental bed for a
   mood at an exact tempo and length, with its beat grid written next to it
   (<file>.beats.json), so beat-synced cuts land exactly.

Moods: tense, epic, calm, upbeat, retro.
Usage: music.py --mood tense --duration 45 [--bpm 110] [--out bed.wav] [--seed 1] [--no-provider]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import wave
from pathlib import Path

import numpy as np

SR = 48000
NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}

# chords: (root semitone from the key, quality); qualities: m, M, m7, M7, sus
MOODS = {
    "tense":  {"bpm": 110, "key": "A", "chords": [(0, "m"), (8, "M"), (5, "m"), (7, "M")], "cut": 2200,
               "drums": "halftime", "arp": "8th", "bass": "pulse", "pad": 0.32},
    "epic":   {"bpm": 92, "key": "D", "chords": [(0, "m"), (10, "M"), (3, "M"), (8, "M")], "cut": 3800,
               "drums": "toms", "arp": None, "bass": "long", "pad": 0.42},
    "calm":   {"bpm": 78, "key": "C", "chords": [(0, "M7"), (9, "m7"), (5, "M7"), (7, "sus")], "cut": 2600,
               "drums": None, "arp": "pluck", "bass": "long", "pad": 0.36},
    "upbeat": {"bpm": 120, "key": "C", "chords": [(0, "M"), (7, "M"), (9, "m"), (5, "M")], "cut": 4500,
               "drums": "four", "arp": "16th", "bass": "offbeat", "pad": 0.26},
    "retro":  {"bpm": 100, "key": "A", "chords": [(0, "m"), (8, "M"), (3, "M"), (10, "M")], "cut": 3200,
               "drums": "gated", "arp": "16th", "bass": "8th", "pad": 0.3},
}
QUALITY = {"m": [0, 3, 7], "M": [0, 4, 7], "m7": [0, 3, 7, 10], "M7": [0, 4, 7, 11], "sus": [0, 5, 7]}


# ------------------------------------------------------------------ dsp helpers (numpy only)


def _fft_filter(x, lo=None, hi=None):
    """Smooth (2nd-order-like) low/high-pass applied in the frequency domain."""
    n = len(x)
    if n == 0:
        return x
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    g = np.ones_like(f)
    if hi:
        g *= 1 / (1 + (f / hi) ** 4)
    if lo:
        g *= (f / lo) ** 4 / (1 + (f / lo) ** 4)
    return np.fft.irfft(X * g, n)


def _midi(semi_from_a4):
    return 440.0 * 2 ** (semi_from_a4 / 12)


def _saw(freq, n, detune=0.0, phase=0.0):
    t = np.arange(n) / SR
    return 2 * ((t * freq * (1 + detune) + phase) % 1.0) - 1


def _adsr(n, a, d, s, r):
    env = np.ones(n) * s
    A, D, R = int(a * SR), int(d * SR), int(r * SR)
    A = min(A, n)
    env[:A] = np.linspace(0, 1, A, endpoint=False) if A else env[:A]
    D = min(D, n - A)
    env[A:A + D] = np.linspace(1, s, D, endpoint=False) if D else env[A:A + D]
    R = min(R, n)
    if R:
        env[n - R:] *= np.linspace(1, 0, R)
    return env


def _kick(rng):
    n = int(0.32 * SR)
    t = np.arange(n) / SR
    f = 48 + 110 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def _tom(rng, pitch=1.0):
    n = int(0.6 * SR)
    t = np.arange(n) / SR
    f = (70 + 60 * np.exp(-t * 12)) * pitch
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 5) + 0.15 * rng.standard_normal(n) * np.exp(-t * 30)


def _hat(rng, length=0.05):
    n = int(length * SR)
    t = np.arange(n) / SR
    return _fft_filter(rng.standard_normal(n), lo=7000) * np.exp(-t * 60)


def _snare(rng, length=0.25):
    n = int(length * SR)
    t = np.arange(n) / SR
    noise = _fft_filter(rng.standard_normal(n), lo=1500, hi=9000) * np.exp(-t * 18)
    return 0.8 * noise + 0.4 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 25)


def _place(buf, sample, at, gain=1.0):
    i = int(at * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sample))
    buf[i:j] += gain * sample[:j - i]


# ------------------------------------------------------------------ the generator


def generate(mood="tense", duration=45.0, bpm=None, seed=1, key=None) -> tuple[np.ndarray, dict]:
    """Stereo float array (n, 2) and a beat dict {bpm, beats, strong_beats, drops}."""
    if mood not in MOODS:
        raise SystemExit(f"unknown mood {mood!r}; options: {', '.join(MOODS)}")
    m = MOODS[mood]
    rng = np.random.default_rng(seed)
    bpm = float(bpm or m["bpm"])
    beat = 60.0 / bpm
    bar = 4 * beat
    n_bars = max(2, int(np.ceil(duration / bar)) + 1)
    n = int((n_bars * bar + 3.0) * SR)
    root = NOTE[key or m["key"]] - 9 - 12  # semitones from A4, one octave down
    pad, bass, arp, drums = (np.zeros(n) for _ in range(4))
    intro = max(1, n_bars // 5)
    build = max(intro + 1, n_bars * 2 // 5)
    outro = n_bars - 1
    kick, hat, snare = _kick(rng), _hat(rng), _snare(rng)
    beats, strong = [], []
    for b in range(n_bars):
        t0 = b * bar
        deg, q = m["chords"][b % len(m["chords"])]
        notes = [root + deg + iv for iv in QUALITY[q]]
        # pad: three detuned saws per chord note, slow attack, one bar plus release
        L = int((bar + 0.8) * SR)
        env = _adsr(L, 0.35 * bar, 0.2, 0.85, 0.8)
        chord = np.zeros(L)
        for nt in notes:
            f = _midi(nt + 12)
            for det in (-0.004, 0.0, 0.005):
                chord += _saw(f, L, det, rng.random())
        _place(pad, chord * env / (3 * len(notes)), t0)
        full = b >= build and b < outro
        if b >= intro and b < outro + 1:  # bass
            f = _midi(root + deg - 12)
            if m["bass"] == "long":
                L = int(bar * SR)
                _place(bass, np.sin(2 * np.pi * f * np.arange(L) / SR) * _adsr(L, 0.05, 0.3, 0.7, 0.3), t0)
            else:
                step = beat / 2 if m["bass"] in ("pulse", "8th", "offbeat") else beat
                k = 0
                while k * step < bar - 1e-6:
                    if m["bass"] == "offbeat" and k % 2 == 0:
                        k += 1
                        continue
                    L = int(step * 0.9 * SR)
                    s = 0.7 * np.sin(2 * np.pi * f * np.arange(L) / SR) + 0.3 * _saw(f, L)
                    _place(bass, s * _adsr(L, 0.005, 0.08, 0.6, 0.05), t0 + k * step)
                    k += 1
        if m["arp"] and b >= intro and b < outro:  # arpeggio
            step = {"8th": beat / 2, "16th": beat / 4, "pluck": beat / 2}[m["arp"]]
            seq = notes + [notes[1] + 12, notes[0] + 12]
            k = 0
            while k * step < bar - 1e-6:
                nt = seq[k % len(seq)] + 24
                L = int(step * (2.5 if m["arp"] == "pluck" else 0.8) * SR)
                tt = np.arange(L) / SR
                if m["arp"] == "pluck":
                    s = np.sin(2 * np.pi * _midi(nt) * tt) * np.exp(-tt * 6)
                else:
                    s = _saw(_midi(nt), L) * np.exp(-tt * 14)
                _place(arp, s, t0 + k * step, 0.9 if k % 4 == 0 else 0.6)
                k += 1
        for k in range(4):  # drums and the beat grid
            tb = t0 + k * beat
            beats.append(round(tb, 4))
            if k == 0:
                strong.append(round(tb, 4))
            if not m["drums"] or not full:
                continue
            d = m["drums"]
            if d == "four":
                _place(drums, kick, tb)
                _place(drums, hat, tb + beat / 2, 0.5)
                if k in (1, 3):
                    _place(drums, snare, tb, 0.7)
            elif d == "halftime":
                if k in (0, 2):
                    _place(drums, kick, tb)
                if k == 2:
                    _place(drums, snare, tb, 0.8)
                for h in range(4):
                    _place(drums, hat, tb + h * beat / 4, 0.25 if h % 2 else 0.4)
            elif d == "toms":
                _place(drums, _tom(rng, 1.0 if k % 2 == 0 else 1.3), tb, 0.8)
                if k == 0:
                    _place(drums, kick, tb, 1.0)
            elif d == "gated":
                if k in (0, 2):
                    _place(drums, kick, tb)
                if k in (1, 3):
                    _place(drums, _snare(rng, 0.35), tb, 0.9)
                _place(drums, hat, tb + beat / 2, 0.4)
    pad = _fft_filter(pad, lo=90, hi=m["cut"]) * m["pad"]
    if m["drums"] == "four":  # pump the pad with the kick
        duck = np.ones(n)
        for tb in beats:
            i = int(tb * SR)
            L = min(int(beat * 0.6 * SR), n - i)
            if L > 0:
                duck[i:i + L] = 1 - 0.45 * np.exp(-np.arange(L) / (0.08 * SR))
        pad *= duck
    bass = _fft_filter(bass, hi=500) * 0.55
    arp = _fft_filter(arp, lo=200, hi=m["cut"] * 1.6) * 0.22
    drums = drums * 0.55
    # build-up: a noise riser into the main section
    rs = build * bar
    L = int(min(bar, rs) * SR)
    if L > 0:
        tt = np.arange(L) / SR
        riser = _fft_filter(rng.standard_normal(L), lo=800) * (tt / tt[-1]) ** 2 * 0.25
        _place(drums, riser, rs - L / SR)
    mono_l = pad + bass + arp + drums
    d = int(0.012 * SR)  # Haas width on the pad
    right = pad.copy()
    right[d:] = pad[:-d]
    st = np.stack([mono_l, right + bass + arp + drums], axis=1)
    total = int(duration * SR)
    st = st[:max(total, int(SR))]
    fade = min(int(2.5 * SR), len(st) // 3)
    st[-fade:] *= np.linspace(1, 0, fade)[:, None]
    st[:int(0.05 * SR)] *= np.linspace(0, 1, int(0.05 * SR))[:, None]
    st = np.tanh(1.2 * st)
    st *= 10 ** (-1.0 / 20) / max(np.abs(st).max(), 1e-6)
    info = {"bpm": bpm, "beat_interval": beat, "beats": [b for b in beats if b < duration],
            "strong_beats": [b for b in strong if b < duration], "drops": [round(build * bar, 3)],
            "mood": mood, "generator": "builtin"}
    return st, info


def write_wav(st: np.ndarray, path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(st, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


# ------------------------------------------------------------------ provider (AI music model)


def load_config(job_dir: Path | None = None) -> dict:
    """music.json: job folder > ~/.config/cutvideo/music.json; {} means built-in only."""
    for p in ((Path(job_dir) / "music.json") if job_dir else None,
              Path(os.environ.get("CUTVIDEO_MUSIC_USER_CONFIG", Path.home() / ".config" / "cutvideo" / "music.json"))):
        if p and p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _find_audio_b64(obj):
    """First long base64 audio payload in a chat reply (message.audio.data, content parts...)."""
    if isinstance(obj, dict):
        for k in ("data", "b64_json", "audio"):
            v = obj.get(k)
            if isinstance(v, str) and len(v) > 1000:
                return v
        for v in obj.values():
            r = _find_audio_b64(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_audio_b64(v)
            if r:
                return r
    elif isinstance(obj, str) and obj.startswith("data:audio") and "," in obj:
        return obj.split(",", 1)[1]
    return None


def from_provider(cfg: dict, prompt: str, out: Path) -> Path:
    """Ask a chat-completions music model for audio; raises on any failure."""
    import urllib.request
    from tts import _env, _open, load_env_file
    load_env_file(cfg)
    url = cfg.get("base_url", "").rstrip("/") + "/chat/completions"
    key = _env("${" + cfg.get("api_key_env", "OPENAI_API_KEY") + "}")
    body = {"model": cfg["model"], "messages": [{"role": "user", "content": prompt}],
            **({"modalities": ["audio"]} if cfg.get("modalities", True) else {}), **cfg.get("extra_body", {})}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with _open(req, float(cfg.get("timeout", 300))) as r:
        reply = json.loads(r.read())
    b64 = _find_audio_b64(reply)
    if not b64:
        raise RuntimeError("no audio in the reply")
    raw = out.with_suffix(".provider")
    raw.write_bytes(base64.b64decode(b64))
    from common import ffmpeg
    ffmpeg(["-i", str(raw), "-ar", str(SR), "-ac", "2", str(out)])
    raw.unlink(missing_ok=True)
    return out


PROMPTS = {"tense": "tense cinematic thriller underscore, pulsing synths and low strings",
           "epic": "epic orchestral trailer music, big drums and brass, heroic",
           "calm": "calm reflective piano and soft pads, gentle",
           "upbeat": "upbeat energetic pop instrumental, punchy drums, bright synths",
           "retro": "80s synthwave, retro drum machine, analog arpeggios"}


def make(mood: str, duration: float, out_dir: Path, bpm=None, seed=1, job_dir=None, use_provider=True) -> tuple[Path, dict]:
    """Music file for `mood`/`duration` (cached) and its beat info."""
    from common import log
    cfg = load_config(job_dir) if use_provider else {}
    key = hashlib.sha1(json.dumps([mood, round(duration, 1), bpm, seed, cfg.get("model")]).encode()).hexdigest()[:10]
    out = Path(out_dir) / f"music_{mood}_{key}.wav"
    side = out.with_suffix(".beats.json")
    if out.exists() and side.exists():
        return out, json.loads(side.read_text())
    if cfg.get("model"):
        try:
            prompt = f"{PROMPTS.get(mood, mood)}, instrumental, about {int(duration)} seconds"
            from_provider(cfg, prompt, out)
            from analyze import music_beats
            info = {**music_beats(str(out)), "mood": mood, "generator": cfg["model"]}
            side.write_text(json.dumps(info))
            log(f"music: {cfg['model']} -> {out.name}")
            return out, info
        except Exception as e:  # provider refused or failed: the built-in bed still gives music
            log(f"music: provider {cfg.get('model')} failed ({str(e)[:120]}); using the built-in generator")
    st, info = generate(mood, duration, bpm, seed)
    write_wav(st, out)
    side.write_text(json.dumps(info))
    log(f"music: built-in {mood} {info['bpm']:.0f} BPM {duration:.0f}s -> {out.name}")
    return out, info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mood", default="tense", choices=list(MOODS))
    ap.add_argument("--duration", type=float, default=45)
    ap.add_argument("--bpm", type=float, default=None)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=".")
    ap.add_argument("--no-provider", action="store_true")
    a = ap.parse_args()
    p, info = make(a.mood, a.duration, Path(a.out), a.bpm, a.seed, Path.cwd(), not a.no_provider)
    print(p, f"{info['bpm']:.1f} BPM", f"{len(info['beats'])} beats")


if __name__ == "__main__":
    main()
