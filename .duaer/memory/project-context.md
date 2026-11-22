# Project context (as-is)

Agents treat this file as implementation truth.

## Stack

- Language / runtime: Python 3.10+ scripts, ffmpeg / ffprobe on PATH
- Libraries: numpy, Pillow, OpenCV (YuNet/SFace face models cached in
  `~/.cache/video-edit/models`), faster-whisper; optional rembg, jieba, opencc
- Package manager: none (pip installs listed in `README.md`)
- Entry points: `.claude/skills/video-edit/scripts/*.py` driven by the
  `video-edit` skill (`SKILL.md`); main ones `analyze.py`, `render.py`,
  `autocut.py`, `styles.py`, `people.py`, `cover.py`, `tts.py`, `music.py`
- Test commands: see `.duaer/memory/testing.md`

## Layout

- Skill: `.claude/skills/video-edit/` (`SKILL.md`, `reference/`, `scripts/`, `tests/`)
- Showcase sources: `examples/` (EDLs and build scripts; renders are published as
  GitHub Release assets, not committed)
- Edit jobs: `edits/<job>/` (footage, analysis, renders) — git-ignored

## Non-negotiable boundaries

- Do not: commit footage, renders, caches, API keys (keys live in env vars or
  `env_file` outside the repo, e.g. `~/.config/duaer/api.env`)
- Do not: call paid APIs from tests (tests force `CUTVIDEO_TTS_USER_CONFIG` to a
  missing file)
- Always: English docs (README, SKILL.md, reference/) with no Chinese characters
- Always: look at frames (`preview.py`) and run `--qa` before calling a render done

## Known gotchas

- The local ffmpeg has no drawtext / libass / vid.stab: text is drawn with Pillow,
  stabilisation uses `deshake`
- python.org macOS Python has no CA bundle: HTTPS goes through `tts._ssl_context()`
- Whisper answers Mandarin in Traditional unless prompted for Simplified
- The Duaer gateway's Lyria music models are refused upstream (403) as of 2026-11-22
