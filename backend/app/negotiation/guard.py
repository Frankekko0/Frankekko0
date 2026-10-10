"""What a model-written negotiation message may say, enforced in code before the user ever sees it.

The model writes WORDS only. It never writes a digit, a euro amount, the name of the item or a contact: every
figure reaches the message through a placeholder that this module fills from the code's own numbers, and a draft
that breaks a rule is dropped (the template stays for that message). The checks run on the RAW model text, before
the placeholders are filled, so the digits of an item name ("Air Max 90") are never mistaken for a price.

    {ITEM}   the item, built by code from the listing title (``assistant.item_label``)
    {OFFER}  the opening offer (never above the most worth paying)
    {MAX}    the most worth paying
    {ASKED}  the asking price (only where it is not above the most worth paying)
    {MEDIAN} what similar items sold for (only when there are enough sales)

Pure module: no network, no database. The wording rules are the ones the rest of the app already uses: the
off-platform phrases of the listing analysis, the pressure phrases of the negotiation assistant and the claims
the listing draft never makes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.agent.guardrails import CLOSE, OPEN, injection_suspected
from app.analysis.text import off_platform_hits
from app.negotiation.assistant import NegotiationPlan, eur, item_label, pressure_phrases
from app.selling.listing_draft import claims_not_supported

KINDS = ("first_offer", "counter_reply", "accept", "decline_politely", "bundle")
AMOUNTS = ("OFFER", "MAX", "ASKED", "MEDIAN")
PLACEHOLDERS = ("ITEM", *AMOUNTS)
MAX_CHARS = 400
MIN_CHARS = 20
EPSILON = 0.005


@dataclass(frozen=True)
class Rule:
    allowed: frozenset[str]
    required: frozenset[str] = frozenset()


# Which placeholders each kind of message may use, and which it must contain. A refusal names no price, a bundle
# asks for a combined price without proposing one, the counter reply is the only place the most worth paying is said.
RULES: dict[str, Rule] = {
    "first_offer": Rule(frozenset({"ITEM", "OFFER", "MEDIAN"}), frozenset({"OFFER"})),
    "counter_reply": Rule(frozenset({"ITEM", "MAX"}), frozenset({"MAX"})),
    "accept": Rule(frozenset({"ITEM", "ASKED"})),
    "decline_politely": Rule(frozenset({"ITEM"})),
    "bundle": Rule(frozenset({"ITEM"})),
}


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class NegotiationFacts:
    """Everything the guard and the renderer know: the code's figures and the label of the item."""

    tone: str
    kinds: tuple[str, ...]
    asked: float
    offer: float | None
    max_price: float | None
    median: float | None
    bundle_possible: bool
    item_label: str

    @classmethod
    def from_plan(
        cls, plan: NegotiationPlan, *, tone: str, title: str | None, median: float | None = None
    ) -> NegotiationFacts:
        """The figures of a plan. The offer is the opening offer clamped to the most worth paying; the median counts
        only when it is below the asking price (the same condition the template uses to quote it)."""
        offer = plan.ideal_offer if plan.ideal_offer is not None else plan.max_acceptable
        if offer is not None and plan.max_acceptable is not None:
            offer = min(offer, plan.max_acceptable)
        return cls(
            tone=tone,
            kinds=tuple(k for k in KINDS if k in plan.messages),
            asked=plan.asked,
            offer=None if offer is None else round(offer, 2),
            max_price=None if plan.max_acceptable is None else round(plan.max_acceptable, 2),
            median=median if median and median < plan.asked else None,
            bundle_possible="bundle" in plan.messages,
            item_label=item_label(title),
        )

    def values(self) -> dict[str, float | None]:
        return {"OFFER": self.offer, "MAX": self.max_price, "ASKED": self.asked, "MEDIAN": self.median}

    def available(self, name: str) -> bool:
        """Whether the figure behind a placeholder exists and may be named: nothing is promised above the most
        worth paying, and the asking price is named only when the most worth paying is known and covers it."""
        if name == "ITEM":
            return True
        value = self.values().get(name)
        if value is None:
            return False
        if name == "MEDIAN":
            return True  # a reference to what similar items sold for, not a price the user commits to
        if name == "ASKED":
            return self.max_price is not None and value <= self.max_price + EPSILON
        return self.max_price is None or value <= self.max_price + EPSILON

    def placeholders(self, kind: str) -> frozenset[str]:
        rule = RULES.get(kind)
        return frozenset(n for n in rule.allowed if self.available(n)) if rule else frozenset()

    def required(self, kind: str) -> frozenset[str]:
        """The placeholders a message of this kind must contain (those whose figure exists)."""
        rule = RULES.get(kind)
        return rule.required & self.placeholders(kind) if rule else frozenset()

    @property
    def distance(self) -> str:
        """How far the offer is below the asking price, in words (the model is not shown the amounts)."""
        if self.offer is None or self.offer >= self.asked:
            return "nessuna"
        gap = (self.asked - self.offer) / self.asked
        return "piccola" if gap < 0.10 else "media" if gap < 0.25 else "grande"

    def prompt_view(self) -> dict[str, Any]:
        """What the model is told about the situation: the tone, which messages to write and which placeholders each
        may use. No amount, no identity, no purchase cost, no margin."""
        return {
            "tono": self.tone,
            "distanza_offerta_dal_prezzo_richiesto": self.distance,
            "messaggi": [
                {
                    "tipo": k,
                    "segnaposto_disponibili": sorted(self.placeholders(k)),
                    "segnaposto_obbligatori": sorted(self.required(k)),
                }
                for k in self.kinds
            ],
        }

    def fingerprint_parts(self) -> list[Any]:
        return [
            self.asked, self.offer, self.max_price, self.median, list(self.kinds), self.item_label,
        ]  # fmt: skip


# ------------------------------------------------------------------ the checks
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]*)\}")
_MONEY = re.compile(
    r"€|\$|£|\beur(?:o|os)?\b|\bcent(?:esimi)?\b|%|\bper ?cento\b|\bpercent\w*|\bsconto del\b|\bribasso del\b|\bdollar\w*",
    re.IGNORECASE,
)
_NUMBER_WORDS = re.compile(
    r"\b(?:due|tre|quattro|cinque|sette|otto|nove|dieci|undici|dodici|tredici|quattordici|quindici|sedici|"
    r"diciassette|diciotto|diciannove|(?:venti|trenta|quaranta|cinquanta|sessanta|settanta|ottanta|novanta)\w*|"
    r"(?:due|tre|quattro|cinque|sei|sette|otto|nove)?cento|mille|\w*mila|milion[ei]|decin[ae]|dozzina|centinaio|"
    r"metà|mezzo|mezza|dimezz\w*|gratis|gratuit\w*)\b",
    re.IGNORECASE,
)
_CONTACT = re.compile(
    r"il mio numero|numero di telefono|telefono|cellulare|chiamami|chiamarmi|\bsms\b|e-?mail|\bmail\b|scrivimi|"
    r"scrivermi|contattami|contattarmi|di persona|ci vediamo|incontriamoci|consegna a mano|ritiro a mano|"
    r"\bsignal\b|\bskype\b|facebook|messenger|\bdm\b|\bdiscord\b|\bwechat\b|\btiktok\b|@",
    re.IGNORECASE,
)
_PAYMENT = re.compile(
    r"ti pago|ti mando|ti invio|\bbonifico\b|in contanti|\bcontanti\b|satispay|carta prepagata|gift ?card|buono amazon",
    re.IGNORECASE,
)
_LINK = re.compile(r"https?\b|www\.|\.com\b|\.it\b", re.IGNORECASE)
_PRESSURE = re.compile(
    r"ultima possibilit|ora o mai|devi decidere|decidi subito|ho fretta|urgente|entro oggi|entro stasera|"
    r"entro domani|oggi stesso|prendere o lasciare|prendi o lascia|non ho tempo",
    re.IGNORECASE,
)
_COMPETITORS = re.compile(
    r"un altro (?:acquirente|compratore|cliente|utente)|qualcun altro|altre offerte|un'altra offerta|altre persone|"
    r"mi hanno (?:offerto|proposto)|ho (?:già )?(?:un'altra|altre) offert|ne ho visti? altri?|ho trovato (?:a|di) meno",
    re.IGNORECASE,
)
_CONDITION = re.compile(
    r"difett|rovinat|macchi|strapp|usurat|danneggiat|sbiadit|bucat|consumat|segni di|condizion", re.IGNORECASE
)
_MARKET = re.compile(
    r"vendut|simili|mercato|comparabil|quotazion|prezzo medio|in media|di solito costa|costa di meno",
    re.IGNORECASE,
)
_ENGLISH = {
    "the", "you", "your", "thanks", "thank", "hello", "please", "would", "could", "offer", "price", "and",
    "with", "for", "this", "that", "dear", "regards", "item", "hi",
}  # fmt: skip
_ENGLISH_STRONG = {"thanks", "thank", "hello", "please", "would", "dear", "regards"}


def _bad_format(text: str) -> str | None:
    for ch in text:
        cat = unicodedata.category(ch)
        if (
            ch in "*`#<>|~\\[]_"
            or cat in ("So", "Sk", "Cs", "Co", "Cn")
            or (cat == "Cc" and ch not in "\n\t")
        ):
            return ch
    return None


def _english(text: str) -> bool:
    words = re.findall(r"[a-z']+", text.lower())
    return any(w in _ENGLISH_STRONG for w in words) or sum(w in _ENGLISH for w in words) >= 2


def check(kind: str, raw: str, facts: NegotiationFacts) -> list[Violation]:
    """The rules a draft of ``kind`` breaks, on the raw model text (empty list: the draft may be shown)."""
    rule = RULES.get(kind)
    if rule is None or kind not in facts.kinds:
        return [Violation("unknown_kind", kind[:40])]
    text = (raw or "").strip()
    if not text:
        return [Violation("empty")]
    out: list[Violation] = []

    def add(code: str, detail: str = "") -> None:
        out.append(Violation(code, detail[:60]))

    if len(text) > MAX_CHARS:
        add("too_long", str(len(text)))
    if len(text) < MIN_CHARS:
        add("too_short", str(len(text)))
    if re.search(r"\n\s*\n", text):
        add("multi_paragraph")
    if (bad := _bad_format(text)) is not None:
        add("bad_format", repr(bad))

    # placeholders: only the known ones, only where the kind allows them, only with a figure behind them
    names = [m.group(1) for m in _PLACEHOLDER.finditer(text)]
    for name in dict.fromkeys(names):
        if name not in PLACEHOLDERS:
            add("unknown_placeholder", name)
        elif name not in rule.allowed:
            add("placeholder_not_allowed", name)
        elif name != "ITEM":
            value = facts.values()[name]
            if value is None or (name == "ASKED" and facts.max_price is None):
                add("placeholder_not_available", name)
            elif name != "MEDIAN" and facts.max_price is not None and value > facts.max_price + EPSILON:
                add("amount_above_max", name)
    for name in sorted(facts.required(kind)):
        if name not in names:
            add("missing_required_amount", name)
    bare = _PLACEHOLDER.sub(" ", text)
    if "{" in bare or "}" in bare:
        add("bad_placeholder")

    # no figure of the model's own: digits, currency, percentages, amounts in words
    if any(c.isnumeric() for c in bare):
        add("literal_number")
    if m := _MONEY.search(bare):
        add("literal_amount", m.group(0))
    if m := _NUMBER_WORDS.search(bare):
        add("spelled_amount", m.group(0))
    if "{MEDIAN}" in text and not any(_MARKET.search(s) for s in re.split(r"[.!?]", text) if "{MEDIAN}" in s):
        add("median_context")
    if _MARKET.search(bare) and facts.median is None:
        add("market_claim_unsupported")

    # nothing outside the platform
    for hit in off_platform_hits(bare):
        add(hit["code"], hit["evidence"])
    if m := _CONTACT.search(bare):
        add("off_platform_contact", m.group(0))
    if m := _PAYMENT.search(bare):
        add("off_platform_payment", m.group(0))
    if m := _LINK.search(bare):
        add("external_link", m.group(0))

    # honest, courteous, no invented facts
    if pressure := pressure_phrases(bare):
        add("pressure", pressure[0])
    if m := _PRESSURE.search(bare):
        add("pressure", m.group(0))
    if m := _COMPETITORS.search(bare):
        add("invented_fact", m.group(0))
    if m := _CONDITION.search(bare):
        add("invented_fact", m.group(0))
    if claims := claims_not_supported(bare):
        add("forbidden_claim", claims[0])
    if "grazie" not in bare.lower():
        add("no_courtesy")
    if _english(bare):
        add("wrong_language")

    # the listing's text is data: nothing of the delimiters or of an order may come back out
    if OPEN in text or CLOSE in text or "annuncio_non_fidato" in text.lower() or injection_suspected(bare):
        add("echoes_injection")
    return out


def render(raw: str, facts: NegotiationFacts) -> str:
    """The text with its placeholders filled, in one pass (a label that looks like a placeholder stays text)."""
    values: dict[str, str] = {"ITEM": facts.item_label}
    values.update({k: eur(v) for k, v in facts.values().items() if v is not None})
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), " ".join(raw.split()))


_EURO_AMOUNT = re.compile(r"(\d+(?:[.,]\d+)?)\s*€")


def amounts_in(text: str) -> list[float]:
    """The euro amounts a finished message names (``eur()`` writes them as ``21,50 €``)."""
    return [float(m.group(1).replace(",", ".")) for m in _EURO_AMOUNT.finditer(text)]


def amounts_are_the_codes(text: str, facts: NegotiationFacts) -> bool:
    """Last line of defence on a rendered message: every amount in it is one of the code's figures."""
    known = {round(v, 2) for v in facts.values().values() if v is not None}
    return all(round(a, 2) in known for a in amounts_in(text))
