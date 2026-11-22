Round: 2
Commit: 014f63d0f8b08997561e53d5be9084b3972a5d8b

What this change does: autocut.py gets recipes for the four menu styles that had none
(3D title opener, text behind people, three.js light effects, talking-head jump cut), so
every style the menu scores can be drafted in one run. Missing footage or a missing
dependency gives a named reason instead of a failure. Round 2 rechecks the two round-1
fixes: the talking cut now keeps every transcribed file of a talk in order, and the title
opener only uses pieces of 2.8 s or more. Load assumed: one person running the script on
one job.

Looks good. Ship.
Checked: the full job diff against develop, callers of features() / Picker / end_card /
fx3d.available, the transcript-subtitle mapping for cut filler words, both new tests
(they fail on the round-1 code), 54 tests passing, real-footage renders of all four
styles with QA and frames looked at.

Verdict: Ship.
Not checked: three.js rendering without Chrome on Linux (no such machine here).
