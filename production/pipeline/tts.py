"""Generate the narration with Kokoro (Apache-2.0, runs locally on CPU).

Reads narration.txt (one beat per line, acts introduced by "## ACTn TITLE"),
synthesises every line separately, trims silence, inserts controlled pauses
and writes:
  build/narration.wav      - the full voice track (24 kHz mono)
  build/narration.json     - per-line and per-word timings (seconds)
"""
import json
import re
import sys
import warnings
from pathlib import Path

import numpy as np
import soundfile as sf

warnings.filterwarnings("ignore")
from kokoro import KPipeline  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SR = 24000

# Explicit pronunciations (misaki phoneme notation) for names the G2P doesn't know.
PRON = {
    "Enron": "ˈɛnɹɑn",
    "Fastow": "fˈæstO",
    "Chewco": "ʧˈukO",
    "Dynegy": "dIˈniʤi",
    "Andersen": "ˈændəɹsᵊn",
    "McKinsey": "məkˈɪnzi",
    "Sherron": "ʃˈɛɹən",
    "McLean": "məklˈAn",
    "Kenneth": "kˈɛnəθ",
    "Arthur": "ˈɑɹθəɹ",
    "401(k)": "fˌɔɹˌOwˈʌnkˈA",
}
YEARS = {
    "1985": "nineteen eighty-five", "1992": "nineteen ninety-two", "1996": "nineteen ninety-six",
    "1997": "nineteen ninety-seven", "1998": "nineteen ninety-eight", "1999": "nineteen ninety-nine",
    "2000": "two thousand", "2001": "two thousand one", "2002": "two thousand two",
    "2005": "two thousand five",
}

# Pause (seconds) after a line, matched on the start of the line. Default below.
DEFAULT_GAP = 0.25
PAUSE_AFTER = {
    "Enron wasn't destroyed": 0.55,
    "The cash didn't have to arrive yet": 0.6,
    "Then, in less than two months, it was gone.": 0.85,
    "To understand how, you need": 1.9,
    "How much of that profit had actually arrived": 0.95,
    "Year one. One giant number.": 0.8,
    "The danger is in one word": 0.7,
    "And here's the crucial part": 0.7,
    "Cash flow is the money": 0.5,
    "But Enron had already booked": 0.9,
    "The accounting had built a treadmill": 0.95,
    "So where do you put the losses": 0.95,
    "Chewco, it later emerged": 0.6,
    "A later investigation for Enron's board": 0.9,
    "The investigation found Fastow": 0.8,
    "In reality, risk was piling up": 0.95,
    "March 2001. In Fortune": 0.8,
    "One line would become famous.": 0.7,
    "\"I am incredibly nervous": 1.15,
    "The cracks were no longer invisible.": 0.95,
    "That day, Enron's stock closes": 1.05,
    "With sixty-three point four billion": 0.6,
    "From more than ninety dollars": 1.25,
    "But Enron was never just a stock chart.": 0.8,
    "Employees lost more than a billion": 0.8,
    "The Supreme Court later overturned": 0.9,
    "Kenneth Lay was convicted too": 1.05,
    "Fastow pleaded guilty.": 1.05,
    "So what really destroyed Enron?": 0.9,
    "The gap between looking profitable": 1.05,
    "And once investors stopped believing": 0.8,
}
ACT_GAP = 1.4          # silence between acts (music carries the transition)
LEAD_IN = 0.5          # silence before the first word

# Per-act speaking speed (Kokoro speed multiplier). Collapse runs a touch faster,
# the human-consequences act a touch slower.
ACT_SPEED = {"ACT0": 1.02, "ACT1": 1.04, "ACT2": 1.02, "ACT3": 1.05, "ACT4": 1.03,
             "ACT5": 1.04, "ACT6": 1.07, "ACT7": 0.97, "ACT8": 0.98}
LINE_SPEED = {"\"I am incredibly nervous": 0.9, "It had weeks.": 0.88}


def parse_script(path):
    acts, cur = [], None
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^## (ACT\d+) (.*)$", line)
        if m:
            cur = {"id": m.group(1), "title": m.group(2), "lines": []}
            acts.append(cur)
        else:
            cur["lines"].append(line)
    return acts


def tts_text(line):
    t = line
    for y, words in YEARS.items():
        t = re.sub(rf"\b{y}\b", words, t)
    t = t.replace("Chapter 11", "Chapter Eleven")
    for word, ph in PRON.items():
        t = re.sub(rf"(?<![\w\[]){re.escape(word)}(?![\w\]])", f"[{word}](/{ph}/)", t)
    t = t.replace("...", ",")
    return t


def lookup(table, line, default):
    for k, v in table.items():
        if line.startswith(k):
            return v
    return default


def trim(audio, thresh_db=-42.0, pad=0.03):
    """Trim leading/trailing silence. Returns trimmed audio and samples removed at start."""
    env = np.abs(audio)
    win = int(0.01 * SR)
    smooth = np.convolve(env, np.ones(win) / win, mode="same")
    thr = 10 ** (thresh_db / 20) * max(1e-6, np.max(smooth))
    idx = np.where(smooth > thr)[0]
    if len(idx) == 0:
        return audio, 0
    a = max(0, idx[0] - int(pad * SR))
    b = min(len(audio), idx[-1] + int(pad * SR * 3))
    return audio[a:b], a


def compress_pauses(audio, words, max_pause=0.42, keep=0.30, thresh_db=-40.0):
    """Shorten silent runs inside a line to `keep` seconds; remap word timestamps (line-relative)."""
    win = int(0.02 * SR)
    rms = np.sqrt(np.convolve(audio.astype(np.float64) ** 2, np.ones(win) / win, mode="same"))
    quiet = rms < 10 ** (thresh_db / 20) * max(1e-9, rms.max())
    runs, i, n = [], 0, len(audio)
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) / SR > max_pause and i > 0 and j < n:
                runs.append((i, j))
            i = j
        else:
            i += 1
    if not runs:
        return audio, words
    pieces, cuts, prev = [], [], 0
    for a, b in runs:
        k = int(keep * SR)
        mid_a, mid_b = a + k // 2, b - k // 2       # keep the edges, drop the middle
        pieces.append(audio[prev:mid_a])
        cuts.append((mid_a / SR, (mid_b - mid_a) / SR))
        prev = mid_b
    pieces.append(audio[prev:])

    def remap(x):
        shift = sum(length for pos, length in cuts if pos <= x)
        return x - shift

    for w in words:
        w["s"], w["e"] = remap(w["s"]), remap(w["e"])
    return np.concatenate(pieces), words


def main():
    acts = parse_script(ROOT / "narration.txt")
    pipe = KPipeline(lang_code="a")
    puck, mich = pipe.load_voice("am_puck"), pipe.load_voice("am_michael")
    voice = 0.6 * puck + 0.4 * mich

    out = [np.zeros(int(LEAD_IN * SR), dtype=np.float32)]
    t = LEAD_IN
    timeline = []
    only = set(sys.argv[1:])
    for ai, act in enumerate(acts):
        if ai > 0:
            out.append(np.zeros(int(ACT_GAP * SR), dtype=np.float32))
            t += ACT_GAP
        act_rec = {"id": act["id"], "title": act["title"], "start": round(t, 3), "lines": []}
        for li, line in enumerate(act["lines"]):
            speed = lookup(LINE_SPEED, line, ACT_SPEED.get(act["id"], 1.0))
            chunks, words, offset = [], [], 0.0
            for r in pipe(tts_text(line), voice=voice, speed=speed):
                a = r.audio.numpy().astype(np.float32)
                for tok in (r.tokens or []):
                    if tok.start_ts is None or tok.end_ts is None or not re.search(r"\w", tok.text):
                        continue
                    words.append({"w": tok.text, "s": offset + tok.start_ts, "e": offset + tok.end_ts})
                chunks.append(a)
                offset += len(a) / SR
            audio = np.concatenate(chunks)
            audio, cut = trim(audio)
            cut_s = cut / SR
            for w in words:
                w["s"], w["e"] = max(0.0, w["s"] - cut_s), max(0.0, w["e"] - cut_s)
            audio, words = compress_pauses(audio, words)
            dur = len(audio) / SR
            for w in words:
                w["s"] = round(t + min(w["s"], dur), 3)
                w["e"] = round(t + min(w["e"], dur), 3)
            rec = {"id": f"{act['id']}_{li + 1:02d}", "text": line, "start": round(t, 3),
                   "end": round(t + dur, 3), "words": words}
            act_rec["lines"].append(rec)
            out.append(audio)
            t += dur
            gap = lookup(PAUSE_AFTER, line, DEFAULT_GAP)
            out.append(np.zeros(int(gap * SR), dtype=np.float32))
            t += gap
            print(f"{rec['id']} {rec['start']:7.2f}-{rec['end']:7.2f} speed {speed:.2f} | {line[:60]}", flush=True)
        act_rec["end"] = round(t, 3)
        timeline.append(act_rec)
    tail = 2.5
    out.append(np.zeros(int(tail * SR), dtype=np.float32))
    t += tail
    full = np.concatenate(out)
    peak = np.max(np.abs(full))
    full = full / peak * 0.89
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    sf.write(build / "narration.wav", full, SR, subtype="PCM_16")
    (build / "narration.json").write_text(json.dumps({"duration": round(t, 3), "acts": timeline}, indent=1))
    words = sum(len(l["text"].split()) for a in timeline for l in a["lines"])
    print(f"TOTAL {t:.1f}s = {t / 60:.2f} min, {words} words, {words / (t / 60):.0f} wpm overall")


if __name__ == "__main__":
    main()
