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
| `audio` | object | `clip_volume` (default 1.0): gain on the joined clip audio |
| `beat_sync` | object | `{"music": 0, "every": 1}` snap cut points to beats of `music[0]` |

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
- `focus`: crop centre for `fit: crop`, 0..1 (x, y). Use it to keep faces in a 9:16 crop.
- `transition`: how this clip enters from the previous one. Omit for a hard cut.

Generated clips:

```json
{"type": "color", "color": "#000000", "duration": 1.5}
{"type": "image", "path": "cover.jpg", "duration": 3, "effects": [{"type": "zoom_in"}]}
```

## Transitions

`cut` (default), `fade`, `fadeblack`, `fadewhite`, `dissolve`, `wipeleft/right/up/down`,
`slideleft/right/up/down`, `smoothleft/right/up/down`, `circleopen`, `circleclose`,
`circlecrop`, `rectcrop`, `radial`, `zoomin`, `pixelize`, `hblur`, `distance`, `squeezeh`,
`squeezev`, `coverleft/right`, `revealleft/right`, `diagtl`, `diagbr`, `hlslice`, `hrslice`,
`vuslice`, `vdslice`, `fadegrays`, `horzopen`, `horzclose`, `vertopen`, `vertclose`.
Aliases: `crossfade`→fade, `black`→fadeblack, `white`/`flash`→fadewhite, `zoom`→zoomin,
`slide`→slideleft, `wipe`→wipeleft, `blur`→hblur, `whip`→smoothleft.

## Effects (per clip, applied in order after framing)

| type | params | look |
|---|---|---|
| `zoom_in` / `zoom_out` | `amount` 0.12, `focus` [x,y] | slow Ken Burns push / pull |
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

## Grades

`none`, `natural`, `vivid`, `cinematic`, `teal_orange`, `warm`, `cool`, `moody`, `vintage`,
`film`, `bw`, `fade`, `bright`, or `lut:/path/to/file.cube`.

## texts

```json
{"text": "第一站 · 京都", "start": 2.0, "end": 4.5, "style": "lower_third",
 "pos": "bottom_left", "size": 0.05, "color": "#FFFFFF", "stroke_color": "#000000", "stroke": 0.08,
 "box": "#00000099", "anim": "slide_up", "highlight": ["京都"], "highlight_color": "#FFE600",
 "align": "left", "max_width": 0.8, "rotate": 0, "shadow": true, "font": "/path.ttf"}
```

Times are output-timeline seconds (see the timeline printed by render.py). Only `text`,
`start`, `end` are required; `style` supplies the rest.

- `style`: `caption`, `title`, `subtitle`, `highlight`, `lower_third`, `label`, `tag`, `quote`.
- `pos`: `center`, `top`, `bottom`, `left`, `right`, `top_left`, `top_right`, `bottom_left`,
  `bottom_right`, `lower_left`, `center_low`, `center_high`, `caption`, or `[x, y]` (0..1).
- `size`: ≤ 1 = fraction of the frame's short side; > 1 = pixels at full resolution.
- `anim`: `none`, `fade`, `pop`, `slide_up`, `typewriter` (`anim_in`, `anim_out`, `type_time`).
- `box`: background colour (`#RRGGBBAA` allowed). `\n` forces a line break.

## subtitles

```json
{"enabled": true, "from": "transcript", "style": "caption", "max_chars": 14,
 "strip_punct": true, "replace": {"剪急": "剪辑"}, "highlight": ["关键"],
 "items": [{"text": "manual caption", "start": 1.0, "end": 2.5}]}
```

Transcript words are mapped through every clip's `in/out/speed` onto the output timeline.
`max_chars`: about 12-16 for 9:16, 20-26 for 16:9 (CJK chars; Latin counts ~0.55 each).
`replace` fixes ASR mistakes. An `.srt` is written next to the output.

## images

```json
{"path": "logo.png", "start": 0, "end": null, "pos": "top_right", "width": 0.12, "opacity": 0.85, "anim": "fade"}
```

`width` ≤ 1 = fraction of frame width. `end: null` = until the end.

## music

```json
{"path": "song.mp3", "start": 0, "end": null, "offset": 12.0, "volume": 0.3,
 "duck": true, "fade_in": 0.5, "fade_out": 2.0, "loop": true}
```

- `start`/`end`: where it sits on the output timeline; `offset`: where to start in the song.
- `duck`: compress the music whenever clip audio (speech) is present.
- Montage with music only: set `audio.clip_volume` to 0-0.3 and music `volume` 0.8-1.0.
