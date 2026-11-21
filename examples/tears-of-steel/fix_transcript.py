"""Correct the ASR mistakes the examples depend on (idempotent). Keeps analysis.orig.json."""
import json
import shutil
import sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else "work/analysis.json")
orig = p.with_name("analysis.orig.json")
if not orig.exists():
    shutil.copy(p, orig)
a = json.loads(p.read_text(encoding="utf-8"))
n = 0
for seg in a["videos"][0]["transcript"]["segments"]:
    for w in seg.get("words", []):
        word, t = w["word"].strip(), w["start"]
        if word.rstrip(".") == "class" and 41.5 < t < 42.5:          # "robotic claws"
            w["word"], n = " claws.", n + 1
        elif word.lower() == "look" and 300 < t < 315:                 # "Look Celia" was mistimed
            w.update({"start": 315.72, "end": 316.1, "word": " Look"})
            n += 1
        elif word == "Celia" and 316 < t < 317:
            w["word"], n = " Celia,", n + 1
        elif word == "We" and 318 < t < 318.2:
            w["word"], n = " we", n + 1
    seg["words"].sort(key=lambda w: w["start"])
p.write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"transcript fixes applied: {n}")
