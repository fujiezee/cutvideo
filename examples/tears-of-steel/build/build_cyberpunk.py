"""Cyberpunk glitch (16:9 full frame, ~32 s): the film's lab and memory-hacking side."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tos import check_shots, end_card, v, write  # noqa: E402

A = {"focus": "auto", "volume": 0.35}
G = [{"type": "glitch", "duration": 0.22}]
RGB = [{"type": "rgb_split"}]
ECHO = [{"type": "echo"}]
clips = [
    v(45.8, 47.8, **A, effects=[{"type": "fade_in", "duration": 0.3}] + ECHO),   # hologram robots
    v(48.0, 49.6, **A, effects=G),                                                 # control panel
    v(50.0, 51.0, **A),
    v(51.3, 53.3, **{**A, "volume": 1.0}, effects=RGB, duck_music=True),                 # "Robot's memory synced"
    v(57.0, 59.0, **A, effects=[{"type": "zoom_in", "amount": 0.12}]),           # the brain
    v(295.2, 296.4, **A, effects=G, transition={"type": "pixelize", "duration": 0.3}),  # PLAYBACK
    v(296.6, 297.4, **A),
    v(297.6, 298.9, **A, effects=RGB),                                             # overwrite 004.77 %
    v(299.1, 300.8, **A, effects=ECHO),                                            # robot wakes
    v(303.0, 305.0, **A, effects=G),
    v(307.8, 310.4, **A, effects=RGB + ECHO),                                      # glitching robot
    v(371.7, 373.8, **A, transition={"type": "hlslice", "duration": 0.3}),        # the Captain
    v(374.0, 375.9, **A, effects=G),                                               # scientist screams
    v(376.1, 377.1, **A),                                                          # PLAYBACK
    v(378.1, 379.9, **A, effects=G + RGB),                                         # ERROR
    v(380.1, 383.0, **A, effects=[{"type": "shake", "amount": 0.02}]),
    v(418.9, 421.5, **A, transition={"type": "pixelize", "duration": 0.3}),      # tech head
    v(425.6, 427.7, **A, effects=ECHO),
    v(537.8, 539.1, **A, effects=RGB),                                             # overwrite 099.78 %
    v(539.3, 541.5, **A, effects=G),
    v(543.5, 547.0, **A, effects=[{"type": "fade_out", "duration": 0.8}]),       # old Thom and the robot
]
last = len(clips) - 1
card, credit = end_card(2.6)
clips.append(card)
NEON_RED = {"style": "neon", "glow": "#FF2D55", "color": "#FFF0F3"}
edl = {
    "analysis": "work/analysis.json",
    "output": {"path": "out/tos_cyberpunk_16x9.mp4", "width": 1920, "height": 1080, "fps": 30, "fit": "crop"},
    "grade": "cyberpunk",
    "clips": clips,
    "music": [{"path": "audio/score_credits.wav", "offset": 0.0, "volume": 0.9, "duck": True, "loop": False,
               "fade_in": 0.5, "fade_out": 2.5}],
    "beat_sync": {"music": 0, "every": 2, "shift": "auto", "clips": [0, last - 1]},
    "sounds": [{"sfx": "glitch", "clip": i, "start": 0.0, "volume": 0.35}
               for i, c in enumerate(clips) if any(e.get("type") == "glitch" for e in c.get("effects", []))]
              + [{"sfx": "boom", "clip": 14, "start": 0.0, "volume": 0.55}],
    "subtitles": {"enabled": True, "from": "manual", "items": [
        {"text": "ROBOT'S MEMORY SYNCED", "clip": 3, "src_start": 51.3, "src_end": 52.6, "style": "caption",
         "font": "mono", "color": "#B8FFF6", "size": 0.04, "pos": [0.5, 0.88]}]},
    "texts": [
        {"text": "MEMORY OVERWRITE", "clip": 0, "start": 0.3, "end": 2.0, "style": "neon", "size": 0.09},
        {"text": "SYSTEM ERROR", "clip": 14, "start": 0.05, "end": 1.6, **NEON_RED, "size": 0.1},
        {"text": "REBOOT", "clip": last, "start": 0.6, "end": 2.6, "style": "neon", "size": 0.08,
         "pos": [0.5, 0.82], "tracking": 0.25},
        credit,
    ],
}
if check_shots(clips):
    print("warning: some clips cross a cut")
write(edl, "edl_cyberpunk.json")
