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
4. Mostly hard cuts; `fade`/`dissolve` for a change of time or place; `fadeblack` at the end.
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

## Beat-synced vertical short (抖音 / Reels / Shorts)

1. 9:16, 7-30 s. First frame must already be interesting (no fade in from black).
2. Hook text in the first second (`title` or `highlight`, `pop`).
3. Cuts every 0.4-1.0 s, locked to beats. `fit: "crop"` with `focus` on the subject.
4. End on a loopable or punchy frame; optional `tag` CTA in the last 1.5 s.

## Product / event / recap

1. Open with the result or best visual, then the context, then details, then the CTA.
2. Clean grade (`natural` or `bright`), `label` / `lower_third` for names and specs.
3. Transitions: `slideleft`, `wipeleft`, `fade`; keep them consistent.
4. Logo via `images` (top corner, opacity 0.8) or an end card (`color` clip + `title`).

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
