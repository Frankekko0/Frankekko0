"""Declared defects: what the seller's own words say is wrong with the item (IT / EN / FR / DE / ES).

One negation-aware extractor shared by the identification engine (risk), the text analysis (dossier),
the AI context and the price (a declared hole lowers the condition the price follows, see
``condition_floor``). Every hit keeps the phrase it was read from, a kind (hole, stain, seam...) and a
severity, and has the provenance ``declared``: it is the seller's claim, never a fact seen on the item.

Rules:

* only fixed vocabularies match, the text is never followed or executed (it is untrusted data);
* a defect named inside a negation ("senza macchie", "no stains or holes", "pas de trous ni de taches",
  "non è rotto nulla") is not a defect, and a negation does not reach past "but"/"except"/a full stop
  ("senza difetti tranne una macchia" has a stain). A comma list continues a negation only while the
  items are bare words ("senza macchie, buchi o strappi"), a new clause does not ("no stains, hole in
  the pocket" has a hole);
* a softener ("piccolo", "leggero", "small", "slight", "petit") lowers the severity one step, an
  intensifier ("grande", "large", "evidente") raises it;
* hashtags are not read (``#FadedDenim`` is a style tag, not a flaw);
* "no visible defect" is not "no defect": absence of a hit says nothing about the item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.identification.taxonomy import fold

SEVERITIES = ("minor", "moderate", "severe")
_RANK = {s: i + 1 for i, s in enumerate(SEVERITIES)}

# Italian user-facing label per kind (the strings the risk factor and the analyst have always shown).
KIND_LABELS = {
    "hole": "buchi",
    "tear": "strappi",
    "stain": "macchie",
    "dirty": "sporco",
    "repair": "rammendi",
    "seam": "cuciture aperte",
    "snag": "fili tirati",
    "zip": "cerniera rotta",
    "button": "bottoni mancanti",
    "sole": "suola rovinata",
    "wear": "usura",
    "scratch": "graffi",
    "fading": "scolorimento",
    "pilling": "pilling",
    "yellowing": "ingiallimento",
    "odor": "odori",
    "broken": "parti rotte",
    "damage": "danni",
    "generic": "difetti dichiarati",
}
# Kinds a seller mentions that need a repair or cleaning before the item is resold at full price.
RESTORATION_KINDS = frozenset(
    {
        "hole",
        "tear",
        "stain",
        "repair",
        "seam",
        "zip",
        "button",
        "sole",
        "broken",
        "damage",
        "yellowing",
        "odor",
    }
)

_GAP = r"(?:\w+\W+){0,2}?"


def _rule(term: str, kind: str, severity: str, pattern: str) -> tuple[str, str, str, re.Pattern[str]]:
    return term, kind, severity, re.compile(pattern)


_ZIP = r"(?:cerniera|zip|lampo|chiusura lampo|zipper|fermeture eclair|fermeture|cremallera|reissverschluss)"
_ZIP_BAD = (
    r"(?:rott[aeio]|bloccat[aeio]|difettos[aeio]|non funzion\w+|funziona male|incastrat[aeio]|guast[aeio]|saltat[aeio]|"
    r"broken|stuck|jammed|damaged|faulty|defective|doesn t work|does not work|not working|"
    r"cassee?s?|bloquee?s?|defectueu\w+|rota|atascada|defekt|kaputt|klemmt)"
)
_BUTTON = r"(?:bottone|bottoni|button|buttons|bouton|boutons|boton|botones|knopf|knoepfe|knopfe)"
_BUTTON_BAD = (
    r"(?:mancant\w+|mancan\w+|manca|staccat\w+|persa|perso|missing|lost|loose|broken|"
    r"manquant\w*|falta\w*|ausente|perdid\w+|fehlt|fehlen|abgerissen)"
)
_SOLE = r"(?:suola|suole|soles?|semelles?|suela|suelas|sohlen?)"
_SOLE_WORN = r"(?:consumat\w+|usurat\w+|rovinat\w+|logor\w+|worn|usee?s?|gastad\w+|abgelaufen|abgenutzt|cracked|crepat\w+)"
_SOLE_SPLIT = (
    r"(?:staccat\w+|scollat\w+|separat\w+|detached|peeling|decollee?s?|despegad\w+|geloest|gelost|lose)"
)

# (term, kind, base severity, pattern over the folded text). The term is the word a hit is reported by.
_RULES: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    # ---- holes and tears
    _rule("buchino", "hole", "moderate", r"\bbuchin[oi]\b|\bforellin[oi]\b"),
    _rule("buco", "hole", "severe", r"\b(?:buco|buchi|bucat[aeio]|bucherellat[aeio]|foro|fori)\b"),
    _rule("hole", "hole", "severe", r"\bholes?\b"),
    _rule("trou", "hole", "severe", r"\btrous?\b"),
    _rule("agujero", "hole", "severe", r"\bagujeros?\b"),
    _rule("loch", "hole", "severe", r"\b(?:loch|locher|lochern)\b"),
    _rule("strappo", "tear", "severe", r"\b(?:strappo|strappi|stracciat[aeio])\b"),
    _rule("strappato", "tear", "moderate", r"\bstrappat[aeio]\b"),
    _rule("tear", "tear", "severe", r"\b(?:tears?|torn|rips?)\b"),
    _rule("ripped", "tear", "moderate", r"\bripped\b"),
    _rule(
        "dechirure",
        "tear",
        "severe",
        r"\b(?:dechir\w+|dechire\w*)\b|\bdesgarr\w+\b|\briss\b|\brisse\b|\bzerrissen\b",
    ),
    # ---- stains and dirt
    _rule("macchiolina", "stain", "minor", r"\bmacchiolin[ae]\b|\bmacchioline\b"),
    _rule("macchia", "stain", "moderate", r"\b(?:macchia|macchie|macchiat[aeio]|chiazz[ae])\b"),
    _rule(
        "alone",
        "stain",
        "moderate",
        r"\balone\W+(?:sotto|sul|sulla|su|vicino|nel|al|alla|dietro|intorno)\b|\baloni\b",
    ),
    _rule("stain", "stain", "moderate", r"\bstain(?:s|ed)?\b"),
    _rule("tache", "stain", "moderate", r"\btaches?\b|\btachee?s?\b"),
    _rule("mancha", "stain", "moderate", r"\bmanchas?\b|\bmanchad[oa]s?\b"),
    _rule("fleck", "stain", "moderate", r"\bfleck(?:en|e)?\b|\bfleckig\w*\b"),
    _rule(
        "sporco",
        "dirty",
        "minor",
        r"\bsporc[aeio]\b|\bda lavare\b|\bto be washed\b|\bneeds? (?:a )?wash\w*\b|\bdirty\b|\bschmutzig\b|\bsucio\b",
    ),
    # ---- repairs, seams, threads
    _rule("rammendo", "repair", "moderate", r"\brammend\w+\b|\bricucit\w+\b|\briparat[aeio]\b"),
    _rule(
        "repaired",
        "repair",
        "moderate",
        r"\b(?:repaired|mended|darned|patched)\b|\brepare[es]?\b|\braccommod\w+\b|\breparad[oa]s?\b|\bgeflickt\b|\brepariert\b|\bausgebessert\b",
    ),
    _rule("scucito", "seam", "moderate", r"\bscucit\w+\b|\bdescucit\w+\b|\bsfilacciat\w+\b"),
    _rule(
        "cucitura aperta",
        "seam",
        "moderate",
        r"\bcucitur[ae]\W+(?:\w+\W+){0,2}?(?:apert[ae]|salta\w+|staccat\w+|rott[ae]|ceduta|cedute)\b"
        r"|\b(?:orlo|fondo|bordo)\W+(?:\w+\W+){0,1}?(?:apert[oi]|staccat[oi]|salta\w+)\b",
    ),
    _rule(
        "seam open",
        "seam",
        "moderate",
        r"\bseams?\W+(?:\w+\W+){0,2}?(?:open|undone|ripped|split|opening|coming undone|unravel\w*)\b"
        r"|\b(?:frayed|fraying|unravel\w*)\b|\bcouture\W+(?:\w+\W+){0,2}?(?:decousue|ouverte|dechiree)\b|\bdecousu\w*\b"
        r"|\bcostura\W+(?:\w+\W+){0,2}?(?:abierta|descosida|rota)\b|\bdescosid[oa]\w*\b"
        r"|\bnaht\W+(?:\w+\W+){0,2}?(?:offen|aufgeplatzt|geplatzt|aufgegangen)\b|\bausgefranst\b",
    ),
    _rule(
        "filo tirato",
        "snag",
        "minor",
        r"\bfil[oi]\W+(?:\w+\W+){0,1}?tirat[aeio]\b|\btirat[ae] di fil\w+\b|\bsnag(?:s|ged)?\b|\baccrocs?\b|\bhilo tirado\b|\bfadenzieher\b|\blaufmasche\b|\bfil tire\b",
    ),
    # ---- broken parts
    _rule("cerniera rotta", "zip", "severe", rf"\b{_ZIP}\W+{_GAP}{_ZIP_BAD}\b|\b{_ZIP_BAD}\W+{_GAP}{_ZIP}\b"),
    _rule(
        "bottone mancante",
        "button",
        "moderate",
        rf"\b{_BUTTON}\W+{_GAP}{_BUTTON_BAD}\b|\b{_BUTTON_BAD}\W+{_GAP}{_BUTTON}\b",
    ),
    _rule(
        "suola consumata",
        "sole",
        "moderate",
        rf"\b{_SOLE}\W+{_GAP}{_SOLE_WORN}\b|\b{_SOLE_WORN}\W+{_GAP}{_SOLE}\b",
    ),
    _rule(
        "suola staccata",
        "sole",
        "severe",
        rf"\b{_SOLE}\W+{_GAP}{_SOLE_SPLIT}\b|\b{_SOLE_SPLIT}\W+{_GAP}{_SOLE}\b|\bscollament\w+\b",
    ),
    _rule(
        "rotto", "broken", "moderate", r"\b(?:rott[aeio]|broken|casse|cassee?s?|roto|rota|kaputt|defekt)\b"
    ),
    _rule(
        "danneggiato",
        "damage",
        "moderate",
        r"\b(?:danneggiat[aeio]|damaged|endommage\w*|danado\w*|beschadigt\w*|beschaedigt\w*)\b",
    ),
    # ---- wear, fading, pilling, yellowing, scratches, odour
    _rule(
        "usura",
        "wear",
        "minor",
        r"\b(?:usura|usurat[aeio]|consumat[aeio]|logor[aeio])\b|\bsigns? of wear\b|\bwear and tear\b|\bworn out\b|\bsignes? d usure\b|\busure\b|\bdesgaste\b|\babgenutzt\b|\babnutzung\w*\b|\bgebrauchsspuren\b|\bverschleiss\b",
    ),
    _rule(
        "graffio",
        "scratch",
        "minor",
        r"\bgraffi\w*\b|\bgraffiat[aeio]\b|\bscratch(?:es|ed)?\b|\bscuff(?:s|ed|ing)?\b|\brayures?\b|\bkratzer\b|\brayad[oa]s?\b|\baranazos?\b",
    ),
    _rule(
        "sbiadito",
        "fading",
        "minor",
        r"\b(?:sbiadit\w+|scolorit\w+|slavatur\w+|decolorat\w+|faded|fading|fades)\b|\bdelave\w*\b|\bdecolor\w+\b|\bverblass\w+\b|\bdescolorid\w+\b|\bdestenid\w+\b",
    ),
    _rule(
        "pilling",
        "pilling",
        "minor",
        r"\bpilling\b|\bpallin[io]\b|\bpallinatur\w+\b|\bbolle\b|\bbollicine\b|\bbouloch\w+\b|\bbolitas\b|\bknotchen\b",
    ),
    _rule(
        "ingiallito",
        "yellowing",
        "moderate",
        r"\b(?:ingiallit\w+|ingiallimento|yellowing|yellowed|vergilbt\w*|amarilleado)\b|\bjauni\w*\b|\bamarillent\w+\b",
    ),
    _rule(
        "odore",
        "odor",
        "moderate",
        r"\bodore\W+(?:\w+\W+){0,1}?(?:di\W+)?(?:fumo|sudore|chiuso|muffa|umido|naftalina|cantina|sigaretta)\b"
        r"|\bodori? (?:sgradevol\w+|forte|forti|persistent\w+)\b|\bpuzz\w+\b|\bmaleodorant\w*\b"
        r"|\bsmells? (?:of|like|bad|musty|strongly)\b|\bmusty\b|\bsmoky\b|\bodeur (?:de|forte|desagreable)\b|\briecht\b|\bhuele a\b",
    ),
    # ---- generic
    _rule(
        "difetto",
        "generic",
        "moderate",
        r"\b(?:difett[oi]|difettos[aeio]|imperfezion[ei]|defects?|flaws?|defauts?|defecto?s?|fehler|maengel|mangel)\b",
    ),
)

# Words that, in the same clause before a defect, say there is none.
_CUES = frozenset(
    {
        "non",
        "no",
        "nessun",
        "nessuna",
        "nessuno",
        "senza",
        "privo",
        "priva",
        "privi",
        "prive",
        "zero",
        "niente",
        "nulla",
        "assenza",
        "pas",
        "sans",
        "aucun",
        "aucune",
        "ni",
        "ohne",
        "kein",
        "keine",
        "keinen",
        "keiner",
        "keines",
        "nicht",
        "without",
        "free",
        "nothing",
        "never",
        "sin",
        "ningun",
        "ninguna",
        "nada",
        "neither",
        "nor",
        "mai",
    }
)
# "mai" negates a defect only as "mai un difetto"; it does not negate "mai usata, ma un buco".
_WEAK_CUES = frozenset({"mai", "never", "free"})
# A contrast word closes the reach of a negation ("senza difetti, tranne una macchia").
_BREAKERS = (
    "tranne", "eccetto", "salvo", "except", "except for", "apart from", "ma", "pero", "mais", "aber", "sauf",
    "excepto", "but", "solo", "only", "tuttavia", "however",
)  # fmt: skip
_BREAKER_RX = re.compile(
    r"\b(?:" + "|".join(sorted({re.escape(fold(b)) for b in _BREAKERS}, key=len, reverse=True)) + r")\b"
)
# Words that make a comma segment a clause of its own rather than a bare list item.
_CLAUSE_WORDS = frozenset(
    [
        "ha",
        "hai",
        "ho",
        "c",
        "ce",
        "e",
        "ci",
        "has",
        "have",
        "had",
        "there",
        "is",
        "are",
        "was",
        "un",
        "una",
        "uno",
        "il",
        "la",
        "lo",
        "le",
        "the",
        "a",
        "an",
        "one",
        "some",
        "del",
        "della",
        "dei",
        "con",
        "with",
        "sul",
        "sulla",
        "sullo",
        "su",
        "in",
        "on",
        "at",
        "near",
        "vicino",
        "al",
        "alla",
        "nel",
        "nella",
        "un",
        "po",
        "qualche",
        "piu",
        "mas",
        "muy",
        "tres",
        "very",
        "trovo",
        "vedi",
        "see",
        "present",
        "presenta",
        "presenti",
    ]
)
_SENTENCE = re.compile(r"[.;:!?\n|()\[\]]+")
_SOFT = re.compile(
    r"\b(?:piccol[aeio]|leggero|leggera|leggeri|leggere|leggermente|lievi?|minim[aeio]|small|slight(?:ly)?|tiny|little|minor|light(?:ly)?|"
    r"petit[es]?|leger\w*|klein\w*|pequen\w+|ligero|ligera|poco|un po)\b"
)
_HARD = re.compile(
    r"\b(?:grand[ei]|grosso|grossa|enorme|evident[ei]|large|big|huge|bad|serious|grave|important[ei]|gros|gross\w*|grande)\b"
)
_HASHTAG = re.compile(r"#\w+")
# Plain reassurances that need no defect word at all (kept next to the general negation handling).
_NO_DEFECT_PHRASES = (
    "senza difetti", "nessun difetto", "privo di difetti", "priva di difetti", "non presenta difetti",
    "non ha difetti", "perfette condizioni", "no defects", "without defects", "no flaws", "sans defaut",
    "aucun defaut", "keine mangel", "ohne mangel", "sin defectos", "no damage", "no visible defects",
)  # fmt: skip


@dataclass(frozen=True)
class DefectHit:
    term: str
    kind: str
    severity: str
    phrase: str

    # Where the claim comes from: the seller wrote it (never something seen on the item).
    source = "declared"

    def as_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "kind": self.kind,
            "label": KIND_LABELS.get(self.kind, self.kind),
            "severity": self.severity,
            "phrase": self.phrase,
            "provenance": self.source,
        }


@dataclass
class DefectReport:
    hits: list[DefectHit] = field(default_factory=list)
    # Defects the seller says are NOT there (phrases): "senza macchie", "no holes".
    denied: list[str] = field(default_factory=list)

    @property
    def terms(self) -> list[str]:
        """Matched words, in text order, without repeats (the list ``declared_defects`` has always held)."""
        return list(dict.fromkeys(h.term for h in self.hits))

    @property
    def labels(self) -> list[str]:
        """Italian label per kind, sorted (the list ``defect_terms`` has always held)."""
        return sorted({KIND_LABELS.get(h.kind, h.kind) for h in self.hits})

    @property
    def worst(self) -> str | None:
        return max((h.severity for h in self.hits), key=_RANK.__getitem__, default=None)

    @property
    def declares_none(self) -> bool:
        return bool(self.denied)

    @property
    def needs_restoration(self) -> bool:
        return any(h.kind in RESTORATION_KINDS and _RANK[h.severity] >= _RANK["moderate"] for h in self.hits)

    def as_dict(self) -> dict[str, Any]:
        return {
            "hits": [h.as_dict() for h in self.hits],
            "worst": self.worst,
            "denied": self.denied,
            "needs_restoration": self.needs_restoration,
        }


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE.split(text) if part.strip()]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text)


def _has_cue(words: list[str], *, near: bool) -> bool:
    for i, w in enumerate(words):
        if w in _CUES and w not in _WEAK_CUES:
            return True
        if w in _WEAK_CUES and near and i >= len(words) - 3:
            return True
    return False


def _negated(sentence: str, start: int, end: int) -> bool:
    """Is the defect at ``sentence[start:end]`` inside a negation of its sentence?"""
    before = sentence[:start]
    last = None
    for m in _BREAKER_RX.finditer(before):
        last = m
    if last is not None:
        before = before[last.end() :]
    segments = before.split(",")
    own = _words(segments[-1])
    if _has_cue(own, near=True):
        return True
    # The comma list goes on only while this segment is a bare item ("senza macchie, buchi o strappi").
    rest = _words(sentence[end:].split(",")[0])
    item = own + rest
    if len(item) > 3 or any(w in _CLAUSE_WORDS for w in item):
        return False
    for seg in reversed(segments[:-1]):
        words = _words(seg)
        if _has_cue(words, near=False):
            return True
        if len(words) > 3 or any(w in _CLAUSE_WORDS for w in words):
            return False
    return False


def _severity(base: str, sentence: str, start: int, end: int) -> str:
    """One step down for a softener next to the defect, one up for an intensifier."""
    pre = re.split(r",|\b(?:e|and|et|und|y|con|with|ma|but)\b", sentence[:start])[-1]
    post = sentence[end:].split(",")[0]
    near = " ".join(_words(pre)[-3:] + _words(post)[:2])
    rank = _RANK[base]
    if _SOFT.search(near):
        rank -= 1
    elif _HARD.search(near):
        rank += 1
    return SEVERITIES[max(1, min(3, rank)) - 1]


_DENIABLE = frozenset({"generic", "wear", "stain", "hole", "tear", "pilling", "fading", "broken", "damage"})


def extract_defects(text: str | None) -> DefectReport:
    """The defects ``text`` declares, with negations, severities and the phrase each was read from."""
    report = DefectReport()
    if not text:
        return report
    folded = fold(_HASHTAG.sub(" ", text)).replace("ß", "ss")
    hits: dict[tuple[str, str], DefectHit] = {}
    for sentence in _sentences(folded):
        for term, kind, base, rx in _RULES:
            for m in rx.finditer(sentence):
                if _negated(sentence, m.start(), m.end()):
                    if kind in _DENIABLE:
                        report.denied.append(" ".join(sentence[max(0, m.start() - 16) : m.end()].split()))
                    continue
                severity = _severity(base, sentence, m.start(), m.end())
                old = hits.get((kind, term))
                if old is None or _RANK[severity] > _RANK[old.severity]:
                    phrase = " ".join(sentence[max(0, m.start() - 12) : m.end() + 14].split())
                    hits[(kind, term)] = DefectHit(term, kind, severity, phrase)
    report.hits = list(hits.values())
    if any(p in folded for p in _NO_DEFECT_PHRASES):
        report.denied.append("dichiarazione di assenza di difetti")
    report.denied = list(dict.fromkeys(report.denied))
    return report


def floor_for_severity(severity: str | None) -> str | None:
    """The best condition a listing whose worst declared defect has this severity can honestly be priced at.

    A hole, a tear, a broken zip or a detached sole is "satisfactory"; a stain, an open seam, a missing
    button or a repair is "good"; wear, pilling and fading leave the declared condition alone. ``None``:
    no floor. Only ever lowers a condition (the caller takes the worse of the two)."""
    if severity == "severe":
        return "satisfactory"
    if severity == "moderate":
        return "good"
    return None


def condition_floor(report: DefectReport) -> str | None:
    return floor_for_severity(report.worst)
