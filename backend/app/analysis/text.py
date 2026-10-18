"""P1 - what the text says, in the languages Vinted sellers write (IT, EN, FR, DE, ES).

Extracts attributes, measures, declared defects, the reason for selling, smoking and pets, the
price paid originally, receipt/box/tags, willingness to negotiate and urgency, vague phrases and
risk phrases (contact or payment outside the platform). It also reports wording that tries to give
orders to whoever reads the text.

The text of a listing is **untrusted data**. Nothing here executes, follows or obeys it: it is
matched against fixed vocabularies and the matches become fields. Wording that addresses the
reader is only reported (``injection_suspected``), and changes no other result.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.agent.guardrails import injection_suspected
from app.analysis.defects import MAX_TEXT, extract_defects
from app.analysis.labels import materials_in, parse_composition
from app.analysis.lots import lot_pieces
from app.identification.taxonomy import fold
from app.ingestion.normalizer import normalize_color, normalize_size

_STOPWORDS = {
    "it": ("il", "la", "di", "con", "per", "non", "che", "molto", "ho", "una", "un", "sono", "come", "mai"),
    "en": ("the", "and", "with", "is", "for", "this", "very", "has", "from", "worn", "only", "never"),
    "fr": ("le", "les", "de", "et", "avec", "est", "tres", "pour", "pas", "une", "un", "sans", "porte"),
    "de": (
        "der",
        "die",
        "das",
        "und",
        "mit",
        "ist",
        "nicht",
        "sehr",
        "fur",
        "ein",
        "eine",
        "ohne",
        "getragen",
    ),
    "es": ("el", "los", "las", "y", "con", "muy", "para", "es", "una", "un", "sin", "usado"),
}

_NO_DEFECT = (
    "senza difetti", "nessun difetto", "privo di difetti", "nessuna macchia", "perfette condizioni",
    "no defects", "without defects", "no flaws", "no stains", "no holes", "sans defaut", "aucun defaut",
    "sans tache", "keine mangel", "ohne mangel", "keine flecken", "sin defectos", "sin manchas",
)  # fmt: skip
_RISK_PHRASES: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "off_platform_contact",
        "Chiede di essere contattato fuori dalla piattaforma",
        re.compile(
            r"whatsapp|telegram|instagram|scrivimi su|contattami|mandami un messaggio su|contactez.moi sur|"
            r"schreib mir auf|escribeme por|(?<![\w.+-])[\w.+-]+@[\w-]+\.\w{2,}\b|(?<!\d)(?:\+?\d[\d ]{8,13}\d)(?!\d)"
        ),
    ),
    (
        "off_platform_payment",
        "Propone un pagamento fuori dalla piattaforma",
        re.compile(
            r"paypal|bonifico|\biban\b|postepay|ricarica|western union|revolut|fuori da vinted|fuori piattaforma|"
            r"pagamento fuori|hors plateforme|paiement hors|ausserhalb|per fuera|paga fuera|"
            r"amici e famiglia|famiglia e amici|friends and family|friends & family|proches"
        ),
    ),
    (
        "advance_payment",
        "Chiede un pagamento anticipato o un acconto",
        re.compile(
            r"pagamento anticipato|acconto|anticipo|advance payment|deposit first|vorkasse|paiement anticipe"
        ),
    ),
    ("external_link", "Contiene un link esterno", re.compile(r"https?://|www\.")),
]


# Invisible characters that are not in the Unicode "other" categories: fillers and joiners that render as nothing,
# and the variation selectors. They can split a word ("Whats\u200bApp") without changing how it reads.
_INVISIBLE = frozenset("\u034f\u115f\u1160\u17b4\u17b5\u3164\uffa0")


def is_invisible(ch: str) -> bool:
    if ch.isspace():
        return False
    return (
        unicodedata.category(ch)
        in ("Cf", "Cc", "Cs", "Co", "Cn")  # zero-width, soft hyphen, bidi, tags, controls
        or ch in _INVISIBLE
        or "\ufe00" <= ch <= "\ufe0f"
        or "\U000e0100" <= ch <= "\U000e01ef"
        or "\u180b" <= ch <= "\u180f"
    )


def strip_invisible(text: str, replacement: str = "") -> str:
    """``text`` without the characters that render as nothing (zero-width space and joiners, soft hyphen, bidi
    controls, Unicode tag characters, variation selectors, fillers, control characters other than whitespace), each
    one replaced by ``replacement``. Matching on such a text cannot be defeated by hiding a word in invisible
    characters; matching on the ``replacement=" "`` form also catches one used as an invisible space."""
    return "".join(replacement if is_invisible(ch) else ch for ch in text)


def off_platform_hits(text: str) -> list[dict[str, str]]:
    """Contact or payment outside the platform, advance payment and external links found in ``text``, as
    ``{code, label, evidence}``. The one definition shared by the listing analysis and by the checks on texts
    the app writes for the user (negotiation messages)."""
    # Lowercase, no accents, compatibility forms flattened (full-width letters become plain ones), and with the
    # invisible characters removed (a word split by them) and as spaces (one used as a separator): both are searched.
    forms = dict.fromkeys(fold(strip_invisible(text, r)).replace("ß", "ss") for r in ("", " "))
    out: list[dict[str, str]] = []
    for code, label, rx in _RISK_PHRASES:
        for low in forms:
            if m := rx.search(low):
                out.append({"code": code, "label": label, "evidence": m.group(0).strip()[:40]})
                break
    return out


_VAGUE = (
    "come da foto", "come in foto", "come nelle foto", "visibile in foto", "vedi foto", "guardate le foto",
    "guarda le foto", "difetto in foto", "as seen in photos", "as seen in the photos", "see photos", "see pictures",
    "as pictured", "voir photos", "comme sur les photos", "wie auf den fotos", "siehe fotos", "ver fotos",
    "como en las fotos",
)  # fmt: skip
_NEGOTIABLE = (
    "trattabile", "tratto", "trattativa", "accetto offerte", "fai la tua offerta", "negotiable", "obo",
    "or best offer", "open to offers", "negociable", "prix a debattre", "negociable", "verhandlungsbasis",
    "verhandelbar", " vb",
)  # fmt: skip
_URGENT = (
    "urgente", "urgent", "dringend", "trasloco", "trasferimento", "svuoto armadio", "svuotando", "liberare spazio",
    "moving", "clearing out", "cleaning out", "demenagement", "umzug", "mudanza", "devo vendere", "must sell",
    "muss weg", "il faut vendre", "svuota",
)  # fmt: skip
_REASONS = {
    "no_longer_used": ("non lo uso", "non lo metto", "non mi va piu", "non lo indosso", "not wearing", "don't wear", "ne le porte plus", "trage ich nicht mehr", "ya no lo uso"),
    "wrong_size": ("taglia sbagliata", "non mi sta", "cambio taglia", "doesn't fit", "does not fit", "too small", "too big", "too tight", "trop petit", "trop grand", "zu klein", "zu gross", "no me queda", "me queda"),
    "gift": ("regalo", "gift", "cadeau", "geschenk"),
    "clearout": ("svuoto armadio", "svuotando", "clearing out", "cleaning out", "vide dressing", "ausmisten", "limpiando armario"),
}  # fmt: skip
_SMOKE = ("fumatori", "fumo ", "smoker", "smoke", "rauch", "fumeur", "fume ", "humo")
_SMOKE_FREE = (
    "non fumatori",
    "no fumatori",
    "senza fumo",
    "smoke free",
    "smoke-free",
    "non smoker",
    "non-smoker",
    "rauchfrei",
    "nichtraucher",
    "non fumeur",
    "sans fumee",
    "libre de humo",
)
_PETS = (
    "animali",
    "gatto",
    "cane",
    "pets",
    " cat",
    " dog",
    "chat ",
    "chien",
    "haustier",
    "katze",
    "hund",
    "perro",
    "gato",
)
_PETS_FREE = (
    "senza animali",
    "no animali",
    "pet free",
    "pet-free",
    "no pets",
    "sans animaux",
    "ohne haustiere",
    "sin mascotas",
)
_RECEIPT = ("scontrino", "ricevuta", "receipt", "ticket de caisse", "quittung", "beleg", "factura", "fattura")
_BOX = ("scatola", "box", "boite", "karton", "schachtel", "caja", "dustbag", "dust bag")
_TAGS = (
    "cartellino",
    "con tag",
    "with tags",
    "nwt",
    "bnwt",
    "avec etiquette",
    "mit etikett",
    "neu mit etikett",
    "con etiqueta",
    "tag attaccato",
    "etichetta attaccata",
)
# The price the seller says was paid: a currency next to the figure, found right after a "bought/paid" cue
# ("Acquistata nel 2022 a 150 euro"), or a plain figure right after a "list price" cue ("retail 300").
_PAID_CUE = re.compile(
    r"\b(?:pagat[oaie]|acquistat[oaie]|comprat[oaie]|costav[ao]|costat[oaie]|paid|bought(?: for)?|"
    r"purchased(?: for)?|paye|achete|achetee|gekauft|bezahlt|pague|compre)\b"
)
_LIST_CUE = re.compile(
    r"\b(?:retail|prezzo (?:di )?(?:listino|originale|negozio)|original price|rrp|neupreis|prix d origine|"
    r"prix neuf|precio original)\b"
)
_AMOUNT = r"(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d{1,5}(?:[.,]\d{1,2})?)"
_MONEY = re.compile(rf"(?:(?:€|eur)\s*{_AMOUNT}|{_AMOUNT}\s*(?:€|eur\b|euros?\b))")
_BARE_FIGURE = re.compile(rf"^\W{{0,8}}{_AMOUNT}")
# No fibre is named by 25 letters in a row; a longer run is not a word, and a regex that reads a letter run from every
# position in it (``parse_composition``) is quadratic on one thousands of letters long.
_LONG_WORD = re.compile(r"[a-z]{25,}")
_GARMENT_WORDS = re.compile(r"\bpiumin[oi]\b|\bpile\b")
# A bare capital letter is a size only in the title ("Felpa blu M"), never next to an apostrophe ("L'ho usata").
_LETTER_SIZE = re.compile(r"(?<![\w/'’])(XXXL|XXL|XL|XXS|XS|S|M|L)(?![\w/'’])")
_SIZE_WORD = re.compile(
    r"\b(?:taglia|tg|tgl|size|sz|taille|grosse|groesse|talla)\.?\s*[:=]?\s*([a-z0-9./]{1,6})\b(?!['’])"
)

_MEASURES: list[tuple[str, re.Pattern[str]]] = [
    ("chest_flat", re.compile(r"(?:pit to pit|ascella(?: ad ascella)?|larghezza|width|largeur|breite|ancho)\D{0,6}(\d{2,3})")),
    ("chest_circ", re.compile(r"(?:torace|petto|circonferenza|bust|chest|poitrine|brustumfang|pecho)\D{0,6}(\d{2,3})")),
    ("length", re.compile(r"(?:lunghezza|length|longueur|lange|laenge|largo)\D{0,6}(\d{2,3})")),
    ("shoulders", re.compile(r"(?:spalle|shoulders?|epaules?|schulter|hombros?)\D{0,6}(\d{2,3})")),
    ("sleeve", re.compile(r"(?:manica|sleeve|manche|armel|aermel|manga)\D{0,6}(\d{2,3})")),
    ("waist", re.compile(r"(?:vita|waist|taille|bund|cintura)\D{0,6}(\d{2,3})")),
    ("inseam", re.compile(r"(?:cavallo|inseam|entrejambe|schritt)\D{0,6}(\d{2,3})")),
]  # fmt: skip


def detect_language(text: str) -> str:
    words = set(re.findall(r"[a-z]+", fold(text)))
    scores = {lang: sum(1 for w in sw if w in words) for lang, sw in _STOPWORDS.items()}
    lang, best = max(scores.items(), key=lambda kv: kv[1])
    return lang if best >= 2 else "unknown"


def _any(text: str, needles: tuple[str, ...]) -> str | None:
    return next((n for n in needles if n in text), None)


def _number(raw: str) -> float | None:
    """A price figure as written: "1.200" is twelve hundred, "12,5" twelve and a half."""
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _paid_price(text: str) -> float | None:
    for m in _PAID_CUE.finditer(text):
        if hit := _MONEY.search(text[m.end() : m.end() + 60]):
            return _number(hit.group(1) or hit.group(2))
    for m in _LIST_CUE.finditer(text):
        if hit := _BARE_FIGURE.match(text[m.end() : m.end() + 14]):
            return _number(hit.group(1))
    return None


@dataclass
class TextSignals:
    language: str
    available: bool  # there was a description to read
    size: str | None = None
    color: str | None = None
    materials: list[str] = field(default_factory=list)
    composition: dict[str, int] = field(default_factory=dict)
    measures_cm: dict[str, float] = field(default_factory=dict)
    declared_defects: list[str] = field(default_factory=list)
    # Each declared defect with its kind, severity and the phrase it was read from (provenance "declared").
    defect_hits: list[dict[str, Any]] = field(default_factory=list)
    defect_severity: str | None = None
    declares_no_defects: bool = False
    sale_reason: str | None = None
    motivated_seller: bool = False
    smoker_home: bool | None = None
    pets_home: bool | None = None
    original_price: float | None = None
    mentions_receipt: bool = False
    mentions_box: bool = False
    mentions_tags: bool = False
    open_to_offers: bool = False
    vague_phrases: list[str] = field(default_factory=list)
    risk_phrases: list[dict[str, str]] = field(default_factory=list)
    lot_pieces: int | None = None
    injection_suspected: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def analyze_text(title: str | None, description: str | None) -> TextSignals:
    """Read a listing's words. Without a description only the title is read and ``available`` is
    false (a catalogue card has none: that is a limit of the source, not an empty description)."""
    desc = (description or "").strip()[:MAX_TEXT]
    raw = f"{title or ''}\n{desc}".strip()
    text = fold(raw).replace("ß", "ss")
    sig = TextSignals(detect_language(raw), available=bool(desc))
    if not text:
        return sig
    sig.injection_suspected = injection_suspected(raw)
    if m := _SIZE_WORD.search(text):
        sig.size = normalize_size(m.group(1).upper())
    elif m := _LETTER_SIZE.search(title or ""):  # "Felpa blu M": a bare capital size, in the title only
        sig.size = normalize_size(m.group(1))
    sig.color = normalize_color(text)
    # "Piumino" is the garment, not the fill: a puffer jacket with a shell-only label is not a lie about the down.
    sig.materials = materials_in(_GARMENT_WORDS.sub(" ", text))
    sig.composition = parse_composition(_LONG_WORD.sub(" ", text))
    for key, rx in _MEASURES:
        if m := rx.search(text):
            sig.measures_cm[key] = float(m.group(1))
    report = extract_defects(raw)
    sig.declares_no_defects = _any(text, _NO_DEFECT) is not None or report.declares_none
    sig.declared_defects = report.terms
    sig.defect_hits = [h.as_dict() for h in report.hits]
    sig.defect_severity = report.worst
    for reason, words in _REASONS.items():
        if _any(text, words):
            sig.sale_reason = reason
            break
    sig.motivated_seller = _any(text, _URGENT) is not None or sig.sale_reason == "clearout"
    if _any(text, _SMOKE_FREE):
        sig.smoker_home = False
    elif _any(text, _SMOKE):
        sig.smoker_home = True
    if _any(text, _PETS_FREE):
        sig.pets_home = False
    elif _any(text, _PETS):
        sig.pets_home = True
    sig.original_price = _paid_price(text)
    sig.mentions_receipt = _any(text, _RECEIPT) is not None
    sig.mentions_box = _any(text, _BOX) is not None
    sig.mentions_tags = _any(text, _TAGS) is not None
    sig.open_to_offers = _any(text, _NEGOTIABLE) is not None
    sig.vague_phrases = [v for v in _VAGUE if v in text]
    sig.risk_phrases = off_platform_hits(text)
    sig.lot_pieces = lot_pieces(text)
    return sig
