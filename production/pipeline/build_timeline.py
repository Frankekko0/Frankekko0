"""Compile pipeline/shots.py into build/timeline.js (renderer) and build/cues.json (audio)."""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import shots as S  # noqa: E402
import subs as SB  # noqa: E402

ABS_KEYS = ("at", "until", "t1")


def convert_el(el, base):
    e = {k: v for k, v in el.items() if v is not None}
    at = e.pop("at", None)
    e["t0"] = round((at - base) if at is not None else 0.0, 3)
    end = e.pop("until", None)
    if end is None:
        end = e.pop("t1", None)
    else:
        e.pop("t1", None)
    if end is not None:
        e["t1"] = round(end - base, 3)
    if "frm" in e:
        e["from"] = e.pop("frm")
    if "rot" in e and e.get("anim") != "stamp":
        e.pop("rot")
    return e


def main():
    shots = sorted(S.SHOTS, key=lambda s: s["start"])
    out = []
    for i, sh in enumerate(shots):
        end = shots[i + 1]["start"] if i + 1 < len(shots) else S.END
        if end - sh["start"] < 0.2:
            print(f"WARNING: very short shot at {sh['start']:.2f} ({end - sh['start']:.2f}s)")
        o = {k: v for k, v in sh.items() if k not in ("els", "rel") and v is not None}
        o["end"] = round(end, 3)
        o["els"] = [convert_el(e, sh["start"]) for e in sh["els"]]
        for e in o["els"]:
            if e["t0"] > o["end"] - o["start"]:
                print(f"WARNING: element starts after shot end at {sh['start']:.2f}: {str(e.get('text') or e.get('kind'))[:40]}")
        out.append(o)
    for ov in S.OVER:
        o = {k: v for k, v in ov.items() if k != "els"}
        o["els"] = [convert_el(e, ov["start"]) for e in ov["els"]]
        out.append(o)

    series = [[r["date"], float(r["close"])] for r in csv.DictReader(open(ROOT / "assets" / "data" / "ene_daily.csv"))]

    # ---- subtitles: a chunk steps aside while a big caption shows the same words
    def toks(s):
        return set(re.findall(r"[a-z0-9$%]+", re.sub(r"\*\*|~~|__", "", s).lower().replace("’", "'")))
    subs, dropped = [], []
    for ch in SB.build(S.NAR):
        mid = (ch["s"] + ch["e"]) / 2
        ct = toks(" ".join(x["t"] for x in ch["words"]))
        hit = any(a - 0.3 <= mid <= b + 0.3 and ct and len(ct & toks(txt)) / len(ct) >= 0.8 for a, b, txt in S.DUPS)
        (dropped if hit else subs).append(ch)
    print(f"subtitles: {len(subs)} chunks ({len(dropped)} replaced by on-screen captions)")

    tl = {"fps": 30, "duration": S.END, "shots": out, "data": {"ene": series}, "subs": subs}
    (ROOT / "build" / "timeline.js").write_text("window.TL = " + json.dumps(tl, separators=(",", ":")) + ";\n")

    acts = [{"id": a["id"], "start": a["start"], "end": a["end"]} for a in S.NAR["acts"]]
    cues = {"duration": S.END, "acts": acts, "sfx": sorted(S.FX)}
    (ROOT / "build" / "cues.json").write_text(json.dumps(cues, indent=1))

    # ---- pacing report: moments where something new appears on screen
    events = sorted({round(s["start"], 2) for s in shots} |
                    {round(ov["start"], 2) for ov in S.OVER} |
                    {round(sh["start"] + e["t0"], 2) for sh in out for e in sh["els"] if e.get("t0", 0) > 0.05})
    gaps = [(a, b) for a, b in zip(events, events[1:]) if b - a > 4.5]
    durs = [o["end"] - o["start"] for o in out if not o.get("layer")]
    print(f"{len(durs)} main shots, {len(S.OVER)} overlays, {len(S.FX)} sfx; video {S.END:.1f}s")
    print(f"shot length: mean {sum(durs) / len(durs):.2f}s, max {max(durs):.2f}s")
    first30 = [d for o, d in zip([o for o in out if not o.get('layer')], durs) if o["start"] < 30]
    print(f"first 30s: {len(first30)} shots, mean {sum(first30) / len(first30):.2f}s")
    print("gaps > 4.5s without a new visual event:", [(round(a, 1), round(b - a, 1)) for a, b in gaps] or "none")


if __name__ == "__main__":
    main()
