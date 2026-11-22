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
USER_CONFIG = Path(os.environ.get("CUTVIDEO_TTS_USER_CONFIG", Path.home() / ".config" / "cutvideo" / "tts.json"))


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


def _ssl_context():
    """Verified TLS even on python.org macOS builds that ship without CA certificates:
    certifi if installed, else the system bundle."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for p in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if Path(p).exists():
            return ssl.create_default_context(cafile=p)
    return ssl.create_default_context()


def _open(req, timeout):
    import urllib.request
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def load_env_file(cfg: dict):
    """"env_file": "~/.config/duaer/api.env" -> KEY=value lines into the environment (keys
    stay outside the repo; variables already set win)."""
    p = cfg.get("env_file")
    if not p:
        return
    p = Path(os.path.expanduser(p))
    if not p.exists():
        raise SystemExit(f"tts: env_file {p} not found")
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip().removeprefix("export ").strip(), v.strip().strip('"').strip("'"))


def list_models(cfg: dict) -> list[dict]:
    """GET {base_url}/models of an OpenAI-compatible provider; speech models flagged."""
    import urllib.request
    load_env_file(cfg)
    base = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
    key = _env("${" + cfg.get("api_key_env", "OPENAI_API_KEY") + "}")
    req = urllib.request.Request(base + "/models", headers={"Authorization": f"Bearer {key}"})
    with _open(req, float(cfg.get("timeout", 30))) as r:
        data = json.loads(r.read())
    items = data.get("data", data if isinstance(data, list) else [])
    out = []
    for m in items:
        mid = m.get("id", m) if isinstance(m, dict) else str(m)
        speech = bool(re.search(r"tts|speech|voice|audio|cosy|sambert|sovits|eleven|minimax-speech", mid, re.I))
        out.append({"id": mid, "speech": speech, "owned_by": m.get("owned_by", "") if isinstance(m, dict) else ""})
    return out


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
    load_env_file(cfg)
    if cfg["provider"] == "openai":
        url = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/") + "/audio/speech"
        headers = {"Authorization": "Bearer ${" + cfg.get("api_key_env", "OPENAI_API_KEY") + "}",
                   "Content-Type": "application/json"}
        body = {"model": cfg.get("model", "tts-1"), "input": "${text}",
                "response_format": cfg.get("format", "wav")}
        if voice or cfg.get("voice"):  # some models have one built-in voice and reject the field
            body["voice"] = "${voice}"
        body.update(cfg.get("extra_body", {}))
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
    with _open(req, float(cfg.get("timeout", 60))) as r:
        data = r.read()
    if reply.get("type") == "json":  # audio inside a JSON reply: base64 field or a URL to fetch
        j = json.loads(data)
        if reply.get("base64"):
            data = base64.b64decode(_dig(j, reply["base64"]))
        elif reply.get("url"):
            with _open(_dig(j, reply["url"]), float(cfg.get("timeout", 60))) as r2:
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


_BREAK_AFTER = set("了着过")            # aspect markers end a phrase
_BREAK_BEFORE = set("和与把被给对从向在就也都还又却而且并只")  # these start one
_NO_BREAK_AFTER = set("的地得一这那个")   # bind to what follows
_NO_BREAK_BEFORE = set("的地得了着过们吗呢吧")  # cannot start a caption line


def _word_bounds(p: str):
    """Word boundaries from jieba when installed (else None: every position allowed)."""
    try:
        import jieba
    except ImportError:
        return None
    jieba.setLogLevel(60)
    b, i = set(), 0
    for w in jieba.cut(p):
        i += len(w)
        b.add(i)
    return b
_NUMERAL = set("零一二两三四五六七八九十百千万亿0123456789")


def _split_cjk(p: str, limit: int, protect=()) -> list[str]:
    """Split a long CJK phrase into balanced pieces of at most ~limit characters, never
    inside a protected word or a number, preferring a cut after a particle (的/了/在/和...)."""
    n = len(p)
    k = -(-n // limit)
    if k <= 1:
        return [p]
    banned = set()
    for w in protect:
        for m in re.finditer(re.escape(w), p):
            banned.update(range(m.start() + 1, m.end()))
    for i in range(1, n):
        if p[i - 1] in _NUMERAL and p[i] in _NUMERAL | set("年岁天个次"):
            banned.add(i)
    words = _word_bounds(p)
    cuts, start = [], 0
    for j in range(1, k):
        ideal = round(n * j / k)
        best = None
        for i in range(max(start + 2, ideal - 5), min(n - 2, ideal + 5) + 1):
            if i in banned or (words is not None and i not in words):
                continue
            score = abs(i - ideal)
            score -= 3 if p[i] in _BREAK_BEFORE else 0
            score -= 3 if p[i - 1] in _BREAK_AFTER else 0
            score += 4 if p[i - 1] in _NO_BREAK_AFTER else 0
            score += 4 if p[i] in _NO_BREAK_BEFORE else 0
            if best is None or score < best[0]:
                best = (score, i)
        if best:
            cuts.append(best[1])
            start = best[1]
    pieces, a = [], 0
    for c in cuts + [n]:
        pieces.append(p[a:c])
        a = c
    return [x for x in pieces if x]


def caption_chunks(text: str, dur: float, max_cjk=16, max_latin=42, protect=()) -> list[tuple[str, float, float]]:
    """Split a spoken line into caption-sized pieces at punctuation, timed by length. Long
    CJK phrases are balanced and never cut inside `protect` words or numbers."""
    parts = [p.strip() for p in re.split(r"(?<=[，。！？；：、,.!?;:])\s*", text) if p.strip()]
    chunks = []
    for p in parts:
        zh = lang_of(p) == "zh"
        limit = max_cjk if zh else max_latin
        if zh and len(p) > limit * 1.15:
            pieces = _split_cjk(p, limit, protect)
        else:
            pieces = []
            while not zh and len(p) > limit * 1.3:
                cut = p.rfind(" ", 0, limit)
                cut = cut if cut > 0 else limit
                pieces.append(p[:cut].strip())
                p = p[cut:].strip()
            pieces.append(p)
        for q in pieces:
            if chunks and len(chunks[-1]) + len(q) <= limit and not re.search(r"[。！？.!?]$", chunks[-1]):
                chunks[-1] += ("" if zh else " ") + q
            else:
                chunks.append(q)
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
    ap.add_argument("--list-models", action="store_true",
                    help="list the models of the configured OpenAI-compatible provider (speech ones marked)")
    ap.add_argument("--lang", default=None, help="with --list-models: use the provider set for this language")
    a = ap.parse_args()
    cfg = load_config(Path.cwd(), a.config)
    if a.show_config:
        print(json.dumps({k: ("<hidden>" if "key" in k.lower() else v) for k, v in cfg.items()},
                         ensure_ascii=False, indent=1))
        return
    if a.list_models:
        c = for_language(cfg, a.lang) if a.lang else cfg
        if c.get("provider") != "openai":
            raise SystemExit("--list-models needs an openai-compatible provider (base_url, api_key_env)")
        ms = list_models(c)
        for m in sorted(ms, key=lambda m: (not m["speech"], m["id"])):
            print(f"{'*' if m['speech'] else ' '} {m['id']}  {m['owned_by']}")
        print(f"{sum(m['speech'] for m in ms)} speech models (*) of {len(ms)}")
        return
    if a.voices:
        print("\n".join(voices()))
        return
    p, d = synth(a.text, Path(a.out), a.voice, a.rate, cfg)
    print(f"{p} {d:.2f}s")


if __name__ == "__main__":
    main()
