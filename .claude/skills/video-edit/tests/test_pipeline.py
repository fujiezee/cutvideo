"""End to end on the synthetic clip: analyse, render, formats, loudness, style menu, autocut."""
from __future__ import annotations

import json
import sys

import qa
from conftest import SCRIPTS, probe, render, run
from styles import catalogue, features


def test_analyze_finds_the_cuts(analysis):
    sc = analysis["videos"][0]["scenes"]
    starts = [s["start"] for s in sc[1:]]
    assert len(sc) == 8, starts
    for want, got in zip(range(3, 24, 3), starts):
        assert abs(want - got) < 0.1


def test_render_vertical_draft(job):
    edl = {"analysis": "work/analysis.json",
           "output": {"path": "out/basic.mp4", "width": 360, "height": 640, "fps": 24, "fit": "crop"},
           "clips": [{"src": "v1", "in": 0.5, "out": 2.5, "focus": "auto"},
                     {"src": "v1", "in": 3.5, "out": 5.5, "transition": {"type": "fade", "duration": 0.3}},
                     {"type": "color", "color": "#000000", "duration": 1.0}],
           "texts": [{"text": "Hello", "clip": 0, "start": 0.2, "end": 1.5, "style": "title"},
                     {"text": "3D", "clip": 1, "start": 0.0, "end": 1.5, "style": "text3d"}]}
    rep = render(edl, job, "edl_basic", "--draft")
    p = probe(rep["output"])
    assert (p["w"], p["h"]) == (180, 320)            # drafts render at half size
    assert abs(p["dur"] - 4.7) < 0.15 and p["audio"]


def test_final_render_meets_loudness_and_peak(job):
    edl = {"analysis": "work/analysis.json",
           "output": {"path": "out/loud.mp4", "width": 320, "height": 180, "fps": 24, "fit": "crop"},
           "clips": [{"src": "v1", "in": 0.0, "out": 4.0, "volume": 6.0}]}
    rep = render(edl, job, "edl_loud")
    m = qa.loudness(rep["output"])
    assert m["true_peak_dbfs"] <= -0.9
    assert abs(m["integrated_lufs"] + 14) <= 2


def test_formats_render_every_aspect(job):
    edl = {"analysis": "work/analysis.json",
           "output": {"path": "out/fmt.mp4", "width": 640, "height": 360, "fps": 24, "fit": "crop"},
           "clips": [{"src": "v1", "in": 0.2, "out": 1.8}, {"src": "v1", "in": 6.2, "out": 7.8}]}
    p = job / "edl_fmt.json"
    p.write_text(json.dumps(edl))
    r = run(sys.executable, SCRIPTS / "render.py", p, "--formats", "9:16,1:1", "--draft", "--no-review", cwd=job)
    outs = json.loads(r.stdout[r.stdout.rindex('{\n "formats"'):])["formats"]
    dims = {k: probe(job / v.replace(".mp4", "_draft.mp4")) if not (job / v).exists() else probe(job / v)
            for k, v in outs.items()}
    assert (dims["9:16"]["w"], dims["9:16"]["h"]) == (540, 960)   # drafts: half size
    assert (dims["1:1"]["w"], dims["1:1"]["h"]) == (540, 540)


def test_style_menu_scores_every_style(job, analysis):
    f = features(analysis, job / "work")
    styles = catalogue(f)
    keys = {s["key"] for s in styles}
    assert {"trailer", "beatmix", "cast", "karaoke", "vhs", "silent", "cyberpunk"} <= keys
    assert all(0 <= s["score"] <= 100 for s in styles)
    assert f["shots"] == 8 and len(f["all"]) == 8


def test_autocut_drafts_render(job):
    r = run(sys.executable, SCRIPTS / "autocut.py", job / "work" / "analysis.json", "--styles", "vhs,cyberpunk",
            "--no-music", "--draft", "--title", "Test", cwd=job)
    rep = json.loads((job / "auto" / "report.json").read_text())
    by = {x["style"]: x for x in rep["styles"]}
    for st in ("vhs", "cyberpunk"):
        assert by[st].get("video"), (st, by[st].get("error"), r.stderr[-1500:])
        p = probe(by[st]["video"])
        assert p["dur"] > 3
    assert (job / "auto" / "compare.jpg").exists()
    # every clip lies inside one detected shot
    sc = json.loads((job / "work" / "analysis.json").read_text())["videos"][0]["scenes"]
    for st in ("vhs", "cyberpunk"):
        for c in json.loads((job / "auto" / f"edl_{st}.json").read_text())["clips"]:
            if c.get("type", "video") == "video":
                assert any(s["start"] <= c["in"] and c["out"] <= s["end"] for s in sc), c


def test_intro_past_clip_end_freezes_last_frame(job):
    edl = {"analysis": "work/analysis.json",
           "output": {"path": "out/intro.mp4", "width": 320, "height": 180, "fps": 24, "fit": "crop"},
           "clips": [{"src": "v1", "in": 3.2, "out": 3.9,
                      "effects": [{"type": "intro", "at": 1.0, "hold": 1.0, "name": "A", "box": [0, 0, 1, 1]}]}]}
    rep = render(edl, job, "edl_intro", "--draft")
    assert probe(rep["output"])["dur"] > 1.5


def test_narration_is_spoken_captioned_and_ducks_music(job):
    import pytest
    from tts import backend
    if not backend():
        pytest.skip("no text-to-speech backend")
    music = job / "src" / "music.wav"
    run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", "8", "-i", "sine=frequency=220:sample_rate=48000",
        "-af", "volume=0.5", music)
    edl = {"analysis": "work/analysis.json",
           "output": {"path": "out/vo.mp4", "width": 320, "height": 180, "fps": 24, "fit": "crop"},
           "clips": [{"src": "v1", "in": 0.2, "out": 2.8, "volume": 0.1}, {"src": "v1", "in": 3.2, "out": 5.8,
                                                                         "volume": 0.1}],
           "music": [{"path": "src/music.wav", "volume": 0.5, "duck": True, "loop": False}],
           "narration": {"items": [{"text": "Hello there, this is a test.", "clip": 0, "start": 0.3}]}}
    rep = render(edl, job, "edl_vo", "--draft")
    subs = json.loads((job / ".render" / "subtitles.json").read_text())
    assert subs and subs[0]["start"] >= 0.29
    graph = (job / ".render" / "graph.txt").read_text()
    assert "sidechaincompress" in graph and "[ns0]" in graph
    assert probe(rep["output"])["dur"] > 4.5


def test_covers_for_every_format(job):
    from PIL import Image
    run(sys.executable, SCRIPTS / "cover.py", job / "work" / "analysis.json", "--title", "Test",
        "--subtitle", "SUB", "--formats", "16:9,9:16,1:1", "--n", "2", cwd=job)
    sizes = {"16x9": (1280, 720), "9x16": (1080, 1920), "1x1": (1080, 1080)}
    for tag, wh in sizes.items():
        for k in (1, 2):
            assert Image.open(job / "covers" / f"cover_{tag}_{k}.jpg").size == wh
    assert (job / "covers" / "sheet.jpg").exists()


def test_voice_preset_renders(job):
    edl = {"analysis": "work/analysis.json", "audio": {"voice": "clean"},
           "output": {"path": "out/voice.mp4", "width": 320, "height": 180, "fps": 24, "fit": "crop"},
           "clips": [{"src": "v1", "in": 0.2, "out": 2.2}, {"src": "v1", "in": 3.2, "out": 5.2, "voice": "broadcast"}]}
    rep = render(edl, job, "edl_voice", "--draft")
    assert probe(rep["output"])["audio"]


def test_autocut_generates_music_when_there_is_none(job):
    run(sys.executable, SCRIPTS / "autocut.py", job / "work" / "analysis.json", "--styles", "beatmix",
        "--draft", "--title", "T", cwd=job)
    rep = json.loads((job / "auto" / "report.json").read_text())
    assert rep["styles"][0].get("video"), rep["styles"][0].get("error")
    edl = json.loads((job / "auto" / "edl_beatmix.json").read_text())
    m = edl["music"][0]["path"]
    assert "music_upbeat" in m and (job / "auto" / m).resolve().with_suffix(".beats.json").exists()


def test_split_ramp_stabilize_and_pip_render(job):
    edl = {"analysis": "work/analysis.json",
           "output": {"path": "out/layers.mp4", "width": 640, "height": 360, "fps": 24, "fit": "crop"},
           "clips": [{"src": "v1", "in": 0.2, "out": 2.6},
                     {"type": "split", "layout": "grid", "duration": 1.5,
                      "items": [{"src": "v1", "in": 3.2, "out": 5.0}, {"src": "v1", "in": 6.2, "out": 8.0},
                                {"src": "v1", "in": 9.2, "out": 11.0}, {"src": "v1", "in": 12.2, "out": 14.0}]},
                     {"src": "v1", "in": 15.2, "out": 17.2, "ramp": [[0, 1.0], [0.5, 0.5], [1.5, 1.0]],
                      "smooth": "blend"},
                     {"src": "v1", "in": 18.2, "out": 20.0, "stabilize": True}],
           "layers": [{"src": "v1", "in": 21.2, "out": 23.0, "clip": 0, "start": 0.3, "width": 0.3, "volume": 0.5}]}
    rep = render(edl, job, "edl_layers", "--draft")
    p = probe(rep["output"])
    plan = 2.4 + 1.5 + sum(dt / sp for _, dt, sp in __import__("render").ramp_steps(edl["clips"][2])) + 1.8
    assert abs(p["dur"] - plan) < 0.2 and (p["w"], p["h"]) == (320, 180)
