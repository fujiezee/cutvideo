#!/usr/bin/env python3
"""Voice-over: text to speech with a configurable provider.

Providers (tts.json, see load_config):
  system   macOS `say` (e.g. Tingting for Mandarin, Samantha for English) or espeak-ng; offline
  openai   any OpenAI-compatible /audio/speech endpoint (base_url, model, voice)
  http     any HTTP API: url, headers, JSON body template, and where the audio is in the reply
Keys come from environment variables named in the config, never from files in the repo.
"languages": {"zh": {...}, "en": {...}} sets a provider/model/voice per language, so
Chinese lines get a Chinese voice and English lines an English one.
Lines are cached by provider settings + text + voice + rate.

Usage: tts.py "text" --out line.wav [--voice Tingting] [--rate 190] [--config tts.json]
       tts.py --voices            list voices of the system backend
       tts.py --show-config       print the merged provider config (keys hidden)
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import ffmpeg, log, probe  # noqa: E402

DEFAULT_VOICE = {"say": {"zh": "Tingting", "en": "Samantha"}, "espeak": {"zh": "cmn", "en": "en-us"}}


SKILL_DIR = Path(__file__).resolve().parent.parent
USER_CONFIG = Path.home() / ".config" / "cutvideo" / "tts.json"


def load_config(job_dir: Path | None = None, extra: str | None = None) -> dict:
    """Merge, later wins: built-in default (system) <- skill tts.json <- user
    ~/.config/cutvideo/tts.json <- job tts.json <- --config file <- CUTVIDEO_TTS_CONFIG."""
    cfg = {"provider": "system"}
    paths = [SKILL_DIR / "tts.json", USER_CONFIG, (Path(job_dir) / "tts.json") if job_dir else None,
             Path(extra) if extra else None,
             Path(os.environ["CUTVIDEO_TTS_CONFIG"]) if os.environ.get("CUTVIDEO_TTS_CONFIG") else None]
    for p in paths:
        if p and p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("provider") and data["provider"] != cfg.get("provider"):
                cfg = {}  # a different provider replaces the settings of the previous one
            cfg.update(data)
    return cfg


def for_language(cfg: dict, lang: str) -> dict:
    """Per-language provider: "languages": {"zh": {...}, "en": {...}} picks the settings for
    the line's language (a Chinese voice for Chinese, an English one for English); an entry
    with a different "provider" replaces the base settings, otherwise it is merged on top."""
    sub = (cfg.get("languages") or {}).get(lang)
    base = {k: v for k, v in cfg.items() if k != "languages"}
    if not sub:
        return base
    if sub.get("provider") and sub["provider"] != base.get("provider"):
        return dict(sub)
    return {**base, **sub}


def _env(s: str) -> str:
    """${NAME} -> environment value (missing variables are an error, not an empty key)."""
    def rep(m):
        v = os.environ.get(m.group(1))
        if v is None:
            raise SystemExit(f"tts: environment variable {m.group(1)} is not set")
        return v
    return re.sub(r"\$\{([A-Z0-9_]+)\}", rep, s)


def _fill(obj, values: dict):
    if isinstance(obj, str):
        for k, v in values.items():
            if obj == "${" + k + "}":
                return v  # keep numbers numeric
            obj = obj.replace("${" + k + "}", str(v))
        return _env(obj)
    if isinstance(obj, dict):
        return {k: _fill(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, values) for v in obj]
    return obj


def _dig(obj, path: str):
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def _http(cfg: dict, text: str, voice, rate, out: Path):
    """Generic / OpenAI-compatible request; writes the returned audio to `out`."""
    import urllib.request
    if cfg["provider"] == "openai":
        url = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/") + "/audio/speech"
        headers = {"Authorization": "Bearer ${" + cfg.get("api_key_env", "OPENAI_API_KEY") + "}",
                   "Content-Type": "application/json"}
        body = {"model": cfg.get("model", "tts-1"), "input": "${text}", "voice": "${voice}",
                "response_format": cfg.get("format", "wav")}
        if rate:
            body["speed"] = "${speed}"
        reply = {"type": "audio"}
    else:
        url, headers = cfg["url"], cfg.get("headers", {"Content-Type": "application/json"})
        body, reply = cfg.get("body", {"text": "${text}"}), cfg.get("response", {"type": "audio"})
    values = {"text": text, "voice": voice or cfg.get("voice", ""), "rate": rate or "",
              "speed": round((rate or 180) / 180, 2), "model": cfg.get("model", "")}
    req = urllib.request.Request(_env(url) if "${" in url else url, method=cfg.get("method", "POST"),
                                 data=json.dumps(_fill(body, values), ensure_ascii=False).encode(),
                                 headers=_fill(headers, values))
    with urllib.request.urlopen(req, timeout=float(cfg.get("timeout", 60))) as r:
        data = r.read()
    if reply.get("type") == "json":  # audio inside a JSON reply: base64 field or a URL to fetch
        j = json.loads(data)
        if reply.get("base64"):
            data = base64.b64decode(_dig(j, reply["base64"]))
        elif reply.get("url"):
            with urllib.request.urlopen(_dig(j, reply["url"]), timeout=float(cfg.get("timeout", 60))) as r2:
                data = r2.read()
        else:
            raise SystemExit("tts: response type json needs 'base64' or 'url' (a dotted path)")
    out.write_bytes(data)


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


def synth(text: str, out_dir: Path, voice: str | None = None, rate: int | None = None,
          config: dict | None = None) -> tuple[Path, float]:
    """Speak `text` to a 48 kHz mono wav in out_dir; returns (path, seconds)."""
    cfg = for_language(config or load_config(), lang_of(text))
    prov = cfg.get("provider", "system")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if prov != "system":
        voice = voice or (cfg.get("voices") or {}).get(lang_of(text)) or cfg.get("voice")
        public = {k: v for k, v in cfg.items() if "key" not in k.lower() and k != "headers"}
        key = hashlib.sha1(f"{json.dumps(public, sort_keys=True)}|{voice}|{rate}|{text}".encode()).hexdigest()[:14]
        wav = out_dir / f"vo_{key}.wav"
        if not wav.exists():
            raw = out_dir / f"vo_{key}.raw"
            _http(cfg, text, voice, rate, raw)
            _finish(raw, wav)
            log(f"tts: {prov} {voice or ''} \"{text[:30]}\"")
        return wav, probe(wav)["duration"]
    b = backend()
    if not b:
        raise SystemExit("no text-to-speech backend: macOS `say` or espeak-ng is needed, "
                         "or configure an API provider in tts.json")
    voice = voice or (cfg.get("voices") or {}).get(lang_of(text)) or DEFAULT_VOICE[b][lang_of(text)]
    key = hashlib.sha1(f"{b}|{voice}|{rate}|{text}".encode()).hexdigest()[:14]
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
        _finish(raw, wav)
        log(f"tts: {voice} \"{text[:30]}\"")
    return wav, probe(wav)["duration"]


def _finish(raw: Path, wav: Path):
    """Any audio the provider returned -> 48 kHz mono wav, leading/trailing silence trimmed
    so lines sit exactly where they are placed."""
    ffmpeg(["-i", str(raw), "-af", "silenceremove=start_periods=1:start_threshold=-50dB,"
            "areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse",
            "-ar", "48000", "-ac", "1", str(wav)])
    raw.unlink(missing_ok=True)


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
    ap.add_argument("--config", default=None, help="extra tts.json to merge on top")
    ap.add_argument("--show-config", action="store_true")
    a = ap.parse_args()
    cfg = load_config(Path.cwd(), a.config)
    if a.show_config:
        print(json.dumps({k: ("<hidden>" if "key" in k.lower() else v) for k, v in cfg.items()},
                         ensure_ascii=False, indent=1))
        return
    if a.voices:
        print("\n".join(voices()))
        return
    p, d = synth(a.text, Path(a.out), a.voice, a.rate, cfg)
    print(f"{p} {d:.2f}s")


if __name__ == "__main__":
    main()
