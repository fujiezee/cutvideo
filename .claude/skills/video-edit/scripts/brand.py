"""Brand preset: product name and link on the end card, optional logo watermark, palette
and font. Defaults live in the skill's brand.json; a job folder may carry its own
brand.json, and an EDL's "brand" object overrides both.

EDL: "brand": true | false | {"signature": "...", "watermark": {...}, ...}
"""
from __future__ import annotations

import json
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT = {
    "name": "cutvideo",
    "url": "github.com/fujiezee/cutvideo",
    "signature": "Made with {name} · {url}",
    "logo": None,
    "font": None,
    "colors": ["#FF3B30", "#0A84FF", "#FFD60A", "#30D158", "#BF5AF2", "#FF9F0A"],
    "watermark": {"enabled": False, "pos": [0.96, 0.05], "width": 0.08, "opacity": 0.6},
}


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_brand(job_dir: Path | None = None, override: dict | None = None) -> dict:
    b = dict(DEFAULT)
    for p in (SKILL_DIR / "brand.json", (Path(job_dir) / "brand.json") if job_dir else None):
        if p and p.exists():
            b = _merge(b, json.loads(p.read_text(encoding="utf-8")))
    b = _merge(b, override or {})
    if b.get("logo") and job_dir and not Path(b["logo"]).is_absolute():
        for root in (Path(job_dir), SKILL_DIR):
            if (root / b["logo"]).exists():
                b["logo"] = str(root / b["logo"])
                break
    return b


def signature(b: dict) -> str:
    return (b.get("signature") or "").format(name=b.get("name", ""), url=b.get("url", "")).strip(" ·")


def apply_brand(edl: dict, base: Path) -> dict | None:
    """Add the brand's end-card signature, watermark and font to the EDL (in place)."""
    spec = edl.get("brand")
    if not spec:
        return None
    b = load_brand(base, spec if isinstance(spec, dict) else None)
    o = edl.setdefault("output", {})
    if b.get("font") and not o.get("font"):
        o["font"] = b["font"]
    sig = signature(b)
    if sig and edl.get("clips"):
        last = edl["clips"][-1]
        on_card = last.get("type") in ("color", "image")
        W, H = int(o.get("width", 1920)), int(o.get("height", 1080))
        edl.setdefault("texts", []).append({
            "text": sig, "clip": -1, "start": 0.4, "style": "subtitle", "size": 0.024 if W >= H else 0.028,
            "color": "#9A9A9A" if on_card else "#FFFFFF", "pos": [0.5, 0.92 if on_card else 0.95],
            "max_width": 0.9, "_brand": True})
    wm = b.get("watermark") or {}
    if wm.get("enabled") and b.get("logo"):
        edl.setdefault("images", []).append({"path": b["logo"], "pos": wm.get("pos", [0.96, 0.05]),
                                             "width": wm.get("width", 0.08), "opacity": wm.get("opacity", 0.6)})
    return b
