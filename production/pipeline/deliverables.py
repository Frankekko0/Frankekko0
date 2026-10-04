"""Write upload-ready companions to the video: SRT captions, YouTube description
(chapters, sources, credits) and the final script, into production/deliverables/."""
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import shots as S  # noqa: E402
import tts  # noqa: E402

OUT = ROOT / "deliverables"
OUT.mkdir(exist_ok=True)


def ts(t, srt=True):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if srt:
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)) % 1000:03d}"
    return f"{int(m)}:{int(s):02d}"


def chunks(text, limit=84):
    """Split a narration line into caption-sized pieces at sentence / clause boundaries."""
    def balanced(p):
        if len(p) <= limit:
            return [p]
        mid = len(p) // 2
        commas = [m.end() for m in re.finditer(r",\s", p) if abs(m.end() - mid) < len(p) * 0.3]
        cut = min(commas, key=lambda c: abs(c - mid)) if commas else min((m.start() for m in re.finditer(" ", p)), key=lambda c: abs(c - mid))
        return balanced(p[:cut].strip()) + balanced(p[cut:].strip())

    out = []
    for p in re.split(r"(?<=[.?!:])\s+", text):
        out += balanced(p)
    merged = []
    for c in out:
        if merged and len(merged[-1]) + 1 + len(c) <= 42:
            merged[-1] += " " + c
        else:
            merged.append(c)
    return merged


def wrap2(c):
    if len(c) <= 42:
        return c
    mid = len(c) // 2
    left, right = c.rfind(" ", 0, mid + 6), c.find(" ", mid - 6)
    cut = left if mid - left <= (right - mid if right > 0 else 99) else right
    return c[:cut].strip() + "\n" + c[cut:].strip()


def write_srt():
    n = 1
    rows = []
    for act in S.NAR["acts"]:
        for line in act["lines"]:
            pieces = chunks(line["text"])
            weights = [len(tts.tts_text(p)) for p in pieces]
            span = line["end"] - line["start"]
            t = line["start"]
            for p, wgt in zip(pieces, weights):
                d = span * wgt / sum(weights)
                rows.append(f"{n}\n{ts(t)} --> {ts(t + d - 0.02)}\n{wrap2(p)}\n")
                n += 1
                t += d
    (OUT / "enron_documentary.en.srt").write_text("\n".join(rows))
    return n - 1


def used_images():
    tl = (ROOT / "build" / "timeline.js").read_text()
    return sorted(set(re.findall(r"build/img/([a-z0-9_]+?)(?:_bw)?\.(?:jpg|png)", tl)))


def write_description():
    E = S.E
    chapters = [(0.0, "Cold open: the profit that wasn't cash"), (E("ACT0_10") + 1.45, "Part 1 — The perfect company"),
                (E("ACT1_11") + 0.25, "Part 2 — The accounting machine (mark-to-market explained)"),
                (E("ACT2_19") + 0.25, "Part 3 — Expectations keep rising"), (E("ACT3_08") + 0.25, "Part 4 — Hiding the problems (SPEs & Fastow)"),
                (E("ACT4_15") + 0.25, "Part 5 — The warning signs"), (E("ACT5_11") + 0.25, "Part 6 — The collapse"),
                (E("ACT6_15") + 0.25, "Part 7 — The human cost"), (E("ACT7_09") + 0.25, "Part 8 — The gap")]
    manifest = {m["key"]: m for m in json.loads((ROOT / "assets" / "img" / "manifest.json").read_text())}
    photo_lines = []
    for k in used_images():
        m = manifest.get(k)
        if not m:
            continue
        author = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.get("author") or ""))).strip() or "unknown author"
        src = m.get("source") or ""
        label = S.LABELS.get(k, k)
        photo_lines.append(f"• {label} — {author}, {m.get('license', '')}{(' — ' + src) if src else ''}")
    music = json.loads((ROOT / "assets" / "music" / "music_credits.json").read_text())
    music_lines = [f'• "{c["title"]}" — Kevin MacLeod (incompetech.com), licensed under Creative Commons: By Attribution 4.0 — https://creativecommons.org/licenses/by/4.0/'
                   for c in music]
    text = f"""TITLE OPTIONS
1. Enron: How America's "Most Innovative Company" Collapsed in Under Seven Weeks
2. The Accounting Idea That Helped Bring Down Enron
3. How Enron Looked Profitable Without the Cash

DESCRIPTION
In 1992, Enron got permission to use mark-to-market accounting for its natural gas contracts — a method that could turn estimated future profits into profits reported today. This documentary explains, in plain language, how aggressive estimates, special-purpose entities, conflicts of interest and opaque reporting combined until investors and lenders stopped believing the numbers — and Enron went from roughly $70 billion in market value to bankruptcy.

CHAPTERS
""" + "\n".join(f"{ts(t, srt=False)} {name}" for t, name in chapters) + """

KEY SOURCES
• Report of Investigation by the Special Investigative Committee of the Board of Directors of Enron Corp. ("Powers Report"), Feb. 1, 2002
• Enron Corp. press release, Oct. 16, 2001: "Enron Reports Recurring Third Quarter Earnings of $0.43 per Diluted Share; Reports Non-Recurring Charges of $1.01 Billion After-Tax…"
• U.S. SEC, complaint in SEC v. Kevin A. Howard and Michael W. Krautz (Enron Broadband "Braveheart"/Blockbuster transaction) — https://www.sec.gov/litigation/complaints/comp18030.htm
• Bethany McLean, "Is Enron Overpriced?", Fortune, March 5, 2001; Bethany McLean & Peter Elkind, "The Smartest Guys in the Room" (2003)
• U.S. House Committee on Energy and Commerce hearings (2002): "The Financial Collapse of Enron"; "Destruction of Enron-Related Documents by Andersen Personnel"
• Arthur Andersen LLP v. United States, 544 U.S. 696 (2005)
• Economic Policy Institute, "No More Enrons: Protecting 401(k) Plans for a Safe Retirement" (401(k) figures)
• Enron (ENE) daily historical share prices, 1998–2001 — Famous Trials, UMKC School of Law — https://famous-trials.com/enron/1791-stockchart
• Wikipedia, "Enron scandal" (cross-check of dates)

Notes: the 10-year contract and the profit/cash curves are simplified illustrations, labelled as such on screen. Mark-to-market accounting is legitimate and widely used; the controversy concerned how Enron applied aggressive assumptions and combined them with other structures and misleading disclosures.

CREDITS
Narration: AI-generated voice (Kokoro-82M, Apache-2.0).
Music:
""" + "\n".join(music_lines) + """
Photos and documents:
""" + "\n".join(photo_lines) + """
Fonts: Inter, Oswald, Playfair Display, IBM Plex Mono, Courier Prime (SIL Open Font License).

TAGS
Enron, Enron scandal, mark-to-market accounting, corporate fraud, business documentary, Jeffrey Skilling, Kenneth Lay, Andrew Fastow, Arthur Andersen, special purpose entities, accounting scandal, stock market collapse, finance explained
"""
    (OUT / "youtube_description.txt").write_text(text)
    return chapters


def write_script(chapters):
    md = ["# Enron: Profit on Paper — full narration script", "",
          "Faceless long-form YouTube documentary · 16:9 · 1080p30 · runtime 10:00 · 1,486 words", ""]
    titles = {a["id"]: a["title"] for a in S.NAR["acts"]}
    for act in S.NAR["acts"]:
        md += [f"## {act['id'].replace('ACT', 'Act ')} — {titles[act['id']].title()}  ({ts(act['start'], srt=False)})", ""]
        for line in act["lines"]:
            md.append(f"`{ts(line['start'], srt=False)}` {line['text']}  ")
        md.append("")
    md += ["## Fact-check table", "",
           "| Claim in the video | Verified against |", "| --- | --- |"]
    facts = [
        ("Enron formed in 1985 from the merger of Houston Natural Gas and InterNorth; Kenneth Lay led it", "Wikipedia: Enron; Kenneth Lay"),
        ("Skilling (ex-McKinsey) hired by Lay in 1990; championed the 'Gas Bank' and mark-to-market", "Wikipedia: Jeffrey Skilling; Enron"),
        ("SEC approved mark-to-market for Enron's natural gas contracts in January 1992 (Jan. 30)", "Wikipedia: Enron scandal (citing contemporary reporting); McLean & Elkind (2003)"),
        ("Fortune's 'America's Most Innovative Company' six years running (1996–2001)", "Wikipedia: Enron"),
        ("Revenue of about $100 billion in 2000 ($100.8bn reported)", "Wikipedia: Enron"),
        ("All-time high $90.75 intraday on Aug. 23, 2000; market value ~ $70bn", "ENE daily price table (Famous Trials/UMKC); Begin To Invest stock history summary"),
        ("Stock +56% in 1999 and +87% in 2000", "Computed from ENE daily closes (28.53 → 44.38 → 83.13)"),
        ("Blockbuster 20-year video-on-demand deal (July 2000), ended March 2001; ~$111m ($110.9m) profit booked", "SEC complaint v. Howard & Krautz; DOJ releases; Wikipedia: Enron scandal"),
        ("Fastow CFO from 1998; ran LJM partnerships with board approval under the code of conduct", "Powers Report; Wikipedia: Andrew Fastow"),
        ("3% outside-equity rule for SPE non-consolidation; Chewco did not meet it", "Powers Report, executive summary and Chewco section"),
        ("Raptors made losses look hedged; counterparty effectively backed by Enron stock", "Powers Report, executive summary"),
        ("Deals inflated earnings by almost $1bn from Q3 2000 to Q3 2001", "Powers Report, p. 4"),
        ("Fastow 'involved on both sides'; received more than $30 million", "Powers Report, executive summary and conclusions"),
        ("McLean, 'Is Enron Overpriced?', Fortune, March 5, 2001", "Fortune; Wikipedia: Enron scandal"),
        ("Skilling resigned Aug. 14, 2001 after six months as CEO; Lay returned", "Wikipedia: Jeffrey Skilling; ENE close $42.93"),
        ("Watkins' anonymous letter, Aug. 15, 2001: 'I am incredibly nervous that we will implode in a wave of accounting scandals.'", "Wikipedia: Enron scandal and Sherron Watkins (quoting the letter)"),
        ("Stock below $35 by end of August 2001 ($34.99 on Aug. 31)", "ENE daily price table"),
        ("Oct. 16, 2001: Q3 net loss $618m after $1.01bn after-tax charges; $1.2bn equity reduction", "Enron press release Oct. 16, 2001; Powers Report"),
        ("Oct. 22: SEC inquiry disclosed, stock $20.65; Oct. 24: Fastow out as CFO", "ENE daily price table; Wikipedia: Enron scandal"),
        ("Nov. 8, 2001 restatement back to 1997 (net income cut by hundreds of millions; debt added: $711m/$561m/$685m/$628m for 1997–2000)", "Powers Report, background section"),
        ("Nov. 9: Dynegy agrees to buy Enron; Nov. 28: ratings cut to junk, Dynegy terminates, ENE closes at $0.61", "CNN Money, Nov. 9, 2001; Dynegy 8-K filings (Nov. 2001); ENE daily price table"),
        ("Dec. 2, 2001 Chapter 11; $63.4bn assets; largest U.S. bankruptcy at the time", "Wikipedia: Enron scandal"),
        ("~4,000 Houston employees laid off within days; ~60% of 401(k) assets in Enron stock; >$1bn retirement savings lost", "EPI issue brief 'No More Enrons'; Wikipedia: Enron scandal"),
        ("Arthur Andersen convicted of obstruction (June 2002); overturned by the Supreme Court (2005)", "Arthur Andersen LLP v. United States, 544 U.S. 696 (2005)"),
        ("Fastow pleaded guilty (6 years); Skilling convicted (24 years, later reduced); Lay convicted, died before sentencing, conviction vacated", "Wikipedia: Andrew Fastow; Jeffrey Skilling; Kenneth Lay"),
    ]
    md += [f"| {c} | {s} |" for c, s in facts]
    (OUT / "script.md").write_text("\n".join(md) + "\n")


if __name__ == "__main__":
    n = write_srt()
    ch = write_description()
    write_script(ch)
    print(f"{n} caption blocks; chapters:", [(ts(t, False), c) for t, c in ch])
    print("images used:", used_images())
