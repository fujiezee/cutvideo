# Editing playbook

Pick the recipe that matches the footage and the ask, then adapt it. Rules of thumb at the end.

## Talking head / explainer / podcast clip

1. Cut on the transcript: drop silences, "嗯/啊/um", false starts, repeated takes (keep the
   last good take of a repeated sentence), off-topic asides.
2. Keep 0.1-0.15 s of air before and after each phrase; never cut mid-word.
3. Hide jump cuts: alternate `punch` (1.1-1.2) on every 2nd-3rd cut, or lay B-roll over
   them.
4. Hook in the first 3 s: open on the strongest sentence (it may come from later in the
   recording), then go back to the start.
5. Captions on; `highlight` the key words; a `title` or `tag` for the topic.
6. Grade `natural`; music optional, low (0.12-0.25) with `duck`.

## Vlog / day-in-the-life / travel

1. Build a story: arrival / setting → activities → highlight → wrap-up.
2. Establishing shot (wide, `zoom_in` 0.06-0.1) at each new location, with a
   `lower_third` naming the place.
3. Shot lengths 1.5-4 s for B-roll, longer for speaking moments; vary them.
4. Mostly hard cuts; `fade` for a change of time or place; `fadeblack` at the end.
5. Grade `warm`, `film` or `teal_orange`; music bed at 0.25-0.4 under speech, louder in
   music-only sections (split into several `music` items if needed).

## Highlight montage / mixed cut / sizzle reel

1. Choose shots by `score` and by what you saw on the sheets: action peaks, faces,
   reactions, colour. Drop near-duplicates.
2. Order for energy: strong opener → build → peak → calm outro (or end on the peak).
3. With music: `beat_sync` (`every: 1` for fast songs < 100 BPM or high energy, `every: 2`
   for 100-140 BPM). Put the best shot on the first `drop`.
4. Accents on strong beats/drops: `flash`, `shake`, `punch`, `glitch`, `zoomin` or
   `whip` transitions. Use them on maybe 1 in 4 cuts, not all.
5. Speed: `speed` 0.5 for one or two hero moments, 1.5-2 for travel/process shots.
6. Clip audio at 0-0.3 (`audio.clip_volume`); music volume 0.8-1.

## Trailer / teaser from a longer film

1. Find the story in the transcript: setup line(s), the conflict, a turn ("years later"), the
   stakes, and one strong or funny line kept for after the title (the "button").
2. One continuous music bed under everything (the film's own score from its credits works and
   shares its licence). Analyse it (`analyze.py --music bed.wav`): note quiet stretches, dips
   and hits in `energy_db_1hz`, and check the detected beats against those hits.
3. Structure on the music: dialogue over the quiet part; a dip of near-silence right before the
   drop; beat-synced action from the drop (`beat_sync`, `every: 2`); dialogue again over the
   calmer middle; a short burst; a beat of silence; title pinned on the biggest hit
   (`start_at_music`); button line; end card.
4. Set the music `offset` so the drop lands where act one ends (plan with `--plan`).
5. Dialogue clips: `duck_music`, `snap: false`, raise quiet lines (`volume` 1.5-2.5), use
   `audio_tail` / `audio_lead` instead of cutting words when the picture must change.
   Action clips: clip audio low (0.3-0.4) so the film's own score does not fight the bed.
6. Sound design with `sounds`: `boom` on the drop and title, `riser` ending just before the
   title (leave ~0.5 s of silence), `swell` into the drop, `whoosh` on a few fast cuts,
   `hit` on accents, `sub_drop` into a mood change.
7. Interstitial `card` texts on black clips tell the story in a few words.
8. Match colour across scenes with per-clip `adjust`, then one shared grade (`trailer`).
9. Credit the source on an end card when its licence requires attribution.

## Beat-synced vertical short (抖音 / Reels / Shorts)

1. 9:16, 7-30 s. First frame must already be interesting (no fade in from black).
2. Hook text in the first second (`title` or `highlight`, `pop`).
3. Cuts every 0.4-1.0 s, locked to beats. `fit: "crop"` with `focus: "track"` so the
   subject stays in frame when they move.
4. End on a loopable or punchy frame; optional `tag` CTA in the last 1.5 s.

## Product / event / recap

1. Open with the result or best visual, then the context, then details, then the CTA.
2. Clean grade (`natural` or `bright`), `label` / `lower_third` for names and specs.
3. Transitions: `slideleft`, `wipeleft`, `fade`; keep them consistent.
4. Logo via `images` (top corner, opacity 0.8) or an end card (`color` clip + `title`).

## Character intros (cast reel, short drama, variety, team / event video)

1. One intro per person, the first time they appear. Choose a frame where they are still,
   face visible, separated from the background (medium shot); extreme close-ups get the
   no-cut-out look.
2. 1-2 s into their shot, `hold` 1.5-2 s; one accent `color` per person, used consistently.
3. `name` short (native name + romanisation is fine), `role` one line, `index` for a
   numbered roster.
4. Keep music running under the freeze; the intro adds its own hit and whoosh.

## Short-video captions (Douyin / Reels / Shorts)

Big bold captions with the word being spoken highlighted (`karaoke: "word"`,
`karaoke_box`), 1-2 lines of 8-14 characters, placed around 70-75 % of the height. Fix the
transcript first: wrong words are very visible when they light up.

## Period and genre looks

Worked examples with plans and build scripts: `examples/tears-of-steel/` in the repository.

- **Retro VHS / home video**: 4:3 (`1440x1080`, `fit: crop`, `focus: auto`), `vhs` grade,
  `images: [{"preset": "scanlines"}]`, `stamp` texts (PLAY ▶, SP, date) with `until_clip`,
  short `glitch` on a few cuts as tracking errors, tape sound via `audio_filter` / music
  `filter` (`highpass=f=180,lowpass=f=4500`), end on a blue STOP screen.
- **Silent film**: 4:3, `silent` grade, `flicker` on every shot, `speed` 1.1-1.2, clips
  `mute`, `intertitle` cards on black instead of dialogue (cards set `grade: silent` to share
  the grain), `circleopen` / `circleclose` iris transitions, gramophone-filtered music.
- **Cyberpunk / glitch**: full-frame 16:9, `cyberpunk` grade, alternate `glitch`,
  `rgb_split`, `echo`; `pixelize` / `hlslice` between sections; `neon` texts; `glitch` SFX on
  glitch cuts; beat sync every 2 beats.
- **Beat-sync vertical mix**: 9:16 `fit: crop` + `focus: auto`, `beat_sync` every beat with
  `shift: auto`, hook text in the first second, accents on ~1 cut in 5.

## Multi-camera / multiple takes of the same moment

Match moments by transcript text or by visual content, keep the best-framed angle for each
line, and cut between angles on sentence boundaries.

## Rules of thumb

- Every cut should have a reason: new information, a reaction, a beat, a better angle.
- Do not cut on a blink or mid-gesture; cut on action (motion continues across the cut).
- Avoid flashes and glitches on more than ~25 % of cuts; they lose impact.
- Keep text within the central 80 % of the frame, away from faces; one idea per text card.
- Leave 1-2 s at the end for music and visuals to resolve (`fade_out` on the last clip).
- Read the transcript for the story; look at the sheets for the pictures; combine both.
