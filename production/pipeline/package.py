"""Final encode and packaging.

  build/enron_documentary_1080p.mp4   YouTube upload master: H.264 High 1080p30 (CRF 16, ~9-10 Mbps), AAC 384 kbps, faststart
  build/enron_frag.mp4                same streams remuxed as fragmented MP4 (plays while it is reassembled in a browser)
  build/chunks/enron_NN.mp4           the fragmented file cut at fragment boundaries into pieces under 19 MiB
  build/chunks/index.json             piece sizes and the total, for the download page
"""
import json
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
LIMIT = 19 * 1024 * 1024


def run(cmd):
    print("$", " ".join(map(str, cmd)))
    subprocess.run(cmd, check=True)


def encode():
    out = BUILD / "enron_documentary_1080p.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", BUILD / "video_only.mp4", "-i", BUILD / "mix.wav",
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-maxrate", "20M", "-bufsize", "40M", "-profile:v", "high", "-level", "4.1",
         "-pix_fmt", "yuv420p", "-r", "30", "-g", "60", "-bf", "2",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
         "-c:a", "aac", "-b:a", "384k", "-ar", "48000", "-shortest", "-movflags", "+faststart",
         "-metadata", "title=Enron: Profit on Paper", out])
    return out


def boxes(path):
    with open(path, "rb") as f:
        data = f.read()
    i = 0
    while i < len(data):
        size, kind = struct.unpack(">I4s", data[i:i + 8])
        if size == 1:
            size = struct.unpack(">Q", data[i + 8:i + 16])[0]
        yield kind.decode(), i, size
        i += size


def split(frag):
    outdir = BUILD / "chunks"
    outdir.mkdir(exist_ok=True)
    for old in outdir.glob("enron_*.mp4"):
        old.unlink()
    data = frag.read_bytes()
    groups, cur, cur_size = [], [], 0
    pending = []          # a moof always travels with the mdat that follows it
    for kind, off, size in boxes(frag):
        pending.append((off, size))
        if kind in ("moof",):
            continue
        unit = sum(s for _, s in pending)
        if cur and cur_size + unit > LIMIT:
            groups.append(cur)
            cur, cur_size = [], 0
        cur += pending
        cur_size += unit
        pending = []
    if pending:
        cur += pending
    if cur:
        groups.append(cur)
    sizes = []
    for n, g in enumerate(groups):
        a, b = g[0][0], g[-1][0] + g[-1][1]
        (outdir / f"enron_{n:02d}.mp4").write_bytes(data[a:b])
        sizes.append(b - a)
    assert sum(sizes) == len(data) and max(sizes) <= LIMIT
    (outdir / "index.json").write_text(json.dumps({"total": len(data), "sizes": sizes}, indent=1))
    print(f"{len(sizes)} pieces, total {len(data)} bytes, largest {max(sizes)}")


def main():
    master = encode()
    frag = BUILD / "enron_frag.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", master, "-c", "copy", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
         "-frag_duration", "2000000", frag])
    split(frag)


if __name__ == "__main__":
    main()
