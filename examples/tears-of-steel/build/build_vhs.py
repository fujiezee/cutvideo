"""Retro VHS home video (4:3, ~35 s): Thom and Celia's Amsterdam as an old tape."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tos import check_shots, end_card, v, write  # noqa: E402

TAPE = "highpass=f=180,lowpass=f=4500"            # narrow, dull tape sound
D = {"focus": "auto", "volume": 1.5, "audio_filter": TAPE, "duck_music": True}
AMB = {"focus": "auto", "volume": 0.5, "audio_filter": TAPE}
GLITCH = [{"type": "glitch", "duration": 0.18}]
clips = [
    v(262.0, 265.5, **AMB, effects=[{"type": "fade_in", "duration": 0.4}]),   # Thom walks the street
    v(270.0, 273.0, **AMB, effects=GLITCH),
    v(280.0, 283.0, **AMB),                                                      # by the canal
    v(312.6, 319.9, **D, effects=GLITCH),                                        # graffiti, "Look Celia..."
    v(320.0, 322.2, **D),                                                        # Celia
    v(322.3, 324.0, **D),                                                        # "...awesome in space" graffiti
    v(324.2, 327.8, **D),                                                        # Thom says it
    v(328.6, 330.2, **AMB, effects=GLITCH),                                      # sunrise over the city
    v(352.8, 356.2, **D),                                                        # "Celia I love your robot hand"
    v(369.7, 371.5, **D, effects=GLITCH),                                        # the kiss on the robot hand
    v(36.9, 40.2, **D),                                                          # "All right fine. I'm freaked out."
    {"type": "color", "color": "#1C3FAA", "duration": 2.0, "transition": {"type": "fadegrays", "duration": 0.2}},
]
BLUE = len(clips) - 1                                                            # blue STOP screen
card, credit = end_card(2.6)
clips.append(card)
edl = {
    "analysis": "work/analysis.json",
    "output": {"path": "out/tos_vhs_4x3.mp4", "width": 1440, "height": 1080, "fps": 30, "fit": "crop"},
    "grade": "vhs",
    "clips": clips,
    "music": [{"path": "audio/score_credits.wav", "offset": 44.0, "volume": 0.45, "duck": True, "loop": False,
               "filter": TAPE, "fade_in": 1.0, "fade_out": 2.0}],
    "images": [{"preset": "scanlines", "opacity": 0.22}],
    "subtitles": {"enabled": True, "from": "transcript", "font": "mono", "size": 0.042, "pos": [0.5, 0.78],
                  "strip_punct": False, "max_chars": 22},
    "texts": [
        {"text": "PLAY ▶", "start": 0.0, "end": 3.0, "style": "stamp", "pos": [0.05, 0.06]},
        {"text": "SP", "start": 0.0, "until_clip": BLUE, "style": "stamp", "pos": [0.95, 0.06], "align": "right"},
        {"text": "NOV. 22  2012\nPM 4:51", "start": 0.0, "until_clip": BLUE, "style": "stamp",
         "pos": [0.95, 0.95], "align": "right"},
        {"text": "■ STOP", "clip": BLUE, "start": 0.2, "end": 2.0, "style": "stamp", "size": 0.07, "pos": "center"},
        credit,
    ],
}
if check_shots(clips):
    print("warning: some clips cross a cut")
write(edl, "edl_vhs.json")
