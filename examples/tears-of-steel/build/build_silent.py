"""Black-and-white silent film (4:3, ~50 s): intertitles instead of dialogue, iris in/out."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tos import ATTRIBUTION, check_shots, v, write  # noqa: E402

FILM = {"focus": "auto", "mute": True, "speed": 1.15, "effects": [{"type": "flicker", "amount": 0.05}]}


def card(seconds=2.8, **x):  # intertitle background, with the same film grain as the shots
    return {"type": "color", "color": "#0B0B0B", "duration": seconds, "grade": "silent", **x}


clips, texts = [], []


def title(text, seconds=2.8, size=0.06, transition=None):
    c = card(seconds)
    if transition:
        c["transition"] = transition
    clips.append(c)
    texts.append({"text": text, "clip": len(clips) - 1, "start": 0.25, "end": seconds - 0.2,
                  "style": "intertitle", "size": size})


IRIS_IN = {"type": "circleopen", "duration": 0.8}
title("钢铁之泪\nTEARS OF STEEL", 3.2, 0.075)
clips.append(v(25.5, 31.0, **FILM, transition=IRIS_IN))                  # the bridge
title("「听着，西莉亚，\n我们都得追随自己的热爱。」")
clips.append(v(316.0, 319.8, **FILM))                                    # Thom
clips.append(v(320.1, 322.1, **FILM))                                    # Celia
title("「你就承认吧，\n你是被我的机械手吓到了！」")
clips.append(v(346.5, 350.1, **FILM))                                    # Celia shows the hand
clips.append(v(36.9, 40.2, **FILM))                                      # Thom gives up
title("四十年后……", 2.4, 0.07)
clips.append(v(70.0, 74.0, **FILM))                                      # the city
clips.append(v(455.0, 457.5, **FILM))                                    # the battle
clips.append(v(471.0, 473.5, **FILM))
clips.append(v(493.0, 495.5, **FILM))
title("「你伤透了我的心。」", 2.4)
clips.append(v(383.2, 385.3, **FILM))                                    # Celia
clips.append(v(532.0, 535.5, **FILM))                                    # old Thom
title("剧  终\nTHE END", 3.4, 0.075, transition={"type": "circleclose", "duration": 0.9})
texts.append({"text": ATTRIBUTION, "clip": len(clips) - 1, "start": 0.8, "end": 3.3, "style": "subtitle",
              "font": "serif", "size": 0.024, "color": "#9A9A9A", "pos": [0.5, 0.86]})

edl = {
    "analysis": "work/analysis.json",
    "output": {"path": "out/tos_silent_4x3.mp4", "width": 1440, "height": 1080, "fps": 24, "fit": "crop"},
    "grade": "silent",
    "clips": clips,
    # quiet orchestral passage of the score, band-limited like a gramophone
    "music": [{"path": "audio/score_credits.wav", "offset": 92.0, "volume": 1.8, "loop": False,
               "filter": "highpass=f=250,lowpass=f=3200", "fade_in": 1.5, "fade_out": 2.5}],
    "texts": texts,
}
if check_shots(clips):
    print("warning: some clips cross a cut")
write(edl, "edl_silent.json")
