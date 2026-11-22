---
name: video-edit
description: Edit raw footage into a finished video by watching its content. Use whenever the user hands over one or more video files (optionally music, a logo, or a brief) and wants them cut, trimmed, mixed into a montage/highlight reel, turned into a short (9:16), given subtitles, transitions, effects, colour grading, titles or background music. Covers talking-head jump cuts, vlogs, highlight montages, beat-synced edits, travel/product/event recaps. Also covers trailers, freeze-frame character intros, word-by-word (karaoke) captions, 3D titles, text behind people, three.js light effects, face recognition of the cast, narrated recaps with text-to-speech, and a style menu of what a film can be cut into. Triggers on "剪辑", "混剪", "剪一下", "做个短视频", "加字幕", "卡点", "预告片", "人物介绍", "逐字字幕", "立体字", "3D特效", "能剪出哪些风格", "全剪", "多种风格", "人物识别", "配音", "旁白", "解说", "一分钟看完", "封面", "降噪", "口头禅", "edit this video", "make a montage", "make a trailer", "cut this down".
---

# Video edit

You are the editor. The user only supplies footage; you watch it (contact sheets +
transcript), decide the story and the cut, write an EDL, render, review your own render, and
iterate until it is good. Ask questions only when a choice is genuinely theirs and cannot be
inferred (see "Defaults").

Scripts live in `.claude/skills/video-edit/scripts/` (call them with `python3`). Requirements:
`ffmpeg`, Python 3 with `numpy`, `opencv-python`, `Pillow`; `faster-whisper` for transcripts
(`pip3 install --user faster-whisper`; the model downloads on first use). Optional: `rembg`
for cleaner cut-outs in character intros.

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

Fix ASR mistakes before captioning: misheard words go in `subtitles.replace`; missing or
mistimed words are corrected in `analysis.json` itself (keep a copy of the original). Check
names, numbers and any word the captions will highlight.

### 2a. Who is in it (footage with people)

```bash
python3 $S/people.py $J/work/analysis.json [--exclude 588-708]
```

Detects faces (YuNet), recognises them (SFace) and clusters them into people P1, P2, ...
(most shots first). Open `$J/work/people.jpg`: one face per person with their shot count.
Name them from what you saw and heard (the run also prints names heard in the dialogue):

```bash
python3 $S/people.py $J/work/analysis.json --name "P1=Thom:wants to go to space" "P2=Celia:engineer"
```

`people.json` lists every appearance (time, face box). Names survive a re-scan. The style
menu, cast intros (one per person, frozen on a frame with their face, cut-out box on that
person) and recap scripts (`@P2`) use it. The models (~38 MB) download once to
`~/.cache/video-edit/models`.

### 2b. When asked what the footage can become: the style menu

```bash
python3 $S/styles.py $J/work/analysis.json [--exclude 588-708 v2:0-5]
```

Writes `work/styles.md`: footage features (dialogue density, quotable lines, action, faces,
close-ups, static portraits, calm wide shots, dark/neon shots, music-like audio) and a score
per style with reasons, supporting shots and gaps. First look at the sheets and exclude
credits, slates and test footage (`--exclude`, seconds). Then check the top styles against
what you saw, drop or add styles by judgment, and present the menu: per style one line of
"what it would be" (story / look), format and length, and what is missing. Offer to cut
several; each chosen style then gets its own plan (step 3).

### 2c. Several styles at once: auto drafts

When the user picks several styles (or says "cut them all"), get first drafts in one run:

```bash
python3 $S/autocut.py $J/work/analysis.json --styles trailer,beatmix,vhs [--exclude 588-708] \
    [--music song.mp3 | --no-music] [--title "Name"] [--credit "attribution"] [--draft]
```

`--styles` takes a list or `recommended` / `top3` / `all`. Recipes exist for `trailer`,
`beatmix`, `cast`, `karaoke`, `vhs`, `silent`, `cyberpunk` and `recap`. Each recipe picks shots from the
style features (every piece lies inside one detected shot, so no flash frames), writes
`$J/auto/edl_<style>.json`, renders `$J/out/auto_<style>_<aspect>.mp4` with `--qa`, and
the run ends with `$J/auto/report.md` (length, QA, notes per style) and
`$J/auto/compare.jpg` (six frames per style). Without `--music`, the footage's longest
stretch of music with no speech (score, credits) is extracted and used.

`recap` is a narrated summary ("one-minute recap"). Write the script yourself after
watching: one sentence per line in story order, each optionally followed by `|` and where
its pictures come from, a source range and/or people:

```text
On a bridge in Amsterdam, Thom and Celia break up. | 0:25-0:45
Forty years later, robots rule the city. | 1:10-1:22
Old Thom wants to rewrite the past. | @P2 2:00-4:10
```

Mark words to colour in the captions with braces, `{Celia}`; names and numbers are
coloured automatically. Pass it with `--script recap.txt [--caption-style explainer|boxed]`. Each line is spoken
(`tts.py`), its shots fill exactly the length of the line, music ducks under the voice, and
captions follow the narration.

Every auto draft carries the brand signature on its end card ("Made with cutvideo · ..."),
see `brand.json`; `--no-brand` leaves it out.

These are drafts, not deliverables. Open `compare.jpg` and each review sheet, read the
notes (placeholder names, line choice, missing music), then refine every EDL you keep:
real names, a story order for the lines, the strongest shots, text. Render again with
`--qa` and hand over only what passes your own review (step 6).

### 3. Design the edit: write `$J/plan.md` before any EDL

Never go from footage straight to an EDL. Pick an approach from
`reference/editing-playbook.md`, then write the plan with this structure:

```markdown
# Edit plan: <job>
Goal / audience / platform: ...            Format: 9:16 1080x1920, ~30 s, fit crop
Logline: <the story of this video in one sentence>
## Structure
| # | Section | Time | Picture (v/shot @ source time) | Sound (dialogue / music / SFX) | Text |
|---|---|---|---|---|---|
| 1 | Hook | 0-3 s | v1 s12 @ 41.2 best reaction | "line..." + boom | title card |
## Music
Track, offset, BPM, where the drop / hits land (pins), what is beat-synced
## Look
Grade, per-shot corrections, transitions and effects budget (which cuts get accents)
## Text
Caption style (plain / karaoke / bilingual), titles, cards, character intros
## Risks / open questions
Weak footage, missing coverage, licence/attribution, things the user should decide
```

Then show the user a short summary of the plan (logline, structure in a few lines, length,
format, music) and go ahead, unless something in it changes the result and cannot be
inferred (purpose, platform/aspect, a length limit, which person is the subject): ask
those, at most 3 questions, before rendering. If the user asked to approve the plan first,
wait. The EDL implements the plan; when you deviate while editing, update `plan.md`.

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

You cannot hear the result, so the final render must run with `--qa`:

```bash
python3 $S/render.py $J/edl.json --qa      # or: python3 $S/qa.py OUT.mp4 --subs $J/.render/subtitles.json
```

It measures loudness (target about -14 LUFS) and true peak (≤ -1 dBFS), lists long silences,
and re-transcribes the output to check that every caption is still recognisable in the mix.
A line below 60 % is probably masked by music/SFX, too quiet, or chopped by a cut or a
freeze: raise the clip `volume`, lower or duck the music, move the cut, then re-run. Read
`.render/qa.json` (it shows what was heard) before deciding: a mismatch can also be an ASR
slip or a wrong caption. Also check the `--plan` / `last_render.json` timeline against the
music hits you targeted. Say in the handoff that the mix was checked by measurement and
ask the user to listen.

### 6. Deliver

Other platforms from the same edit:

```bash
python3 $S/render.py $J/edl.json --formats 16:9,9:16,1:1 --qa
```

Each format gets its own output (`name_9x16.mp4`, ...): narrower targets switch to
face-tracked crops, captions are re-placed and re-wrapped, intro boxes are re-detected.
Put per-format tweaks under `"formats": {"9:16": {...}}` in the EDL and review every format.

Covers: `python3 $S/cover.py $J/work/analysis.json --title "..." [--subtitle "..."]`
makes cover images for 16:9 / 9:16 / 3:4 / 1:1 from the main characters' best face frames
(people.json) with a 3D title, in `$J/covers/` with a `sheet.jpg` to choose from. Look at
the sheet; use `--at <seconds>` to force frames you prefer.

Give the user: the output path, length/format, a 3-6 line description of the cut (story,
key moments used, effects, music), the `.srt` path if subtitles were made, and anything in
`plan.md` that changed or could not be done. Offer
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
- Character intros (`{"type": "intro"}` effect) freeze the clip at `at`; the clip gets longer
  by `hold`, captions pause during the freeze. `box` is in output-frame coordinates (after
  crop/pad); omit it to find the person from the face. Extreme close-ups get the
  no-cut-out look automatically. Pick a frame where the person is still and clearly
  separated from the background (medium shot, face visible).
- Karaoke captions (`subtitles.karaoke`) need accurate word timings: fix the transcript
  first; captions with no word timings spread their words evenly.
- Voice-over: `narration` in the EDL (see `reference/edl.md`) speaks lines, captions them
  and ducks music under them. The provider is configurable in `tts.json` (system voice by
  default; OpenAI-compatible or any HTTP API), with a provider/voice per language so
  Chinese lines get a Chinese voice and English lines an English one. See
  `tts.example.json` (the Duaer model gateway: Fish Audio for Chinese, ElevenLabs for
  English); keys come from environment variables or an `env_file` outside the repo.
  `python3 $S/tts.py --show-config` prints what is in effect, `--list-models` what the
  provider offers. Check a new voice by ear-proxy: synthesise a line and run `qa.py`.
- Speech clean-up: `"audio": {"voice": "clean"}` (or `"voice"` per clip): `clean`,
  `denoise`, `broadcast`. For talking heads, analyse with `--verbatim` and draft with
  `draft_edl.py --mode jumpcut --drop-fillers` to cut "um / uh / 嗯 / 呃" out.
- Brand: `"brand": true` adds the product signature to the end card (and a logo watermark
  when `brand.json` has `logo` and `watermark.enabled`). A job's own `brand.json` overrides
  the skill default.
- 3D: `text3d` style (extruded, gradient face, `flip_in` / `swing`, `rotate_x/y`) for titles;
  `text_behind` clip effect puts big text between background and person (rembg per frame,
  ~0.7 s/frame: keep it to 3-5 s on static shots); `overlays` add three.js light effects
  (`particles`, `hologram`, `streaks`, `grid`) rendered by headless Chrome and keyed by
  brightness, so they add light like a screen blend. Use them as accents, not on every shot.
- Prefer anchoring texts and manual captions to clips (`"clip": i` with `start`/`end`, or
  `src_start`/`src_end` in source seconds) over absolute output times: they stay correct
  when clips are trimmed, reordered or transitions change.
- `beat_sync` rewrites clip `out` points to land cuts on beats; set `every: 2` or `4` for
  slower pacing.
- This ffmpeg build may lack `drawtext`/`libass`; all text is drawn by Pillow, so any font
  that Pillow can open works (`output.font` or per-text `font`).
- Vertical output from horizontal footage: `fit: "crop"` with `focus: "track"` (the crop pans
  to follow the main face, or the body in wider shots; `track_hint: "left"|"right"|x` picks
  the person in a two-shot), `"auto"` (one static face-centred position), or `[x, y]`; or
  `fit: "blur"` to keep the whole picture. Tracking falls back to a static crop when people
  are found in fewer than 30 % of samples (very wide shots): set `focus` by hand there.
- Freeze-frame intros are moved out of words automatically (the audio pauses during the
  freeze); the log says when.
