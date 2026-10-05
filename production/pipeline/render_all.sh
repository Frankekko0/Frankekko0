#!/usr/bin/env bash
# Render the whole timeline in 8 segments, 4 at a time, then concatenate.
set -euo pipefail
cd "$(dirname "$0")/.."
export NODE_PATH=${NODE_PATH:-/opt/node22/lib/node_modules}
DUR=$(python3 -c "import json,re;t=open('build/timeline.js').read();print(json.loads(t[t.index('{'):t.rindex('}')+1])['duration'])")
mkdir -p build/segs && rm -f build/segs/seg_*.mp4 build/segs/seg_*.log
STEP=$(python3 -c "import math;print(math.ceil($DUR/8*30)/30)")
run() { local i=$1; local a=$(python3 -c "print(round($i*$STEP,4))"); local b=$(python3 -c "print(round(min(($i+1)*$STEP,$DUR),4))");
  node pipeline/render.js --start "$a" --end "$b" --out "build/segs/seg_$i.mp4" > "build/segs/seg_$i.log" 2>&1; }
for i in 0 1 2 3; do run $i & done; wait
for i in 4 5 6 7; do run $i & done; wait
ls build/segs/seg_*.mp4 | sort -V | sed "s#^build/segs/#file '#; s#\$#'#" > build/segs/list.txt
ffmpeg -y -v error -f concat -safe 0 -i build/segs/list.txt -c copy build/video_only.mp4
echo ALLDONE
