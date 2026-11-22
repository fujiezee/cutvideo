# Baseline (project-specific)

Frozen decisions for the adopting project: language, stack, architecture
boundaries, and branch model.

## Branch model (mandatory)

| Branch | Role |
|---|---|
| `main` | Production / officially online |
| `develop` | Day-to-day integration |
| `feat/<name>` | Features |
| `fix/<name>` | Bug fixes |

See [branching-and-release](agent/branching-and-release.md).

**This project:** integration branch `develop`; promote to **`main`** when
shipping; no application runtime (scripts run on demand, nothing to restart on
handoff).

When you copy agent ops into another repository, keep this branch model unless
the project explicitly documents a justified exception in an ADR (not
recommended).


## Integration branch

`develop`

## Frozen decisions (cutvideo)

- Product: the `video-edit` Claude Code skill turns footage into finished edits
  with no extra input from the user.
- Rendering: ffmpeg for video and audio; every text, caption and title is drawn
  with Pillow (the target ffmpeg builds lack drawtext / libass).
- Recognition: faster-whisper for transcription and audio QA; OpenCV YuNet + SFace
  for faces.
- Generative services (voice, music, images) go through a configurable
  OpenAI-compatible gateway (the Duaer model gateway by default); every one has
  an offline fallback, and tests never call them.
- Edits are described as JSON EDLs (`reference/edl.md`) and rendered by
  `render.py`; recipes and auto drafts write EDLs, never video directly.
- Docs are English with no Chinese characters; user-facing UI text in renders
  follows the footage's language.
