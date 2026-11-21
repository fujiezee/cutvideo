#!/usr/bin/env bash
# Build every example EDL and render it. Run from the repository root after prepare.sh.
set -euo pipefail
ROOT="$(pwd)"
R="$ROOT/.claude/skills/video-edit/scripts/render.py"
B="$ROOT/examples/tears-of-steel/build"
cd "$ROOT/edits/tears-of-steel"
for b in trailer cast karaoke beatmix vhs silent cyberpunk; do python3 "$B/build_$b.py"; done
for e in edl_trailer edl_trailer_9x16 edl_cast edl_karaoke edl_beatmix edl_vhs edl_silent edl_cyberpunk; do
  echo "== $e"; python3 "$R" "$e.json" --no-review "$@"
done
