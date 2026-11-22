# Feature Specification: Auto drafts for four more styles

**Feature Branch**: `feat/autocut-more-styles`
**Created**: 2026-11-22
**Status**: Confirmed
**Input**: User picked "continue with features: auto drafts for the 3D title opener,
text behind person, particle / hologram effects and talking-head jump cut".

## Goal

`autocut.py` makes a draft for every style the style menu lists. Today it has recipes
for eight styles; `title3d`, `text_behind`, `fx3d` and `talking` are scored by
`styles.py` but need a hand-written EDL.

## In scope

- Recipe `title3d`: a 3D title opener on a calm wide shot (blurred shot or black as
  fallback), slow push-in, title reveal with a hit, a few quick story shots, end card.
- Recipe `text_behind`: two or three static single-person shots, each with a word or
  name placed between the background and the person (names from `people.json` when
  named, else the film title / keywords).
- Recipe `fx3d`: dark / colourful shots with three.js overlays (particles, hologram,
  streaks, grid) as accents, on music.
- Recipe `talking`: a jump cut of the speech (pauses and filler words removed when the
  transcript has them), speech clean-up, captions from the transcript, vertical with the
  speaker tracked.
- Each recipe reports a specific reason instead of failing when its footage or its
  dependency is missing (rembg for `text_behind`, Node + puppeteer + Chrome for `fx3d`);
  other styles still render.
- `MOOD` entries for generated music, docs (SKILL.md, README, E2E catalog).

## Out of scope

- New effects or new render features; recipes only combine existing EDL features.
- Pushing to GitHub, re-rendering the showcase.

## Acceptance

1. `autocut.py --styles title3d,text_behind,fx3d,talking` on Tears of Steel renders
   `title3d`, `text_behind` and `fx3d` drafts with QA passing (loudness near -14 LUFS,
   true peak <= -1 dBTP) and frames that show the effect (L3, frames looked at).
2. `talking` renders a jump cut on a talking clip (synthetic speech with filler words)
   with the fillers gone from the output transcript; on footage that is not mostly
   one person talking it still produces a draft from the densest dialogue stretch and
   says so in the report.
3. A recipe whose dependency or footage is missing returns a note naming the reason;
   `--styles all` still finishes.
4. Unit tests build each new recipe's EDL from a synthetic analysis; the full suite passes.
5. English docs updated (no Chinese characters); E2E catalog scenario E2E-003 covers the
   new styles.

## Assumptions

- Recipes reuse existing effects as documented in `reference/edl.md`.
- `text_behind` costs about 0.7 s per frame, so its draft keeps the effect to about
  3 s per title.
