# Enron: Profit on Paper

A 10-minute faceless YouTube documentary (16:9, 1080p30) about how Enron reported its
financial performance — mark-to-market accounting, special-purpose entities, conflicts
of interest — and how the company collapsed in under seven weeks in late 2001.

Everything is produced with free tools: a locally run open-source voice (Kokoro-82M,
Apache-2.0), public-domain / Creative Commons photos and documents, CC BY music by
Kevin MacLeod, procedurally synthesised sound effects, and a code-driven motion-graphics
renderer (HTML/CSS/canvas rendered frame-by-frame in headless Chromium, encoded with ffmpeg).

## Deliverables

| File | What it is |
| --- | --- |
| `production/deliverables/script.md` | Full narration script with timestamps |
| `production/deliverables/youtube_description.txt` | Title options, description, chapters, sources, music/photo credits, tags |
| `production/deliverables/enron_documentary.en.srt` | English captions for YouTube upload |
| `production/narration.txt` | Narration source text (one beat per line) |

The rendered MP4 is too large for git; rebuild it with the steps below.

## Rebuild

Requirements: Python 3.11, Node 22 with Playwright + Chromium, ffmpeg, espeak-ng.

```bash
pip install kokoro soundfile scipy numpy pillow faster-whisper
cd production
python3 pipeline/fetch_music.py        # CC BY music cues (not stored in git)
python3 pipeline/tts.py                # narration -> build/narration.wav + word timings
python3 pipeline/prep_images.py        # resize + colour-grade photos -> build/img/
python3 pipeline/build_timeline.py     # shot list -> build/timeline.js, build/cues.json
python3 pipeline/sfx.py                # synthesised sound effects -> build/sfx/
python3 pipeline/audio.py              # mix, ducking, -14 LUFS -> build/mix.wav
for i in 0 1 2 3 4 5 6 7; do           # render (parallelise as CPU allows)
  NODE_PATH=$(npm root -g) node pipeline/render.js --start $((i*75)) --end $((i*75+75)) --out build/segs/seg_$i.mp4
done
bash pipeline/assemble.sh              # concat + mux -> build/enron_documentary_1080p.mp4
python3 pipeline/deliverables.py       # SRT, description, script
```

QC stills for any timestamps: `node pipeline/render.js --stills 12.5,61,300 --outdir build/stills`.

## How the edit is built

- `pipeline/shots.py` is the edit: every shot, caption, chart, document highlight and sound cue is
  anchored to a spoken word (`w("ACT2_05", "book")`), so re-generating the voice re-times the whole film.
- `web/engine.js` poses every element for an exact time `t`; nothing animates on its own,
  so each frame is deterministic.
- Charts use real daily ENE closing prices (1998–2001); restatement figures come from the
  Powers Report; document highlights are computed from the PDFs' text layer.
- Simplified explainers (the 10-year contract, growth and profit-vs-cash curves) are labelled
  "hypothetical" or "illustrative" on screen.
