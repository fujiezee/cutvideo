# Edit plan: beat-sync vertical mix

Goal / platform: a short that stops the scroll on Douyin / Reels / Shorts.
Format: 9:16 1080x1920, 30 fps, ~22 s, `fit: crop` with `focus: auto` (face-centred crop).
Logline: forty years after a breakup, robots have taken Amsterdam, told in one beat per shot.

## Structure
| # | Section | Time | Picture | Sound | Text |
|---|---|---|---|---|---|
| 1 | Hook | 0-3 s | city under robot rule, sniper, ship in scope (2 beats each) | score from 0 s, hit | "40年后 / 机器人占领了阿姆斯特丹" |
| 2 | The couple | 3-4.5 s | Thom, Celia, Celia angry (1 beat each, flash) | score | - |
| 3 | The lab | 4.5-7 s | holograms, controls, brain, overwrite, robot wakes | score | - |
| 4 | Battle | 7-14 s | 13 action shots, one per beat, shakes and flashes | boom on the first | - |
| 5 | Heartbreak | 14-18 s | scientist scream, "You broke my heart", "crush you" (2 beats) | whoosh | - |
| 6 | Out | 18-22 s | robot, dark robot fading, end card | score fades | "钢铁之泪", attribution |

## Music
Credits score from 0 s (its loudest, steadiest part, 120 BPM). `beat_sync` every beat over
all clips, `shift: auto` to put cuts on real onsets.

## Look
`vivid` grade; flashes on 3 cuts, shakes on 2, one punch-in. Clip audio at 0.25 as texture.

## Risks
2.39:1 source cropped to 9:16 keeps ~24 % of the width: rely on `focus: auto`; shots without
faces fall back to a centre crop.
