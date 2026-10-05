"""Burned-in subtitles: word-timed, readable chunks built from the narration.

Each narration line is aligned word by word with the voice track, spoken numbers
are rewritten as figures ("six hundred and eighteen million dollars" -> "$618 million"),
and the words are grouped into short chunks that the renderer animates one word at
a time. The same timed words feed the SRT file, so both always agree with the voice.
"""
import re

import tts

# Lines whose words are already the main on-screen typography: no subtitle on top.
SUPPRESS = {
    "ACT1_01",  # Houston, Texas. 1985. (label + datestamp)
    "ACT1_09",  # Enron looked like the future.
    "ACT1_11",  # How much of that profit had actually arrived as cash?
    "ACT2_06",  # Year one. One giant number.
    "ACT2_08",  # The danger is in one word. Estimate.
    "ACT4_14",  # On paper, Enron looked stronger than ever.
    "ACT5_03",  # How exactly does Enron make its money?
    "ACT5_09",  # Watkins quote, typed on screen
    "ACT5_11",  # The cracks were no longer invisible.
    "ACT6_01",  # October 16, 2001.
    "ACT7_01",  # But Enron was never just a stock chart.
    "ACT8_01",  # So what really destroyed Enron?
    "ACT8_02",  # Not one accounting method...
    "ACT8_09",  # It had weeks.
}

UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                       "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
ORD_UNITS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9,
             "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
             "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20, "thirtieth": 30}
MONTHS = {m.lower() for m in "January February March April May June July August September October November December".split()}
ABBR = {"cfo": "CFO", "ceo": "CEO", "sec": "SEC"}

RED = {"gone", "collapsed", "collapse", "bankruptcy", "junk", "loss", "losses", "debt", "fraud", "convicted", "shredded",
       "implode", "scandals", "struggling", "risk", "lost", "erased", "vanishing", "guilty", "obstruction", "nervous", "cracks",
       "laid", "off", "walks", "conflict", "hiding", "hidden"}
CYAN = {"cash"}
GOLD = {"profit", "profits", "profitable", "revenue", "earnings", "spectacular", "mark-to-market", "innovative", "estimate",
        "assumptions", "optimistic", "options", "admired", "future"}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def spoken_form(word, prev):
    """What the voice actually says for one script word (years and Chapter 11 are expanded before TTS)."""
    core = word
    for y, words in tts.YEARS.items():
        core = re.sub(rf"\b{y}\b", words, core)
    if prev and norm(prev) == "chapter" and norm(word) == "11":
        core = "Eleven"
    return norm(core)


def align(line):
    """Pair every script word of a line with its (start, end) in the voice track."""
    script = line["text"].split()
    toks = [(norm(w["w"]), w["s"], w["e"]) for w in line["words"]]
    toks = [t for t in toks if t[0]]
    out, j = [], 0
    for i, word in enumerate(script):
        target = spoken_form(word, script[i - 1] if i else None)
        if not target:          # pure punctuation such as "—"
            if out:
                out[-1]["raw"] += " " + word
            continue
        acc, s0, e0 = "", None, None
        while j < len(toks) and len(acc) < len(target):
            acc += toks[j][0]
            s0 = toks[j][1] if s0 is None else s0
            e0 = toks[j][2]
            j += 1
        if acc != target:
            raise ValueError(f"{line['id']}: cannot align {word!r} (voice gave {acc!r})")
        out.append({"raw": word, "s": s0, "e": e0})
    if j != len(toks):
        raise ValueError(f"{line['id']}: {len(toks) - j} voice tokens left over")
    return out


def split_punct(raw):
    m = re.match(r"^([\"“(]*)(.*?)([\"”).,:;?!…]*)$", raw)
    return m.group(1), m.group(2), m.group(3)


def card_value(word):
    """Value of one cardinal number word ('fifty-six' -> 56); None if not a number word."""
    w = word.lower()
    if w in UNITS:
        return UNITS[w]
    if w in TENS:
        return TENS[w]
    if "-" in w:
        a, b = w.split("-", 1)
        if a in TENS and b in UNITS and UNITS[b] < 10:
            return TENS[a] + UNITS[b]
    return None


def ord_value(word):
    w = word.lower()
    if w in ORD_UNITS:
        return ORD_UNITS[w]
    if "-" in w:
        a, b = w.split("-", 1)
        if a in TENS and b in ORD_UNITS and ORD_UNITS[b] < 10:
            return TENS[a] + ORD_UNITS[b]
    return None


def parse_number(words, i):
    """Parse a spoken number starting at words[i]. Returns (value, n_words, has_decimal) or None."""
    total, cur, seen, dec = 0, 0, False, None
    k = i
    while k < len(words):
        core = split_punct(words[k]["raw"])[1].lower()
        nxt_core = split_punct(words[k + 1]["raw"])[1].lower() if k + 1 < len(words) else ""
        v = card_value(core)
        if v is not None:
            if dec is not None:
                if v > 9:
                    break
                dec += str(v)
            else:
                cur += v
            seen = True
        elif core == "hundred" and seen and dec is None:
            cur *= 100
        elif core == "thousand" and seen and dec is None:
            total += cur * 1000
            cur = 0
        elif core == "and" and seen and dec is None and cur >= 100 and card_value(nxt_core) is not None:
            pass
        elif core == "point" and seen and dec is None and card_value(nxt_core) is not None and card_value(nxt_core) < 10:
            dec = ""
        else:
            break
        k += 1
        # a word carrying punctuation (comma, full stop) ends the number
        if split_punct(words[k - 1]["raw"])[2]:
            break
    if not seen:
        return None
    value = total + cur
    if dec:
        return float(f"{value}.{dec}"), k - i, True
    return value, k - i, False


def fmt(v, decimal):
    if decimal:
        return f"{v}"
    return f"{v:,}"


def display_words(line):
    """Script words of a line with timings, rewritten for reading (figures, abbreviations)."""
    words = align(line)
    out = []
    i = 0
    while i < len(words):
        wd = words[i]
        lead, core, trail = split_punct(wd["raw"])
        low = core.lower()
        nxt = split_punct(words[i + 1]["raw"]) if i + 1 < len(words) else ("", "", "")
        # Month + ordinal day -> "October 16"
        if low in MONTHS and not trail and i + 1 < len(words) and ord_value(nxt[1]) is not None:
            out.append({"t": f"{lead}{core} {ord_value(nxt[1])}{nxt[2]}", "s": wd["s"], "e": words[i + 1]["e"]})
            i += 2
            continue
        if "." in core and norm(core) in ABBR:
            nxt_raw = words[i + 1]["raw"] if i + 1 < len(words) else ""
            end_of_sentence = (not nxt_raw) or nxt_raw[:1].isupper()
            tail = trail.replace(".", "", 1) if trail.startswith(".") else trail
            tail = ("." if end_of_sentence and not tail else tail)
            out.append({"t": f"{lead}{ABBR[norm(core)]}{tail}", "s": wd["s"], "e": wd["e"]})
            i += 1
            continue
        if low == "nineties":
            out.append({"t": f"{lead}’90s{trail}", "s": wd["s"], "e": wd["e"]})
            i += 1
            continue
        m = re.match(r"^([a-z]+)-(year)$", low)
        if m and card_value(m.group(1)) is not None:
            out.append({"t": f"{lead}{card_value(m.group(1))}-year{trail}", "s": wd["s"], "e": wd["e"]})
            i += 1
            continue
        # "a billion dollars" -> "$1 billion"
        if low == "a" and not trail and i + 2 < len(words) and split_punct(words[i + 1]["raw"])[1].lower() in ("million", "billion") \
                and split_punct(words[i + 2]["raw"])[1].lower().startswith("dollar"):
            scale = split_punct(words[i + 1]["raw"])[1].lower()
            tr = split_punct(words[i + 2]["raw"])[2]
            out.append({"t": f"{lead}$1 {scale}{tr}", "s": wd["s"], "e": words[i + 2]["e"]})
            i += 3
            continue
        p = parse_number(words, i)
        if p:
            v, n, decimal = p
            j = i + n
            last = words[j - 1]
            trail_num = split_punct(last["raw"])[2]
            after = [split_punct(words[k]["raw"]) for k in range(j, min(j + 2, len(words)))]
            a0 = after[0][1].lower() if after and not trail_num else ""
            if a0 == "percent":
                out.append({"t": f"{lead}{fmt(v, decimal)}%{after[0][2]}", "s": wd["s"], "e": words[j]["e"]})
                i = j + 1
                continue
            if a0 in ("million", "billion"):
                end_k, tr = j, after[0][2]
                if len(after) > 1 and not after[0][2] and after[1][1].lower() in ("dollars", "dollar"):
                    end_k, tr = j + 1, after[1][2]
                out.append({"t": f"{lead}${fmt(v, decimal)} {a0}{tr}", "s": wd["s"], "e": words[end_k]["e"]})
                i = end_k + 1
                continue
            if a0 in ("dollars", "dollar"):
                out.append({"t": f"{lead}${fmt(v, decimal)}{after[0][2]}", "s": wd["s"], "e": words[j]["e"]})
                i = j + 1
                continue
            if a0 == "cents":
                out.append({"t": f"{lead}{fmt(v, decimal)} cents{after[0][2]}", "s": wd["s"], "e": words[j]["e"]})
                i = j + 1
                continue
            if decimal or v >= 10:
                out.append({"t": f"{lead}{fmt(v, decimal)}{trail_num}", "s": wd["s"], "e": last["e"]})
                i = j
                continue
        out.append({"t": wd["raw"], "s": wd["s"], "e": wd["e"]})
        i += 1
    for wd in out:   # typographic quotes and apostrophes
        t = wd["t"].replace("'", "’")
        t = re.sub(r'^"', "“", t)
        wd["t"] = t.replace('"', "”")
    return out


BREAK_BEFORE = {"and", "but", "or", "to", "of", "in", "with", "for", "that", "as", "from", "on", "into", "where", "while",
                "because", "when", "after", "by", "at", "than", "until", "like"}
MAX_CHARS, MAX_WORDS = 34, 7
DANGLING = {"a", "an", "the", "to", "of", "in", "and", "but", "or", "its", "their", "his", "that", "was", "were", "is", "as",
            "at", "by", "for", "from", "on", "with", "into", "than", "it", "this", "had", "has", "have", "could", "would",
            "will", "very", "more", "most", "just", "not", "no", "an", "about", "under", "over"}


def chunk_words(words):
    """Group display words into short subtitle chunks at natural breaks."""
    sentences, cur = [], []
    for wd in words:
        cur.append(wd)
        if re.search(r"[.?!:]['\"”)]*$", wd["t"]):
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)

    def length(ws):
        return sum(len(w["t"]) for w in ws) + len(ws) - 1

    def split(ws):
        if length(ws) <= MAX_CHARS and len(ws) <= MAX_WORDS:
            return [ws]
        best, score = None, None
        for k in range(1, len(ws)):
            left, right = ws[:k], ws[k:]
            sc = 0.6 * abs(length(left) - length(right))
            if ws[k - 1]["t"].endswith((",", ";")):
                sc -= 14
            if norm(ws[k]["t"]) in BREAK_BEFORE:
                sc -= 7
            if len(left) == 1 or len(right) == 1:
                sc += 12
            if norm(left[-1]["t"]) in DANGLING:
                sc += 16
            opened = sum(w["t"].count("“") - w["t"].count("”") for w in left)
            if opened > 0:
                sc += 30
            a, b = left[-1]["t"], ws[k]["t"]
            if a[:1].isupper() and (b[:1].isupper() or b[:1].isdigit()) and not re.search(r"[.,:;?!]$", a):
                sc += 25   # keep names, dates and "Chapter 11" together
            if score is None or sc < score:
                best, score = k, sc
        return split(ws[:best]) + split(ws[best:])

    chunks = []
    for sent in sentences:
        parts = []
        # commas are natural breaks once a clause has a few words in it
        piece = []
        for wd in sent:
            piece.append(wd)
            if wd["t"].endswith(",") and length(piece) >= 12:
                parts.append(piece)
                piece = []
        if piece:
            parts.append(piece)
        for part in parts:
            chunks += split(part)
    # very short chunks (< 0.5 s on screen) merge with a neighbour when they still fit
    merged = []
    for ch in chunks:
        if merged and (ch[-1]["e"] - ch[0]["s"] < 0.5 or merged[-1][-1]["e"] - merged[-1][0]["s"] < 0.5) \
                and length(merged[-1] + ch) <= MAX_CHARS + 4 and not re.search(r"[.?!]$", merged[-1][-1]["t"]):
            merged[-1] = merged[-1] + ch
        else:
            merged.append(ch)
    return merged


def color_of(text):
    core = norm(text) if not text.lstrip("\"“(").startswith("$") else "$"
    raw = re.sub(r"[^\w$%’'-]", "", text).lower()
    if core == "$" or raw.endswith("%"):
        return "gold"
    if raw in RED:
        return "red"
    if raw in CYAN:
        return "cyan"
    if raw in GOLD:
        return "gold"
    return ""


def build(nar):
    """All subtitle chunks for the film: [{s, e, line, words: [{t, s, e, c}]}]."""
    out = []
    for act in nar["acts"]:
        for line in act["lines"]:
            words = display_words(line)
            if line["id"] in SUPPRESS:
                continue
            for ch in chunk_words(words):
                colored = 0
                ws = []
                for wd in ch:
                    c = color_of(wd["t"])
                    if c and colored >= 1:
                        c = ""
                    colored += bool(c)
                    ws.append({"t": wd["t"], "s": round(wd["s"], 3), "e": round(wd["e"], 3), "c": c})
                out.append({"line": line["id"], "s": ws[0]["s"], "e": ws[-1]["e"], "words": ws})
    # on-screen window: appear just before the first word, hold through short pauses
    for k, ch in enumerate(out):
        nxt = out[k + 1]["s"] if k + 1 < len(out) else None
        ch["s"] = round(ch["s"] - 0.06, 3)
        hold = ch["e"] + 0.45
        if nxt is not None and nxt - ch["e"] < 0.75:
            hold = nxt - 0.06
        if nxt is not None:
            hold = min(hold, nxt - 0.06)
        ch["e"] = round(max(hold, ch["words"][-1]["e"]), 3)
    return out


def srt_blocks(nar, max_line=42):
    """Caption blocks for the .srt file: whole sentences where they fit in two lines of 42 characters."""
    cap = 2 * max_line - 6

    def length(ws):
        return sum(len(w["t"]) for w in ws) + len(ws) - 1

    def split(ws):
        if length(ws) <= cap:
            return [ws]
        best, score = None, None
        for k in range(1, len(ws)):
            sc = 0.5 * abs(length(ws[:k]) - length(ws[k:]))
            if ws[k - 1]["t"].endswith((",", ";", ":")):
                sc -= 20
            if norm(ws[k]["t"]) in BREAK_BEFORE:
                sc -= 6
            if norm(ws[k - 1]["t"]) in DANGLING:
                sc += 14
            if score is None or sc < score:
                best, score = k, sc
        return split(ws[:best]) + split(ws[best:])

    blocks = []
    for act in nar["acts"]:
        for line in act["lines"]:
            sent, sents = [], []
            for wd in display_words(line):
                sent.append(wd)
                if re.search(r"[.?!:][”’)]*$", wd["t"]):
                    sents.append(sent)
                    sent = []
            if sent:
                sents.append(sent)
            pieces = [pc for s_ in sents for pc in split(s_)]
            merged = []
            for pc in pieces:   # short neighbouring sentences share a block
                if merged and length(merged[-1] + pc) <= cap and pc[-1]["e"] - merged[-1][0]["s"] <= 5.5 and length(merged[-1]) < 30:
                    merged[-1] = merged[-1] + pc
                else:
                    merged.append(pc)
            blocks += merged

    def wrap(text):
        if len(text) <= max_line:
            return text
        spaces = [i for i, ch in enumerate(text) if ch == " "]
        ok = [i for i in spaces if i <= max_line and len(text) - i - 1 <= max_line]
        pool = ok or spaces
        def cost(i):
            c = max(i, len(text) - i - 1)
            if text[i - 1] in ",.;:?":
                c -= 6
            if min(i, len(text) - i - 1) < 12:
                c += 10
            return c
        cut = min(pool, key=cost)
        return text[:cut] + "\n" + text[cut + 1:]

    out = []
    for k, b in enumerate(blocks):
        text = " ".join(x["t"] for x in b)
        s, e = b[0]["s"], b[-1]["e"]
        nxt = blocks[k + 1][0]["s"] if k + 1 < len(blocks) else e + 3
        e = max(e + 0.3, s + 1.0, s + len(text) / 20.0)   # >= 1 s and <= 20 characters per second
        e = min(e, nxt - 0.04)
        out.append((s, e, wrap(text)))
    return out
