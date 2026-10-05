# Enron: Profit on Paper

A 10-minute faceless YouTube documentary (16:9, 1080p30, burned-in animated subtitles) about how Enron reported its
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
| `production/deliverables/enron_documentary.en.srt` | English captions for YouTube upload (figures, ≤ 42 characters per line) |
| `production/deliverables/thumbnail*.png` | Three thumbnails for YouTube's Test & Compare |
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
python3 pipeline/build_timeline.py     # shot list + subtitles -> build/timeline.js, build/cues.json
node pipeline/layout_audit.js          # reports any graphic that touches a subtitle
python3 pipeline/sfx.py                # synthesised sound effects -> build/sfx/
python3 pipeline/audio.py              # mix, ducking, -14 LUFS / -1.5 dBTP -> build/mix.wav
bash pipeline/render_all.sh            # 8 segments, 4 in parallel -> build/video_only.mp4
python3 pipeline/package.py            # upload master + fragmented copy cut into <19 MiB pieces
python3 pipeline/deliverables.py       # SRT, description, script
node pipeline/thumbnail.js             # thumbnails
```

QC stills for any timestamps: `node pipeline/render.js --stills 12.5,61,300 --outdir build/stills`.

## How the edit is built

- `pipeline/shots.py` is the edit: every shot, caption, chart, document highlight and sound cue is
  anchored to a spoken word (`w("ACT2_05", "book")`), so re-generating the voice re-times the whole film.
- `web/engine.js` poses every element for an exact time `t`; nothing animates on its own,
  so each frame is deterministic.
- `pipeline/subs.py` aligns the script word by word with the voice, rewrites spoken numbers as
  figures ("six hundred and eighteen million dollars" -> "$618 million") and groups the words into
  short chunks. The renderer lights each word as it is spoken (gold bar under the current word,
  keywords in the film's colours: gold for profit and money, cyan for cash, red for losses).
  Lines already shown as large on-screen type carry no subtitle.
- Charts use real daily ENE closing prices (1998–2001); restatement figures come from the
  Powers Report; document highlights are computed from the PDFs' text layer.
- Simplified explainers (the 10-year contract, growth and profit-vs-cash curves) are labelled
  "hypothetical" or "illustrative" on screen.
