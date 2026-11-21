"""Demo: 9:16 breakup scene with word-by-word (karaoke) subtitles and two character intros."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tos import ATTRIBUTION, check_shots, write  # noqa: E402

clips = [
    {"src": "v1", "in": 315.6, "out": 319.95, "audio_tail": 0.12, "focus": [0.36, 0.5], "duck_music": True,
     "effects": [{"type": "intro", "at": 0.5, "hold": 1.6, "name": "托姆", "role": "THOM · 想去太空", "color": "#FF9F0A"}]},
    {"src": "v1", "in": 320.0, "out": 322.2, "focus": [0.68, 0.45], "audio_tail": 0.2, "duck_music": True,
     "effects": [{"type": "intro", "at": 0.6, "hold": 1.6, "name": "西莉亚", "role": "CELIA · 机器人工程师",
                  "color": "#FF375F"}]},
    {"src": "v1", "in": 325.2, "out": 328.3, "focus": [0.4, 0.5], "duck_music": True, "volume": 1.6},
    {"src": "v1", "in": 346.46, "out": 350.1, "focus": [0.57, 0.5], "audio_lead": 0.2, "duck_music": True},
    {"src": "v1", "in": 36.8, "out": 40.24, "focus": [0.36, 0.5], "duck_music": True, "volume": 1.4,
     "effects": [{"type": "punch", "scale": 1.12, "start": 1.6}]},
    {"src": "v1", "in": 40.26, "out": 42.6, "focus": [0.35, 0.5], "duck_music": True, "volume": 1.4,
     "effects": [{"type": "zoom_in", "amount": 0.12}]},
    {"type": "color", "color": "#000000", "duration": 1.7},                     # "We're done." over black
    {"type": "color", "color": "#000000", "duration": 2.6, "transition": {"type": "fadeblack", "duration": 0.3}},
]
edl = {
    "analysis": "work/analysis.json",
    "output": {"path": "out/tos_karaoke_9x16.mp4", "width": 1080, "height": 1920, "fps": 24,
               "fit": "crop"},
    "grade": "natural",
    "clips": clips,
    "music": [{"path": "audio/score_credits.wav", "offset": 44.0, "volume": 0.35, "duck": True, "loop": False,
               "fade_in": 1.0, "fade_out": 2.0}],
    "sounds": [{"path": "v1", "in": 43.8, "out": 45.0, "clip": 6, "start": 0.15, "volume": 1.4,
                "fade_in": 0.05, "fade_out": 0.2}],
    "subtitles": {"enabled": True, "from": "transcript", "karaoke": "word", "karaoke_box": "#FFE600",
                  "size": 0.064, "pos": [0.5, 0.74], "max_chars": 13, "strip_punct": False, "stroke": 0.08,
                  "items": [{"text": "We're done.", "clip": 6, "start": 0.3, "end": 1.4}]},  # words spread evenly
    "texts": [
        {"text": "Tears of Steel (2012) · Blender Foundation", "clip": -1, "start": 0.3, "end": 2.6,
         "style": "subtitle", "size": 0.03, "pos": [0.5, 0.47]},
        {"text": "CC BY 3.0 · mango.blender.org", "clip": -1, "start": 0.5, "end": 2.6, "style": "subtitle",
         "size": 0.026, "color": "#BBBBBB", "pos": [0.5, 0.52]},
    ],
}
if check_shots(clips):
    print("warning: some clips cross a cut")
write(edl, "edl_karaoke.json")
