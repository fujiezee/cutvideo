"""Beat-sync vertical mix (9:16, ~22 s): a cut on every beat, accents on the strong ones."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tos import check_shots, end_card, v, write  # noqa: E402

A = {"focus": "auto", "volume": 0.25}
FLASH = [{"type": "flash", "duration": 0.12}]
SHAKE = [{"type": "shake", "amount": 0.025, "end": 0.35}]
clips = [
    v(71.0, 72.0, **A, effects=[{"type": "zoom_in", "amount": 0.1}]),     # city under robot rule (2 beats)
    v(83.0, 84.0, **A),                                                     # sniper
    v(105.0, 106.0, **A),                                                   # ship in the scope
    v(317.0, 317.5, **A),                                                   # Thom
    v(321.0, 321.5, **A),                                                   # Celia
    v(347.0, 347.5, **A, effects=FLASH),                                    # Celia, angry
    v(46.0, 46.5, **A),                                                     # hologram robots
    v(50.0, 50.5, **A),                                                     # lab controls
    v(59.0, 59.5, **A),                                                     # the brain
    v(297.6, 298.1, **A),                                                   # overwrite
    v(299.2, 299.7, **A),                                                   # robot wakes
    v(455.0, 455.5, **A, effects=SHAKE),                                    # battle starts
    v(462.2, 462.7, **A),
    v(465.4, 465.9, **A),
    v(468.5, 469.0, **A, effects=FLASH),
    v(471.0, 471.5, **A),
    v(474.5, 475.0, **A),
    v(480.4, 480.9, **A),
    v(483.6, 484.1, **A, effects=SHAKE),
    v(488.2, 488.7, **A),
    v(492.0, 492.5, **A),
    v(495.5, 496.0, **A),
    v(500.0, 500.5, **A),
    v(502.6, 503.1, **A, effects=FLASH),                                    # Barley yells
    v(374.2, 374.7, **A),                                                   # scientist screams
    v(384.0, 385.0, **A, effects=[{"type": "punch", "scale": 1.15}]),     # "You broke my heart" (2 beats)
    v(390.5, 391.5, **A),                                                   # "crush you" (2 beats)
    v(522.0, 523.0, **A, effects=[{"type": "zoom_in", "amount": 0.1}]),
    v(529.0, 530.0, **A, effects=[{"type": "fade_out", "duration": 0.5}]),
]
last = len(clips) - 1
card, credit = end_card(2.6, transition=None)
clips.append(card)
edl = {
    "analysis": "work/analysis.json",
    "output": {"path": "out/tos_beatmix_9x16.mp4", "width": 1080, "height": 1920, "fps": 30, "fit": "crop"},
    "grade": "vivid",
    "clips": clips,
    "music": [{"path": "audio/score_credits.wav", "offset": 0.0, "volume": 0.95, "loop": False,
               "fade_in": 0.2, "fade_out": 2.5}],
    "beat_sync": {"music": 0, "every": 1, "shift": "auto", "clips": [0, last]},
    "sounds": [{"sfx": "hit", "clip": 0, "start": 0.0, "volume": 0.6},
               {"sfx": "boom", "clip": 11, "start": 0.0, "volume": 0.6},
               {"sfx": "whoosh", "clip": 25, "start": -0.3, "volume": 0.4}],
    "texts": [
        {"text": "40年后\n机器人占领了阿姆斯特丹", "clip": 0, "start": 0.05, "end": 2.6, "style": "highlight",
         "size": 0.075, "pos": [0.5, 0.3], "rotate": 0, "line_colors": ["#FFFFFF", "#FFE600"]},
        {"text": "钢铁之泪", "clip": last, "start": 0.0, "end": 1.0, "style": "card", "size": 0.09},
        credit,
    ],
}
if check_shots(clips):
    print("warning: some clips cross a cut")
write(edl, "edl_beatmix.json")
