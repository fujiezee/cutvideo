Round: 1
Commit: 290512c3e828ebf75a371e55d0841be0af08ad6b

What this change does: autocut.py gets recipes for the four menu styles that had none
(3D title opener, text behind people, three.js light effects, talking-head jump cut), so
every style the menu scores can be drafted in one run. Missing footage or a missing
dependency now gives a named reason instead of a failure. Load assumed: one person
running the script on one job.

**Must fix:**

1. **Talking-head cut keeps only one file** (`scripts/autocut.py` r_talking)
   - **What this is:** The talking recipe builds the jump cut from the transcript.
   - **Problem:** It picks the single video with the most words. A talk recorded as three
     phone clips becomes a draft of one clip; the other two are dropped without a note.
   - **Fix:** When the footage is mostly talking, take the speech of every transcribed video
     in order; keep the single-video densest minute only for footage that is not mostly talk.
   - **If we skip it:** Users with split recordings get a third of their talk and no warning.

2. **Title can vanish on a short opener shot** (`scripts/autocut.py` r_title3d)
   - **What this is:** The opener title runs from 0.7 s to the end of the first shot.
   - **Problem:** When every wide shot (and the fallback best shot) is shorter than about
     1 s, the piece is under 0.8 s, so the title ends before it starts and never shows.
   - **Fix:** Only use opener pieces of at least 2.8 s, for the wide shots and the fallback.
   - **If we skip it:** Footage of short cuts gets an opener with no title.

Verdict: fix 1 and 2 first.
Not checked: three.js rendering without Chrome on Linux (no such machine here).
