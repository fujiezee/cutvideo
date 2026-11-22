"""Fast checks of the pure logic: no footage, no rendering."""
from __future__ import annotations

import copy
import json

import autocut
import qa
import render
from common import find_font
from overlay import _perspective, render_text_block


# ---------------------------------------------------------------- module surface
def test_render_entry_points_exist():
    # these were once lost in a merge; the CLI depends on them
    for name in ("derive_format", "render_formats", "fix_loudness", "snap_intros_to_pauses",
                 "apply_beat_sync", "build_subtitles", "write_srt"):
        assert callable(getattr(render, name, None)), name


# ---------------------------------------------------------------- QA text matching
def test_tokens_normalise_numbers_and_punctuation():
    assert qa.tokens("Four, three!") == ["four", "three"]
    assert qa.tokens("4 3 2 1") == ["four", "three", "two", "one"]


def test_recall():
    assert qa.recall(["a", "b"], ["a"]) == 0.5
    assert qa.recall([], ["x"]) == 1.0
    assert qa.recall(["celia"], ["celia,"[:-1]]) == 1.0


# ---------------------------------------------------------------- formats
EDL = {"output": {"path": "out/demo_16x9.mp4", "width": 1920, "height": 1080, "fps": 24, "fit": "crop"},
       "clips": [{"src": "v1", "in": 0, "out": 2, "fit": "pad"},
                 {"src": "v1", "in": 3, "out": 5, "effects": [{"type": "intro", "box": [0, 0, 1, 1]}]}],
       "subtitles": {"enabled": True, "pos": [0.5, 0.9], "items": [{"text": "x", "pos": [0.5, 0.9]}]},
       "formats": {"9:16": {"grade": "vivid", "output": {"fps": 30}}}}


def test_derive_vertical_from_horizontal():
    v = render.derive_format(copy.deepcopy(EDL), "9:16", src_vertical=False)
    o = v["output"]
    assert (o["width"], o["height"]) == (1080, 1920)
    assert o["path"].endswith("demo_9x16.mp4")
    assert o["fit"] == "crop" and o["fps"] == 30          # per-format override merged
    assert v["grade"] == "vivid"
    assert all(c.get("focus") == "track" and "fit" not in c for c in v["clips"])
    assert "box" not in v["clips"][1]["effects"][0]        # old-format box dropped
    assert v["subtitles"]["pos"] == render.CAPTION_POS["9:16"]
    assert "formats" not in v


def test_derive_same_format_keeps_edit():
    v = render.derive_format(copy.deepcopy(EDL), "16:9", src_vertical=False)
    assert v["clips"][0].get("fit") == "pad"
    assert v["output"]["path"].endswith("demo_16x9.mp4")


# ---------------------------------------------------------------- intro snapping
def _intro_case(at):
    words = [{"start": 10.3, "end": 10.7, "word": "Look"}, {"start": 11.0, "end": 11.4, "word": "Celia"}]
    an = {"videos": [{"path": "/x.mp4", "transcript": {"segments": [{"words": words}]}}]}
    edl = {"clips": [{"path": "/x.mp4", "in": 10.0, "out": 14.0, "effects": [{"type": "intro", "at": at}]}]}
    render.snap_intros_to_pauses(edl, an)
    return edl["clips"][0]["effects"][0]["at"]


def test_intro_moves_out_of_a_word():
    at = _intro_case(0.5)                     # 10.5 s is inside "Look"
    assert abs(at - 0.76) < 0.05
    assert not (0.3 <= at <= 0.7 or 1.0 <= at <= 1.4)


def test_intro_in_a_pause_stays():
    assert _intro_case(0.85) == 0.85


# ---------------------------------------------------------------- 3D text
def test_text3d_is_bigger_than_flat_and_perspective_narrows():
    fp = find_font()
    flat = render_text_block({"text": "AB", "style": "title"}, 640, 360, fp)
    deep = render_text_block({"text": "AB", "style": "text3d", "size": 0.1}, 640, 360, fp)
    flat2 = render_text_block({"text": "AB", "style": "text3d", "size": 0.1, "depth": 0}, 640, 360, fp)
    assert deep.width > flat2.width and deep.height > flat2.height
    assert flat.mode == deep.mode == "RGBA"
    assert _perspective(deep, ry=40).width < deep.width


# ---------------------------------------------------------------- autocut picking
SHOTS = [{"vid": "v1", "start": 0.0, "end": 3.0, "score": 0.5, "motion": 0.2},
         {"vid": "v1", "start": 3.0, "end": 3.5, "score": 0.9, "motion": 0.3},   # too short
         {"vid": "v1", "start": 3.5, "end": 9.0, "score": 0.8, "motion": 0.1},
         {"vid": "v1", "start": 20.0, "end": 24.0, "score": 0.7, "motion": 0.4}]


def test_picker_stays_inside_shots_and_never_overlaps():
    P = autocut.Picker()
    got = P.take(SHOTS, 5, 2.0, min_gap=0)
    assert got and [s["start"] for s, _ in got] == sorted(s["start"] for s, _ in got)
    for s, (a, b) in got:
        assert s["start"] + P.margin <= a < b <= s["end"] - P.margin
    again = P.take(SHOTS, 5, 2.0, min_gap=0)               # everything long enough is used
    for s, (a, b) in again:
        assert all(b <= ua or a >= ub for _, ua, ub in P.used[:len(got)])


def test_choose_styles():
    ranked = [{"key": "no_recipe", "score": 90}, {"key": "trailer", "score": 80}, {"key": "vhs", "score": 50},
              {"key": "silent", "score": 70}]
    assert autocut.choose("top2", ranked) == ["trailer", "vhs"]
    assert autocut.choose("recommended", ranked) == ["trailer", "silent"]
    assert autocut.choose("vhs,cast", ranked) == ["vhs", "cast"]


def test_line_quality_prefers_whole_sentences():
    frag = {"text": "And a dick, but there's no reason to destroy the world"}
    good = {"text": "You broke my heart."}
    assert autocut.line_quality(good) > autocut.line_quality(frag)


def test_parse_script_line():
    assert autocut.parse_script_line("A bridge. | 0:25-0:45") == ("A bridge.", {"range": (25.0, 45.0), "people": []})
    assert autocut.parse_script_line("No range here") == ("No range here", None)
    assert autocut.parse_script_line("x | 1:02:03-1:02:10")[1]["range"] == (3723.0, 3730.0)
    assert autocut.parse_script_line("Old Thom. | @P2 8:00-9:48")[1] == {"range": (480.0, 588.0), "people": ["P2"]}


# ---------------------------------------------------------------- people clustering
def test_cluster_separates_people():
    import numpy as np
    import people
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=128), rng.normal(size=128)
    dets = []
    for base, n in ((a, 5), (b, 4)):
        for _ in range(n):
            f = base + 0.25 * rng.normal(size=128)
            dets.append({"feat": f / np.linalg.norm(f)})
    groups = sorted(people.cluster(dets), key=len, reverse=True)
    assert [len(g) for g in groups] == [5, 4]
    assert set(groups[0]) == set(range(5))


def test_person_box_stays_in_frame():
    import people
    box = people.person_box([0.9, 0.1, 0.1, 0.2])
    assert 0 <= box[0] < box[2] <= 1 and box[3] == 1.0


# ---------------------------------------------------------------- voice-over captions
def test_caption_chunks_cover_the_line():
    from tts import caption_chunks
    ch = caption_chunks("四十年后，机器人占领了阿姆斯特丹，一小队科学家决定改写过去。", 6.0)
    assert len(ch) == 3 and ch[0][1] == 0.0 and abs(ch[-1][2] - 6.0) < 0.01
    en = caption_chunks("Forty years later, robots rule the city and a few scientists try to rewrite the past.", 5.0)
    assert all(len(t) <= 55 for t, _, _ in en)


# ---------------------------------------------------------------- TTS providers
def _serve(handler_body):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        seen = {}

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            H.seen = {"auth": self.headers.get("Authorization"), "body": json.loads(self.rfile.read(n))}
            ctype, payload = handler_body()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, H


def _wav_bytes(tmp_path):
    import subprocess
    p = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", "1.2", "-i", "sine=frequency=440",
                    str(p)], check=True)
    return p.read_bytes()


def test_tts_http_provider_raw_and_json(tmp_path, monkeypatch):
    import base64
    import tts
    audio = _wav_bytes(tmp_path)
    monkeypatch.setenv("FAKE_TTS_KEY", "secret123")
    srv, H = _serve(lambda: ("audio/wav", audio))
    cfg = {"provider": "http", "url": f"http://127.0.0.1:{srv.server_port}/speak",
           "headers": {"Authorization": "Bearer ${FAKE_TTS_KEY}", "Content-Type": "application/json"},
           "body": {"input": "${text}", "voice": "${voice}", "speed": "${speed}"}, "voice": "narrator"}
    p, d = tts.synth("Hello there.", tmp_path / "out", config=cfg)
    assert abs(d - 1.2) < 0.15 and H.seen["auth"] == "Bearer secret123"
    assert H.seen["body"] == {"input": "Hello there.", "voice": "narrator", "speed": 1.0}
    srv.shutdown()

    srv, H = _serve(lambda: ("application/json",
                             json.dumps({"data": {"audio": base64.b64encode(audio).decode()}}).encode()))
    cfg2 = {**cfg, "url": f"http://127.0.0.1:{srv.server_port}/speak",
            "response": {"type": "json", "base64": "data.audio"}}
    p2, d2 = tts.synth("Second line.", tmp_path / "out", config=cfg2)
    assert p2 != p and abs(d2 - 1.2) < 0.15
    srv.shutdown()


def test_tts_missing_key_is_an_error(monkeypatch):
    import pytest
    import tts
    monkeypatch.delenv("NOPE_KEY", raising=False)
    with pytest.raises(SystemExit):
        tts._env("Bearer ${NOPE_KEY}")


def test_tts_config_layers(tmp_path, monkeypatch):
    import tts
    (tmp_path / "tts.json").write_text(json.dumps({"provider": "openai", "model": "m1", "voice": "alloy"}))
    monkeypatch.setattr(tts, "USER_CONFIG", tmp_path / "none.json")
    cfg = tts.load_config(tmp_path)
    assert cfg == {"provider": "openai", "model": "m1", "voice": "alloy"}


def test_tts_picks_provider_by_language():
    import tts
    cfg = {"provider": "system", "languages": {
        "zh": {"provider": "http", "url": "http://x/zh", "voice": "zh-female"},
        "en": {"voice": "Samantha"}}}
    zh = tts.for_language(cfg, tts.lang_of("四十年后，机器人占领了这座城市。"))
    en = tts.for_language(cfg, tts.lang_of("Forty years later, robots rule the city."))
    assert zh == {"provider": "http", "url": "http://x/zh", "voice": "zh-female"}
    assert en == {"provider": "system", "voice": "Samantha"}


# ---------------------------------------------------------------- brand, fillers, voice presets
def test_brand_signature_on_end_card(tmp_path):
    from brand import apply_brand
    edl = {"output": {"width": 1920, "height": 1080}, "brand": True,
           "clips": [{"src": "v1", "in": 0, "out": 2}, {"type": "color", "duration": 2}]}
    b = apply_brand(edl, tmp_path)
    sig = [t for t in edl["texts"] if t.get("_brand")]
    assert len(sig) == 1 and sig[0]["clip"] == -1
    assert b["name"] in sig[0]["text"] and b["url"] in sig[0]["text"]
    edl2 = {"clips": [{"type": "color", "duration": 2}], "brand": False}
    assert apply_brand(edl2, tmp_path) is None and "texts" not in edl2


def test_brand_job_override(tmp_path):
    from brand import load_brand
    (tmp_path / "brand.json").write_text(json.dumps({"name": "Acme", "signature": "by {name}"}))
    from brand import signature
    assert signature(load_brand(tmp_path)) == "by Acme"


def test_fillers_are_cut_out():
    from draft_edl import speech_spans_without_fillers
    w = lambda a, b, t: {"start": a, "end": b, "word": t}  # noqa: E731
    words = [w(0.0, 0.3, " Umm,"), w(0.5, 0.8, " so"), w(0.85, 1.2, " today,"), w(1.4, 1.6, " uh,"),
             w(1.8, 2.0, " we"), w(2.05, 2.4, " talk."), w(2.6, 2.8, " 嗯"), w(3.0, 3.4, " 好")]
    spans = speech_spans_without_fillers(words, pad=0.0)
    assert spans == [[0.5, 1.2], [1.8, 2.4], [3.0, 3.4]]


def test_voice_presets_exist():
    for k in ("clean", "denoise", "broadcast"):
        assert "afftdn" in render.VOICE[k]


def test_tts_env_file_and_model_list(tmp_path, monkeypatch):
    import http.server
    import threading
    import tts
    monkeypatch.delenv("FAKE_DUAER_KEY", raising=False)
    (tmp_path / "api.env").write_text("export FAKE_DUAER_KEY='k-123'\n# comment\n")
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["auth"] = self.headers.get("Authorization")
            body = json.dumps({"data": [{"id": "cosyvoice-v2", "owned_by": "x"}, {"id": "deepseek-chat"},
                                        {"id": "gpt-4o-mini-tts"}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = {"provider": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1/",
           "api_key_env": "FAKE_DUAER_KEY", "env_file": str(tmp_path / "api.env")}
    ms = {m["id"]: m["speech"] for m in tts.list_models(cfg)}
    srv.shutdown()
    assert seen["auth"] == "Bearer k-123"
    assert ms == {"cosyvoice-v2": True, "deepseek-chat": False, "gpt-4o-mini-tts": True}


def test_cjk_captions_break_between_phrases():
    from tts import caption_chunks
    prot = ["托姆", "西莉亚", "四十年"]
    lines = {"最后，托姆终于说出了那句迟到四十年的对不起。": None,
             "科学家们要做一件疯狂的事：把当年分手的那一刻重演一遍。": None}
    for text in lines:
        chunks = [c for c, _, _ in caption_chunks(text, 5, max_cjk=12, protect=prot)]
        assert "".join(chunks).replace("，", "").replace("：", "") == text.replace("，", "").replace("：", "")
        for c in chunks:
            assert not c.startswith(("的", "了")) and len(c) <= 15, chunks
        if "四十年" in text:
            assert any("四十年" in c for c in chunks), chunks


# ---------------------------------------------------------------- caption designs
def test_every_caption_style_renders():
    from captions import CAPTION_STYLES
    fp = find_font()
    for name in CAPTION_STYLES:
        img = render_text_block({"text": "四十年后 robots", "style": name, "highlight": ["robots"]}, 1920, 1080, fp)
        assert img.mode == "RGBA" and img.getbbox() is not None, name


def test_full_caption_highlight_is_dropped():
    fp = find_font()
    a = render_text_block({"text": "四十年后", "style": "variety", "highlight": ["四十年后"]}, 1920, 1080, fp)
    b = render_text_block({"text": "四十年后", "style": "variety"}, 1920, 1080, fp)
    assert a.tobytes() == b.tobytes()


def test_marked_lines_use_the_emphasis_style(tmp_path, monkeypatch):
    import tts
    monkeypatch.setattr(tts, "synth", lambda text, d, voice=None, rate=None, config=None: (tmp_path / "x.wav", 2.0))
    edl = {"narration": {"emphasis_style": "variety", "items": [
        {"text": "平常的一句话。", "at": 0}, {"text": "{四十年后}，一切都变了。", "at": 3}]}}
    render.prepare_narration(edl, tmp_path / ".render")
    caps = edl["subtitles"]["items"]
    assert [c.get("style") for c in caps] == [None, "variety"]  # the short line stays one caption
    assert edl["narration"]["items"][1]["text"] == "四十年后，一切都变了。"
    assert "四十年后" in edl["subtitles"]["highlight"]


# ---------------------------------------------------------------- music generator, follow captions
def test_music_generator_beats_and_level():
    import numpy as np
    import music
    st, info = music.generate("upbeat", 6.0, seed=3)
    assert st.shape[1] == 2 and abs(len(st) / music.SR - 6.0) < 0.05
    assert 0.5 < np.abs(st).max() <= 10 ** (-0.9 / 20)
    assert np.allclose(np.diff(info["beats"]), 0.5)  # 120 BPM grid
    for mood in music.MOODS:
        s2, i2 = music.generate(mood, 3.0)
        assert np.isfinite(s2).all() and i2["bpm"] > 0


def test_follow_words_map_display_to_source():
    times = [(0.0, 0.2), None, (0.4, 0.6), (0.6, 0.8)]
    words = render._follow_words("托　姆和", "托，姆和", times, 0.0)
    assert [(w["s"], w["dt0"]) for w in words] == [(0, 0.0), (2, 0.4), (3, 0.6)]


def test_follow_karaoke_dims_unspoken_words():
    fp = find_font()
    item = {"text": "四十年后", "style": "explainer", "karaoke": "follow",
            "words": [{"s": i, "e": i + 1, "t0": i * 0.2, "t1": (i + 1) * 0.2} for i in range(4)]}
    a = render_text_block(item, 1920, 1080, fp, kara=0)
    b = render_text_block(item, 1920, 1080, fp, kara=3)
    assert a.size == b.size and a.tobytes() != b.tobytes()


def test_beat_sync_does_not_drift(tmp_path):
    m = tmp_path / "bed.wav"
    m.write_bytes(b"")
    m.with_suffix(".beats.json").write_text(json.dumps({"beats": [k * 0.5 for k in range(80)]}))
    render.PLAN_FPS[0] = 30.0
    clips = [{"src": "v1", "in": 10.0 * k, "out": 10.0 * k + 0.55} for k in range(24)]
    edl = {"clips": clips, "music": [{"path": str(m)}], "beat_sync": {"music": 0, "every": 1, "shift": "auto"}}
    render.apply_beat_sync(edl, None, tmp_path)
    starts = render.plan_starts(edl["clips"])
    assert max(abs((s + 0.25) % 0.5 - 0.25) for s in starts) < 1 / 30 + 1e-6, starts


# ---------------------------------------------------------------- speed ramps
def test_ramp_duration_and_expression():
    c = {"src": "v1", "in": 10.0, "out": 13.0, "ramp": [[0, 1.0], [1.0, 0.25], [2.0, 0.25], [2.5, 1.0]]}
    steps = render.ramp_steps(c)
    assert abs(sum(dt for _, dt, _ in steps) - 3.0) < 1e-6
    out = sum(dt / sp for _, dt, sp in steps)
    assert 7.0 < out < 7.5  # exact integral of 1/speed: 1.85 + 4 + 0.92 + 0.5 = 7.27 s
    render.PLAN_FPS[0] = 24.0
    assert abs(render._clen_raw(c) - out) < 1e-6
    expr = render.ramp_setpts(steps)
    assert expr.startswith("(min(max(T-0.000,0)") and expr.endswith(")/TB")


def test_split_length_is_the_shortest_item():
    c = {"type": "split", "items": [{"src": "v1", "in": 0, "out": 3}, {"src": "v1", "in": 5, "out": 7}]}
    assert render._clen_raw(c) == 2.0
    assert render._clen_raw({**c, "duration": 1.5}) == 1.5


# ---------------------------------------------------------------- auto drafts: four more styles
def _shot(t, dur=5.0, **k):
    return {"vid": "v1", "start": t, "end": t + dur, "score": 0.5, "motion": 0.03, "sharpness": 5.0,
            "colorfulness": 0.5, "brightness": 0.3, "faces": 1, "face_area": 0.05, **k}


def _feat():
    shots = [_shot(10.0 * i) for i in range(30)]
    return {"all": shots, "establishing": [dict(s, faces=0) for s in shots[:3]], "action": shots[3:12],
            "closeups": shots[12:18], "portraits_static": shots[18:24], "dark_neon": shots[20:30],
            "speech_ratio": 0.8,
            "people": [{"id": "P1", "name": "西莉亚  CELIA", "appearances": [{"vid": "v1", "t": 182.0, "area": 0.05}]},
                       {"id": "P2", "name": "", "appearances": [{"vid": "v1", "t": 192.0, "area": 0.06}]},
                       {"id": "P3", "name": "THOM", "appearances": [{"vid": "v1", "t": 202.0, "area": 0.04}]}]}


def _opts(**k):
    return {"title": "Steel", "credit": "", "music": None, **k}


def test_title3d_recipe():
    edl, notes = autocut.r_title3d(_feat(), autocut.Picker(), _opts())
    assert edl["clips"][0]["src"] == "v1" and len(edl["clips"]) == 6
    t = edl["texts"][0]
    assert t["style"] == "text3d" and t["anim"] == "flip_in" and t["end"] > t["start"] + 2
    assert notes == []


def test_text_behind_recipe_uses_names(monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: object() if n == "rembg" else real(n, *a))
    edl, notes = autocut.r_text_behind(_feat(), autocut.Picker(), _opts())
    texts = [c["effects"][0]["text"] for c in edl["clips"] if c.get("effects")]
    assert sorted(texts) == ["CELIA", "THOM"]
    assert all(c["effects"][0]["type"] == "text_behind" for c in edl["clips"] if c.get("effects"))


def test_text_behind_without_rembg_names_the_reason(monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: None if n == "rembg" else real(n, *a))
    edl, notes = autocut.r_text_behind(_feat(), autocut.Picker(), _opts())
    assert edl is None and "rembg" in notes[0]


def test_fx3d_recipe_and_missing_dependency(monkeypatch):
    import fx3d
    monkeypatch.setattr(fx3d, "available", lambda: None)
    edl, _ = autocut.r_fx3d(_feat(), autocut.Picker(), _opts())
    assert [o["fx"] for o in edl["overlays"]][:4] == ["particles", "hologram", "streaks", "grid"]
    monkeypatch.setattr(fx3d, "available", lambda: "three.js effects need Node.js")
    edl, notes = autocut.r_fx3d(_feat(), autocut.Picker(), _opts())
    assert edl is None and notes == ["three.js effects need Node.js"]


def test_talking_recipe_cuts_fillers_and_pauses():
    w = lambda a, b, t: {"start": a, "end": b, "word": t}  # noqa: E731
    words = [w(0.5, 0.8, " Um,"), w(1.0, 1.3, " so"), w(1.35, 1.8, " today"), w(1.9, 2.1, " uh"),
             w(2.3, 2.6, " we"), w(2.65, 3.1, " talk."), w(5.0, 5.4, " Next"), w(5.45, 5.9, " step"),
             w(5.95, 6.4, " is"), w(6.45, 7.0, " editing.")]
    an = {"videos": [{"id": "v1", "duration": 8.0, "transcript": {"segments": [{"words": words}]}}]}
    edl, notes = autocut.r_talking(_feat(), autocut.Picker(), _opts(analysis=an))
    pieces = [(c["in"], c["out"]) for c in edl["clips"] if c.get("src")]
    assert pieces[0][0] >= 0.9 and all(not (a < 2.0 < b) for a, b in pieces)  # "Um" and "uh" gone
    assert edl["audio"]["voice"] == "clean" and edl["subtitles"]["from"] == "transcript"
    assert edl["output"]["height"] > edl["output"]["width"]
    assert any("2 filler words cut" in n for n in notes)
    edl, notes = autocut.r_talking(_feat(), autocut.Picker(), _opts(analysis={"videos": []}))
    assert edl is None and "transcript" in notes[0]


def test_every_menu_style_has_a_recipe():
    import styles
    from pathlib import Path
    feats = styles.features({"videos": []}, Path("/nonexistent"))
    menu = {s["key"] for s in styles.catalogue(feats)}
    assert menu <= set(autocut.RECIPES)


def test_talking_recipe_keeps_every_file_of_a_talk():
    w = lambda a, b, t: {"start": a, "end": b, "word": t}  # noqa: E731
    seg = lambda off: {"words": [w(off + 0.2, off + 0.6, " one"), w(off + 0.65, off + 1.1, " two"),  # noqa: E731
                                 w(off + 1.15, off + 1.6, " three.")]}
    an = {"videos": [{"id": f"v{k}", "duration": 3.0, "transcript": {"segments": [seg(0.0)]}} for k in (1, 2, 3)]}
    edl, _ = autocut.r_talking(_feat(), autocut.Picker(), _opts(analysis=an))
    assert [c["src"] for c in edl["clips"] if c.get("src")] == ["v1", "v2", "v3"]


def test_title3d_skips_openers_too_short_for_the_title():
    f = _feat()
    f["establishing"] = [dict(s, end=s["start"] + 0.9, faces=0) for s in f["establishing"]]
    f["all"] = f["establishing"] + [_shot(400.0, 6.0)]
    edl, notes = autocut.r_title3d(f, autocut.Picker(), _opts())
    c0, t = edl["clips"][0], edl["texts"][0]
    assert c0["out"] - c0["in"] >= 2.8 and t["end"] - t["start"] >= 2.0
    assert "blurred" in notes[0]
    f["all"] = f["establishing"]
    assert autocut.r_title3d(f, autocut.Picker(), _opts())[0] is None
