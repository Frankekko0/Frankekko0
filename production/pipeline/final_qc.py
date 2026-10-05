"""Measurements on the final encoded file: container, loudness, black/freeze, flashes,
intelligibility (Whisper) and subtitle-to-voice sync. Prints a summary and writes build/qc.json."""
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
FILE = Path(sys.argv[1]) if len(sys.argv) > 1 else BUILD / "enron_documentary_1080p.mp4"
QC = {}


def probe():
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration,size,bit_rate:stream=codec_name,profile,level,width,height,r_frame_rate,pix_fmt,color_space,bit_rate,nb_frames,sample_rate,channels,duration",
                          "-of", "json", str(FILE)], capture_output=True, text=True).stdout
    QC["probe"] = json.loads(out)


def loudness():
    wav = BUILD / "qc_audio.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(FILE), "-vn", "-c:a", "pcm_f32le", str(wav)], check=True)
    err = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-i", str(wav), "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    s = err[err.rfind("Summary:"):]
    QC["loudness"] = {"I": float(re.search(r"I:\s+(-?[\d.]+)", s).group(1)), "LRA": float(re.search(r"LRA:\s+(-?[\d.]+)", s).group(1)),
                      "TP": float(re.search(r"Peak:\s+(-?[\d.]+)", s).group(1))}
    return wav


def video_pass():
    frames = BUILD / "qc_frames.txt"
    err = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-i", str(FILE), "-vf",
                          f"scale=480:270,signalstats,blackdetect=d=0.1:pix_th=0.10,freezedetect=n=0.003:d=2,metadata=print:key=lavfi.signalstats.YAVG:file={frames}",
                          "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
    blacks = [tuple(map(float, m)) for m in re.findall(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", err)]
    freezes = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", err)]
    T, Y, cur = [], [], None
    for line in frames.read_text().splitlines():
        if line.startswith("frame:"):
            cur = float(re.search(r"pts_time:([\d.]+)", line).group(1))
        elif "YAVG=" in line:
            T.append(cur)
            Y.append(float(line.split("=")[1]))
    T, Y = np.array(T), np.array(Y)
    L = 200 * np.clip((Y - 16) / 219, 0, 1) ** 2.4
    d = np.diff(L)
    trans, start = [], 0
    for k in range(1, len(d)):
        if np.sign(d[k]) != np.sign(d[k - 1]) or k == len(d) - 1:
            delta = L[k] - L[start]
            if abs(delta) >= 20 and min(L[k], L[start]) < 160:
                trans.append((T[k], delta))
            start = k
    fl = np.array([b[0] for a, b in zip(trans, trans[1:]) if np.sign(a[1]) != np.sign(b[1])])
    mx = max([((fl >= t0) & (fl < t0 + 1)).sum() for t0 in fl], default=0)
    QC["video"] = {"frames": len(Y), "luma_median": float(np.median(Y)), "share_below_40": float((Y < 40).mean()),
                   "blacks_over_2s": [b for b in blacks if b[2] > 2], "black_total": round(sum(b[2] for b in blacks), 1),
                   "freeze_max": max(freezes, default=0), "flashes_max_per_s": int(mx)}


def intelligibility(wav):
    from faster_whisper import WhisperModel
    m = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=4)
    segs, _ = m.transcribe(str(wav), language="en", word_timestamps=True, beam_size=5, condition_on_previous_text=False)
    words = [(w.word.strip(), w.start, w.end) for s in segs for w in s.words]
    nar = json.loads((BUILD / "narration.json").read_text())

    def norm(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())
    num = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}
    ref = [norm(w["w"]) for a in nar["acts"] for l in a["lines"] for w in l["words"]]
    hyp = [norm(w[0]) for w in words]
    import difflib
    sm = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False)
    errs, real = 0, []
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            continue
        r, h = " ".join(ref[a0:a1]), " ".join(hyp[b0:b1])
        if any(ch.isdigit() for ch in h):      # "$70" for "seventy ... dollars" is a spelling difference, not an error
            continue
        errs += max(a1 - a0, b1 - b0)
        real.append((r, h, round(words[b0][1], 1) if b0 < len(words) else None))
    QC["whisper"] = {"ref_words": len(ref), "real_word_errors": errs, "error_rate": round(errs / len(ref), 4), "diffs": real[:60]}
    # subtitle sync: matching words between burned-in subtitles and what Whisper heard
    tl = (BUILD / "timeline.js").read_text()
    subs = json.loads(tl[tl.index("{"):tl.rindex("}") + 1])["subs"]
    sw = [(norm(x["t"]), x["s"]) for c in subs for x in c["words"]]
    sm2 = difflib.SequenceMatcher(a=[a for a, _ in sw], b=hyp, autojunk=False)
    offs = []
    for blk in sm2.get_matching_blocks():
        for k in range(blk.size):
            offs.append(sw[blk.a + k][1] - words[blk.b + k][1])
    offs = np.array(offs)
    QC["sync"] = {"matched_words": len(offs), "median_offset_s": round(float(np.median(offs)), 3),
                  "p95_abs_offset_s": round(float(np.percentile(np.abs(offs), 95)), 3)}


if __name__ == "__main__":
    probe()
    wav = loudness()
    video_pass()
    intelligibility(wav)
    (BUILD / "qc.json").write_text(json.dumps(QC, indent=1))
    p = QC["probe"]
    print("format:", p["format"]["duration"], "s,", int(p["format"]["size"]) / 2 ** 20, "MiB,", int(p["format"]["bit_rate"]) / 1e6, "Mbps")
    for s in p["streams"]:
        print("  ", {k: s.get(k) for k in ("codec_name", "profile", "width", "height", "r_frame_rate", "bit_rate", "nb_frames", "sample_rate", "duration")})
    print("loudness:", QC["loudness"])
    print("video:", QC["video"])
    print("whisper:", {k: v for k, v in QC["whisper"].items() if k != "diffs"})
    print("  diffs:", QC["whisper"]["diffs"])
    print("sync:", QC["sync"])
