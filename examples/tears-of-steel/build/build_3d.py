"""3D titles, text behind people and three.js light effects (16:9 full frame, ~22 s)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tos import check_shots, end_card, v, write  # noqa: E402

A = {"focus": "track", "volume": 0.4}
clips = [
    v(70.0, 74.0, **{**A, "focus": "auto"}, effects=[{"type": "zoom_in", "amount": 0.06}]),     # city
    v(357.0, 361.0, **A, transition={"type": "fade", "duration": 0.4},
      effects=[{"type": "text_behind", "text": "CELIA", "start": 0.3, "end": 4.0, "style": "text3d",
                "size": 0.3, "pos": [0.5, 0.4]}]),                                               # Thom, Celia
    v(51.2, 53.6, **A, transition={"type": "fade", "duration": 0.3}),                             # the lab
    v(471.0, 474.5, **{**A, "focus": "auto"}),                                                    # battle
    v(364.6, 367.6, **A, transition={"type": "fade", "duration": 0.3},
      effects=[{"type": "text_behind", "text": "RESIST", "start": 0.0, "end": 3.0, "style": "title",
                "size": 0.3, "stroke": 0, "shadow": False, "anim": "fade", "pos": [0.5, 0.36],
                "tracking": 0.08}]),                                                              # Djenghis
    v(537.8, 541.5, **{**A, "focus": "auto"}, transition={"type": "pixelize", "duration": 0.3}),  # overwrite
]
card, credit = end_card(2.6)
clips.append(card)
edl = {
    "analysis": "work/analysis.json",
    "output": {"path": "out/tos_3d_effects_16x9.mp4", "width": 1920, "height": 1080, "fps": 24, "fit": "crop"},
    "grade": "trailer",
    "clips": clips,
    "music": [{"path": "audio/score_credits.wav", "offset": 0.0, "volume": 0.85, "loop": False,
               "fade_in": 0.5, "fade_out": 2.5}],
    "overlays": [
        {"fx": "particles", "clip": 0, "start": 0, "params": {"color": "#FFB25C", "count": 320}},
        {"fx": "hologram", "clip": 2, "start": 0.1, "params": {"pos": [0.76, 0.45], "scale": 0.8}},
        {"fx": "streaks", "clip": 3, "start": 0, "params": {"colors": ["#7FD4FF", "#FFB36B"], "count": 8}},
        {"fx": "grid", "clip": 5, "start": 0, "opacity": 0.85, "params": {"color": "#FF2BD6"}},
    ],
    "sounds": [{"sfx": "boom", "clip": 0, "start": 0.4, "volume": 0.6},
               {"sfx": "whoosh", "clip": 3, "start": 0, "volume": 0.4},
               {"sfx": "glitch", "clip": 5, "start": 0, "volume": 0.35}],
    "texts": [
        {"text": "钢铁之泪", "clip": 0, "start": 0.4, "end": 3.9, "style": "text3d", "size": 0.17, "pos": [0.5, 0.45]},
        {"text": "TEARS OF STEEL", "clip": 0, "start": 1.0, "end": 3.9, "style": "card", "size": 0.035,
         "pos": [0.5, 0.64], "tracking": 0.4},
        {"text": "SYSTEM REBOOT", "clip": 5, "start": 0.6, "end": 3.6, "style": "neon", "size": 0.085,
         "glow": "#FF2BD6", "color": "#FFF0FB", "pos": [0.5, 0.3]},
        credit,
    ],
}
if check_shots(clips):
    print("warning: some clips cross a cut")
write(edl, "edl_3d.json")
