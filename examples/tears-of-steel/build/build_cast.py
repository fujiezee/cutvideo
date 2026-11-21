"""Demo: freeze-frame character intros for the Tears of Steel cast."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tos import ATTRIBUTION, check_shots, write  # noqa: E402

cast = [  # in, out, freeze at (clip seconds), name, role, accent, person box
    (315.6, 319.9, 1.6, "托姆  THOM", "四十年前 · 想去太空的人", "#FF9F0A", [0.12, 0.0, 0.62, 1.0]),
    (320.0, 322.2, 1.0, "西莉亚  CELIA", "机器人工程师", "#FF375F", [0.42, 0.0, 0.95, 1.0]),
    (140.0, 143.2, 1.2, "巴利  BARLEY", "狙击手", "#0A84FF", [0.3, 0.12, 0.93, 1.0]),
    (427.9, 431.4, 1.0, "船长  CAPTAIN", "抵抗军指挥官", "#FF453A", [0.4, 0.0, 0.93, 1.0]),
    (419.6, 422.8, 1.2, "技术员  TECH HEAD", "负责记忆重写", "#BF5AF2", [0.5, 0.0, 0.95, 1.0]),
    (364.6, 367.6, 1.0, "詹吉斯  DJENGHIS", "战士", "#30D158", [0.45, 0.08, 0.88, 1.0]),
]
clips = [{"type": "color", "color": "#000000", "duration": 2.2}]
for k, (a, b, at, name, role, col, box) in enumerate(cast):
    clips.append({"src": "v1", "in": a, "out": b, "volume": 0.6,
                  "effects": [{"type": "intro", "at": at, "hold": 1.8, "name": name, "role": role,
                               "color": col, "box": box, "index": f"{k + 1:02d}", "bg": "bw"}]})
clips[1]["transition"] = {"type": "fadeblack", "duration": 0.4}
clips.append({"type": "color", "color": "#000000", "duration": 3.0, "transition": {"type": "fadeblack", "duration": 0.4}})

edl = {
    "analysis": "work/analysis.json",
    "output": {"path": "out/tos_cast_16x9.mp4", "width": 1920, "height": 1080, "fps": 24, "fit": "pad"},
    "grade": "trailer",
    "clips": clips,
    "music": [{"path": "audio/score_credits.wav", "offset": 2.0, "volume": 0.7, "loop": False,
               "fade_in": 1.0, "fade_out": 2.5}],
    "sounds": [{"sfx": "hit", "clip": 0, "start": 0.3, "volume": 0.5}],
    "texts": [
        {"text": "钢铁之泪", "clip": 0, "start": 0.3, "end": 2.0, "style": "card", "pos": [0.5, 0.46]},
        {"text": "人 物 介 绍", "clip": 0, "start": 0.6, "end": 2.0, "style": "card", "size": 0.03,
         "pos": [0.5, 0.56], "color": "#BBBBBB"},
        {"text": "Tears of Steel (2012) · Blender Foundation · CC BY 3.0", "clip": -1, "start": 0.3, "end": 3.0,
         "style": "subtitle", "size": 0.026, "pos": [0.5, 0.5], "color": "#BBBBBB"},
    ],
}
if check_shots(clips):
    print("warning: some clips cross a cut")
write(edl, "edl_cast.json")
