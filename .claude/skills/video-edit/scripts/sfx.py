"""Synthesised trailer sound effects (no sample library needed).

sfx_file(name, out_dir, **params) -> Path to a cached 48 kHz stereo WAV.
Names: boom, hit, whoosh, riser, sub_drop, tick, swell, glitch.
Common params: duration (s), pitch (multiplier), seed.
"""
from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import numpy as np

SR = 48000


def _t(d):
    return np.arange(int(SR * d)) / SR


def _onepole_lp(x, fc):
    """One-pole low-pass; fc may be an array (time-varying cutoff)."""
    fc = np.broadcast_to(np.asarray(fc, dtype=float), x.shape)
    alpha = 1 - np.exp(-2 * np.pi * fc / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += alpha[i] * (x[i] - acc)
        y[i] = acc
    return y


def _hp(x, fc):
    return x - _onepole_lp(x, fc)


def boom(duration=3.0, pitch=1.0, seed=1):
    rng = np.random.default_rng(seed)
    t = _t(duration)
    f = (38 + 70 * np.exp(-t * 9)) * pitch
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 1.6)
    crack = _hp(_onepole_lp(rng.standard_normal(len(t)), 2500), 200) * np.exp(-t * 10) * 2.2  # audible on small speakers
    knock = np.sin(2 * np.pi * 180 * pitch * t) * np.exp(-t * 14) * 0.8
    tail = _onepole_lp(rng.standard_normal(len(t)), 120) * np.exp(-t * 1.2) * 1.5
    return np.tanh(1.1 * (body + crack + knock + tail))


def hit(duration=1.2, pitch=1.0, seed=2):
    rng = np.random.default_rng(seed)
    t = _t(duration)
    f = (55 + 120 * np.exp(-t * 30)) * pitch
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 5)
    click = _hp(rng.standard_normal(len(t)), 1200) * np.exp(-t * 35)
    knock = np.sin(2 * np.pi * 220 * pitch * t) * np.exp(-t * 18)
    return np.tanh(1.2 * (body + 1.2 * click + 0.7 * knock))


def whoosh(duration=0.7, pitch=1.0, seed=3):
    rng = np.random.default_rng(seed)
    t = _t(duration)
    p = t / duration
    env = np.sin(np.pi * p) ** 2
    fc = (300 + 3500 * np.sin(np.pi * p) ** 1.5) * pitch
    x = _onepole_lp(rng.standard_normal(len(t)), fc)
    x = _hp(x, 150)
    return x * env / (np.abs(x).max() + 1e-9)


def riser(duration=3.0, pitch=1.0, seed=4):
    rng = np.random.default_rng(seed)
    t = _t(duration)
    p = t / duration
    env = p ** 2.2
    fc = (400 + 7000 * p ** 2) * pitch
    nz = _hp(_onepole_lp(rng.standard_normal(len(t)), fc), 300)
    f = (180 + 700 * p ** 2) * pitch
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.5 * np.sin(2 * np.pi * np.cumsum(f * 1.5) / SR)
    x = (nz / (np.abs(nz).max() + 1e-9) + 0.35 * tone) * env
    x[-int(0.01 * SR):] *= np.linspace(1, 0, int(0.01 * SR))
    return x


def sub_drop(duration=2.0, pitch=1.0, seed=5):
    t = _t(duration)
    f = (90 * np.exp(-t * 1.8) + 28) * pitch
    return np.tanh(1.1 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 1.1))


def tick(duration=0.15, pitch=1.0, seed=6):
    rng = np.random.default_rng(seed)
    t = _t(duration)
    return _hp(rng.standard_normal(len(t)), 3000) * np.exp(-t * 80) + \
        0.5 * np.sin(2 * np.pi * 1800 * pitch * t) * np.exp(-t * 60)


def swell(duration=2.0, pitch=1.0, seed=7):
    """Reverse-cymbal style swell that ends abruptly (lead-in to a hit)."""
    rng = np.random.default_rng(seed)
    t = _t(duration)
    x = _hp(rng.standard_normal(len(t)), 2500 * pitch) * np.exp(-t * 2.5)
    x = x[::-1]
    x[-int(0.005 * SR):] *= np.linspace(1, 0, int(0.005 * SR))
    return x / (np.abs(x).max() + 1e-9)


def glitch(duration=0.4, pitch=1.0, seed=8):
    rng = np.random.default_rng(seed)
    t = _t(duration)
    sq = np.sign(np.sin(2 * np.pi * 220 * pitch * t * (1 + 3 * (rng.random(len(t)) > 0.97))))
    gate = (np.floor(t * 40) % 2 == 0) * 1.0
    return 0.5 * sq * gate * np.exp(-t * 4) + 0.3 * rng.standard_normal(len(t)) * gate * np.exp(-t * 8)


GENERATORS = {"boom": boom, "hit": hit, "whoosh": whoosh, "riser": riser, "sub_drop": sub_drop,
              "tick": tick, "swell": swell, "glitch": glitch}


def sfx_file(name: str, out_dir, **params) -> Path:
    if name not in GENERATORS:
        raise SystemExit(f"unknown sfx '{name}'. options: {', '.join(GENERATORS)}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(json.dumps([name, params, 2], sort_keys=True).encode()).hexdigest()[:10]
    path = out_dir / f"{name}_{key}.wav"
    if path.exists():
        return path
    x = GENERATORS[name](**params)
    ramp = int(0.004 * SR)  # soft attack: avoids clicks and codec overshoot on the transient
    x[:ramp] *= np.linspace(0, 1, ramp)
    x = x / (np.abs(x).max() + 1e-9) * 0.8
    # slight stereo width: tiny delay on the right channel
    d = int(0.0007 * SR)
    st = np.stack([x, np.concatenate([np.zeros(d), x[:-d]])], axis=1)
    pcm = (st * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "sfx_preview")
    for n in GENERATORS:
        print(sfx_file(n, out))
