# E2E scenario catalog (cutvideo)

User-visible paths of the `video-edit` skill. When a change touches one, update its
scenario and run the levels the risk table in
[`.duaer/memory/testing.md`](../../.duaer/memory/testing.md) requires. Real-footage
runs use a job folder under `edits/` (git-ignored); the reference job is
`edits/tears-of-steel/` (Tears of Steel, CC BY 3.0).

## Scenario template

```text
ID:          E2E-NNN
Title:       Short name
Preconditions:
Steps:
Expected:
Automated:   test names, or "L3 manual"
```

## Scenarios

```text
ID:          E2E-001
Title:       Analyse footage
Preconditions: one video in src/
Steps:       analyze.py src/*.mp4 --out work
Expected:    work/analysis.json with shots, transcript, music beats; contact sheets
Automated:   test_pipeline.py::test_analyze_finds_the_cuts
```

```text
ID:          E2E-002
Title:       Style menu
Steps:       styles.py work/analysis.json
Expected:    work/styles.md ranks every style with evidence and gaps
Automated:   test_pipeline.py::test_style_menu_scores_every_style
```

```text
ID:          E2E-003
Title:       Auto drafts in several styles
Steps:       autocut.py work/analysis.json --styles all [--exclude credits ranges]
Expected:    one video per style (title3d, text_behind, fx3d, talking included),
             auto/report.md, auto/compare.jpg, loudness near -14 LUFS, true peak
             <= -1 dBTP, brand signature on the end card; a style without footage or
             a dependency (rembg, Node/puppeteer/Chrome, transcript) is listed with
             its reason and the other styles still render
Automated:   test_pipeline.py::test_autocut*, test_unit.py::test_*_recipe*,
             test_unit.py::test_every_menu_style_has_a_recipe; L3 frames on real footage
```

```text
ID:          E2E-004
Title:       Narrated recap
Preconditions: tts.json provider (system voice works offline)
Steps:       autocut.py ... --styles recap --script recap.txt
Expected:    every line spoken in a voice of its language, captions follow the
             voice word by word, key lines in the emphasis style, music ducks
Automated:   test_pipeline.py::test_narration*, test_unit.py::test_tts_*; voice L4
```

```text
ID:          E2E-005
Title:       Recognise people and cast intros
Steps:       people.py work/analysis.json; --name P1=...; autocut --styles cast
Expected:    one intro per named person, frozen on their face, cut-out on that person
Automated:   test_unit.py::test_cluster_*; L3 on real footage
```

```text
ID:          E2E-006
Title:       Covers
Steps:       cover.py work/analysis.json --title ...
Expected:    covers/ for 16:9, 9:16, 3:4, 1:1 and sheet.jpg
Automated:   test_pipeline.py::test_covers_for_every_format
```

```text
ID:          E2E-007
Title:       Split screen, picture in picture, speed ramp, stabilisation
Steps:       render an EDL with split / layers / ramp / stabilize
Expected:    exact planned length, windows slide in, ramp eases in and out
Automated:   test_pipeline.py::test_split_ramp_stabilize_and_pip_render
```
