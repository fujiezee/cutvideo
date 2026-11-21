# EDL format (render.py)

JSON. Paths are relative to the EDL file unless absolute.

## Top level

| key | type | meaning |
|---|---|---|
| `analysis` | path | `analysis.json` from analyze.py; enables `src: "v1"` ids and transcript captions |
| `output` | object | see below |
| `grade` | string | default colour grade for all clips (see Grades) |
| `clips` | array | the timeline, in order (see Clips) |
| `texts` | array | titles, labels, callouts on the output timeline |
| `subtitles` | object | captions from the transcript and/or manual items |
| `images` | array | logos / stickers / watermarks |
| `music` | array | background music tracks |
| `sounds` | array | audio-only items: synthesised SFX or ranges of any file / source video |
| `audio` | object | `clip_volume` (default 1.0): gain on all clip audio |
| `beat_sync` | object | snap cut points to beats of a music track (see Timing) |

## output

```json
{"path": "out/final.mp4", "width": 1080, "height": 1920, "fps": 30,
 "fit": "crop", "bg": "black", "crf": 18, "preset": "medium", "loudness": -14, "font": "/path/font.ttf"}
```

`fit`: `crop` (fill frame, crop edges), `blur` (whole picture over a blurred copy), `pad`
(letterbox with `bg`).

## Clips

Video clip:

```json
{"src": "v1", "in": 12.3, "out": 15.8, "speed": 1.0, "volume": 1.0, "mute": false,
 "fit": "crop", "focus": [0.5, 0.4], "grade": "warm",
 "effects": [ ... ], "transition": {"type": "fade", "duration": 0.4}}
```

- `src` (or `path`): `v1`… from analysis, or a file path. `dur` may replace `out`.
- `speed`: 0.25-4. Audio is time-stretched (pitch kept).
- `focus`: crop position for `fit: crop`, 0..1 (x, y) of the overflow (0.5 = centre). Use it to
  keep faces in a 9:16 crop. `"auto"` centres the crop on the largest face found in three
  sampled frames (slight headroom), falling back to the centre when no face is found.
- `transition`: how this clip enters from the previous one. Omit for a hard cut.
- `adjust`: per-clip colour correction applied before the shared grade, to match shots:
  `{"temperature": 5600, "temperature_mix": 0.5, "brightness": 0.03, "contrast": 1.05,
  "saturation": 0.9, "gamma": 1.0}` (temperature < 6500 warms, > 6500 cools).

Audio of a clip (rendered as its own track, so it can extend past the picture):

| key | meaning |
|---|---|
| `volume` | gain (raise quiet dialogue to 1.5-2.5) |
| `mute` | drop the clip's audio |
| `audio_lead` | J-cut: audio starts this many seconds before the picture (taken from the source before `in`) |
| `audio_tail` | L-cut: audio continues this many seconds after the picture (from the source after `out`) |
| `duck_music` | this clip's audio ducks `duck: true` music. If no clip sets it, all clip audio ducks |
| `audio_filter` | raw ffmpeg audio filters for this clip, e.g. `"highpass=f=180,lowpass=f=4500"` (tape / phone sound) |

Transitions crossfade the clip audio automatically. Changing only audio keys does not
re-encode the video.

Generated clips (colour clips are not graded unless they set their own `grade`):

```json
{"type": "color", "color": "#000000", "duration": 1.5}
{"type": "image", "path": "cover.jpg", "duration": 3, "effects": [{"type": "zoom_in"}]}
```

## Transitions

`cut` (default), `fade`, `fadeblack`, `fadewhite`, `dissolve` (noisy pixel dissolve, not a crossfade), `wipeleft/right/up/down`,
`slideleft/right/up/down`, `smoothleft/right/up/down`, `circleopen`, `circleclose`,
`circlecrop`, `rectcrop`, `radial`, `zoomin`, `pixelize`, `hblur`, `distance`, `squeezeh`,
`squeezev`, `coverleft/right`, `revealleft/right`, `diagtl`, `diagbr`, `hlslice`, `hrslice`,
`vuslice`, `vdslice`, `fadegrays`, `horzopen`, `horzclose`, `vertopen`, `vertclose`.
Aliases: `crossfade`→fade, `black`→fadeblack, `white`/`flash`→fadewhite, `zoom`→zoomin,
`slide`→slideleft, `wipe`→wipeleft, `blur`→hblur, `whip`→smoothleft.

## Effects (per clip, applied in order after framing)

| type | params | look |
|---|---|---|
| `zoom_in` / `zoom_out` | `amount` 0.12, `focus` [x,y] | slow Ken Burns push / pull (with `fit: pad` the picture moves inside static bars) |
| `punch` | `scale` 1.18, `focus`, optional `start`,`end` (clip seconds) | instant punch-in (hides jump cuts, emphasis) |
| `shake` | `amount` 0.03, `start`, `end` | camera shake (impact, bass hit) |
| `flash` | `duration` 0.25 | white flash at clip start |
| `glitch` | `duration` 0.35 | RGB split + noise at clip start |
| `rgb_split` | – | constant chromatic offset |
| `fade_in` / `fade_out` | `duration` | from/to black, audio too (use on first/last clip) |
| `freeze` | `duration` 0.6 | hold last frame |
| `reverse` | – | play backwards (keep clips short) |
| `bw` | – | black and white |
| `blur` | `amount` 12 | gaussian blur (background behind text) |
| `vignette`, `grain` (`amount`), `sharpen`, `mirror`, `echo` | – | misc looks; `echo` = ghost trails |
| `flicker` | `amount` 0.05 | old-projector brightness flicker |
| `intro` | see below | freeze-frame character intro (name card) |

### Character intro (`intro` effect)

```json
{"type": "intro", "at": 1.2, "hold": 1.8, "name": "BARLEY", "role": "Sniper", "index": "03",
 "color": "#0A84FF", "bg": "bw", "box": [0.3, 0.1, 0.97, 1.0], "side": "auto",
 "cutout": true, "sfx": "hit", "sfx_volume": 0.7}
```

At `at` seconds into the clip the picture freezes for `hold` seconds (the clip becomes that
much longer; its audio pauses; a `hit` + whoosh play unless `"sfx": null`). The frozen frame:
background `bg` = `bw` | `dark` | `tint` | `blur`; the person cut out in colour with a white
outline and an accent glow; an accent panel (`color`) slides in with `name`, `role` and an
optional `index`. `side`: where the text goes (`auto` = opposite the person).

- Cut-out: OpenCV GrabCut seeded by the face (bundled Haar cascades) and the `box`
  (fractions of the output frame, after crop/pad). Without `box` the person is found from the
  face. If `rembg` is installed it is used instead (better edges, model downloads once to
  `~/.rembg`); the `box` and the detected face still decide which person is the subject.
  Cached clips re-render when the backend changes.
- If no person can be isolated (no face and no box, or the cut-out covers <8% or >85% of the
  frame) the intro uses the no-cut-out look: colour frame, darkened edges, same panel.

## Grades

`none`, `natural`, `vivid`, `cinematic`, `teal_orange`, `warm`, `cool`, `moody`, `vintage`,
`film`, `bw`, `fade`, `bright`, `trailer`, `vhs` (faded colour, chroma shift, softness,
noise), `silent` (high-contrast B&W, grain, vignette), `cyberpunk` (cyan shadows, magenta
highlights, high saturation), or `lut:/path/to/file.cube`.

## texts

```json
{"text": "Kyoto", "start": 2.0, "end": 4.5, "style": "lower_third",
 "pos": "bottom_left", "size": 0.05, "color": "#FFFFFF", "stroke_color": "#000000", "stroke": 0.08,
 "box": "#00000099", "anim": "slide_up", "highlight": ["Kyoto"], "highlight_color": "#FFE600",
 "align": "left", "max_width": 0.8, "rotate": 0, "shadow": true, "font": "/path.ttf"}
```

Times are output-timeline seconds (see the timeline printed by render.py). Only `text`,
`start`, `end` are required; `style` supplies the rest.

Anchor to a clip instead, so timings survive re-cutting (works for `texts` and
`subtitles.items`; `clip` is the index in `clips`, negative counts from the end):

```json
{"text": "40 years later", "clip": 6, "start": 0.3, "end": 3.0}
{"text": "We have to follow our passions", "clip": 2, "src_start": 318.1, "src_end": 320.06}
```

`start`/`end` with `clip` are seconds from that clip's start (`end` defaults to the clip's
end). `src_start`/`src_end` are source-video seconds (e.g. word timings from the
transcript), mapped through the clip's `in` and `speed` and clamped to the part of the clip
that is visible (up to the midpoint of its transitions).

- `style`: `caption`, `title`, `subtitle`, `highlight`, `lower_third`, `label`, `tag`, `quote`,
  `card` (trailer interstitial: wide letter spacing, slow push-in), `intertitle` (silent-film
  card: serif, double frame), `neon` (glowing text), `stamp` (camcorder date/PLAY overlay,
  monospace).
- `border`: colour of a double-rule frame around the text; `glow`: colour of a neon glow.
- `until_clip`: end the text where that clip becomes visible (e.g. stamps that stop at a
  "STOP" screen); works with or without `clip`.
- `pos`: `center`, `top`, `bottom`, `left`, `right`, `top_left`, `top_right`, `bottom_left`,
  `bottom_right`, `lower_left`, `center_low`, `center_high`, `caption`, or `[x, y]` (0..1).
- `size`: ≤ 1 = fraction of the frame's short side; > 1 = pixels at full resolution.
- `anim`: `none`, `fade`, `pop`, `slide_up`, `typewriter`, `zoom` (`anim_in`, `anim_out`,
  `type_time`, `zoom` = push-in amount, default 0.08).
- `tracking`: letter spacing as a fraction of the font size (0.1-0.3 for titles).
- `weight`: `bold` uses a bold CJK-capable face (default for `caption`, `title`,
  `highlight`, `tag`, `card`); `font` may name a face inside a collection as `path.ttc#index`,
  or the aliases `serif` / `mono`.
- `line_colors`: one colour per line, e.g. bilingual subtitles `["#FFFFFF", "#C8C8C8"]`.
- `box`: background colour (`#RRGGBBAA` allowed). `\n` forces a line break.

## subtitles

```json
{"enabled": true, "from": "transcript", "style": "caption", "max_chars": 14,
 "strip_punct": true, "replace": {"misheard": "corrected"}, "highlight": ["key"],
 "items": [{"text": "manual caption", "start": 1.0, "end": 2.5}]}
```

Word-by-word highlight (karaoke):

```json
"subtitles": {"enabled": true, "karaoke": "word", "karaoke_box": "#FFE600",
              "karaoke_color": "#FFE600", "karaoke_dim": "#FFFFFF99"}
```

- `karaoke`: `word` = only the word being spoken is highlighted; `fill` = spoken words stay
  highlighted.
- `karaoke_box`: draw a rounded box in this colour behind the current word (its text turns
  dark); without it the word is drawn in `karaoke_color`. `karaoke_dim`: colour for words not
  yet spoken.
- Word timings come from the transcript; manual `items` without timings spread their words
  evenly. Captions shorter than 0.45 s are merged into a touching neighbour, and captions
  pause during character intros.
- Other keys copied onto every caption: `size`, `color`, `stroke`, `stroke_color`, `pos`,
  `box`, `font`, `weight`, `highlight`, `line_colors`, `max_width`, `anim`, `shadow`.

Use `"from": "manual"` to skip the transcript and caption only `items` (handy when the
ASR is unreliable, e.g. dialogue under loud music, or for translated captions).
Transcript words are mapped through every clip's `in/out/speed` onto the output timeline.
`max_chars`: about 12-16 for 9:16, 20-26 for 16:9 (CJK chars; Latin counts ~0.55 each).
`replace` fixes ASR mistakes. An `.srt` is written next to the output.

## images

```json
{"path": "logo.png", "start": 0, "end": null, "pos": "top_right", "width": 0.12, "opacity": 0.85, "anim": "fade"}
```

`width` ≤ 1 = fraction of frame width. `end: null` = until the end.
Built-in full-frame overlay: `{"preset": "scanlines", "opacity": 0.22}` (CRT / VHS lines).

## music

```json
{"path": "song.mp3", "start": 0, "end": null, "offset": 12.0, "volume": 0.3,
 "duck": true, "duck_threshold": 0.03, "duck_ratio": 8, "duck_release": 400,
 "fade_in": 0.5, "fade_out": 2.0, "loop": true}
```

- `path`: a file, or a source id like `v1` (e.g. the film's own score from its credits).
- `filter`: raw ffmpeg audio filters applied to this track (e.g. a gramophone band-pass
  `"highpass=f=250,lowpass=f=3200"`).
- `start`/`end`: where it sits on the output timeline; `offset`: where to start in the song.
- `duck`: compress the music while dialogue plays (see `duck_music` on clips).
- Montage with music only: set `audio.clip_volume` to 0-0.3 and music `volume` 0.8-1.0.
- A music bed cut from a longer file: extract it to WAV first (`ffmpeg -ss A -to B -i src -vn
  bed.wav`) and analyse it with `analyze.py --music bed.wav` to get its beats and energy.

## sounds

```json
{"sfx": "boom", "clip": 12, "start": 0.0, "volume": 0.8, "params": {"duration": 4}}
{"sfx": "riser", "clip": 33, "start": -3.0, "volume": 0.5}
{"path": "v1", "in": 389.2, "out": 392.1, "at": 41.5, "volume": 1.0, "fade_in": 0.1, "fade_out": 0.2}
```

- Placement: `clip` + `start` (seconds from that clip's start, may be negative) or `at`
  (output seconds). Sounds are not ducked.
- `sfx` (synthesised, no samples needed): `boom` (big low impact with crack), `hit`
  (short punch), `whoosh` (transition swish), `riser` (tension build, ends abruptly - put
  its end on the hit), `swell` (reverse-cymbal lead-in), `sub_drop`, `tick`, `glitch`.
  `params`: `duration`, `pitch`, `seed`.
- `path`/`src` + `in`/`out`: any audio range, e.g. a line of dialogue laid over other shots.

## Timing: beat sync, pins, planning

```json
"beat_sync": {"music": 0, "every": 2, "clips": [12, 30], "offset_beats": 1, "shift": -0.25}
```

- Snaps the visible cut at the end of each clip in `clips` (inclusive range) to the beat grid;
  a transition's switch point is its midpoint. Clips with `"snap": false` keep their length
  (dialogue). Colour/image clips change `duration`.
- `every`: cut every N beats; `offset_beats`: which beat of the N to start on (parity);
  `shift`: nudge the grid in seconds, or `"auto"` to pick the offset (±0.3 s) that puts the
  grid on the strongest onsets inside the synced span. Beat-tracker phase can drift within
  one song, so prefer `"auto"`; it is logged.

Pins land one clip on an exact moment, e.g. the title on a musical hit; the previous clip
absorbs the difference:

```json
{"src": "v1", "in": 66.0, "out": 69.8, "start_at_music": 99.05}
{"type": "color", "duration": 2, "start_at": 30.0}
```

`start_at_music` is seconds in the song (`pin_music`: index, default 0) and accounts for its
`offset`/`start`. Pins are applied after beat sync.

`render.py EDL --plan` prints the resolved timeline (after beat sync and pins) without
rendering. Clips render to an exact frame count, so the plan matches the output to the frame.
`.render/resolved_edl.json` holds the in/out points that were actually rendered.
