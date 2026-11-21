---
name: video-edit
description: Edit raw footage into a finished video by watching its content. Use whenever the user hands over one or more video files (optionally music, a logo, or a brief) and wants them cut, trimmed, mixed into a montage/highlight reel, turned into a short (9:16), given subtitles, transitions, effects, colour grading, titles or background music. Covers talking-head jump cuts, vlogs, highlight montages, beat-synced edits, travel/product/event recaps. Triggers on "剪辑", "混剪", "剪一下", "做个短视频", "加字幕", "卡点", "edit this video", "make a montage", "cut this down".
---

# Video edit

You are the editor. The user only supplies footage; you watch it (contact sheets +
transcript), decide the story and the cut, write an EDL, render, review your own render, and
iterate until it is good. Ask questions only when a choice is genuinely theirs and cannot be
inferred (see "Defaults").

Scripts live in `.claude/skills/video-edit/scripts/` (call them with `python3`). Requirements:
`ffmpeg`, Python 3 with `numpy`, `opencv-python`, `Pillow`; `faster-whisper` for transcripts
(`pip3 install --user faster-whisper`; the model downloads on first use).

## Workflow

### 1. Set up a job folder inside the project

Put everything under `edits/<job-name>/` in the current project (not `/tmp`): file tools
may only read images inside the project, and you need to look at the sheets.

```bash
J=edits/<job-name>; mkdir -p $J
S=.claude/skills/video-edit/scripts
```

### 2. Analyze (watch the footage)

```bash
python3 $S/analyze.py IN1.mp4 IN2.mov ... --out $J/work [--music song.mp3] [--lang zh] [--model small]
```

- `--model`: `base` is fast, `small` (default) is a good balance, `large-v3` for hard audio.
- Long footage (> 20 min total) takes minutes; run it in the background.
- Add `--no-transcribe` for footage without meaningful speech.

Then **read `$J/work/summary.md` and open every contact sheet in `$J/work/sheets/`** with the
Read tool. Each tile is labelled `v<n> s<shot> m:ss.xx`. From this, write yourself a short
content log: who/what/where, the best moments (with timestamps), weak shots (blurry, dark,
shaky, duplicate), what is said, and the natural story order. If you need a closer look at a
moment, extract frames:

```bash
python3 $S/preview.py IN.mp4 --at 12.5 13.0 13.5 --every 999 --out $J/work/look
```

`analysis.json` has the full data: shot list with `motion / brightness / sharpness /
colorfulness / loudness_db / score`, `audio.silences`, `audio.speech_spans`, transcript
segments with word timings, and for music `bpm`, `beats`, `strong_beats`, `drops`.

### 3. Decide the edit

Pick an approach from `reference/editing-playbook.md` (talking head, vlog, montage,
beat-synced short, recap, ...). Decide: format, target length, story order, which moments
stay, pacing, transitions, effects, grade, text and music. Keep it purposeful: effects should
serve the content, not decorate every cut.

Optional shortcut for a first pass, then refine by hand:

```bash
python3 $S/draft_edl.py $J/work/analysis.json --mode jumpcut|montage --out $J/edl.json \
  [--format 9:16|16:9|1:1|4:5|source] [--duration 30] [--music song.mp3] [--title "..."] [--grade cinematic]
```

`jumpcut` keeps speech and drops silences (it uses the transcript); `montage` takes the
highest-scoring shots and beat-syncs them. A draft is never the final edit: remove retakes,
filler and mistakes you found in the transcript, reorder for story, add B-roll cutaways over
speech, punch-ins, titles.

### 4. Write the EDL

Write `$J/edl.json`. Full format: `reference/edl.md`. Minimal shape:

```json
{
  "analysis": "work/analysis.json",
  "output": {"path": "out/final.mp4", "width": 1080, "height": 1920, "fps": 30, "fit": "blur"},
  "grade": "natural",
  "clips": [
    {"src": "v1", "in": 3.2, "out": 7.9, "effects": [{"type": "zoom_in", "amount": 0.08}]},
    {"src": "v2", "in": 0.0, "out": 2.4, "transition": {"type": "fade", "duration": 0.3}}
  ],
  "texts": [{"text": "Title", "start": 0.2, "end": 2.5, "style": "title"}],
  "subtitles": {"enabled": true, "style": "caption"},
  "music": [{"path": "song.mp3", "volume": 0.3, "duck": true}]
}
```

Paths in the EDL are relative to the EDL file. `src` can be `v1`, `v2`... (ids from analysis).

### 5. Render, then review your own cut

```bash
python3 $S/render.py $J/edl.json --draft   # half-res, fast: use while iterating
python3 $S/render.py $J/edl.json           # final
```

Rendered clips are cached in `$J/.render/clips/`, so re-renders after small EDL changes are
fast. The command prints the timeline (output time ↔ source range) and writes review sheets
to `$J/.render/review/`. **Open the review sheets and check**: framing after the aspect
change (heads cut off? use `focus` or `fit: "blur"`), text legibility and overlap with
faces, subtitle accuracy (fix misheard words with `subtitles.replace`), black/frozen frames,
pacing, grade consistency. Also re-read `$J/.render/subtitles.json` against the transcript.
Fix and re-render. Do at least one review pass before reporting.

You cannot hear the result, so verify audio by measurement: integrated loudness and true peak
(`ffmpeg -i out.mp4 -af ebur128=peak=true -f null -`, target about -14 LUFS / ≤ -1 dBFS),
momentary loudness at each dialogue line and hit (quiet dialogue needs more `volume`), and
the `--plan` / `last_render.json` timeline against the music hits you targeted. Say in the
handoff that the mix was checked by measurement, and ask the user to listen.

### 6. Deliver

Give the user: the output path, length/format, a 3-6 line description of the cut (story,
key moments used, effects, music), and the `.srt` path if subtitles were made. Offer
alternatives worth trying (other length, other format, different music) in one line.
Keep `edl.json`: later change requests are edits to it plus a re-render.

## Defaults (when the user does not say)

- Format: same orientation as most of the footage. "短视频 / 抖音 / Reels / Shorts / 小红书" ⇒
  9:16 1080x1920; "B站 / YouTube" ⇒ 16:9 1920x1080.
- Length: talking head ⇒ all meaningful content, tightened. Montage / highlight ⇒ 15-45 s.
  Social short ⇒ under 60 s.
- Subtitles: on whenever there is speech (burned in, plus `.srt`).
- Music: only if the user supplied it or the footage has none and the user asked for music.
  Never invent a music source; if music would clearly help and none was given, say so in the
  handoff. Original speech always stays intelligible (`duck: true`, volume 0.15-0.35).
- Grade: `natural` for people/talking; `cinematic` or `teal_orange` for travel/montage;
  keep one grade per video unless a change is a deliberate effect.
- Loudness: -14 LUFS (set automatically).
- Language of on-screen text: the language spoken in the footage / used by the user.

## Gotchas

- `in`/`out` are source seconds; on-screen length is `(out-in)/speed` (+ `freeze`).
- An xfade transition overlaps two clips by its duration, shortening the total. Keep
  transitions ≤ 0.5 s and shorter than both neighbouring clips.
- Captions come from the transcript and follow cuts and speed changes automatically; muted or
  reversed clips get none. Manual captions: `subtitles.items`.
- Music-driven edits: set the bed's `offset` so its drop lands where you want, `beat_sync`
  the action range, pin key moments with `start_at_music`, check with `--plan`. See the
  trailer recipe in the playbook.
- Synthesised SFX (`sounds` with `sfx`) need no sample files; keep them under the music
  except on drops and the title.
- Prefer anchoring texts and manual captions to clips (`"clip": i` with `start`/`end`, or
  `src_start`/`src_end` in source seconds) over absolute output times: they stay correct
  when clips are trimmed, reordered or transitions change.
- `beat_sync` rewrites clip `out` points to land cuts on beats; set `every: 2` or `4` for
  slower pacing.
- This ffmpeg build may lack `drawtext`/`libass`; all text is drawn by Pillow, so any font
  that Pillow can open works (`output.font` or per-text `font`).
- Vertical output from horizontal footage: `fit: "crop"` with `focus: [x, y]` (0..1) to keep
  the subject in frame, or `fit: "blur"` to keep the whole picture.
