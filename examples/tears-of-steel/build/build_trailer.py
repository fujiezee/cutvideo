"""Tears of Steel trailer v2: continuous score bed, beat-synced action, J/L cuts, SFX, cards.

Music: film credits score (audio/score_credits.wav). Key events in score time:
  dip 71.2-73.9, hit 74.0 (drop), hit 94.8, hit 99.1 (title). Beats detected on off-beats -> shift -0.25.
Output time t maps to score time t + OFFSET.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tos import ATTRIBUTION, check_shots, write  # noqa: E402

DROP_SCORE, TITLE_SCORE, HIT2_SCORE = 74.0, 99.05, 94.8
WARM = {"temperature": 5600, "temperature_mix": 0.35}          # cold future shots, pulled toward neutral
COOL = {"temperature": 7600, "temperature_mix": 0.4}           # gold battle shots, pulled toward neutral


def v(a, b, **x):
    return {"src": "v1", "in": a, "out": b, **x}


DIALOG = {"duck_music": True, "snap": False}
AMB = {"volume": 0.35}

act1 = [
    v(5.2, 12.2, effects=[{"type": "fade_in", "duration": 0.6}], **DIALOG),                  # countdown -> ignition
    v(14.0, 16.6, volume=0.8,
      effects=[{"type": "zoom_in", "amount": 0.08}, {"type": "flash", "duration": 0.3}]),                                         # engines
    {"type": "color", "color": "#000000", "duration": 2.8,
     "transition": {"type": "fadeblack", "duration": 0.5}},                                    # card: 40 years ago
    v(315.45, 319.95, volume=1.8, audio_tail=0.12, **DIALOG),                                                                # Look Celia...
    v(320.6, 322.2, volume=2.2, audio_tail=0.25, **DIALOG),                                                                # You have your robotics
    v(325.4, 327.7, volume=1.8, **DIALOG),                                                                 # awesome in space
    v(346.45, 350.1, audio_lead=0.2, **DIALOG),                                                # robot hand?
    v(42.1, 45.5, transition={"type": "fadeblack", "duration": 0.8}, volume=0.5),              # 40 YEARS LATER
    v(70.0, 73.6, transition={"type": "fade", "duration": 0.5}, adjust=WARM, **AMB,
      effects=[{"type": "zoom_in", "amount": 0.1}]),                                          # city
    v(82.2, 84.6, adjust=WARM, **AMB),                                                         # sniper
    v(109.0, 111.4, adjust={**WARM, "brightness": 0.03}, **AMB),                               # scope
    v(49.8, 52.0, **DIALOG),                                                                   # Robot's memory synced
]
action1 = [
    v(462.0, 463.0, adjust=COOL, **AMB),
    v(465.2, 466.2, adjust={**COOL, "brightness": 0.04}, **AMB, effects=[{"type": "shake", "amount": 0.02, "end": 0.5}]),
    v(454.6, 455.6, adjust={**COOL, "brightness": 0.03}, **AMB),
    v(468.4, 469.3, adjust={**COOL, "temperature": 8200}, **AMB, effects=[{"type": "flash", "duration": 0.12}]),
    v(471.5, 472.5, adjust=COOL, **AMB),
    v(488.1, 489.1, adjust={**COOL, "brightness": -0.03}, **AMB),
    v(489.7, 490.7, adjust={**COOL, "brightness": 0.05}, **AMB),
    v(493.0, 494.0, adjust=COOL, **AMB, effects=[{"type": "shake", "amount": 0.02, "end": 0.4}]),
    v(499.8, 500.8, adjust=COOL, **AMB),
    v(502.4, 503.4, adjust={**COOL, "temperature": 8200}, **AMB, effects=[{"type": "flash", "duration": 0.12}]),
]
emotion = [
    v(360.65, 362.95, transition={"type": "fadeblack", "duration": 0.4}, **DIALOG),            # destroy the world
    v(383.13, 385.0, **DIALOG),                                                                # broke my heart
    v(386.97, 388.9, audio_lead=0.12, **DIALOG),                                               # forgot me on Earth
]
action2 = [
    v(522.0, 523.0, adjust=COOL, **AMB, effects=[{"type": "glitch", "duration": 0.2}]),
    v(474.0, 475.0, adjust=COOL, **AMB),
    v(496.0, 497.0, adjust=COOL, **AMB),
    v(466.6, 467.6, adjust={**COOL, "brightness": 0.04}, **AMB, effects=[{"type": "punch", "scale": 1.15}]),
    v(476.0, 477.0, adjust=COOL, **AMB),
    v(523.4, 525.0, adjust=COOL, volume=0.5, start_at_music=HIT2_SCORE, snap=False,
      effects=[{"type": "zoom_in", "amount": 0.06}]),                                         # on hit 94.8
]
pretitle = [
    v(532.0, 534.6, transition={"type": "fadeblack", "duration": 0.4},
      adjust={"brightness": 0.04, **COOL}, volume=0.5, snap=False),                            # old Thom, quiet
    {"type": "color", "color": "#000000", "duration": 0.6, "snap": False},                      # beat of silence before title (pinned)
]
title = [
    v(66.0, 69.8, effects=[{"type": "fade_out", "duration": 0.8}], volume=0.0, snap=False,
      start_at_music=TITLE_SCORE),                                                              # TEARS OF STEEL
    v(390.6, 392.4, audio_lead=0.3, **DIALOG),                                                  # button: crush you
    {"type": "color", "color": "#000000", "duration": 3.8, "transition": {"type": "fadeblack", "duration": 0.3}},
]
clips = act1 + action1 + emotion + action2 + pretitle + title
I_A1, I_E, I_A2, I_PT, I_T = len(act1), len(act1) + len(action1), len(act1) + len(action1) + len(emotion), \
    len(act1) + len(action1) + len(emotion) + len(action2), len(act1) + len(action1) + len(emotion) + len(action2) + len(pretitle)


def clip_len(c):
    return c["duration"] if c.get("type") == "color" else c["out"] - c["in"]


def starts(cl):
    acc, out = 0.0, []
    for k, c in enumerate(cl):
        if k and (c.get("transition") or {}).get("type", "cut") != "cut":
            acc -= c["transition"]["duration"]
        out.append(acc)
        acc += clip_len(c)
    return out


st = starts(clips)
drop_t = st[I_A1]
OFFSET = round(DROP_SCORE - drop_t, 3)  # score time at output 0
title_target = TITLE_SCORE - OFFSET
hit2_target = HIT2_SCORE - OFFSET

lines = [  # (clip index, src start, src end, chinese, english)
    (0, 5.2, 9.6, "主引擎启动……四、三、二、一", "We have main engine start... four, three, two, one"),
    (3, 315.5, 320.1, "听着，西莉亚，我们都得追随自己的热爱", "Look Celia, we have to follow our passions"),
    (4, 321.1, 322.2, "你有你的机器人研究", "You have your robotics"),
    (5, 325.5, 327.6, "而我只想在太空闯出一番名堂", "And I just want to be awesome in space"),
    (6, 346.3, 350.0, "你就承认吧，你是被我的机械手吓到了", "Why don't you just admit that you're freaked out by my robot hand?"),
    (11, 49.85, 52.0, "机器人记忆已同步", "Robot's memory synced"),
    (I_E, 360.7, 362.9, "……但这不是毁灭世界的理由", "...but there's no reason to destroy the world"),
    (I_E + 1, 383.13, 384.95, "你伤透了我的心", "You broke my heart"),
    (I_E + 2, 386.87, 388.85, "你把我丢在了地球", "You forgot me on Earth"),
    (I_T + 1, 390.3, 392.35, "我真该捏碎你", "I should just crush you"),
]
SUB = {"size": 0.034, "pos": [0.5, 0.985], "stroke": 0.06, "line_spacing": 1.28, "line_colors": ["#FFFFFF", "#C8C8C8"]}
items = [{"text": f"{zh}\n{en}", "clip": ci, "src_start": a, "src_end": b, **SUB} for ci, a, b, zh, en in lines]

texts = [
    {"text": "四十年前，他离开了她", "clip": 2, "start": 0.35, "end": 2.6, "style": "card"},
    {"text": "她回来了", "clip": 7, "start": 1.0, "end": 3.3, "style": "card", "size": 0.045, "pos": [0.5, 0.62]},
    {"text": "钢 铁 之 泪", "clip": I_T, "start": 1.6, "end": 4.2, "style": "card", "size": 0.04, "pos": [0.5, 0.6],
     "tracking": 0.3},
    {"text": "Tears of Steel (2012) · Blender Foundation", "clip": -1, "start": 0.3, "end": 3.6, "style": "subtitle",
     "size": 0.032, "pos": [0.5, 0.44]},
    {"text": "CC BY 3.0 · mango.blender.org", "clip": -1, "start": 0.6, "end": 3.6, "style": "subtitle", "size": 0.025,
     "color": "#BBBBBB", "pos": [0.5, 0.52]},
    {"text": "预告剪辑 · Claude", "clip": -1, "start": 0.9, "end": 3.6, "style": "subtitle", "size": 0.023,
     "color": "#888888", "pos": [0.5, 0.6]},
]

sounds = [
    {"sfx": "sub_drop", "clip": 0, "start": 3.65, "volume": 0.5},                    # ignition
    {"sfx": "hit", "clip": 2, "start": 0.35, "volume": 0.45},                        # card appears
    {"sfx": "boom", "clip": 7, "start": 0.0, "volume": 0.6},                         # 40 years later
    {"sfx": "swell", "clip": I_A1, "start": -2.0, "volume": 0.45},                   # into the drop
    {"sfx": "boom", "clip": I_A1, "start": 0.0, "volume": 0.8},                      # drop
    {"sfx": "whoosh", "clip": I_A1 + 3, "start": -0.35, "volume": 0.4},
    {"sfx": "whoosh", "clip": I_A1 + 6, "start": -0.35, "volume": 0.4},
    {"sfx": "hit", "clip": I_A1 + 9, "start": 0.0, "volume": 0.5},
    {"sfx": "sub_drop", "clip": I_E, "start": 0.0, "volume": 0.5},                   # into emotion
    {"sfx": "glitch", "clip": I_A2, "start": 0.0, "volume": 0.3},
    {"sfx": "hit", "clip": I_A2 + 5, "start": 0.0, "volume": 0.8},                   # hit 94.8
    {"sfx": "riser", "clip": I_T, "start": -3.45, "volume": 0.5, "params": {"duration": 3.0}},
    {"sfx": "boom", "clip": I_T, "start": 0.0, "volume": 0.9, "params": {"duration": 4.0}},  # title
]

edl = {
    "analysis": "work/analysis.json",
    "output": {"path": "out/tos_trailer_16x9.mp4", "width": 1920, "height": 1080, "fps": 24,
               "fit": "pad", "crf": 18},
    "grade": "trailer",
    "clips": clips,
    "music": [{"path": "audio/score_credits.wav", "offset": OFFSET, "volume": 0.85, "loop": False,
               "duck": True, "duck_threshold": 0.04, "duck_ratio": 6, "fade_in": 1.5, "fade_out": 3.0}],
    "beat_sync": {"music": 0, "every": 2, "shift": -0.25, "offset_beats": 1, "clips": [I_A1, I_PT - 1]},
    "sounds": sounds,
    "texts": texts,
    "subtitles": {"enabled": True, "from": "manual", "items": items},
}
if check_shots(clips):
    print("warning: some clips cross a cut")
write(edl, "edl_trailer.json")

# vertical version: same edit, blurred-background fill, subtitles under the picture
import copy  # noqa: E402
vert = copy.deepcopy(edl)
vert["output"].update({"path": "out/tos_trailer_9x16.mp4", "width": 1080, "height": 1920, "fit": "blur"})
for it in vert["subtitles"]["items"]:
    it.update({"pos": [0.5, 0.7], "size": 0.036})
for t in vert["texts"]:
    if t["text"] == "她回来了":
        t["pos"] = [0.5, 0.6]
write(vert, "edl_trailer_9x16.json")
print(f"clips={len(clips)} drop_t={drop_t:.2f} offset={OFFSET} title_target={title_target:.2f} hit2_target={hit2_target:.2f}")
print("planned starts:", {k: round(st[i], 2) for k, i in
                          [("action1", I_A1), ("emotion", I_E), ("action2", I_A2), ("pretitle", I_PT), ("title", I_T)]})
