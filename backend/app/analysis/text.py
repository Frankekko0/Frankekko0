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
from dataclasses import dataclass, field
from typing import Any

from app.agent.guardrails import injection_suspected
from app.analysis.labels import materials_in, parse_composition
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

_DEFECTS = (
    "macchia", "macchie", "buco", "buchi", "strappo", "difetto", "difetti", "pilling", "bolle", "bollicine",
    "usura", "usurato", "scolorit", "sbiadit", "filo tirato", "fili tirati", "scucit", "rotto", "alone", "aloni",
    "stain", "hole", "torn", "tear", "defect", "flaw", "faded", "fading", "worn out", "snag", "scuff",
    "scratch", "damaged", "tache", "trou", "dechir", "defaut", "bouloch", "delave", "decolor", "usure",
    "abime", "accroc", "fleck", "loch", "riss", "mangel", "defekt", "knotchen", "verblass", "abgenutzt",
    "beschadigt", "kratzer", "mancha", "agujero", "defecto", "bolitas", "desgaste", "descolorid", "danado",
)  # fmt: skip
_NO_DEFECT = (
    "senza difetti", "nessun difetto", "privo di difetti", "nessuna macchia", "perfette condizioni",
    "no defects", "without defects", "no flaws", "no stains", "no holes", "sans defaut", "aucun defaut",
    "sans tache", "keine mangel", "ohne mangel", "keine flecken", "sin defectos", "sin manchas",
)  # fmt: skip
_NEGATIONS = (
    "senza",
    "nessun",
    "nessuna",
    "privo",
    "no ",
    "sans",
    "aucun",
    "ohne",
    "kein",
    "sin ",
    "without",
    "non ",
)

_RISK_PHRASES: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "off_platform_contact",
        "Chiede di essere contattato fuori dalla piattaforma",
        re.compile(
            r"whatsapp|telegram|instagram|scrivimi su|contattami|mandami un messaggio su|contactez.moi sur|"
            r"schreib mir auf|escribeme por|\b[\w.+-]+@[\w-]+\.\w{2,}\b|(?<!\d)(?:\+?\d[\d ]{8,13}\d)(?!\d)"
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
_PAID = re.compile(
    r"(?:pagato|acquistato a|comprato a|paid|bought for|purchased for|retail|prezzo originale|original price|"
    r"paye|achete|prix d origine|gekauft fur|bezahlt|neupreis|pague|compre)\D{0,12}(\d{1,5}(?:[.,]\d{1,2})?)"
)
_LOT = re.compile(r"\b(lotto|stock|set di|bundle|lot of|lot de|posten|conjunto)\b")
_PIECES = re.compile(r"\b(\d{1,2})\s*(?:pezzi|pz|pcs|pieces|pieces|stuck|piezas)\b")
_LETTER_SIZE = re.compile(r"(?<![\w/])(XXXL|XXL|XL|XXS|XS|S|M|L)(?![\w/])")
_SIZE_WORD = re.compile(r"\b(?:taglia|size|taille|grosse|groesse|talla)\s*[:=]?\s*([a-z0-9./]{1,6})\b")

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


def _declared_defects(text: str) -> list[str]:
    found: list[str] = []
    for term in _DEFECTS:
        for m in re.finditer(re.escape(term), text):
            # Only the words of the same clause can negate it ("no stains, a hole" has a hole).
            before = re.split(r"[.,;:!?\n]", text[max(0, m.start() - 24) : m.start()])[-1]
            if any(n in before for n in _NEGATIONS):
                continue
            if term not in found:
                found.append(term)
            break
    return found


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
    desc = (description or "").strip()
    raw = f"{title or ''}\n{desc}".strip()
    text = fold(raw).replace("ß", "ss")
    sig = TextSignals(detect_language(raw), available=bool(desc))
    if not text:
        return sig
    sig.injection_suspected = injection_suspected(raw)
    if m := _SIZE_WORD.search(text):
        sig.size = normalize_size(m.group(1).upper())
    elif m := _LETTER_SIZE.search(raw):  # "Felpa blu M": a bare capital size in the title or text
        sig.size = normalize_size(m.group(1))
    sig.color = normalize_color(text)
    sig.materials = materials_in(text)
    sig.composition = parse_composition(text)
    for key, rx in _MEASURES:
        if m := rx.search(text):
            sig.measures_cm[key] = float(m.group(1))
    sig.declares_no_defects = _any(text, _NO_DEFECT) is not None
    sig.declared_defects = _declared_defects(text)
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
    if m := _PAID.search(text):
        try:
            sig.original_price = float(m.group(1).replace(",", "."))
        except ValueError:
            sig.original_price = None
    sig.mentions_receipt = _any(text, _RECEIPT) is not None
    sig.mentions_box = _any(text, _BOX) is not None
    sig.mentions_tags = _any(text, _TAGS) is not None
    sig.open_to_offers = _any(text, _NEGOTIABLE) is not None
    sig.vague_phrases = [v for v in _VAGUE if v in text]
    for code, label, rx in _RISK_PHRASES:
        if m := rx.search(text):
            sig.risk_phrases.append({"code": code, "label": label, "evidence": m.group(0).strip()[:40]})
    if _LOT.search(text):
        sig.lot_pieces = int(m.group(1)) if (m := _PIECES.search(text)) else 2
    elif m := _PIECES.search(text):
        sig.lot_pieces = int(m.group(1)) if int(m.group(1)) > 1 else None
    return sig
