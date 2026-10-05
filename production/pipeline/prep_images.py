"""Resize and colour-grade source photos into build/img/ so every shot shares one look."""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "img"
DST = ROOT / "build" / "img"
DST.mkdir(parents=True, exist_ok=True)

# Images that must not be graded (documents, logos, mugshots get a light touch).
NO_GRADE = {"enron_logo", "hearing_doc_collapse", "hearing_doc_shredding", "skilling_indictment", "code_ethics_cover"}
LIGHT = {"ken_lay", "skilling_mug", "mclean", "watkins", "andersen_witnesses", "hearing_0124"}
MID_LIFT = 0.87   # gamma < 1 lifts mid-tones (~ +10% at 50% grey) without touching black or white
BW = {"ken_lay", "skilling_mug", "andersen_witnesses", "enron_complex", "houston_pano_night", "wallst_2000", "shredder_detail",
      "casey_courthouse", "supreme_court", "rbc_floor", "pipeline", "enron_field"}


def grade(arr, strength=1.0):
    x = arr.astype(np.float32) / 255.0
    lum = (0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2])[..., None]
    x = lum + (x - lum) * (1 - 0.2 * strength)                       # desaturate a touch
    x = 0.5 + (x - 0.5) * (1 + 0.08 * strength)                       # contrast
    shadows = np.clip(1 - lum * 2.2, 0, 1)
    highs = np.clip((lum - 0.55) * 2.2, 0, 1)
    x += strength * (shadows * np.array([-0.018, 0.006, 0.03]) + highs * np.array([0.03, 0.012, -0.022]))
    x = 0.035 * strength + x * (1 - 0.05 * strength)                  # lifted, filmic blacks
    x = np.clip(x, 0, 1) ** MID_LIFT                                   # open up the mid-tones for phone screens
    return (np.clip(x, 0, 1) * 255).astype(np.uint8)


def to_bw(arr):
    x = arr.astype(np.float32) / 255.0
    lum = 0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]
    lum = 0.5 + (lum - 0.5) * 1.18
    lum = 0.03 + np.clip(lum, 0, 1) * 0.94
    lum = lum ** MID_LIFT
    out = np.stack([lum * 1.0, lum * 0.985, lum * 0.95], -1)            # faint warm paper tone
    return (np.clip(out, 0, 1) * 255).astype(np.uint8)


def main():
    done = []
    for p in sorted(SRC.glob("*")):
        if p.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        key = p.stem
        im = Image.open(p)
        im = ImageOps.exif_transpose(im)
        if key == "enron_logo":
            im = im.convert("RGBA")
            im.thumbnail((1400, 1400), Image.LANCZOS)
            im.save(DST / f"{key}.png")
            done.append(key)
            continue
        im = im.convert("RGB")
        w, h = im.size
        limit = 2600 if w >= h else 2000
        if max(w, h) > limit:
            im.thumbnail((limit, limit), Image.LANCZOS)
        arr = np.asarray(im)
        if key not in NO_GRADE:
            arr = grade(arr, 0.5 if key in LIGHT else 1.0)
        Image.fromarray(arr).save(DST / f"{key}.jpg", quality=90)
        if key in BW:
            Image.fromarray(to_bw(np.asarray(im))).save(DST / f"{key}_bw.jpg", quality=90)
        done.append(key)
    print(len(done), "images ->", DST)
    print(json.dumps({k: list(Image.open(next(DST.glob(k + '.*'))).size) for k in done}))


if __name__ == "__main__":
    main()
