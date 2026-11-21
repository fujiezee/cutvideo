#!/usr/bin/env bash
# Set up the job folder for the Tears of Steel examples. Run from the repository root:
#   bash examples/tears-of-steel/prepare.sh
set -euo pipefail
ROOT="$(pwd)"
S="$ROOT/.claude/skills/video-edit/scripts"
J="$ROOT/edits/tears-of-steel"
mkdir -p "$J/src" "$J/audio"
cd "$J"
if [ ! -f src/tos_720p.mov ]; then
  echo "downloading Tears of Steel 720p (CC BY 3.0, ~370 MB)..."
  curl -L -o src/tos_720p.mov https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov
fi
if [ ! -f work/analysis.json ]; then
  python3 "$S/analyze.py" src/tos_720p.mov --out work --lang en --model small
fi
# the film's credits score is the music bed for every example
[ -f audio/score_credits.wav ] || ffmpeg -v error -y -ss 589 -to 734 -i src/tos_720p.mov -vn -ac 2 -ar 48000 audio/score_credits.wav
python3 "$ROOT/examples/tears-of-steel/fix_transcript.py" work/analysis.json
echo "ready: $J"
