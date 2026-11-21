# cutvideo

A [Claude Code](https://claude.com/claude-code) skill that turns raw footage into a finished
video. You hand Claude the video files; it watches them (contact sheets + transcript), plans
the cut, renders it with ffmpeg, reviews its own render, and iterates.

It handles talking-head jump cuts, vlogs, highlight montages, beat-synced shorts (9:16),
and product or event recaps, with subtitles, transitions, effects, colour grading, titles and
background music.

## Requirements

- `ffmpeg` / `ffprobe`
- Python 3.10+ with `numpy`, `opencv-python`, `Pillow`
- `faster-whisper` for transcripts and subtitles (the model downloads on first use)

```bash
pip3 install --user numpy opencv-python Pillow faster-whisper
```

## Install

Use it in this repository, or copy the skill into another project or into your user skills:

```bash
cp -r .claude/skills/video-edit <your-project>/.claude/skills/   # one project
cp -r .claude/skills/video-edit ~/.claude/skills/                # all projects
```

## Usage

Open Claude Code in the project and give it footage:

```text
Edit trip/*.mp4 into a 30-second vertical highlight reel with music.mp3, beat-synced
```

Claude creates a job folder under `edits/<job>/`, then produces the video, an `.srt` when
there is speech, and the `edl.json` it rendered from. To make changes later, ask Claude: it
edits `edl.json` and renders again.

## How it works

| Step | Script | Output |
|---|---|---|
| Analyze | `scripts/analyze.py` | shot list with motion, sharpness, colour and loudness scores; silences; transcript with word timings; music BPM, beats and drops; labelled contact sheets |
| Draft (optional) | `scripts/draft_edl.py` | a first-pass EDL: `jumpcut` (speech only) or `montage` (best shots, beat-synced) |
| Render | `scripts/render.py` | the video: trim, speed, reframe, effects, grade, transitions, text and subtitle layer, music ducking, -14 LUFS loudness |
| Review | `scripts/preview.py` | frame sheets of the output for checking framing, text and pacing |

The scripts also run on their own:

```bash
S=.claude/skills/video-edit/scripts
python3 $S/analyze.py in.mp4 --out edits/demo/work --music song.mp3
python3 $S/draft_edl.py edits/demo/work/analysis.json --mode montage --format 9:16 --music song.mp3 --out edits/demo/edl.json
python3 $S/render.py edits/demo/edl.json --draft   # half resolution, fast
python3 $S/render.py edits/demo/edl.json           # final
```

## Features

- **Transitions:** about 40 xfade types (fade, dissolve, slides, wipes, circle, zoom, whip, ...)
- **Effects:** Ken Burns zoom, punch-in, shake, flash, glitch, freeze frame, reverse, speed
  change, black and white, blur, grain, vignette, ghost trails
- **Grades:** natural, vivid, cinematic, teal_orange, warm, cool, moody, vintage, film, bw,
  fade, bright, or a `.cube` LUT
- **Text:** title, caption, highlight, lower third, label, tag, quote; fade, pop, slide and
  typewriter animations; per-word colour highlights; CJK fonts
- **Subtitles:** built from the transcript and kept in sync across cuts and speed changes;
  exported as `.srt`
- **Audio:** background music with ducking under speech, fades, loudness normalisation
- **Framing:** crop with a focus point, blurred background fill, or letterbox for any aspect
  ratio

Text is drawn with Pillow, so ffmpeg builds without `drawtext` or `libass` work.

## Docs

- [`SKILL.md`](.claude/skills/video-edit/SKILL.md): the workflow Claude follows
- [`reference/edl.md`](.claude/skills/video-edit/reference/edl.md): EDL format
- [`reference/editing-playbook.md`](.claude/skills/video-edit/reference/editing-playbook.md):
  editing recipes by video type
