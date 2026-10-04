#!/usr/bin/env bash
# Concatenate rendered segments, mux the final mix, encode the YouTube master.
set -euo pipefail
cd "$(dirname "$0")/.."
ls build/segs/seg_*.mp4 | sort -V | sed "s#^build/segs/#file '#; s#\$#'#" > build/segs/list.txt
ffmpeg -y -v error -f concat -safe 0 -i build/segs/list.txt -c copy build/video_only.mp4
# YouTube master: H.264 High, 1080p30, ~16 Mbps cap, AAC 320k, faststart
ffmpeg -y -v error -i build/video_only.mp4 -i build/mix.wav \
  -map 0:v:0 -map 1:a:0 \
  -c:v libx264 -preset slow -crf 18 -maxrate 16M -bufsize 32M -profile:v high -level 4.1 \
  -pix_fmt yuv420p -r 30 -g 60 -bf 2 -movflags +faststart \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 \
  -c:a aac -b:a 320k -ar 48000 -shortest \
  -metadata title="Enron: Profit on Paper" \
  build/enron_documentary_1080p.mp4
ffprobe -v error -show_entries format=duration,size,bit_rate -of default=nw=1 build/enron_documentary_1080p.mp4
