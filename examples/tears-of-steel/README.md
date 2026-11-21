# Example edits: Tears of Steel

Eight edits of the Blender Foundation short film *Tears of Steel* (2012), each in a
different style, all made with the `video-edit` skill. Download the finished videos from the
[showcase release](https://github.com/fujiezee/cutvideo/releases/tag/showcase-v1).

Source: *Tears of Steel* © Blender Foundation, [mango.blender.org](https://mango.blender.org),
licensed under [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). Every edit ends with
an attribution card.

| Style | Format | Length | Plan | Build script |
|---|---|---|---|---|
| Trailer | 16:9 | 1:11 | [trailer.md](plans/trailer.md) | [build_trailer.py](build/build_trailer.py) |
| Trailer, vertical | 9:16 | 1:11 | [trailer.md](plans/trailer.md) | [build_trailer.py](build/build_trailer.py) |
| Cast intros | 16:9 | 0:35 | [cast.md](plans/cast.md) | [build_cast.py](build/build_cast.py) |
| Word-highlight captions | 9:16 | 0:26 | [karaoke.md](plans/karaoke.md) | [build_karaoke.py](build/build_karaoke.py) |
| Beat-sync mix | 9:16 | 0:20 | [beatmix.md](plans/beatmix.md) | [build_beatmix.py](build/build_beatmix.py) |
| Retro VHS | 4:3 | 0:39 | [vhs.md](plans/vhs.md) | [build_vhs.py](build/build_vhs.py) |
| Silent film | 4:3 | 0:46 | [silent.md](plans/silent.md) | [build_silent.py](build/build_silent.py) |
| Cyberpunk glitch | 16:9 | 0:41 | [cyberpunk.md](plans/cyberpunk.md) | [build_cyberpunk.py](build/build_cyberpunk.py) |

## Reproduce

From the repository root (needs the skill's requirements; about 10 minutes of rendering):

```bash
bash examples/tears-of-steel/prepare.sh      # download the film, analyse it, extract the score
bash examples/tears-of-steel/render_all.sh   # build every EDL and render into edits/tears-of-steel/out/
```

`prepare.sh` also runs [`fix_transcript.py`](fix_transcript.py), which corrects the speech
recognition mistakes the captions depend on. To render one style, run its build script from
`edits/tears-of-steel/` and pass the EDL it writes to `render.py`.

Each build script is a plain Python file that writes the EDL (JSON). Read one next to its plan
to see how a plan turns into clips, music, sounds and text.
