"""The edit: every shot, caption, overlay and sound cue, anchored to narration words.

Times are absolute seconds taken from build/narration.json, so re-generating the
voice re-times the whole edit automatically. build_timeline.py turns this into
build/timeline.js for the renderer and build/cues.json for the audio mix.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAR = json.loads((ROOT / "build" / "narration.json").read_text())
LINES = {l["id"]: l for a in NAR["acts"] for l in a["lines"]}
ACTS = {a["id"]: a for a in NAR["acts"]}
DOCS = json.loads((ROOT / "assets" / "docs" / "docs.json").read_text())
OUTRO = 18.5                                           # end card for YouTube end-screen elements
END = round(LINES["ACT8_09"]["end"] + 1.2 + OUTRO, 2)  # video length


def L(i):
    return LINES[i]["start"]


def E(i):
    return LINES[i]["end"]


def w(i, word, n=1):
    """Start time of the n-th token in line i that starts with `word` (case-insensitive)."""
    hits = [x for x in LINES[i]["words"] if x["w"].lower().startswith(word.lower())]
    if len(hits) < n:
        raise KeyError(f"{word!r} #{n} not in {i}: {[x['w'] for x in LINES[i]['words']]}")
    return hits[n - 1]["s"]


def we(i, word, n=1):
    hits = [x for x in LINES[i]["words"] if x["w"].lower().startswith(word.lower())]
    return hits[n - 1]["e"]


SHOTS, OVER, FX = [], [], []
DUPS = []   # (start, end, text) of on-screen captions that say exactly what the narrator says


def shot(start, bg=None, els=(), **kw):
    SHOTS.append(dict(start=round(start, 3), bg=bg, els=list(els), **kw))


def over(start, end, els, layer=5, **kw):
    OVER.append(dict(start=round(start, 3), end=round(end, 3), els=list(els), layer=layer, **kw))


def sfx(t, name, db=0.0):
    FX.append((round(t, 3), name, db))


# ---------------------------------------------------------------- element helpers
def T(text, x=960, y=540, cls="cap-l shadow", at=None, until=None, anim="up", **kw):
    return dict(kind="text", text=text, x=x, y=y, cls=cls, at=at, until=until, anim=anim, **kw)


def LBL(text, x, y, at=None, cls="label", anim="fade", **kw):
    return T(text, x, y, cls=cls, at=at, anim=anim, **kw)


def CNT(to, x=960, y=540, at=None, frm=0, fmt=None, cls="cap-xl shadow gold", dur=1.6, **kw):
    return dict(kind="counter", to=to, frm=frm, x=x, y=y, cls=cls, at=at, fmt=fmt or {}, cdur=dur, anim="fade", ad=0.25, **kw)


def BOX(x, y, w_, h, at=None, cls="", text=None, html=None, style=None, anim="pop", kind="block", **kw):
    return dict(kind=kind, x=x, y=y, w=w_, h=h, at=at, cls=cls, text=text, html=html, style=style or {}, anim=anim, **kw)


def RECT(x, y, w_, h, at=None, style=None, anim="fade", **kw):
    return dict(kind="rect", x=x, y=y, w=w_, h=h, at=at, style=style or {}, anim=anim, **kw)


def PATH(d, at=None, stroke="#e8b04b", sw=4, anim="draw", ad=0.7, arrow=False, dash=None, **kw):
    return dict(kind="path", d=d, at=at, stroke=stroke, sw=sw, anim=anim, ad=ad, arrow=arrow, dash=dash, **kw)


def IMG(src, x, y, w_, h, at=None, cls="", anim="fade", **kw):
    return dict(kind="img", src=img(src), x=x, y=y, w=w_, h=h, at=at, cls=cls, anim=anim, **kw)


def STAMP(text, x, y, at, color="red", rot=-6, cls="cap-m", **kw):
    style = {"border": f"6px solid var(--{color})", "color": f"var(--{color})", "padding": "6px 26px 10px",
             "borderRadius": "10px", "background": "rgba(0,0,0,0.35)"}
    return dict(kind="text", text=text, x=x, y=y, cls=cls, at=at, anim="stamp", ad=0.35, rot=rot, style=style, **kw)


def img(key):
    return f"../build/img/{key}.jpg" if key != "enron_logo" else "../build/img/enron_logo.png"


# ---------------------------------------------------------------- photos + credits
MANIFEST = {m["key"]: m for m in json.loads((ROOT / "assets" / "img" / "manifest.json").read_text())}
LABELS = {
    "enron_complex": "Enron's former headquarters complex, Houston (2008)",
    "enron_field": "Enron Field, Houston",
    "enron_jet": "An Enron corporate jet",
    "houston_iss": "Houston, seen from the International Space Station",
    "houston_pano_night": "Houston skyline at night",
    "houston_night_2000": "Downtown Houston, December 2000",
    "houston_sunrise_2002": "Houston, January 2002",
    "houston_chase_night": "Downtown Houston at night",
    "ken_lay": "Kenneth Lay (2004)",
    "skilling_mug": "Jeffrey Skilling (2004)",
    "mclean": "Bethany McLean (2018)",
    "watkins": "Sherron Watkins (later years)",
    "andersen_witnesses": "Arthur Andersen witnesses at a House hearing, Jan. 24, 2002",
    "hearing_0124": "House subcommittee hearing on Enron, Jan. 24, 2002",
    "wallst_2000": "Wall Street, circa 2000",
    "wallst_sign": "Wall Street, New York",
    "wallst_ticker": "New York Stock Exchange",
    "nyse_floor": "New York Stock Exchange trading floor",
    "rbc_floor": "A bank trading floor",
    "chicago_floor": "Chicago Stock Exchange quotation board",
    "pipeline": "Natural gas pipeline construction",
    "shredder_detail": "Paper shredder",
    "blockbuster_closed": "A Blockbuster store",
    "blockbuster_sign": "A Blockbuster Video store sign (2018)",
    "enron_downtown": "Skybridge at Enron's former downtown complex, Houston",
    "supreme_court": "U.S. Supreme Court",
    "casey_courthouse": "Bob Casey Federal Courthouse, Houston",
    "dabhol": "The Enron-backed Dabhol power plant, India (photo: 2010)",
    "fiber_laying": "Fiber-optic cable reels",
    "code_ethics_cover": "Enron Code of Ethics (July 2000)",
    "andersen_tower": "Arthur Andersen offices",
    "enron_logo": "Enron logo (Paul Rand design)",
    "hearing_doc_shredding": "House hearing record, \"Destruction of Enron-Related Documents by Andersen Personnel\" (2002)",
}


def have(key):
    return (ROOT / "build" / "img" / f"{key}.jpg").exists()


def credit(key):
    m = MANIFEST.get(key, {})
    author = (m.get("author") or "").replace("\n", " ").strip()
    if len(author) > 48:
        author = author[:46] + "…"
    lic = m.get("license") or ""
    who = f"{author} · {lic}" if author else lic
    return f"<b>{LABELS.get(key, '')}</b> &nbsp;·&nbsp; Photo: {who}"


KB = {   # [scale0, x0, y0, scale1, x1, y1]  (scale relative to "cover")
    "in": [1.03, 0, 0, 1.13, 0, 0],
    "out": [1.15, 0, 0, 1.04, 0, 0],
    "left": [1.12, 70, 0, 1.12, -70, 0],
    "right": [1.12, -70, 0, 1.12, 70, 0],
    "up": [1.12, 0, 50, 1.14, 0, -50],
    "down": [1.12, 0, -50, 1.14, 0, 50],
    "push": [1.05, 0, 0, 1.28, 0, 0],
}


def PH(key, kb="in", dim=0.0, bw=False, credit_on=True, fallback=None, tint=None):
    if not have(key) and fallback:
        key = fallback
    src = img(key + ("_bw" if bw and (ROOT / "build" / "img" / f"{key}_bw.jpg").exists() else ""))
    return dict(kind="photo", src=src, kb=KB[kb] if isinstance(kb, str) else kb, dim=dim, tint=tint,
                credit=credit(key) if credit_on else None)


DARK = dict(kind="dark", grid=True)
NAVY = dict(kind="navy", grid=True)
EMBER = dict(kind="ember", grid=False)
PAPER = dict(kind="paper")
BLACK = dict(kind="black")


# ---------------------------------------------------------------- reusable overlays
def cap(text, at, until=None, pos="low", cls=None, anim="up", **kw):
    """Emphasis caption on top of whatever is on screen."""
    if pos == "low":   # lower third, kept clear of the subtitle band
        el = T(text, 110, 800, cls=cls or "cap-m boxed shadow", at=at, until=until, anim=anim, anchor="bl", **kw)
    elif pos == "center":
        el = T(text, 960, 540, cls=cls or "cap-l shadow", at=at, until=until, anim=anim, **kw)
    elif pos == "high":
        el = T(text, 960, 200, cls=cls or "cap-m shadow", at=at, until=until, anim=anim, **kw)
    else:
        el = T(text, pos[0], pos[1], cls=cls or "cap-m shadow", at=at, until=until, anim=anim, **kw)
    return el


def caption(text, at, until, dup=False, **kw):
    """dup=True: the caption repeats the narration word for word, so the subtitle steps aside for it."""
    over(at, until + 0.3, [cap(text, at, until, **kw)])
    if dup:
        DUPS.append((at, until, text))


def illus(at, text="ILLUSTRATIVE", dark=False, until=None):
    """Marker for anything that is not archival: same size and place (top right) in every scene."""
    return T(text, 1840, 78, cls="tagline" + (" dark" if dark else ""), at=at, until=until, anim="fade", anchor="r")


def datestamp(text, at, until, sub=None):
    els = [RECT(0, 0, 1100, 300, at=at, style={"background": "radial-gradient(ellipse at 0% 0%, rgba(0,0,0,0.7) 0%, rgba(0,0,0,0) 70%)"}, anim="fade", until=until),
           RECT(96, 96, 14, 14, at=at, style={"background": "var(--red)", "borderRadius": "50%"}, anim="fade", pulse=0.25, pulseHz=1.0),
           T(text, 128, 104, cls="mono shadow", at=at, until=until, anim="type", ad=0.45, anchor="l",
             style={"fontSize": "40px", "letterSpacing": "0.08em"})]
    if sub:
        els.append(T(sub, 128, 152, cls="label shadow", at=at + 0.3, until=until, anim="fade", anchor="l"))
    over(at, until + 0.3, els, layer=4)
    sfx(at, "type", -10)


def chapter(num, title, at):
    """Chapter card: its own short shot in the silence between two acts."""
    shot(at, DARK, [LBL(f"PART {num}", 960, 455, at=at + 0.05, cls="label-l gold", anim="up"),
                    RECT(860, 500, 200, 3, at=at + 0.15, style={"background": "var(--gold)"}, anim="wipe", ad=0.5),
                    T(title, 960, 572, cls="cap-l", at=at + 0.2, anim="blur", ad=0.5)],
         cam=[[0, 1.0, 0, 0], [3, 1.05, 0, 0]], tin="dip", tinD=0.25)
    sfx(at - 0.05, "whoosh", -5)


def person(key, name, role, at, side="left", note=None, extra=()):
    """Portrait card layout on a dark background."""
    px = 230 if side == "left" else 1190
    tx = 860 if side == "left" else 170
    els = [IMG(key + "_bw" if (ROOT / "build" / "img" / f"{key}_bw.jpg").exists() else key, px, 170, 500, 640, at=at, anim="fade", ad=0.5,
               cls="", style={"border": "1px solid rgba(255,255,255,0.25)", "boxShadow": "0 30px 90px rgba(0,0,0,.7)"}, kb=[1.0, 1.06]),
           T(name, tx, 430, cls="cap-l shadow", at=at + 0.25, anim="left", anchor="l"),
           RECT(tx, 505, 120, 4, at=at + 0.45, style={"background": "var(--gold)"}, anim="wipe"),
           T(role, tx, 560, cls="label-l gold", at=at + 0.55, anim="fade", anchor="l")]
    if note:
        els.append(T(note, px, 836, cls="note", at=at + 0.6, anim="fade", anchor="tl"))
    return els + list(extra)


def stock(view, reveal, ann=(), red_from=None, x=70, y=120, w_=1780, h=780, ystep=10, at=None, head=True, color="#e8b04b", lw=5, **kw):
    return dict(kind="stock", x=x, y=y, w=w_, h=h, view=[list(v) for v in view], reveal=[list(r) for r in reveal],
                ann=[dict(a) for a in ann], redFrom=red_from, ystep=ystep, at=at, anim="fade", ad=0.3, head=head, color=color, lw=lw, **kw)


def doc(key, cam, hl=(), base_w=900, at=None):
    d = DOCS[key]
    marks = []
    for idx, t0, cls in hl:
        marks.append(dict(boxes=d["highlights"][idx], t0=t0, cls=cls))
    return dict(kind="doc", src=f"../assets/docs/{key}.png", pw=d["w"], ph=d["h"], baseW=base_w,
                cam=[list(c) for c in cam], hl=marks, at=at, anim="fade", ad=0.3)


def bars(items, x0, base_y, bw, gap, scale, at, step, color="var(--gold)", labels=True, fs=30, val_fmt=None, anim="grow"):
    """Vertical bars: items = [(label, value)]."""
    els = []
    for i, (lab, val) in enumerate(items):
        x = x0 + i * (bw + gap)
        h = max(2, val * scale)
        t = at + i * step
        els.append(RECT(x, base_y - h, bw, h, at=t, anim=anim, ad=0.45, style={"background": color, "borderRadius": "6px 6px 0 0"}))
        if labels:
            els.append(T(lab, x + bw / 2, base_y + 34, cls="mono muted", at=t, anim="fade", style={"fontSize": f"{fs}px"}))
        if val_fmt:
            els.append(T(val_fmt(val), x + bw / 2, base_y - h - 30, cls="mono", at=t + 0.2, anim="up", style={"fontSize": f"{fs}px"}))
    return els


ICON_FLAME = "M0,-60 C30,-25 45,0 30,30 C20,52 -20,52 -30,30 C-42,5 -25,-15 -10,-35 C-8,-15 5,-8 10,-20 C14,-32 8,-45 0,-60 Z"
ICON_BOLT = "M10,-62 L-30,8 L-2,8 L-12,62 L32,-10 L4,-10 Z"


def icon(d, cx, cy, at, color="var(--gold)", scale=1.0):
    pts = d
    return dict(kind="html", html=f'<svg width="160" height="160" viewBox="-80 -80 160 160"><path d="{pts}" fill="none" stroke="{color}" stroke-width="7" stroke-linejoin="round" transform="scale({scale})"/></svg>',
                x=cx, y=cy, at=at, anim="pop", cls="")


def network_icon(cx, cy, at, color="#e8b04b"):
    nodes = [(-50, -40), (45, -50), (0, 10), (-55, 50), (50, 45)]
    edges = [(0, 2), (1, 2), (2, 3), (2, 4), (0, 1), (3, 4)]
    svg = '<svg width="170" height="170" viewBox="-85 -85 170 170">'
    for a, b in edges:
        svg += f'<line x1="{nodes[a][0]}" y1="{nodes[a][1]}" x2="{nodes[b][0]}" y2="{nodes[b][1]}" stroke="{color}" stroke-width="5"/>'
    for x_, y_ in nodes:
        svg += f'<circle cx="{x_}" cy="{y_}" r="13" fill="#0b0f14" stroke="{color}" stroke-width="6"/>'
    svg += "</svg>"
    return dict(kind="html", html=svg, x=cx, y=cy, at=at, anim="pop")


# =====================================================================================
# ACT 0 — COLD OPEN
# =====================================================================================
t = 0.0
shot(0.0, DARK, [
    IMG("enron_logo", 960 - 230, 540 - 230, 460, 460, at=0.05, anim="blur", ad=0.9, style={"filter": "drop-shadow(0 0 40px rgba(255,255,255,0.15))"}),
], cam=[[0, 1.0, 0, 0], [2.2, 1.12, 0, 0]], tin="dip", tinD=0.6)
sfx(0.0, "hit", -2)
shot(w("ACT0_01", "ran"), PH("enron_complex", "push", dim=0.25, bw=True), [], tin="cut")
shot(L("ACT0_02"), PH("pipeline", "left", dim=0.15), [])
shot(w("ACT0_02", "moving"), PH("pipeline", [1.45, -260, 80, 1.6, -300, 60], dim=0.2, bw=True), [])
datestamp("2001", L("ACT0_02") + 0.1, w("ACT0_02", "its", 2) - 0.1, sub="STILL OPERATING")
shot(w("ACT0_02", "its", 2), PH("rbc_floor", "right", dim=0.2), [])
# rewind 2001 -> 1992
shot(L("ACT0_03"), DARK, [
    LBL("THE REAL PROBLEM STARTED", 960, 360, at=L("ACT0_03") + 0.1, anim="up"),
    CNT(1992, 960, 540, at=w("ACT0_03", "almost") - 0.2, frm=2001, fmt={}, cls="cap-xxl shadow", dur=1.9, cease="inOut"),
    LBL("ALMOST A DECADE EARLIER", 960, 700, at=w("ACT0_03", "decade"), cls="label-l gold"),
], cam=[[0, 1.0, 0, 0], [5, 1.06, 0, 0]])
sfx(w("ACT0_03", "almost") - 0.2, "rewind", -6)
shot(w("ACT0_03", "decision"), dict(kind="dark"), [
    doc("earnings_1016", [[0, 850, 300, 1.25, -2], [5, 850, 520, 1.45, -2]], base_w=900),
    RECT(0, 0, 1920, 1080, style={"background": "rgba(5,7,9,0.62)"}, anim="none"),
    T("A DECISION ABOUT\n**HOW TO COUNT MONEY**", 960, 540, cls="cap-l shadow", at=w("ACT0_03", "decision") + 0.1, align="center"),
])
sfx(w("ACT0_03", "decision"), "paper", -8)
# 1992
s0 = L("ACT0_04")
shot(s0, NAVY, [
    T("1992", 960, 520, cls="cap-xxl shadow", at=s0 + 0.05, anim="zoom", ad=0.5),
], cam=[[0, 1.0, 0, 0], [2, 1.08, 0, 0]])
sfx(s0, "hit", -6)
g0 = w("ACT0_04", "got")
shot(g0, NAVY, [
    LBL("1992 · ENRON GETS PERMISSION TO USE", 960, 380, at=g0 + 0.05, cls="label-l"),
    T("**MARK-TO-MARKET**", 960, 530, cls="cap-xl shadow", at=w("ACT0_04", "accounting") - 0.15, anim="wipe", ad=0.6),
    LBL("AN ACCOUNTING METHOD", 960, 660, at=w("ACT0_04", "accounting") + 0.2, cls="label-l gold"),
], cam=[[0, 1.06, 0, 0], [3.5, 1.0, 0, 0]])
sfx(w("ACT0_04", "accounting") - 0.15, "whoosh", -10)
# future profits pulled into today
s1 = w("ACT0_04", "profits")
years = [f"YR {i}" for i in range(1, 11)]
els = [illus(s1),
       LBL("EXPECTED FUTURE PROFITS", 1150, 300, at=s1, cls="label-l gold"),
       RECT(180, 640, 1560, 3, at=s1, style={"background": "rgba(255,255,255,0.35)"}, anim="wipe", ad=0.6),
       BOX(150, 480, 230, 160, at=s1, cls="", text="TODAY", style={"border": "3px solid var(--ink)", "fontSize": "46px", "color": "var(--ink)"}, anim="pop")]
merge = w("ACT0_04", "into")
for i in range(9):
    x = 520 + i * 135
    els.append(BOX(x, 520, 105, 105, at=s1 + 0.12 * i, text="$", anim="pop",
                   style={"background": "rgba(232,176,75,0.18)", "border": "3px solid var(--gold)", "color": "var(--gold)", "fontSize": "54px"},
                   kf=[[merge - s1 + 0.0, 0, 0, 1, 1], [merge - s1 + 0.6 + 0.05 * i, 265 - x + i * 2, -8 - i * 3, 0.7, 1]]))
    els.append(T(years[i + 1], x + 52, 690, cls="mono muted", at=s1 + 0.12 * i, anim="fade", style={"fontSize": "24px"}, until=merge - s1 + 0.4 + s1))
els.append(T("PROFITS **REPORTED TODAY**", 960, 860, cls="cap-m shadow", at=w("ACT0_04", "reported"), anim="up"))
shot(s1, DARK, els)
sfx(merge + 0.1, "whoosh", -6)
# cash didn't have to arrive
s2 = L("ACT0_05")
shot(s2, DARK, [
    T("PROFIT", 620, 430, cls="cap-xl gold shadow", at=s2, anim="up"),
    T("REPORTED ✓", 620, 560, cls="label-l gold", at=s2 + 0.3, anim="fade"),
    T("CASH", 1300, 430, cls="cap-xl cyan shadow", at=w("ACT0_05", "cash"), anim="up", style={"opacity": "0.9"}),
    T("NOT YET", 1300, 560, cls="label-l cyan", at=w("ACT0_05", "arrive"), anim="fade"),
    RECT(958, 330, 4, 320, at=s2 + 0.2, style={"background": "rgba(255,255,255,0.25)"}, anim="wipeD"),
])
shot(w("ACT0_05", "on"), PH("enron_field", "in", dim=0.15), [])
s3 = L("ACT0_06")
shot(s3, PH("wallst_2000", "left", dim=0.1), [])
shot(w("ACT0_06", "keeping"), DARK, [
    stock([[0, "1998-01-01", "2000-08-31", 0, 100]], [[0, "1998-01-02"], [E("ACT0_06") - w("ACT0_06", "keeping") + 0.3, "2000-08-23"]], ystep=20),
], rel=True)
s4 = L("ACT0_07")
shot(s4, NAVY, [
    LBL("ENRON'S MARKET VALUE, 2000", 960, 360, at=s4, cls="label-l"),
    LBL("NEARLY", 960, 440, at=s4 + 0.4, cls="label-l gold"),
    CNT(70, 960, 560, at=s4 + 0.4, fmt={"pre": "$", "suf": " BILLION"}, cls="cap-xl gold shadow", dur=2.0),
], cam=[[0, 1.0, 0, 0], [4, 1.07, 0, 0]])
sfx(s4 + 0.4, "riser", -8)
s5 = L("ACT0_08")
l5 = w("ACT0_08", "less")
shot(s5, EMBER, [
    T("FALL 2001", 960, 150, cls="label-l red", at=s5 + 0.15),
    stock([[0, "2001-10-12", "2001-12-05", 0, 40]], [[0, "2001-10-12"], [l5 - s5 - 0.1, "2001-12-02"]], red_from="2001-10-12", ystep=10, head=True),
], rel=True)
sfx(w("ACT0_08", "collapsed"), "hit", -6)
shot(l5 - 0.05, BLACK, [
    T("47 DAYS", 960, 500, cls="cap-xxl", at=l5, anim="zoom", ad=0.35),
    LBL("OCT 16 → DEC 2, 2001", 960, 660, at=l5 + 0.45, cls="label-l red"),
])
sfx(l5, "hit", -1)
s6 = L("ACT0_09")
shot(s6, PH("houston_sunrise_2002", "in", dim=0.2), [])
shot(w("ACT0_09", "thousands"), PH("enron_complex", "left", dim=0.25, bw=True), [])
shot(w("ACT0_09", "one"), PH("enron_jet", "right", dim=0.1), [])
s7 = L("ACT0_10")
shot(s7, DARK, [T("ONE ACCOUNTING IDEA", 960, 540, cls="cap-l shadow", at=s7 + 0.1, anim="blur", until=w("ACT0_10", "changed") - 0.1)])
s8 = w("ACT0_10", "changed")
shot(s8, BLACK, [
    IMG("enron_logo", 960 - 110, 250, 220, 220, at=s8 + 0.1, anim="fade", ad=0.8),
    T("ENRON", 960, 600, cls="cap-xxl", at=s8, anim="zoom", ad=0.7, style={"letterSpacing": "0.18em"}),
    T("PROFIT **ON PAPER**", 960, 760, cls="label-l", at=s8 + 0.6, anim="fade", style={"letterSpacing": "0.5em"}),
], cam=[[0, 1.0, 0, 0], [3.5, 1.04, 0, 0]], tout="dip", toutD=0.6)
sfx(s8, "hit", 0)

# =====================================================================================
# ACT 1 — THE PERFECT COMPANY
# =====================================================================================
a1 = L("ACT1_01")
chapter(1, "THE PERFECT COMPANY", E("ACT0_10") + 1.45)
shot(L("ACT1_01") - 0.1, PH("houston_iss", [1.0, 0, 0, 1.35, -120, 60], dim=0.1), [
    RECT(1076, 590, 26, 26, at=a1 + 0.6, style={"background": "var(--gold)", "borderRadius": "50%", "boxShadow": "0 0 30px var(--gold)"}, anim="pop", pulse=0.18, fixed=True),
    T("HOUSTON, TEXAS", 1125, 603, cls="cap-s shadow", at=a1 + 0.7, anim="left", anchor="l", fixed=True),
])
datestamp("1985", a1 + 0.9, E("ACT1_01") + 0.6)
s = L("ACT1_02")
shot(s, NAVY, [
    BOX(240, 420, 560, 200, at=s + 0.1, text="HOUSTON<br>NATURAL GAS", style={"border": "3px solid var(--ink)", "fontSize": "48px"},
        kf=[[w("ACT1_02", "merge") - s, 0, 0, 1, 1], [w("ACT1_02", "enron") - s, 230, 0, 1, 0]]),
    BOX(1120, 420, 560, 200, at=s + 0.3, text="INTERNORTH", style={"border": "3px solid var(--ink)", "fontSize": "48px"},
        kf=[[w("ACT1_02", "merge") - s, 0, 0, 1, 1], [w("ACT1_02", "enron") - s, -230, 0, 1, 0]]),
    BOX(660, 380, 600, 280, at=w("ACT1_02", "enron") - 0.1, text="", style={"border": "3px solid var(--gold)", "background": "rgba(10,14,20,0.95)"}, anim="pop"),
    IMG("enron_logo", 960 - 85, 410, 170, 170, at=w("ACT1_02", "enron"), anim="pop"),
    T("ENRON", 960, 615, cls="cap-m", at=w("ACT1_02", "enron") + 0.1, anim="up"),
    LBL("1985 MERGER", 960, 300, at=s + 0.4, cls="label-l gold"),
])
sfx(w("ACT1_02", "enron"), "hit", -10)
shot(w("ACT1_02", "led"), dict(kind="dark", grid=True), person("ken_lay", "KENNETH LAY", "CHAIRMAN & CEO", w("ACT1_02", "led"), note="2004 BOOKING PHOTO"))
sfx(w("ACT1_02", "led"), "shutter", -10)
s = L("ACT1_03")
shot(s, PH("pipeline", "right", dim=0.12), [])
caption("STEADY.", w("ACT1_03", "steady"), w("ACT1_03", "predictable") - 0.05, pos=(960, 540), cls="cap-xl shadow", dup=True, aod=0.08)
caption("PREDICTABLE.", w("ACT1_03", "predictable"), w("ACT1_03", "a", 2) - 0.05, pos=(960, 540), cls="cap-xl shadow", dup=True, aod=0.08)
caption("A LITTLE **BORING.**", w("ACT1_03", "a", 2), E("ACT1_03") + 0.2, pos=(960, 540), cls="cap-xl shadow", dup=True)
shot(L("ACT1_04"), dict(kind="dark", grid=True), person("skilling_mug", "JEFFREY SKILLING", "FORMER McKINSEY CONSULTANT", L("ACT1_04") + 0.4, side="right", note="2004 BOOKING PHOTO"))
sfx(L("ACT1_04") + 0.4, "shutter", -10)
s = L("ACT1_05")
shot(s, NAVY, [
    LBL("SKILLING'S BIG IDEA", 960, 400, at=s, cls="label-l"),
    T('THE "**GAS BANK**"', 960, 520, cls="cap-xl shadow", at=w("ACT1_05", "gas") - 0.1, anim="zoom"),
], cam=[[0, 1.0, 0, 0], [3, 1.06, 0, 0]])
s = w("ACT1_05", "enron")
ar = w("ACT1_05", "trade")
shot(s, NAVY, [
    BOX(130, 430, 420, 190, at=s, text="GAS<br>PRODUCERS", style={"border": "3px solid var(--ink)", "fontSize": "44px"}),
    BOX(750, 400, 420, 250, at=s + 0.2, text="ENRON", style={"border": "4px solid var(--gold)", "color": "var(--gold)", "fontSize": "70px"}),
    BOX(1370, 430, 420, 190, at=s + 0.4, text="BUYERS", style={"border": "3px solid var(--ink)", "fontSize": "44px"}),
    PATH("M 560 500 L 735 500", at=s + 0.6, stroke="#9aa0a8", arrow=True),
    PATH("M 1180 500 L 1355 500", at=s + 0.8, stroke="#9aa0a8", arrow=True),
    PATH("M 735 570 L 560 570", at=ar, stroke="#e8b04b", arrow=True, dash="14 12", anim="draw"),
    PATH("M 1355 570 L 1180 570", at=ar + 0.2, stroke="#e8b04b", arrow=True, dash="14 12", anim="draw"),
    T("MOVE ENERGY", 960, 330, cls="label-l muted", at=s + 0.5, until=ar - 0.1),
    T("**TRADE IT** — LIKE A BANK TRADES MONEY", 960, 760, cls="cap-s shadow", at=ar, anim="up"),
    T("CONTRACTS · PRICES · RISK", 960, 840, cls="label gold", at=ar + 0.6),
])
s = L("ACT1_06")
shot(s, PH("nyse_floor", "in", dim=0.2, fallback="chicago_floor"), [])
s = w("ACT1_06", "natural")
shot(s - 0.4, DARK, [
    LBL("BY THE LATE 1990s, ENRON TRADED", 960, 260, at=s - 0.35, cls="label-l"),
    icon(ICON_FLAME, 480, 520, at=s), T("NATURAL GAS", 480, 690, cls="cap-s", at=s + 0.1),
    icon(ICON_BOLT, 960, 520, at=w("ACT1_06", "electricity")), T("ELECTRICITY", 960, 690, cls="cap-s", at=w("ACT1_06", "electricity") + 0.1),
    network_icon(1440, 520, at=w("ACT1_06", "internet")), T("INTERNET BANDWIDTH", 1440, 690, cls="cap-s", at=w("ACT1_06", "internet") + 0.1),
])
for nm in [("natural", 0), ("electricity", 0), ("internet", 0)]:
    sfx(w("ACT1_06", nm[0]), "pop", -14)
s = L("ACT1_07")
yrs = ["1996", "1997", "1998", "1999", "2000", "2001"]
els = [LBL("FORTUNE MAGAZINE", 960, 300, at=s, cls="label-l"),
       T("“AMERICA'S MOST\nINNOVATIVE COMPANY”", 960, 470, cls="serif shadow", at=w("ACT1_07", "america"), anim="blur", ad=0.6, align="center", style={"fontSize": "92px", "lineHeight": "1.1"})]
for i, y in enumerate(yrs):
    els.append(T(y, 560 + i * 160, 720, cls="mono gold pill", at=w("ACT1_07", "six") + 0.12 * i, anim="pop", style={"fontSize": "32px"}))
els.append(LBL("SIX YEARS IN A ROW", 960, 820, at=w("ACT1_07", "row") - 0.2, cls="label-l"))
shot(s, NAVY, els)
s = L("ACT1_08")
shot(s, NAVY, [
    LBL("REPORTED REVENUE, 2000", 960, 380, at=s + 0.1, cls="label-l"),
    LBL("ABOUT", 960, 450, at=s + 0.3, cls="label-l gold"),
    CNT(100, 960, 560, at=w("ACT1_08", "one"), fmt={"pre": "$", "suf": " BILLION"}, cls="cap-xl gold shadow", dur=1.4),
])
s = w("ACT1_08", "that")
shot(s, DARK, [
    stock([[0, "1998-01-01", "2000-12-31", 0, 120]], [[0, "1998-01-02"], [w("ACT1_08", "more") - s, "2000-08-23"]], ystep=20,
          ann=[dict(d="2000-08-23", p=90.75, t0=w("ACT1_08", "more") - s, label="ALL-TIME HIGH (INTRADAY)\n$90.75 · AUG 23, 2000", dy=-60, color="#e8b04b", fs=32)]),
], rel=True)
sfx(w("ACT1_08", "more"), "hit", -10)
s = L("ACT1_09")
shot(s, PH("enron_complex", "push", dim=0.0), [])
caption("ENRON LOOKED LIKE **THE FUTURE**", s + 0.1, E("ACT1_09") + 0.3, pos="center", cls="cap-l shadow")
s = L("ACT1_10")
shot(s, PH("enron_complex", [1.25, 0, 0, 1.45, 0, 40], dim=0.45, bw=True), [])
caption("BUT UNDERNEATH THAT SUCCESS...", L("ACT1_10") + 0.1, E("ACT1_10") + 0.1, pos="center", cls="cap-m shadow", dup=True)
s = L("ACT1_11")
shot(s, BLACK, [
    T("HOW MUCH OF THAT **PROFIT**", 960, 460, cls="cap-l", at=s + 0.05, anim="up"),
    T("ARRIVED AS __CASH__?", 960, 600, cls="cap-l", at=w("ACT1_11", "arrived"), anim="up"),
], tout="dip", toutD=0.5)
sfx(w("ACT1_11", "cash"), "hit", -4)

# =====================================================================================
# ACT 2 — THE ACCOUNTING MACHINE
# =====================================================================================
chapter(2, "THE ACCOUNTING MACHINE", E("ACT1_11") + 0.25)
s = L("ACT2_01") - 0.1
shot(s, NAVY, [
    CNT(1992, 960, 560, at=L("ACT2_01") + 0.6, frm=2000, cls="cap-xxl shadow", dur=1.8, cease="inOut"),
    LBL("BACK TO", 960, 400, at=L("ACT2_01") + 0.4, cls="label-l gold"),
])
sfx(L("ACT2_01") + 0.6, "rewind", -8)
s = L("ACT2_02")
shot(s, DARK, [
    IMG("skilling_mug", 170, 230, 420, 540, at=s, anim="fade", kb=[1.0, 1.05], style={"border": "1px solid rgba(255,255,255,.25)", "filter": "grayscale(1)"}),
    LBL("SKILLING WANTED", 760, 400, at=s + 0.2, cls="label-l", anchor="l"),
    T("**MARK-TO-MARKET**", 760, 490, cls="cap-l shadow", at=w("ACT2_02", "mark"), anim="type", ad=0.7, anchor="l"),
    T("ACCOUNTING", 760, 590, cls="cap-m muted", at=w("ACT2_02", "accounting"), anim="up", anchor="l"),
])
sfx(w("ACT2_02", "mark"), "type", -10)
s = w("ACT2_02", "january")
shot(s - 0.2, PAPER, [
    illus(s - 0.15, "ILLUSTRATIVE RECREATION", dark=True),
    T("U.S. SECURITIES AND EXCHANGE COMMISSION", 960, 300, cls="typew dark", at=s - 0.15, anim="type", ad=0.9, style={"fontSize": "44px", "fontWeight": "700"}),
    T("JANUARY 30, 1992", 960, 390, cls="typew dark", at=s + 0.2, anim="type", ad=0.6, style={"fontSize": "40px"}),
    T("Mark-to-market accounting for Enron's\nnatural gas trading contracts", 960, 540, cls="typew dark", at=w("ACT2_02", "approved") - 0.5, anim="fade", align="center", style={"fontSize": "40px", "lineHeight": "1.35"}),
    STAMP("APPROVED", 1320, 760, at=w("ACT2_02", "approved"), color="red", rot=-8, cls="cap-l"),
], cam=[[0, 1.0, 0, 0], [6, 1.07, 0, 20]])
sfx(w("ACT2_02", "approved"), "stamp", -2)
# ---- the 10-year contract explainer
s = L("ACT2_03")
shot(s, PAPER, [
    illus(s, "HYPOTHETICAL EXAMPLE", dark=True),
    RECT(560, 150, 800, 700, at=s, style={"background": "#fbf8f1", "boxShadow": "0 30px 80px rgba(0,0,0,.25)", "border": "1px solid #d8cfbd"}, anim="up"),
    T("NATURAL GAS SUPPLY AGREEMENT", 960, 240, cls="typew dark", at=s + 0.2, anim="fade", style={"fontSize": "38px", "fontWeight": "700"}),
    T("TERM: **10 YEARS**", 960, 355, cls="typew dark", at=w("ACT2_03", "ten"), anim="type", ad=0.5, style={"fontSize": "48px"}),
    RECT(640, 425, 640, 2, at=s + 0.4, style={"background": "#b9ad97"}, anim="wipe"),
    T("EXPECTED PROFIT:", 960, 510, cls="typew dark", at=w("ACT2_03", "expects"), anim="fade", style={"fontSize": "40px"}),
    T("$100,000,000", 960, 600, cls="typew", at=w("ACT2_03", "one"), anim="type", ad=0.8, style={"fontSize": "66px", "fontWeight": "700", "color": "#9a6a0a"}),
    T("(estimated)", 960, 680, cls="typew", at=w("ACT2_03", "profit"), anim="fade", style={"fontSize": "30px", "color": "#7a6f5d"}),
], cam=[[0, 1.0, 0, 0], [6, 1.06, 0, 0]])
sfx(w("ACT2_03", "one"), "type", -8)
yrs10 = [(f"Y{i}", 10) for i in range(1, 11)]
s = L("ACT2_04")
shot(s, DARK, [
    illus(s, "HYPOTHETICAL EXAMPLE"),
    LBL("TRADITIONAL ACCOUNTING", 960, 150, at=s, cls="label-l"),
    T("PROFIT RECORDED **AS IT IS EARNED**", 960, 230, cls="cap-s", at=s + 0.3),
    RECT(250, 820, 1420, 3, at=s, style={"background": "rgba(255,255,255,.4)"}, anim="wipe"),
    *bars(yrs10, 290, 820, 100, 42, 30, at=w("ACT2_04", "gradually") - 0.2, step=0.42, val_fmt=lambda v: f"${v}M", fs=26),
    LBL("PER YEAR", 1650, 880, at=w("ACT2_04", "money"), cls="label"),
])
s = L("ACT2_05")
yb = w("ACT2_05", "book")
els = [illus(s, "HYPOTHETICAL EXAMPLE"),
       LBL("MARK-TO-MARKET", 960, 150, at=s, cls="label-l gold"),
       T("ESTIMATE THE WHOLE CONTRACT'S VALUE **TODAY**", 960, 230, cls="cap-s", at=w("ACT2_05", "estimate")),
       RECT(250, 820, 1420, 3, at=s, style={"background": "rgba(255,255,255,.4)"}, anim="none")]
for i in range(10):
    x = 290 + i * 142
    els.append(RECT(x, 520, 100, 300, at=s, anim="none", style={"background": "var(--gold)", "borderRadius": "6px 6px 0 0", "opacity": "0.9"},
                    kf=[[yb - s - 0.3 + i * 0.06, 0, 0, 1, 1], [yb - s + 0.35 + i * 0.06, 290 - x, -300 * i * 0 - 0, 1, 0]]))
    els.append(T(f"Y{i + 1}", x + 50, 854, cls="mono muted", at=s, anim="none", style={"fontSize": "26px"}))
els.append(RECT(290, 820 - 600, 100, 600, at=yb + 0.25, anim="grow", ad=0.6, style={"background": "linear-gradient(#ffd27a, var(--gold))", "borderRadius": "6px 6px 0 0", "boxShadow": "0 0 50px rgba(232,176,75,.6)"}))
els.append(T("$100M", 340, 180, cls="cap-m gold shadow", at=yb + 0.7, anim="pop"))
els.append(T("BOOKED AS PROFIT NOW", 470, 420, cls="label-l gold", at=w("ACT2_05", "now") - 0.1, anchor="l"))
els.append(T("Simplified: in practice, expected future cash flows were estimated and discounted to a present value.", 960, 893, cls="label",
             at=yb + 0.8, until=E("ACT2_05") - 0.2, style={"fontSize": "21px", "letterSpacing": "0.06em", "textTransform": "none", "color": "rgba(242,239,232,.72)"}))
shot(s, DARK, els, cam=[[0, 1.0, 0, 0], [E("ACT2_05") - s, 1.0, 0, 0], [E("ACT2_06") - s, 1.15, 330, 90]])
sfx(yb + 0.25, "whoosh", -6)
caption("YEAR ONE.", L("ACT2_06"), w("ACT2_06", "one", 2) - 0.12, pos=(1180, 620), cls="cap-xl shadow", aod=0.08, aout="fade")
caption("ONE **GIANT** NUMBER.", w("ACT2_06", "one", 2), E("ACT2_06") + 0.4, pos=(1180, 620), cls="cap-l shadow")
sfx(L("ACT2_06"), "hit", -6)
s = L("ACT2_07")
shot(s, PH("chicago_floor", "in", dim=0.35), [])
caption("MARK-TO-MARKET **≠ FRAUD**", s + 0.4, w("ACT2_07", "it's") - 0.05, pos="center", cls="cap-l shadow")
caption("FINE FOR THINGS WITH **CLEAR MARKET PRICES**", w("ACT2_07", "it's"), E("ACT2_07") + 0.2, pos="center", cls="cap-m shadow")
s = L("ACT2_08")
shot(s, DARK, [
    LBL("THE DANGER IS IN ONE WORD", 960, 380, at=s + 0.05, cls="label-l", anim="up"),
    T("~~ESTIMATE.~~", 960, 560, cls="cap-xxl", at=w("ACT2_08", "estimate"), anim="zoom", ad=0.35),
], cam=[[0, 1.0, 0, 0], [4.5, 1.1, 0, 0]])
sfx(w("ACT2_08", "estimate"), "hit", -2)
s = L("ACT2_09")
dials = [("FUTURE PRICES", 520), ("DEMAND", 960), ("COSTS", 1400)]
opt = w("ACT2_10", "optimistic")
els = [illus(s),
       LBL("MARKET PRICE TO CHECK AGAINST:", 960, 230, at=s, cls="label-l"),
       T("~~NONE~~", 960, 320, cls="cap-m", at=w("ACT2_09", "market") + 0.3, anim="stamp"),
       T("VALUE = **ENRON'S OWN ASSUMPTIONS**", 960, 450, cls="cap-s", at=w("ACT2_09", "assumptions") - 0.6)]
for i, (name, cx) in enumerate(dials):
    ta = w("ACT2_09", "assumptions") - 0.3 + i * 0.15
    els += [RECT(cx - 170, 600, 340, 10, at=ta, style={"background": "rgba(255,255,255,.18)", "borderRadius": "6px"}, anim="wipe"),
            RECT(cx - 20, 575, 40, 60, at=ta + 0.1, style={"background": "var(--ink)", "borderRadius": "8px"}, anim="pop",
                 kf=[[opt - s + i * 0.12, 0, 0], [opt - s + 0.9 + i * 0.12, 120, 0]]),
            T(name, cx, 680, cls="label-l", at=ta + 0.1)]
els += [LBL("OPTIMISTIC →", 1500, 520, at=opt, cls="label gold"),
        LBL("ESTIMATED PROFIT (HYPOTHETICAL)", 960, 760, at=w("ACT2_10", "profit") - 0.6, cls="label"),
        CNT(140, 960, 838, at=w("ACT2_10", "bigger") - 0.1, frm=100, fmt={"pre": "$", "suf": "M"}, cls="cap-l gold shadow", dur=0.9, cease="outExpo")]
shot(s, DARK, els, cam=[[0, 1.0, 0, 0], [5.5, 1.05, 0, -20], [11, 1.0, 0, 0]])
caption("**INSTANTLY.**", w("ACT2_10", "instantly"), E("ACT2_10") + 0.2, pos=(1500, 838), cls="cap-m shadow", dup=True)
sfx(w("ACT2_10", "instantly"), "pop", -6)
# reported now vs cash later
s = L("ACT2_11")
now = w("ACT2_11", "profit")
later = w("ACT2_11", "cash")
never = w("ACT2_11", "never")
els = [illus(s),
       LBL("THE CRUCIAL PART", 960, 150, at=s, cls="label-l gold"),
       RECT(200, 760, 1520, 3, at=s + 0.2, style={"background": "rgba(255,255,255,.4)"}, anim="wipe"),
       T("TODAY", 300, 810, cls="mono", at=s + 0.3, anim="fade", style={"fontSize": "28px"}),
       T("YEAR 10", 1640, 810, cls="mono muted", at=s + 0.3, anim="fade", style={"fontSize": "28px"}),
       RECT(240, 360, 120, 400, at=now, anim="grow", ad=0.5, style={"background": "var(--gold)", "borderRadius": "6px 6px 0 0"}),
       T("PROFIT\nREPORTED", 300, 290, cls="label-l gold", at=now + 0.2, align="center"),
       PATH("M 300 360 L 1700 360", at=now + 0.4, stroke="rgba(232,176,75,.55)", sw=3, dash="12 12", anim="draw", ad=0.8),
       LBL("THE ESTIMATE", 1560, 330, at=now + 0.8, cls="label gold")]
for i in range(9):
    x = 420 + i * 140
    hh = [20, 35, 50, 40, 60, 45, 70, 55, 50][i]
    els.append(RECT(x, 760 - hh, 90, hh, at=later + 0.15 * i, anim="grow", ad=0.35, style={"background": "var(--cyan)", "borderRadius": "4px 4px 0 0"}))
els += [T("__CASH__ ARRIVES SLOWLY", 1000, 600, cls="cap-s", at=later + 0.3),
        PATH("M 1700 700 L 1700 380", at=never, stroke="#e5484d", sw=5, arrow=True, anim="draw", ad=0.5),
        T("~~THE GAP~~", 1560, 520, cls="cap-m", at=never + 0.3, anim="pop", anchor="r")]
shot(s, DARK, els)
sfx(never + 0.3, "hit", -9)
# profit vs cash definitions
s = L("ACT2_12")
cf = w("ACT2_12", "cash")
shot(s, DARK, [
    RECT(0, 0, 960, 1080, at=s, style={"background": "linear-gradient(180deg, rgba(232,176,75,0.10), rgba(232,176,75,0.02))"}, anim="fade"),
    T("ACCOUNTING\nPROFIT", 480, 330, cls="cap-l gold shadow", at=s, anim="up", align="center"),
    T("WHAT THE FINANCIAL\nSTATEMENTS SAY\nYOU EARNED", 480, 620, cls="label-l", at=w("ACT2_12", "what"), align="center", style={"lineHeight": "1.5"}),
    RECT(958, 160, 4, 700, at=s, style={"background": "rgba(255,255,255,.2)"}, anim="wipeD"),
    RECT(960, 0, 960, 1080, at=cf, style={"background": "linear-gradient(180deg, rgba(89,201,211,0.10), rgba(89,201,211,0.02))"}, anim="fade"),
    T("CASH\nFLOW", 1440, 330, cls="cap-l cyan shadow", at=cf, anim="up", align="center"),
    T("THE MONEY THAT ACTUALLY\nCOMES THROUGH THE DOOR", 1440, 620, cls="label-l", at=w("ACT2_12", "money"), align="center", style={"lineHeight": "1.5"}),
])
s = L("ACT2_13")
shot(s, BLACK, [
    T("PROFIT", 960, 540, cls="cap-xl gold shadow", at=s, anim="none", kf=[[0.2, 0, 0], [1.6, -420, 0]]),
    T("CASH", 960, 540, cls="cap-xl cyan shadow", at=s, anim="none", kf=[[0.2, 0, 0], [1.6, 420, 0]]),
    T("≠", 960, 530, cls="cap-xxl red", at=s + 1.0, anim="pop"),
    LBL("AT ENRON, THE TWO DRIFTED FAR APART", 960, 820, at=s + 0.6, cls="label-l"),
], cam=[[0, 1.06, 0, 0], [2.4, 1.0, 0, 0]])
sfx(s + 1.0, "hit", -3)
# Blockbuster
s = L("ACT2_14")
shot(s, PH("blockbuster_sign", "in", dim=0.3), [])
datestamp("JULY 2000", s + 0.4, E("ACT2_15"), sub="PHOTO: 2018")
caption("ENRON + BLOCKBUSTER", w("ACT2_14", "deal") - 0.6, E("ACT2_14") + 0.3)
s = L("ACT2_15")
shot(s, DARK, [
    T("PILOT TESTS ONLY", 960, 420, cls="cap-l", at=w("ACT2_15", "small") - 0.2),
    STAMP("ENDED · MARCH 2001", 960, 620, at=w("ACT2_15", "ended"), color="red", rot=-4, cls="cap-m"),
])
sfx(w("ACT2_15", "ended"), "stamp", -6)
s = L("ACT2_16")
shot(s, NAVY, [
    LBL("BUT ENRON HAD ALREADY BOOKED", 960, 340, at=s + 0.1, cls="label-l"),
    CNT(111, 960, 500, at=w("ACT2_16", "one") - 0.1, fmt={"pre": "$", "suf": " MILLION"}, cls="cap-xl gold shadow", dur=1.6),
    LBL("IN PROFIT FROM THE VENTURE (Q4 2000 – Q1 2001)", 960, 640, at=w("ACT2_16", "profit"), cls="label-l"),
    T("BASED LARGELY ON **PROJECTED FUTURE EARNINGS**", 960, 780, cls="cap-s", at=w("ACT2_16", "projected") - 0.2),
    LBL("SOURCE: U.S. SEC AND JUSTICE DEPARTMENT FILINGS", 960, 880, at=w("ACT2_16", "projected"), cls="label", style={"fontSize": "20px"}),
])
s = L("ACT2_17")
els = [illus(s),
       LBL("ONCE IT'S BOOKED...", 960, 150, at=s, cls="label-l gold"),
       RECT(250, 820, 1420, 3, at=s, style={"background": "rgba(255,255,255,.4)"}, anim="none"),
       RECT(290, 220, 100, 600, at=s, anim="none", style={"background": "var(--gold)", "borderRadius": "6px 6px 0 0", "opacity": "0.45"}),
       T("Y1", 340, 854, cls="mono muted", at=s, anim="none", style={"fontSize": "26px"})]
for i in range(1, 10):
    x = 290 + i * 142
    els += [RECT(x, 760, 100, 60, at=w("ACT2_17", "can't") - 0.3 + i * 0.05, anim="fade", style={"border": "2px dashed rgba(255,255,255,.3)", "borderRadius": "6px"}),
            T(f"Y{i + 1}", x + 50, 854, cls="mono muted", at=s, anim="none", style={"fontSize": "26px"})]
els += [T("NOTHING LEFT TO REPORT FROM THIS DEAL", 1080, 640, cls="cap-s", at=w("ACT2_17", "again")),
        T("$0", 1080, 540, cls="cap-l red", at=w("ACT2_17", "tomorrow"), anim="pop")]
shot(s, DARK, els)
s = L("ACT2_18")
els = [LBL("TO KEEP GROWING", 960, 150, at=s, cls="label-l")]
qs = [f"Q{i}" for i in range(1, 9)]
for i, q in enumerate(qs):
    x = 300 + i * 170
    h = 70 * (1.32 ** i)
    els += [RECT(x, 860 - h, 120, h, at=w("ACT2_18", "more") + 0.2 * i, anim="grow", ad=0.4, style={"background": "linear-gradient(var(--gold), #a8781f)", "borderRadius": "6px 6px 0 0"}),
            T(q, x + 60, 900, cls="mono muted", at=s + 0.2, anim="fade", style={"fontSize": "26px"})]
els += [LBL("NEW DEALS NEEDED", 340, 230, at=s + 0.4, cls="label", anchor="l"), illus(s)]
shot(s, DARK, els)
caption("MORE DEALS.", w("ACT2_18", "more"), w("ACT2_18", "bigger") - 0.05, pos=(760, 330), cls="cap-m shadow", aod=0.08)
caption("**BIGGER** DEALS.", w("ACT2_18", "bigger"), w("ACT2_18", "every") - 0.05, pos=(760, 330), cls="cap-m shadow", dup=True, aod=0.08)
caption("EVERY SINGLE **QUARTER.**", w("ACT2_18", "every"), E("ACT2_18") + 0.2, pos=(760, 330), cls="cap-m shadow", dup=True)
s = L("ACT2_19")
els = [T("THE **TREADMILL**", 960, 260, cls="cap-l shadow", at=w("ACT2_19", "treadmill") - 0.2)]
for i in range(14):
    els.append(BOX(-300 + i * 260, 520, 220, 130, at=s, text="DEAL", anim="none",
                   style={"border": "3px solid var(--gold)", "color": "var(--gold)", "fontSize": "42px", "background": "rgba(232,176,75,.08)"},
                   kf=[[0, 0, 0], [1.2, 160, 0], [2.4, 520, 0], [3.4, 1300, 0]], kfEase="in"))
els += [RECT(0, 690, 1920, 6, at=s, anim="none", style={"background": "rgba(255,255,255,.25)"}),
        T("SPEEDING UP", 960, 820, cls="cap-s red", at=w("ACT2_19", "speeding"))]
shot(s, DARK, els, tout="dip", toutD=0.5)
sfx(w("ACT2_19", "speeding"), "riser", -6)

# =====================================================================================
# ACT 3 — EXPECTATIONS KEEP RISING
# =====================================================================================
chapter(3, "EXPECTATIONS", E("ACT2_19") + 0.25)
s = L("ACT3_01")
shot(s, DARK, [
    stock([[0, "1998-10-01", "2000-12-31", 0, 100]], [[0, "1998-12-31"], [w("ACT3_01", "fifty") - s + 0.2, "1999-12-31"], [w("ACT3_01", "eighty") - s + 0.4, "2000-12-29"]], ystep=20,
          ann=[dict(d="1999-12-31", t0=w("ACT3_01", "fifty") - s + 0.2, label="+56%\nIN 1999", dy=-190, dx=-40, color="#4cc38a", fs=40),
               dict(d="2000-12-29", t0=w("ACT3_01", "eighty") - s + 0.4, label="+87%\nIN 2000", dy=70, dx=-190, color="#4cc38a", fs=40)]),
], rel=True)
sfx(w("ACT3_01", "fifty"), "beep", -14)
sfx(w("ACT3_01", "eighty"), "beep", -14)
s = L("ACT3_02")
tick = "ENE ▲ 83.13 &nbsp; ENE 82.00 &nbsp; ENE ▲ 84.06 &nbsp; ENE 79.88 &nbsp; ENE ▲ 81.25"
shot(s, PH("enron_downtown", "in", dim=0.25, fallback="houston_night_2000"), [
    dict(kind="ticker", y=760, h=70, text="**ENE** &nbsp;" + tick, speed=170, at=s + 0.2, anim="fade", fs=34),
])
caption("STOCK TICKERS IN THE **LOBBIES & ELEVATORS**", s + 0.4, w("ACT3_02", "executives") - 0.05, pos=(960, 300), cls="cap-m shadow")
s = w("ACT3_02", "executives")
shot(s, NAVY, [
    LBL("EXECUTIVE PAY", 960, 330, at=s, cls="label-l"),
    T("**STOCK OPTIONS**", 960, 450, cls="cap-xl shadow", at=s + 0.2, anim="zoom"),
    T("HIGHER SHARE PRICE = BIGGER PAYOFF", 960, 620, cls="cap-s", at=w("ACT3_02", "options") + 0.2),
])
s = L("ACT3_03")
shot(s, PH("enron_field", "in", dim=0.35), [
    BOX(560, 300, 800, 330, at=s + 0.3, html='<div style="font-family:IBM Plex Mono;font-size:150px;color:#ffb83d;text-shadow:0 0 30px rgba(255,170,40,.8);letter-spacing:.06em">ENE 83</div>',
        style={"background": "rgba(10,8,6,.88)", "border": "6px solid #3a3227", "borderRadius": "10px"}),
    LBL("ENRON SHARE PRICE, DEC. 29, 2000 · $83.13", 960, 670, at=s + 0.6, cls="label shadow"),
])
caption("IT WAS **THE SCOREBOARD**", w("ACT3_03", "it") - 0.05, E("ACT3_03") + 0.4, pos=(960, 820), cls="cap-m shadow", dup=True)
s = L("ACT3_04")
els = [LBL("ANALYSTS EXPECTED: HIT THE TARGET. EVERY QUARTER.", 960, 200, at=s, cls="label-l")]
checks = [w("ACT3_04", "delivered"), w("ACT3_04", "again"), w("ACT3_04", "again", 2)]
for i in range(8):
    x = 230 + i * 190
    tt = checks[min(i // 3, 2)] + (i % 3) * 0.12 if i < 9 else checks[2]
    els += [BOX(x, 420, 160, 160, at=s + 0.1 * i, text=f"Q{(i % 4) + 1}", anim="pop", style={"border": "2px solid rgba(255,255,255,.3)", "fontSize": "44px"}),
            T("✓", x + 80, 680, cls="cap-l green", at=tt, anim="pop")]
els.append(illus(s))
shot(s, DARK, els)
for c in checks:
    sfx(c, "pop", -12)
s = L("ACT3_05")
els = [LBL("EVERY SUCCESS RAISED THE BAR", 960, 160, at=s, cls="label-l"), illus(s)]
for i in range(6):
    x = 330 + i * 220
    h = 140 + i * 85
    els += [RECT(x, 880 - h, 150, h, at=s + 0.15 * i, anim="grow", ad=0.35, style={"background": "rgba(232,176,75,.85)", "borderRadius": "6px 6px 0 0"}),
            RECT(x - 25, 880 - h - 70, 200, 6, at=s + 0.15 * i + 0.2, anim="wipe", style={"background": "var(--red)"})]
els.append(T("THE BAR", 1620, 300, cls="cap-s red", at=s + 0.9))
shot(s, DARK, els)
s = L("ACT3_06")
shot(s, PH("dabhol", "in", dim=0.25, fallback="pipeline"), [])
shot(w("ACT3_06", "broadband"), PH("fiber_laying", "right", dim=0.25, fallback="houston_night_2000"), [])
caption("~~STRUGGLING~~", w("ACT3_06", "struggling"), E("ACT3_06") + 0.3, pos="center", cls="cap-xl shadow")
s = L("ACT3_07")
need = "M 260 860 C 700 840, 1100 700, 1660 220"
real = "M 260 860 C 700 830, 1100 760, 1660 700"
shot(s, DARK, [
    RECT(240, 870, 1460, 3, at=s, style={"background": "rgba(255,255,255,.4)"}, anim="wipe"),
    PATH(need, at=s + 0.1, stroke="#e8b04b", sw=6, ad=1.6),
    T("GROWTH WALL STREET EXPECTED", 1640, 190, cls="label-l gold", at=s + 1.2, anchor="r"),
    PATH(real, at=w("ACT3_07", "real"), stroke="#9aa0a8", sw=6, ad=1.4),
    T("REAL GROWTH", 1640, 650, cls="label-l", at=w("ACT3_07", "harder"), anchor="r"),
    illus(s),
])
s = L("ACT3_08")
shot(s, EMBER, [
    BOX(560, 320, 360, 160, at=w("ACT3_08", "losses"), text="LOSSES", style={"background": "rgba(229,72,77,.18)", "border": "4px solid var(--red)", "color": "var(--red)", "fontSize": "60px"}),
    BOX(1000, 320, 360, 160, at=w("ACT3_08", "debt"), text="DEBT", style={"background": "rgba(229,72,77,.18)", "border": "4px solid var(--red)", "color": "var(--red)", "fontSize": "60px"}),
    T("WHERE DO YOU PUT THEM?", 960, 640, cls="cap-l shadow", at=w("ACT3_08", "when") - 0.3),
], tout="dip", toutD=0.5)
sfx(E("ACT3_08") - 0.3, "hit", -5)

# =====================================================================================
# ACT 4 — HIDING THE PROBLEMS
# =====================================================================================
chapter(4, "HIDING THE PROBLEMS", E("ACT3_08") + 0.25)
s = L("ACT4_01") - 0.1
shot(s, dict(kind="dark"), [
    doc("powers_p1", [[0, 830, 560, 1.0, -1.5], [6, 880, 590, 1.55, -1.5]], hl=[(0, L("ACT4_01") + 1.4 - s, "")], base_w=1050),
    BOX(1180, 630, 640, 190, at=L("ACT4_01") + 0.3, html='<div class="cap-l">ANDREW FASTOW</div><div class="label-l gold" style="margin-top:8px">CFO, 1998 – 2001</div>',
        style={"background": "rgba(5,7,9,.9)", "borderLeft": "6px solid var(--gold)", "flexDirection": "column", "alignItems": "flex-start", "padding": "0 34px", "justifyContent": "center", "textTransform": "none"},
        anim="left", fixed=True),
    LBL("SOURCE: POWERS REPORT TO ENRON'S BOARD, FEB. 2002", 1180, 862, at=L("ACT4_01") + 0.6, cls="label ink boxed", anchor="l", fixed=True, style={"fontSize": "18px"}),
])
_extra_hl = [[547, 535, 1343, 562], [217, 612, 582, 640]]
SHOTS[-1]["els"][0]["hl"] = [dict(boxes=_extra_hl, t0=L("ACT4_01") + 1.0 - s, cls="")]
sfx(L("ACT4_01") + 0.3, "paper", -8)
s = L("ACT4_02")
shot(s, NAVY, [
    LBL("HIS SPECIALTY", 960, 370, at=s, cls="label-l"),
    T("SPECIAL-PURPOSE\n**ENTITIES**", 960, 540, cls="cap-xl shadow", at=w("ACT4_02", "special") - 0.1, anim="zoom", align="center"),
])
s = L("ACT4_03")
shot(s, DARK, [
    BOX(260, 380, 520, 300, at=s, text="ENRON", style={"border": "4px solid var(--gold)", "color": "var(--gold)", "fontSize": "80px"}),
    PATH("M 800 530 L 1120 530", at=s + 0.4, stroke="#9aa0a8", arrow=True),
    BOX(1140, 400, 480, 260, at=s + 0.7, text="SPE", style={"border": "4px solid var(--cyan)", "color": "var(--cyan)", "fontSize": "80px"}),
    T("A SEPARATE COMPANY\nFOR ONE SPECIFIC JOB", 1380, 780, cls="label-l", at=w("ACT4_03", "separate"), align="center", style={"lineHeight": "1.5"}),
    T("**LEGAL. COMMON.**", 960, 230, cls="cap-m", at=w("ACT4_03", "legitimately")),
])
s = L("ACT4_04")
ind = w("ACT4_04", "independent")
shot(s, DARK, [
    illus(s, "SIMPLIFIED DIAGRAM"),
    LBL("THE RULE BACK THEN", 960, 100, at=s, cls="label-l"),
    BOX(660, 200, 600, 600, at=s + 0.1, text="", style={"border": "4px solid var(--cyan)", "background": "rgba(89,201,211,.06)"}),
    T("SPE", 960, 170, cls="cap-s cyan", at=s + 0.1),
    RECT(664, 204, 592, 574, at=s + 0.3, anim="fade", style={"background": "rgba(255,255,255,.07)"}),
    T("OTHER FUNDING", 960, 490, cls="label-l", at=s + 0.4),
    RECT(664, 778, 592, 18, at=w("ACT4_04", "three") - 0.1, anim="growX", ad=0.5, style={"background": "var(--gold)", "boxShadow": "0 0 25px var(--gold)"}),
    T("**3%** FROM AN OUTSIDE INVESTOR, AT RISK", 960, 856, cls="cap-s", at=w("ACT4_04", "three"), style={"fontSize": "40px"}),
    STAMP("COUNTS AS INDEPENDENT", 960, 330, at=ind, color="cyan", rot=-5, cls="cap-s"),
])
sfx(ind, "stamp", -8)
s = L("ACT4_05")
lift = w("ACT4_05", "debt")
shot(s, DARK, [
    illus(s, "SIMPLIFIED DIAGRAM"),
    BOX(200, 200, 640, 640, at=s, text="", style={"border": "3px solid var(--ink)", "background": "rgba(255,255,255,.03)"}),
    T("ENRON BALANCE SHEET", 520, 170, cls="label-l", at=s),
    BOX(250, 260, 540, 140, at=s + 0.1, text="ASSETS", style={"background": "rgba(76,195,138,.15)", "border": "3px solid var(--green)", "color": "var(--green)", "fontSize": "46px"}),
    BOX(250, 430, 540, 140, at=s + 0.2, text="EQUITY", style={"background": "rgba(232,176,75,.12)", "border": "3px solid var(--gold)", "color": "var(--gold)", "fontSize": "46px"}),
    BOX(250, 600, 540, 210, at=s + 0.3, text="DEBT", style={"background": "rgba(229,72,77,.18)", "border": "3px solid var(--red)", "color": "var(--red)", "fontSize": "60px"},
        kf=[[lift - s, 0, 0], [lift - s + 1.0, 1000, -20]]),
    BOX(1180, 480, 560, 340, at=w("ACT4_05", "mattered"), text="", style={"border": "4px dashed var(--cyan)"}),
    T("SPE", 1460, 450, cls="cap-s cyan", at=w("ACT4_05", "mattered")),
    T("OFF THE BALANCE SHEET", 1460, 862, cls="label-l cyan", at=lift + 1.0),
    T("**LOOKS STRONGER**", 520, 880, cls="cap-s", at=w("ACT4_05", "balance") - 0.2),
])
sfx(lift, "whoosh", -8)
s = L("ACT4_06")
els = [T("AGAIN AND AGAIN", 960, 170, cls="label-l", at=s)]
for i in range(12):
    els.append(BOX(140 + (i % 6) * 280, 300 + (i // 6) * 180, 220, 120, at=s + 0.08 * i, text="SPE", anim="pop",
                   style={"border": "2px solid rgba(89,201,211,.35)", "color": "rgba(89,201,211,.5)", "fontSize": "34px"}))
els += [BOX(160, 670, 460, 170, at=w("ACT4_06", "chewco"), text="CHEWCO", style={"background": "#0d1218", "border": "4px solid var(--gold)", "fontSize": "62px"}),
        BOX(730, 670, 460, 170, at=w("ACT4_06", "ljm"), text="LJM", style={"background": "#0d1218", "border": "4px solid var(--gold)", "fontSize": "62px"}),
        BOX(1300, 670, 460, 170, at=w("ACT4_06", "raptors"), text="RAPTORS", style={"background": "#0d1218", "border": "4px solid var(--gold)", "fontSize": "62px"}),
        T("NAMED AFTER THE VELOCIRAPTORS OF JURASSIC PARK", 1760, 878, cls="label", at=w("ACT4_06", "jurassic") - 0.3, anchor="r", style={"fontSize": "21px"})]
shot(s, DARK, els)
for nm in ["chewco", "ljm", "raptors"]:
    sfx(w("ACT4_06", nm), "pop", -10)
s = L("ACT4_07")
shot(s, DARK, [
    illus(s, "SIMPLIFIED DIAGRAM"),
    BOX(660, 200, 600, 600, at=s, text="", anim="none", style={"border": "4px solid var(--cyan)", "background": "rgba(89,201,211,.06)"}),
    T("SPE", 960, 170, cls="cap-s cyan", at=s, anim="none"),
    RECT(664, 778, 592, 18, at=s, anim="none", style={"background": "var(--gold)", "boxShadow": "0 0 25px var(--gold)"}),
    T("THE \"OUTSIDE\" 3%", 1300, 790, cls="cap-s", at=s, anim="none", anchor="l", style={"fontSize": "40px"}),
    BOX(60, 710, 360, 150, at=w("ACT4_07", "outside"), text="ENRON", style={"border": "4px solid var(--gold)", "color": "var(--gold)", "fontSize": "54px"}),
    PATH("M 420 785 C 520 785, 560 790, 650 788", at=w("ACT4_07", "wasn't") - 0.1, stroke="#e5484d", sw=6, arrow=True, ad=0.6),
    T("~~NOT REALLY OUTSIDE~~", 960, 490, cls="cap-m", at=w("ACT4_07", "really"), anim="stamp"),
])
s = L("ACT4_08")
shot(s, dict(kind="dark"), [
    doc("powers_p5", [[0, 760, 1390, 1.1, 1.0], [3.5, 760, 1390, 1.45, 1.0]], hl=[(0, 0.4, "")], base_w=1100),
    RECT(0, 0, 1920, 1080, style={"background": "linear-gradient(0deg, rgba(5,7,9,.9) 0%, rgba(5,7,9,0) 45%)"}, anim="none", fixed=True),
    STAMP("CHEWCO: DIDN'T MEET IT", 960, 790, at=w("ACT4_08", "didn't"), color="red", rot=-3, cls="cap-m", fixed=True),
])
sfx(w("ACT4_08", "didn't"), "stamp", -6)
s = L("ACT4_09")
bk = w("ACT4_09", "backed")
shot(s, DARK, [
    illus(s, "SIMPLIFIED DIAGRAM"),
    BOX(140, 380, 460, 260, at=s, text="ENRON'S<br>INVESTMENTS", style={"border": "3px solid var(--ink)", "fontSize": "44px"}),
    T("▼ LOSING VALUE", 370, 700, cls="label-l red", at=w("ACT4_09", "losses")),
    PATH("M 620 510 L 1180 510", at=w("ACT4_09", "hedged") - 0.2, stroke="#e8b04b", arrow=True, ad=0.6),
    T("\"HEDGED\"", 900, 460, cls="cap-s gold", at=w("ACT4_09", "hedged")),
    BOX(1200, 360, 560, 300, at=w("ACT4_09", "covered") - 0.3, text="RAPTOR", style={"border": "4px solid var(--cyan)", "color": "var(--cyan)", "fontSize": "72px"}),
    T("WILL COVER THE LOSSES", 1480, 710, cls="label-l cyan", at=w("ACT4_09", "covered")),
    BOX(1250, 760, 460, 120, at=bk, text="BACKED BY ENRON STOCK", style={"background": "rgba(229,72,77,.2)", "border": "4px solid var(--red)", "color": "var(--red)", "fontSize": "36px"}),
    PATH("M 1240 830 C 900 900, 520 900, 370 735", at=bk + 0.3, stroke="#e5484d", sw=5, arrow=True, dash="16 12", ad=0.9),
    T("~~A HEDGE WITH ITSELF~~", 760, 250, cls="cap-m", at=w("ACT4_09", "own"), anim="stamp"),
])
s = L("ACT4_10")
shot(s, dict(kind="dark"), [
    doc("powers_p4", [[0, 820, 1300, 0.95, 0.8], [1.6, 820, 1600, 1.15, 0.8], [6.5, 780, 1820, 1.55, 0.8]],
        hl=[(0, 0.9, ""), (1, w("ACT4_10", "almost") - s, "red")], base_w=1050),
    LBL("POWERS REPORT · FEB. 1, 2002", 100, 90, at=s + 0.2, cls="label ink boxed", anchor="l", fixed=True),
])
sfx(s + 0.1, "paper", -8)
s = L("ACT4_11")
shot(s, EMBER, [T("CONFLICT OF\n~~INTEREST~~", 960, 540, cls="cap-xl shadow", at=s + 0.05, anim="zoom", align="center")])
sfx(s + 0.05, "hit", -4)
s = L("ACT4_12")
ran = w("ACT4_12", "ran")
appr = w("ACT4_12", "board")
shot(s, DARK, [
    BOX(160, 330, 520, 240, at=s, text="ENRON", style={"border": "4px solid var(--gold)", "color": "var(--gold)", "fontSize": "76px"}),
    BOX(1240, 330, 520, 240, at=w("ACT4_12", "ljm"), text="LJM", style={"border": "4px solid var(--cyan)", "color": "var(--cyan)", "fontSize": "76px"}),
    PATH("M 700 450 L 1220 450", at=w("ACT4_12", "partnerships"), stroke="#9aa0a8", arrow=True),
    PATH("M 1220 500 L 700 500", at=w("ACT4_12", "partnerships") + 0.2, stroke="#9aa0a8", arrow=True),
    BOX(220, 640, 400, 120, at=w("ACT4_12", "design") - 0.2, text="FASTOW · CFO", style={"background": "#0d1218", "border": "2px solid var(--ink)", "fontSize": "40px"}),
    BOX(1300, 640, 400, 120, at=ran, text="FASTOW · RUNS LJM", style={"background": "#0d1218", "border": "2px solid var(--red)", "color": "var(--red)", "fontSize": "40px"}),
    T("SAME MAN", 960, 640, cls="cap-s red", at=ran + 0.4, until=appr - 0.3),
    IMG("code_ethics_cover", 840, 560, 240, 336, at=appr - 0.2, anim="up", style={"boxShadow": "0 30px 80px rgba(0,0,0,.7)"}),
    STAMP("BOARD-APPROVED EXCEPTION", 1380, 820, at=appr + 0.4, color="gold", rot=-4, cls="cap-s"),
])
sfx(appr + 0.4, "stamp", -8)
s = L("ACT4_13")
th = w("ACT4_13", "made")
shot(s, dict(kind="dark"), [
    doc("powers_both_sides", [[0, 600, 820, 1.0, -0.8], [th - s, 600, 880, 1.6, -0.8]], hl=[(0, w("ACT4_13", "both") - s - 0.1, "")], base_w=1050),
])
shot(th - 0.15, dict(kind="dark"), [
    doc("powers_p3", [[0, 820, 830, 1.2, 0.6], [4, 800, 860, 1.6, 0.6]], hl=[(0, 0.5, "red")], base_w=1050),
    BOX(1240, 680, 600, 170, at=w("ACT4_13", "thirty"), html='<div class="cap-l gold">$30 MILLION+</div><div class="label" style="margin-top:6px">FROM THE PARTNERSHIPS</div>',
        style={"background": "rgba(5,7,9,.92)", "borderLeft": "6px solid var(--gold)", "flexDirection": "column", "alignItems": "flex-start", "padding": "0 30px", "textTransform": "none"}, anim="left", fixed=True),
])
sfx(w("ACT4_13", "thirty"), "hit", -10)
s = L("ACT4_14")
shot(s, PH("enron_complex", "in", dim=0.05), [])
caption("ON PAPER: **STRONGER THAN EVER**", s + 0.1, E("ACT4_14") + 0.2, pos="center", cls="cap-l shadow", dup=True)
s = L("ACT4_15")
els = []
for i in range(10):
    els.append(BOX(200 + (i * 157) % 1500, 640 - (i % 3) * 140, 240, 110, at=s + 0.25 * i, text="RISK", anim="fade", ad=0.8,
                   style={"border": "2px solid rgba(229,72,77,.6)", "color": "rgba(229,72,77,.8)", "background": "rgba(229,72,77,.08)", "fontSize": "34px"}))
shot(s, PH("enron_complex", [1.15, 0, 0, 1.3, 0, 30], dim=0.6, bw=True), els, tout="dip", toutD=0.5)
caption("IN REALITY, **RISK** WAS PILING UP", s + 0.2, E("ACT4_15") + 0.3, pos=(960, 250), cls="cap-m shadow")

# =====================================================================================
# ACT 5 — THE WARNING SIGNS
# =====================================================================================
chapter(5, "THE WARNING SIGNS", E("ACT4_15") + 0.25)
s = L("ACT5_01") - 0.1
shot(s, BLACK, [
    IMG("enron_logo", 960 - 260, 540 - 260, 520, 520, at=s, anim="fade", ad=1.0, style={"opacity": "0.9"}),
    dict(kind="rect", x=-1920, y=-1080, w=5760, h=3240, at=s, anim="none",
         style={"background": "radial-gradient(circle 300px at 50% 50%, rgba(0,0,0,0) 0%, rgba(0,0,0,0.96) 100%)"},
         kf=[[0, -500, -200], [1.4, 300, 100], [2.6, -100, 0]]),
])
s = L("ACT5_02")
shot(s, dict(kind="dark", grid=True), person("mclean", "BETHANY McLEAN", "REPORTER, FORTUNE", s + 0.2, side="right", note="PHOTO: 2018",
                                            extra=[T("IN THE SAME MAGAZINE THAT KEPT\nCALLING ENRON **INNOVATIVE**", 170, 740, cls="label-l", at=w("ACT5_02", "same") - 0.1, anchor="l", style={"lineHeight": "1.5"})]))
datestamp("MARCH 2001", s + 0.1, w("ACT5_02", "asked"))
s = w("ACT5_02", "asked") - 0.1
shot(s, PAPER, [
    LBL("FORTUNE · MARCH 5, 2001", 960, 330, at=s, cls="label", style={"color": "#6f6553"}),
    T("Is Enron Overpriced?", 960, 520, cls="serif dark", at=s + 0.2, anim="type", ad=1.0, style={"fontSize": "130px"}),
    RECT(560, 640, 800, 3, at=s + 1.2, anim="wipe", style={"background": "#2a2520"}),
], cam=[[0, 1.0, 0, 0], [4, 1.06, 0, 0]])
sfx(s + 0.2, "type", -6)
s = L("ACT5_03")
shot(s, NAVY, [
    T("HOW EXACTLY DOES ENRON\n**MAKE ITS MONEY?**", 960, 540, cls="cap-l shadow", at=s + 0.1, anim="blur", align="center"),
], cam=[[0, 1.0, 0, 0], [3.5, 1.08, 0, 0]])
s = L("ACT5_04")
dbt_t, cash_t = w("ACT5_04", "debt"), w("ACT5_04", "cash")
shot(s, DARK, [
    *[RECT(330, 255 + i * 30, 520 - (i * 83) % 220, 12, at=s + 0.03 * i, anim="fade", ad=0.4, style={"background": "rgba(255,255,255,0.13)", "borderRadius": "6px"}) for i in range(6)],
    RECT(330, 255, 520, 170, at=w("ACT5_04", "nearly") - 0.1, anim="fade", style={"background": "rgba(5,7,9,0.55)"}),
    T("?", 590, 340, cls="cap-l muted", at=w("ACT5_04", "nearly"), anim="pop"),
    T("FINANCIAL STATEMENTS", 1060, 285, cls="label-l", at=s + 0.1, anchor="l"),
    T("**NEARLY IMPOSSIBLE TO DECODE**", 1060, 360, cls="cap-s", at=w("ACT5_04", "nearly") - 0.1, anchor="l"),
    *[RECT(330 + i * 130, 690 - hh, 100, hh, at=dbt_t + 0.12 * i, anim="grow", ad=0.35, style={"background": "rgba(229,72,77,.85)", "borderRadius": "4px 4px 0 0"})
      for i, hh in enumerate([40, 70, 110, 160])],
    T("DEBT", 1060, 560, cls="label-l", at=dbt_t, anchor="l"),
    T("~~GROWING ▲~~", 1060, 635, cls="cap-s", at=dbt_t + 0.1, anchor="l"),
    RECT(380, 850 - 150, 110, 150, at=cash_t, anim="grow", ad=0.4, style={"background": "var(--gold)", "borderRadius": "4px 4px 0 0"}),
    RECT(540, 850 - 45, 110, 45, at=cash_t + 0.3, anim="grow", ad=0.4, style={"background": "var(--cyan)", "borderRadius": "4px 4px 0 0"}),
    T("PROFIT", 435, 878, cls="mono gold", at=cash_t, style={"fontSize": "24px"}), T("CASH", 595, 878, cls="mono cyan", at=cash_t + 0.3, style={"fontSize": "24px"}),
    T("CASH ≠ PROFITS", 1060, 840, cls="cap-s cyan", at=cash_t + 0.2, anchor="l"),
    illus(s),
])
s = L("ACT5_05")
shot(s, dict(kind="dark", grid=True), person("skilling_mug", "JEFFREY SKILLING", "CEO FOR SIX MONTHS", s + 0.1, side="left", note="2004 BOOKING PHOTO",
                                            extra=[STAMP("RESIGNS", 1300, 760, at=w("ACT5_05", "resigned"), color="red", rot=-5, cls="cap-l"),
                                                   T("\"PERSONAL REASONS\"", 1300, 878, cls="label-l", at=w("ACT5_05", "personal"))]))
datestamp("AUGUST 14, 2001", s + 0.1, E("ACT5_05"))
sfx(w("ACT5_05", "resigned"), "stamp", -4)
s = L("ACT5_06")
shot(s, dict(kind="dark", grid=True), person("ken_lay", "KENNETH LAY", "BACK AS CEO", s + 0.1, side="right", note="2004 BOOKING PHOTO",
                                            extra=[T("\"NO CHANGE IN\nPERFORMANCE\nOR OUTLOOK\"", 170, 760, cls="cap-s gold", at=w("ACT5_06", "assured") + 0.3, anchor="l")]))
s = L("ACT5_07")
shot(s, dict(kind="dark", grid=True), person("watkins", "SHERRON WATKINS", "ENRON VICE PRESIDENT", s + 0.1, side="left", note="PHOTO: LATER YEARS",
                                            extra=[T("AN **ANONYMOUS LETTER**\nTO KENNETH LAY", 860, 760, cls="cap-s", at=w("ACT5_07", "anonymous") - 0.2, anchor="l")])
     if have("watkins") else [LBL("ENRON VICE PRESIDENT", 960, 400, at=s, cls="label-l"),
                              T("SHERRON WATKINS", 960, 520, cls="cap-xl shadow", at=s + 0.2),
                              T("AN **ANONYMOUS LETTER** TO KENNETH LAY", 960, 700, cls="cap-s", at=w("ACT5_07", "anonymous") - 0.2)])
datestamp("AUGUST 15, 2001", s + 0.1, E("ACT5_07"))
s = L("ACT5_08")
shot(s, PAPER, [
    *[RECT(360, 260 + i * 46, 1200 - (i * 97) % 300, 12, at=s, anim="fade", ad=0.6, style={"background": "rgba(40,34,26,0.16)", "borderRadius": "6px"}) for i in range(13)],
], cam=[[0, 1.0, 0, 0], [2, 1.05, 0, 0]])
s = L("ACT5_09")
shot(s - 0.3, PAPER, [
    T("“I am incredibly nervous that we will\nimplode in a wave of accounting scandals.”", 960, 500, cls="typew dark", at=s, anim="type", ad=4.0, align="left",
      style={"fontSize": "60px", "lineHeight": "1.45", "fontWeight": "700"}),
    LBL("— SHERRON WATKINS, LETTER TO KENNETH LAY, AUGUST 2001", 960, 760, at=E("ACT5_09") - 0.4, cls="label", style={"color": "#6f6553"}),
], cam=[[0, 1.0, 0, 0], [5.5, 1.08, 0, 0]])
sfx(s, "typelong", -6)
s = L("ACT5_10")
shot(s, DARK, [
    stock([[0, "2000-07-01", "2001-09-10", 0, 100]], [[0, "2000-07-01"], [2.4, "2001-08-31"]], ystep=20, red_from="2000-08-24",
          ann=[dict(d="2001-08-31", t0=2.5, label="$34.99\nAUG 31, 2001", dy=-110, dx=-40, color="#e5484d", fs=34)]),
], rel=True)
s = L("ACT5_11")
crack = "M 980 0 L 940 160 L 1010 290 L 930 430 L 990 560 L 900 720 L 960 860 L 910 1080"
crack2 = "M 940 430 L 760 470 L 640 440 L 470 520"
shot(s, PH("enron_complex", [1.2, 0, 0, 1.24, 0, 0], dim=0.35, bw=True), [
    PATH(crack, at=s + 0.2, stroke="#f2efe8", sw=5, ad=0.35, fixed=True),
    PATH(crack2, at=s + 0.45, stroke="#f2efe8", sw=4, ad=0.3, fixed=True),
], shake=[[0.2, 0.7, 10]], tout="dip", toutD=0.45)
caption("THE CRACKS WERE NO LONGER **INVISIBLE**", s + 0.5, E("ACT5_11") + 0.4, pos=(960, 900), cls="cap-m shadow")
sfx(s + 0.2, "crack", -2)

# =====================================================================================
# ACT 6 — THE COLLAPSE
# =====================================================================================
chapter(6, "THE COLLAPSE", E("ACT5_11") + 0.25)
HUD = []


def hud(price, at, until, color="#e5484d"):
    HUD.append(T(f"ENE <span style='color:{color}'>${price:.2f}</span>", 1820, 104, cls="mono shadow", at=at, until=until, anim="flicker", anchor="r",
                 style={"fontSize": "40px"}))
    sfx(at, "beep", -16)


s = L("ACT6_01")
shot(L("ACT6_01") - 0.1, BLACK, [
    T("OCT 16", 960, 470, cls="cap-xxl", at=s, anim="zoom", ad=0.35),
    T("2001", 960, 640, cls="label-l red", at=s + 0.3),
])
sfx(s, "tick", -6)
hud(33.84, s + 0.4, w("ACT6_04", "october") - 0.1)
s = L("ACT6_02")
shot(s, dict(kind="dark"), [
    doc("earnings_1016", [[0, 980, 560, 1.2, -1], [2.5, 1040, 560, 1.45, -1], [3.2, 900, 1320, 1.25, -1], [8, 900, 1330, 1.5, -1]],
        hl=[(0, 0.6, "red"), (1, w("ACT6_02", "six") - s, "red"), (2, w("ACT6_02", "six") - s + 0.6, "red")], base_w=1000),
    LBL("ENRON PRESS RELEASE · OCT. 16, 2001", 100, 175, at=s, cls="label ink boxed", anchor="l", fixed=True),
])
sfx(s, "paper", -8)
s = L("ACT6_03")
shot(s, EMBER, [
    LBL("SHAREHOLDERS' EQUITY", 960, 360, at=s + 0.2, cls="label-l"),
    CNT(1.2, 960, 500, at=w("ACT6_03", "one") - 0.1, frm=0, fmt={"pre": "–$", "suf": " BILLION", "dec": 1}, cls="cap-xl red shadow", dur=1.0),
    T("TIED TO LJM — RUN BY ENRON'S OWN CFO", 960, 680, cls="cap-s", at=w("ACT6_03", "partnerships") - 0.2),
])
sfx(w("ACT6_03", "one"), "hit", -4)
s = L("ACT6_04")
shot(s, BLACK, [T("OCT 22", 960, 470, cls="cap-xxl", at=s, anim="zoom", ad=0.3), T("2001", 960, 640, cls="label-l red", at=s + 0.2)])
sfx(s, "tick", -6)
s2 = w("ACT6_04", "the")
shot(s2, DARK, [
    T("THE SEC STARTS **ASKING QUESTIONS**", 960, 200, cls="cap-m shadow", at=s2),
    stock([[0, "2001-10-10", "2001-10-26", 10, 40]], [[0, "2001-10-10"], [w("ACT6_04", "falls") - s2 + 0.8, "2001-10-22"]], red_from="2001-10-15", ystep=5, y=250, h=680),
], rel=True)
hud(20.65, w("ACT6_04", "falls"), L("ACT6_05") + 0.5)
s = L("ACT6_05")
shot(s, BLACK, [
    T("OCT 24", 960, 400, cls="cap-xxl", at=s, anim="zoom", ad=0.3),
    STAMP("FASTOW OUT AS CFO", 960, 650, at=w("ACT6_05", "fastow"), color="red", rot=-4, cls="cap-m"),
])
sfx(s, "tick", -6)
sfx(w("ACT6_05", "fastow"), "stamp", -4)
hud(16.41, s + 0.3, L("ACT6_06") + 0.6)
s = L("ACT6_06")
shot(s, BLACK, [T("NOV 8", 960, 420, cls="cap-xxl", at=s, anim="zoom", ad=0.3), T("ENRON RESTATES ITS RESULTS BACK TO **1997**", 960, 640, cls="cap-s", at=w("ACT6_06", "restates"))])
sfx(s, "tick", -6)
hud(8.41, s + 0.3, L("ACT6_09") + 0.6)
s = w("ACT6_06", "hundreds") - 0.3
inc = [("1997", 105, 28), ("1998", 703, 133), ("1999", 893, 248), ("2000", 979, 99)]
dbt = [("1997", 711), ("1998", 561), ("1999", 685), ("2000", 628)]
dh = w("ACT6_06", "hundreds", 2)
els = [LBL("NET INCOME: REPORTED vs. RESTATED ($M)", 490, 150, at=s, cls="label"),
       RECT(140, 842, 700, 3, at=s, anim="wipe", style={"background": "rgba(255,255,255,.4)"})]
for i, (yr, rep, cut) in enumerate(inc):
    x = 170 + i * 170
    hrep = rep * 0.62
    els += [RECT(x, 842 - hrep, 120, hrep, at=s + 0.1 * i, anim="grow", ad=0.4, style={"background": "var(--gold)", "borderRadius": "4px 4px 0 0"}),
            RECT(x, 842 - hrep, 120, cut * 0.62, at=s + 0.9 + 0.12 * i, anim="wipeD", ad=0.4, style={"background": "repeating-linear-gradient(45deg, rgba(229,72,77,.95) 0 10px, rgba(120,20,20,.95) 10px 20px)"}),
            T(f"–{cut}", x + 60, 842 - hrep - 34, cls="mono red", at=s + 1.0 + 0.12 * i, style={"fontSize": "28px"}),
            T(yr, x + 60, 880, cls="mono muted", at=s, anim="fade", style={"fontSize": "26px"})]
els += [LBL("DEBT ADDED BACK ($M)", 1430, 150, at=dh - 0.3, cls="label"),
        RECT(1080, 842, 700, 3, at=dh - 0.3, anim="wipe", style={"background": "rgba(255,255,255,.4)"})]
for i, (yr, d) in enumerate(dbt):
    x = 1110 + i * 170
    hh = d * 0.62
    els += [RECT(x, 842 - hh, 120, hh, at=dh + 0.12 * i, anim="grow", ad=0.4, style={"background": "rgba(229,72,77,.85)", "borderRadius": "4px 4px 0 0"}),
            T(f"+{d}", x + 60, 842 - hh - 34, cls="mono red", at=dh + 0.2 + 0.12 * i, style={"fontSize": "28px"}),
            T(yr, x + 60, 880, cls="mono muted", at=dh - 0.3, anim="fade", style={"fontSize": "26px"})]
els.append(LBL("SOURCE: POWERS REPORT (2002), RESTATEMENT FOR CHEWCO AND LJM1", 960, 88, at=s + 0.5, cls="label", style={"fontSize": "19px"}))
shot(s, DARK, els)
s = L("ACT6_07")
shot(s, PH("rbc_floor", "in", dim=0.35, bw=True), [])
s = L("ACT6_08")
shot(s, BLACK, [T("TRUST", 960, 540, cls="cap-xxl", at=s, anim="none", t1=s + 0.7, aout="blur", aod=0.7)])
s = L("ACT6_09")
shot(s, BLACK, [T("NOV 9", 960, 420, cls="cap-xxl", at=s, anim="zoom", ad=0.3), T("A LIFELINE", 960, 640, cls="label-l gold", at=w("ACT6_09", "lifeline"))])
sfx(s, "tick", -6)
hud(8.63, s + 0.3, L("ACT6_11") + 0.6, color="#e8b04b")
s2 = w("ACT6_09", "dynegy")
shot(s2, PH("houston_chase_night", "right", dim=0.3, fallback="houston_pano_night"), [])
s = L("ACT6_10")
shot(s, DARK, [
    stock([[0, "2001-11-01", "2001-11-28", 0, 14]], [[0, "2001-11-09"], [2.0, "2001-11-27"]], red_from="2001-11-12", ystep=2, y=160, h=740),
    T("THE MORE DYNEGY LOOKED, **THE WORSE IT GOT**", 960, 110, cls="cap-s shadow", at=s + 0.1),
], rel=True)
s = L("ACT6_11")
shot(s, BLACK, [
    T("NOV 28", 960, 300, cls="cap-xxl", at=s, anim="zoom", ad=0.3),
    STAMP("CREDIT RATING: JUNK", 960, 560, at=w("ACT6_11", "junk") - 0.2, color="red", rot=-3, cls="cap-m"),
    STAMP("DYNEGY WALKS AWAY", 960, 760, at=w("ACT6_11", "walks") - 0.1, color="red", rot=3, cls="cap-m"),
])
sfx(s, "tick", -6)
sfx(w("ACT6_11", "junk") - 0.2, "stamp", -4)
sfx(w("ACT6_11", "walks") - 0.1, "stamp", -4)
s = L("ACT6_12")
shot(s, EMBER, [
    stock([[0, "2001-11-01", "2001-11-30", 0, 14]], [[0, "2001-11-01"], [1.4, "2001-11-28"]], red_from="2001-11-01", ystep=2, y=160, h=740, head=False,
          ann=[dict(d="2001-11-28", t0=1.5, label="$0.61", dy=-150, dx=-60, color="#e5484d", fs=110)]),
], rel=True)
hud(0.61, s + 1.5, L("ACT6_15") - 0.1)
sfx(s + 1.5, "hit", -1)
s = L("ACT6_13")
shot(s, PH("enron_complex", [1.1, 0, 0, 1.2, 0, 0], dim=0.55, bw=True), [
    T("DEC 2, 2001", 960, 300, cls="cap-l shadow", at=s, anim="zoom", ad=0.3, fixed=True),
    STAMP("CHAPTER 11", 960, 560, at=w("ACT6_13", "chapter"), color="red", rot=-5, cls="cap-xl", fixed=True),
    T("BANKRUPTCY PROTECTION", 960, 760, cls="label-l shadow", at=w("ACT6_13", "bankruptcy"), fixed=True),
], shake=[[w("ACT6_13", "chapter") - s, w("ACT6_13", "chapter") - s + 0.5, 14]])
sfx(w("ACT6_13", "chapter"), "stamp", 0)
sfx(w("ACT6_13", "chapter"), "hit", -3)
s = L("ACT6_14")
shot(s, EMBER, [
    LBL("ASSETS", 960, 330, at=s, cls="label-l"),
    CNT(63.4, 960, 470, at=s + 0.1, fmt={"pre": "$", "suf": " BILLION", "dec": 1}, cls="cap-xl shadow", dur=1.6),
    T("THE LARGEST BANKRUPTCY IN U.S. HISTORY", 960, 650, cls="cap-m red shadow", at=w("ACT6_14", "largest") - 0.1),
    LBL("AT THE TIME", 960, 740, at=w("ACT6_14", "time") - 0.3, cls="label-l"),
])
s = L("ACT6_15")
shot(s, DARK, [
    stock([[0, "2000-07-01", "2001-12-10", 0, 100]], [[0, "2000-08-23"], [3.4, "2001-11-30"]], red_from="2000-08-24", ystep=20,
          ann=[dict(d="2000-08-23", p=90.75, t0=0.1, label="$90.75", dy=-60, color="#e8b04b", fs=44),
               dict(d="2001-11-30", t0=3.5, label="$0.26", dy=-160, dx=-40, color="#e5484d", fs=60)]),
    T("**15 MONTHS**", 1180, 260, cls="cap-l shadow", at=w("ACT6_15", "fifteen") - 0.1),
], rel=True, tout="dip", toutD=0.7)

# =====================================================================================
# ACT 7 — HUMAN CONSEQUENCES
# =====================================================================================
HUD.insert(0, RECT(1020, 0, 900, 260, at=L("ACT6_01") + 0.3, style={"background": "radial-gradient(ellipse at 100% 0%, rgba(0,0,0,0.7) 0%, rgba(0,0,0,0) 70%)"}, anim="fade"))
over(L("ACT6_01") + 0.3, E("ACT6_15") + 0.3, HUD, layer=4)
s = L("ACT7_01")
chapter(7, "THE HUMAN COST", E("ACT6_15") + 0.25)
shot(L("ACT7_01") - 0.1, PH("enron_complex", "out", dim=0.35, bw=True), [])
caption("ENRON WAS NEVER JUST **A STOCK CHART**", s + 0.2, E("ACT7_01") + 0.5, pos="center", cls="cap-m shadow")
s = L("ACT7_02")
shot(s, PH("houston_sunrise_2002", [1.04, 0, 0, 1.14, -30, 0], dim=0.25), [], tin="fade", tinD=0.6)
over(w("ACT7_02", "about") - 0.1, E("ACT7_02") + 0.6, [
    LBL("WITHIN DAYS OF THE FILING", 960, 380, at=s + 0.4, cls="label-l shadow"),
    CNT(4000, 960, 520, at=w("ACT7_02", "about") - 0.1, fmt={"pre": "~", "comma": True}, cls="cap-xl shadow", dur=1.6),
    LBL("EMPLOYEES LAID OFF IN HOUSTON", 960, 660, at=w("ACT7_02", "employees"), cls="label-l shadow"),
])
s = L("ACT7_03")
sixty = w("ACT7_03", "sixty")
circ = 2 * 3.14159 * 230
shot(s, NAVY, [
    LBL("ENRON 401(k) PLAN ASSETS", 960, 140, at=s, cls="label-l"),
    dict(kind="html", html=f'<svg width="620" height="620" viewBox="-310 -310 620 620"><circle r="230" fill="none" stroke="rgba(255,255,255,.12)" stroke-width="90"/></svg>', x=960, y=560, at=s + 0.2, anim="fade"),
    dict(kind="html", html=f'<svg width="620" height="620" viewBox="-310 -310 620 620" style="transform:rotate(-90deg)"><circle r="230" fill="none" stroke="#e5484d" stroke-width="90" stroke-dasharray="{circ * 0.6:.1f} {circ:.1f}"/></svg>',
         x=960, y=560, at=sixty - 0.2, anim="wipe", ad=0.9),
    T("~60%", 960, 535, cls="cap-xl red", at=sixty, anim="pop"),
    T("ENRON STOCK", 960, 640, cls="label-l", at=sixty + 0.2),
    LBL("APPROXIMATE SHARE AT THE END OF 2000", 960, 878, at=sixty + 0.3, cls="label", style={"fontSize": "20px"}),
])
s = L("ACT7_04")
shot(s, DARK, [
    LBL("EMPLOYEES LOST MORE THAN", 960, 380, at=s, cls="label-l"),
    CNT(1, 960, 520, at=w("ACT7_04", "billion") - 0.5, fmt={"pre": "$", "suf": " BILLION"}, cls="cap-xl red shadow", dur=0.6),
    LBL("IN RETIREMENT SAVINGS", 960, 660, at=w("ACT7_04", "retirement"), cls="label-l"),
], cam=[[0, 1.0, 0, 0], [4, 1.05, 0, 0]])
s = L("ACT7_05")
shot(s, DARK, [
    LBL("ENRON MARKET VALUE", 960, 170, at=s, cls="label-l"),
    RECT(460, 860, 1000, 3, at=s, anim="wipe", style={"background": "rgba(255,255,255,.4)"}),
    RECT(560, 860 - 580, 280, 580, at=s + 0.2, anim="grow", ad=0.6, style={"background": "var(--gold)", "borderRadius": "6px 6px 0 0"}),
    T("~$70B", 700, 240, cls="cap-m gold", at=s + 0.6),
    T("AUG 2000", 700, 890, cls="mono muted", at=s + 0.2, anim="fade", style={"fontSize": "28px"}),
    RECT(1080, 856, 280, 4, at=w("ACT7_05", "tens"), anim="grow", style={"background": "var(--red)"}),
    T("< $1B", 1220, 800, cls="cap-m red", at=w("ACT7_05", "tens") + 0.2),
    T("NOV 30, 2001", 1220, 890, cls="mono muted", at=w("ACT7_05", "tens"), anim="fade", style={"fontSize": "28px"}),
])
s = L("ACT7_06")
shot(s, dict(kind="dark", grid=True, credit=credit("andersen_witnesses")), [
    IMG("andersen_witnesses", 960 - 420, 120, 840, 562, at=s, anim="fade", ad=0.5, kb=[1.0, 1.06],
        style={"border": "1px solid rgba(255,255,255,.25)", "boxShadow": "0 30px 90px rgba(0,0,0,.7)"}),
    T("ARTHUR **ANDERSEN**", 960, 772, cls="cap-m shadow", at=s + 0.3, anim="up"),
    LBL("ENRON'S AUDITOR · HOUSE HEARING, JAN. 2002", 960, 850, at=s + 0.6, cls="label"),
], cam=[[0, 1.0, 0, 0], [6, 1.04, 0, 0]])
s = L("ACT7_07")
shot(s, DARK, [
    IMG("hearing_doc_shredding", 960 - 300, 80, 600, 776, at=s, anim="up", kb=[1.0, 1.05], style={"boxShadow": "0 40px 120px rgba(0,0,0,.75)"}),
    LBL("U.S. HOUSE HEARING RECORD, 2002", 960, 892, at=s + 0.3, cls="label", style={"fontSize": "20px"}),
], cam=[[0, 1.0, 0, 0], [4, 1.08, 0, -30]])
sfx(s + 0.1, "paper", -8)
s2 = w("ACT7_07", "in")
shot(s2, PH("shredder_detail", "push", dim=0.3, bw=True), [])
sfx(s2, "shred", -12)
datestamp("JUNE 2002", s2 + 0.1, E("ACT7_07") + 0.3, sub="ILLUSTRATIVE PHOTO")
s = L("ACT7_08")
shot(s, PH("supreme_court", "in", dim=0.3, bw=True), [])
caption("2005: SUPREME COURT **OVERTURNS** THE CONVICTION", s + 0.2, w("ACT7_08", "by") - 0.05)
caption("TOO LATE", w("ACT7_08", "by") + 0.3, E("ACT7_08") + 0.4, pos="center", cls="cap-xl shadow")
s = L("ACT7_09")
cards = [("ANDREW FASTOW", "PLEADED GUILTY · SENTENCED TO 6 YEARS", w("ACT7_09", "fastow")),
         ("JEFFREY SKILLING", "CONVICTED (2006) · 24 YEARS, LATER REDUCED", w("ACT7_09", "skilling")),
         ("KENNETH LAY", "CONVICTED (2006) · DIED BEFORE SENTENCING;\nCONVICTION VACATED", w("ACT7_09", "lay"))]
els = []
for i, (nm, verdict, tt) in enumerate(cards):
    y = 200 + i * 235
    els += [BOX(260, y, 1400, 200, at=tt - 0.1, anim="left", html=f'<div class="cap-m" style="text-align:left">{nm}</div><div class="label gold" style="margin-top:10px;text-align:left;line-height:1.5">{verdict.replace(chr(10), "<br>")}</div>',
                style={"background": "rgba(5,7,9,.86)", "borderLeft": "6px solid var(--gold)", "flexDirection": "column", "alignItems": "flex-start", "padding": "0 40px", "textTransform": "none"})]
shot(s, PH("casey_courthouse", "in", dim=0.55, bw=True, fallback="supreme_court"), els, tout="dip", toutD=0.6)

# =====================================================================================
# ACT 8 — THE FINAL REVEAL
# =====================================================================================
s = L("ACT8_01")
chapter(8, "THE GAP", E("ACT7_09") + 0.25)
shot(s - 0.1, BLACK, [
    IMG("enron_logo", 960 - 200, 540 - 200, 400, 400, at=s - 0.1, anim="fade", ad=1.2, style={"opacity": "0.22", "filter": "grayscale(1)"}),
    T("SO WHAT REALLY\n**DESTROYED ENRON?**", 960, 540, cls="cap-l shadow", at=s + 0.1, anim="blur", align="center"),
], cam=[[0, 1.0, 0, 0], [3, 1.06, 0, 0]])
s = L("ACT8_02")
items = [("ONE ACCOUNTING METHOD", w("ACT8_02", "accounting") - 0.3), ("ONE PARTNERSHIP", w("ACT8_02", "partnership") - 0.3), ("ONE EXECUTIVE", w("ACT8_02", "executive") - 0.3)]
els = []
for i, (txt, tt) in enumerate(items):
    y = 340 + i * 170
    els += [T(f"NOT {txt}", 960, y, cls="cap-m shadow", at=tt), RECT(560, y - 4, 800, 8, at=tt + 0.35, anim="wipe", ad=0.3, style={"background": "var(--red)"})]
shot(s, DARK, els)
s = L("ACT8_03")
parts = [("AGGRESSIVE\nESTIMATES", "aggressive", 420, 300), ("HIDDEN DEBT\n& LOSSES", "debt", 1500, 300), ("A CFO ON\nBOTH SIDES", "c.f", 360, 700),
         ("DISCLOSURES\nNO ONE COULD READ", "disclosures", 1560, 700), ("PRESSURE TO\nKEEP CLIMBING", "relentless", 960, 790)]
els = [T("A COMBINATION", 960, 120, cls="label-l gold", at=s + 0.1)]
for txt, word, x, y in parts:
    tt = w("ACT8_03", word) - 0.15
    els += [PATH(f"M {x} {y} L 960 490", at=tt + 0.2, stroke="rgba(229,72,77,.6)", sw=3, ad=0.5),
            BOX(x - 220, y - 85, 440, 170, at=tt, text=txt.replace("\n", "<br>"), anim="pop",
                style={"background": "#0d1218", "border": "3px solid var(--gold)", "fontSize": "38px", "lineHeight": "1.15"})]
els.append(BOX(780, 410, 360, 160, at=w("ACT8_03", "relentless") + 1.2, text="COLLAPSE", anim="pop",
               style={"background": "rgba(229,72,77,.25)", "border": "4px solid var(--red)", "color": "var(--red)", "fontSize": "56px"}))
mid3 = w("ACT8_03", "c.f") - s
shot(s, DARK, els, cam=[[0, 1.1, 0, 60], [mid3, 1.03, -40, 20], [E("ACT8_03") - s, 0.97, 0, 0]])
for word in ["aggressive", "debt", "disclosures", "relentless"]:
    sfx(w("ACT8_03", word) - 0.15, "pop", -12)
sfx(w("ACT8_03", "c.f") - 0.15, "pop", -12)
sfx(w("ACT8_03", "relentless") + 1.2, "hit", -8)
s = L("ACT8_04")
gap = w("ACT8_04", "gap")
shot(s, DARK, [
    RECT(240, 870, 1460, 3, at=s, style={"background": "rgba(255,255,255,.4)"}, anim="wipe"),
    PATH("M 260 860 C 700 760, 1100 520, 1660 220", at=s + 0.2, stroke="#e8b04b", sw=7, ad=1.8),
    T("REPORTED PROFIT", 1640, 190, cls="label-l gold", at=s + 1.6, anchor="r"),
    PATH("M 260 860 C 700 850, 1100 800, 1660 720", at=s + 0.8, stroke="#59c9d3", sw=7, ad=1.8),
    T("CASH", 1640, 670, cls="label-l cyan", at=s + 2.2, anchor="r"),
    PATH("M 1500 330 L 1500 700", at=gap, stroke="#e5484d", sw=5, arrow=True, ad=0.4),
    PATH("M 1500 700 L 1500 330", at=gap, stroke="#e5484d", sw=5, arrow=True, ad=0.4),
    T("~~THE GAP~~", 1440, 520, cls="cap-m", at=gap + 0.2, anim="pop", anchor="r"),
    illus(s),
])
s = L("ACT8_05")
shot(s, BLACK, [
    T("LOOKING PROFITABLE", 960, 380, cls="cap-l gold", at=s + 0.1),
    T("≠", 960, 530, cls="cap-xl red", at=w("ACT8_05", "and") - 0.1, anim="pop"),
    T("GENERATING CASH", 960, 690, cls="cap-l cyan", at=w("ACT8_05", "generating")),
])
s = L("ACT8_06")
shot(s, PH("wallst_2000", [1.08, 0, 0, 1.2, 0, 0], dim=0.4, bw=True), [], tin="fade", tinD=0.5)
s = L("ACT8_07")
shot(s, DARK, [
    stock([[0, "1998-01-01", "2001-12-31", 0, 100]], [[0, "1998-01-02"], [w("ACT8_07", "collided") - s, "2001-11-30"]], red_from="2000-08-24", ystep=20, revealEase="inOut"),
], rel=True)
sfx(w("ACT8_07", "reality") - 0.1, "hit", -6)
s = L("ACT8_08")
shot(s, BLACK, [
    IMG("enron_logo", 960 - 230, 540 - 230, 460, 460, at=s, anim="none", style={"filter": "grayscale(1)"}, t1=E("ACT8_08") - 0.4, aout="fade", aod=1.6,
        kf=[[0, 0, 0, 1.0, 0.8], [E("ACT8_08") - s, 0, 0, 0.9, 0.5]]),
])
s = L("ACT8_09")
shot(s - 0.15, BLACK, [T("IT HAD WEEKS.", 960, 540, cls="cap-m", at=s, anim="fade", ad=0.4, t1=E("ACT8_09") + 1.25, aod=0.6)])
sfx(s, "hit", -6)

# ---- end card: 18 s of room for YouTube end-screen elements (right two thirds stay clear)
OC = E("ACT8_09") + 2.3
shot(OC, NAVY, [
    stock([[0, "1998-01-01", "2001-12-31", 0, 100]], [[0, "1998-01-02"], [9.0, "2001-11-30"]], red_from="2000-08-24", ystep=20, head=False,
          x=700, y=140, w_=1180, h=700, revealEase="inOut", style={"opacity": "0.22"}),
    IMG("enron_logo", 140, 250, 120, 120, at=OC + 0.3, anim="fade", ad=0.8),
    T("ENRON", 140, 455, cls="cap-l", at=OC + 0.5, anim="left", anchor="l", style={"letterSpacing": "0.08em"}),
    T("**PROFIT ON PAPER**", 140, 560, cls="cap-m", at=OC + 0.8, anim="left", anchor="l"),
    RECT(140, 625, 140, 4, at=OC + 1.1, style={"background": "var(--gold)"}, anim="wipe"),
    T("SOURCES, PHOTO CREDITS\nAND MUSIC IN THE DESCRIPTION", 140, 700, cls="label", at=OC + 1.4, anim="fade", anchor="l", style={"lineHeight": "1.6"}),
], tin="fade", tinD=0.8, tout="dip", toutD=1.2)
