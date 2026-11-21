"""Fast checks of the pure logic: no footage, no rendering."""
from __future__ import annotations

import copy

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
    ranked = [{"key": "fx3d", "score": 90}, {"key": "trailer", "score": 80}, {"key": "vhs", "score": 50},
              {"key": "silent", "score": 70}]
    assert autocut.choose("top2", ranked) == ["trailer", "vhs"]
    assert autocut.choose("recommended", ranked) == ["trailer", "silent"]
    assert autocut.choose("vhs,cast", ranked) == ["vhs", "cast"]


def test_line_quality_prefers_whole_sentences():
    frag = {"text": "And a dick, but there's no reason to destroy the world"}
    good = {"text": "You broke my heart."}
    assert autocut.line_quality(good) > autocut.line_quality(frag)
