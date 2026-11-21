# Edit plan: trailer

Goal: a cinema-style trailer that sells the story in about a minute.
Format: 16:9 1920x1080, 24 fps, ~71 s, letterboxed (`fit: pad`) to keep the 2.39:1 frame.
A vertical cut of the same edit uses `fit: blur` (`edl_trailer_9x16.json`).
Logline: he chose space over her; forty years later she came back with an army of robots.

## Structure
| # | Section | Time | Picture | Sound | Text |
|---|---|---|---|---|---|
| 1 | Launch | 0-9 s | countdown on black, engines igniting | "main engine start... one", sub drop | - |
| 2 | Card | 9-11 s | black | hit | 四十年前，他离开了她 |
| 3 | The quarrel | 11-24 s | Thom / Celia close-ups on the bridge | four lines, ducked score | bilingual captions |
| 4 | The turn | 24-37 s | 40 YEARS LATER, city, sniper, scope, the lab | boom; score dips to near silence | 她回来了 |
| 5 | Drop | 37-47 s | 10 battle shots, one per bar | boom + swell on the drop | - |
| 6 | Heartbreak | 47-53 s | "no reason to destroy the world", "You broke my heart", "You forgot me on Earth" | dialogue over the score | captions |
| 7 | Burst | 53-58 s | 6 action shots, last one on the second hit (57.5 s) | hit | - |
| 8 | Title | 59-66 s | old Thom, half a second of silence, TEARS OF STEEL | riser, boom on the title | 钢 铁 之 泪 |
| 9 | Button | 66-68 s | "I should just crush you" (J-cut) | line | caption |
| 10 | Credit | 68-71 s | black | - | attribution |

## Music
Credits score as one continuous bed, `offset` chosen so its 74 s hit is the drop; action
beat-synced every 2 beats; the second hit and the title pinned with `start_at_music`.

## Look
Per-shot `adjust` pulls the cold future shots and the gold battle shots toward each other,
then `trailer` grade; letterbox bars stay static during zooms.
