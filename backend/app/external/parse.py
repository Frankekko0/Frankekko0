"""Parsing of the search results: prices, dates, sold markers, conditions, sizes, offers.

Pure functions (no I/O). Results come from Serper.dev (Google results as JSON):

* ``/shopping`` -> ``shopping[]``: ``title``, ``source`` (shop name), ``link``, ``price``
  ("59,99 €", "€59.99", "£40.00", "$45.00 used"), ``delivery``, ``imageUrl``, ``rating``,
  ``ratingCount``, ``offers``, ``productId``, ``position``;
* ``/search`` -> ``organic[]``: ``title``, ``link``, ``snippet``, ``date`` ("12 set 2026",
  "3 days ago"), ``position``, sometimes ``attributes`` (rich-snippet pairs such as
  ``{"Prezzo": "45,00 €"}``), ``price``/``currency`` or ``priceRange``;
* both carry ``credits`` (credits charged) and ``searchParameters``.

Prices, dates and markers are read in Italian, English, French, German and Spanish, the languages
of the second-hand marketplaces searched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from app.domain.enums import Condition
from app.identification.taxonomy import fold
from app.ingestion.normalizer import normalize_condition, normalize_size

# ---------------------------------------------------------------------------- prices
_CURRENCY = {
    "€": "EUR",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "$": "USD",
    "us$": "USD",
    "us $": "USD",
    "usd": "USD",
    "chf": "CHF",
    "fr.": "CHF",
    "pln": "PLN",
    "zł": "PLN",
    "zl": "PLN",
    "czk": "CZK",
    "kč": "CZK",
    "sek": "SEK",
    "dkk": "DKK",
}
# A number: "1.234,56", "1,234.56", "59,99", "59.99", "45" (thin/no-break spaces as thousands).
_NUM = r"\d{1,3}(?:[.,  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
# Currencies before the number; ``XX$`` other than US$ (A$, C$, R$...) is a known-unknown.
_PRE = r"US\s?\$|[A-Z]{1,3}\$|EUR|GBP|USD|CHF|PLN|CZK|SEK|DKK|€|£|\$|Fr\.|zł|Kč"
_POST = r"€|EUR|euros?|£|GBP|\$|USD|CHF|PLN|zł|CZK|Kč|SEK|DKK|kr|¥|₹"
_PRICE_RE = re.compile(
    rf"(?<![\w$])(?P<pre>{_PRE})\s?(?P<n1>{_NUM})(?![\d])"
    rf"|(?<![\d.,])(?P<n2>{_NUM})\s?(?P<post>{_POST})(?![A-Za-z])",
    re.IGNORECASE,
)
# A price right after these words is not the item price (shipping, list price, "from").
_NOT_ITEM_BEFORE = re.compile(
    r"(spedizion\w*|shipping|postage|spese|consegna|delivery|versand\w*|env[ií]o|envoi|livraison|"
    r"listino|was|rrp|prezzo originale|invece di|anzich[eé]|statt|au lieu de|"
    r"\bda|\bfrom|a partire da|\bab|desde|[àa] partir de|risparmi\w*|save|sconto|off)\W{0,3}$",
    re.IGNORECASE,
)
_NOT_ITEM_AFTER = re.compile(
    r"^\W{0,3}(di spedizione|spedizione|per la spedizione|shipping|postage|delivery|consegna|"
    r"versand|livraison|env[ií]o|di sconto|off\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PriceHit:
    amount: Decimal
    currency: str | None  # None: a currency we do not know (A$, kr, ¥...)
    text: str
    start: int
    end: int


def to_decimal(raw: str) -> Decimal | None:
    """ "1.234,56" / "1,234.56" / "59,99" / "59.99" / "1 234" -> Decimal."""
    s = raw.replace(" ", "").replace(" ", "").strip()
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in s:
        tail = s.rpartition(",")[2]
        s = s.replace(",", "") if len(tail) == 3 else s.replace(",", ".")
    elif "." in s:
        tail = s.rpartition(".")[2]
        if len(tail) == 3 or s.count(".") > 1:
            s = s.replace(".", "")
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _currency(token: str) -> str | None:
    t = re.sub(r"\s+", " ", token.strip().lower())
    return _CURRENCY.get(t)


def find_prices(text: str | None) -> list[PriceHit]:
    """Every price in ``text`` with its currency (``None`` when the currency is not one we know)."""
    out: list[PriceHit] = []
    for m in _PRICE_RE.finditer(text or ""):
        cur, num = (m.group("pre"), m.group("n1")) if m.group("pre") else (m.group("post"), m.group("n2"))
        amount = to_decimal(num)
        if amount is None:
            continue
        out.append(PriceHit(amount, _currency(cur), m.group(0), m.start(), m.end()))
    return out


def item_price(text: str | None) -> PriceHit | None:
    """The price of the item in a title/snippet: the first one that is not a shipping cost, a list
    price or a "from" price (a range on a listing page)."""
    text = text or ""
    for hit in find_prices(text):
        before = text[max(0, hit.start - 30) : hit.start]
        after = text[hit.end : hit.end + 30]
        if _NOT_ITEM_BEFORE.search(before) or _NOT_ITEM_AFTER.search(after):
            continue
        return hit
    return None


def parse_price(text: str | None) -> PriceHit | None:
    """A single price string ("59,99 €", "€ 59.99", "EUR 45,00", "£40", "$45")."""
    hits = find_prices(text)
    return hits[0] if hits else None


# ---------------------------------------------------------------------------- dates
_MONTHS: dict[str, int] = {}
for _n, _names in enumerate(
    (
        "gen gennaio jan january janv janvier januar jän jaen ene enero",
        "feb febbraio february fevr fevrier februar febrero",
        "mar marzo march mars marz maerz",
        "apr aprile april avr avril abr abril",
        "mag maggio may mai mayo",
        "giu giugno jun june juin juni junio",
        "lug luglio jul july juil juillet juli julio",
        "ago agosto aug august aout",
        "set sett settembre sep sept september septembre septiembre setiembre",
        "ott ottobre oct october octobre okt oktober octubre",
        "nov novembre november noviembre",
        "dic dicembre dec december decembre dez dezember diciembre",
    ),
    start=1,
):
    for _name in _names.split():
        _MONTHS[fold(_name)] = _n
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DMY = re.compile(
    rf"\b(\d{{1,2}})(?:\.|º|°)?\s*(?:de\s+)?({_MONTH_ALT})(?![a-z])\.?,?(?:\s*(?:de\s+)?(\d{{4}}))?"
)
_MDY = re.compile(rf"\b({_MONTH_ALT})(?![a-z])\.?\s+(\d{{1,2}})(?!\d)(?:,?\s*(\d{{4}}))?")
_ISO = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d{2})\b")
_UNITS = (
    (r"minut\w*", timedelta(minutes=1)),
    (r"or[ae]|hours?|heures?|stunden?|horas?", timedelta(hours=1)),
    (r"giorn[oi]|days?|jours?|tag(?:en|e)?|dias?", timedelta(days=1)),
    (r"settiman[ae]|weeks?|semaines?|wochen?|semanas?", timedelta(weeks=1)),
    (r"mes[ei]|months?|mois|monat(?:en|e)?|meses", timedelta(days=30)),
    (r"ann[oi]|years?|ans?|jahr(?:en|e)?|anos?", timedelta(days=365)),
)
_UNIT_ALT = "|".join(u for u, _ in _UNITS)
_ONE = r"\d+|un[ao]?|une?|a|an|one|einem|einer|einen"
_RELATIVE = (
    re.compile(rf"\b({_ONE})\s+({_UNIT_ALT})\s+(?:fa|ago)\b"),
    re.compile(rf"\bil y a\s+({_ONE})\s+({_UNIT_ALT})\b"),
    re.compile(rf"\bvor\s+({_ONE})\s+({_UNIT_ALT})\b"),
    re.compile(rf"\bhace\s+({_ONE})\s+({_UNIT_ALT})\b"),
)
_YESTERDAY = re.compile(r"\b(ieri|yesterday|hier|gestern|ayer)\b")
_TODAY = re.compile(r"\b(oggi|today|aujourd'hui|heute|hoy)\b")


def _date(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day, 12, 0, tzinfo=UTC)
    except ValueError:
        return None


def _no_year(month: int, day: int, now: datetime) -> datetime | None:
    """A day without its year: this year, or last year when that would be in the future."""
    when = _date(now.year, month, day)
    if when is not None and when > now + timedelta(days=1):
        when = _date(now.year - 1, month, day)
    return when


def parse_date(text: str | None, now: datetime, *, allow_no_year: bool = False) -> datetime | None:
    """The first date in ``text``: "12 set 2026", "Sep 12, 2026", "12. Sept. 2026", "2026-09-12",
    "12/09/2026", "3 days ago", "3 giorni fa", "il y a 3 jours", "vor 3 Tagen", "hace 3 días",
    "ieri". Future dates (beyond a day of clock skew) are ignored."""
    t = fold(text or "")
    if not t:
        return None
    found: list[tuple[int, datetime]] = []
    for m in _DMY.finditer(t):
        day, month = int(m.group(1)), _MONTHS[m.group(2)]
        if m.group(3):
            when = _date(int(m.group(3)), month, day)
        else:
            when = _no_year(month, day, now) if allow_no_year else None
        if when:
            found.append((m.start(), when))
    for m in _MDY.finditer(t):
        month, day = _MONTHS[m.group(1)], int(m.group(2))
        if m.group(3):
            when = _date(int(m.group(3)), month, day)
        else:
            when = _no_year(month, day, now) if allow_no_year else None
        if when:
            found.append((m.start(), when))
    for m in _ISO.finditer(t):
        if when := _date(int(m.group(1)), int(m.group(2)), int(m.group(3))):
            found.append((m.start(), when))
    for m in _NUMERIC.finditer(t):
        if when := _date(int(m.group(3)), int(m.group(2)), int(m.group(1))):
            found.append((m.start(), when))
    for pattern in _RELATIVE:
        for m in pattern.finditer(t):
            count = int(m.group(1)) if m.group(1).isdigit() else 1
            unit = next(step for u, step in _UNITS if re.fullmatch(u, m.group(2)))
            found.append((m.start(), now - count * unit))
    if m := _YESTERDAY.search(t):
        found.append((m.start(), now - timedelta(days=1)))
    if m := _TODAY.search(t):
        found.append((m.start(), now))
    valid = [(pos, when) for pos, when in found if when <= now + timedelta(days=1)]
    return min(valid, key=lambda x: x[0])[1] if valid else None


# ---------------------------------------------------------------------------- sold markers
_SOLD = re.compile(
    r"\b(venduto|venduta|venduti|vendute|sold|vendu|vendue|vendus|verkauft|vendido|vendida|vendidos)\b"
)
# "venduto da" (seller), "sold out", "sold as is", "venduto con scatola": not a concluded sale.
_SOLD_NOT_AFTER = re.compile(
    r"^\s*(da|dal|dalla|by|par|von|por|out|separately|separatamente|singolarmente|individually|"
    r"as|come|cosi|con|with|senza|without|in|e spedito|and shipped|tel|wie|como)\b"
)
# "più venduti", "best sold", "20 venduti", "oltre 20 venduti", "20+ sold": not this item's sale.
_SOLD_NOT_BEFORE = re.compile(
    r"(\bpiu|\bmost|\bbest|\btop|\bmeist|\bmas|(?<!\d)\d{1,4}\+?|\boltre|\bover)\s*$"
)
_SOLD_AFTER = re.compile(r"^\s*($|[·|:\-–—!,.)\]]|(il|on|le|am|el|a|at|for|per|in data)\b|\d)")


@dataclass(frozen=True)
class SoldMarker:
    sold: bool
    date: datetime | None = None
    marker: str | None = None


def find_sold(text: str | None, now: datetime) -> SoldMarker:
    """Whether ``text`` says the item was sold ("Venduto il 12 set 2026", "Sold Sep 12, 2026",
    "Vendu le 3 sept. 2026", "Verkauft am 3. Sep. 2026", "Vendido el...") and the date of the sale
    when stated next to the marker."""
    t = fold(text or "")
    for m in _SOLD.finditer(t):
        before, after = t[max(0, m.start() - 12) : m.start()], t[m.end() :]
        if _SOLD_NOT_BEFORE.search(before) or _SOLD_NOT_AFTER.search(after):
            continue
        when = parse_date(after[:40], now, allow_no_year=True)
        if when is None and not _SOLD_AFTER.search(after):
            continue
        return SoldMarker(True, when, m.group(1))
    return SoldMarker(False)


# ---------------------------------------------------------------------------- condition, size
_USED = re.compile(
    r"\b(usat[oaie]|used|pre-?owned|pre-?loved|seconda mano|second ?hand|gebraucht|d'occasion|"
    r"occasion|usad[oa]s?|indossat[oaie]|worn|ricondizionat[oaie]|refurbished|non nuov[oaie]|not new)\b"
)
_NEW = re.compile(
    r"\b(nuov[oaie]|brand new|new(?!\s+(?:balance|era|york|look|rock))|neuf|neuve|neu|nuev[oa]s?|"
    r"deadstock|ds)\b"
)
# Precise phrases in every gender/number ("mai indossate", "come nuove") beyond the listing ones.
_PRECISE = (
    (
        re.compile(r"\b(mai (?:indossat|usat|mess)[oaie]|never worn|unworn|deadstock|ds)\b"),
        "new_without_tags",
    ),
    (re.compile(r"\b(come nuov[oaie]|pari al nuovo|like new|as new|perfette condizioni)\b"), "very_good"),
)


def detect_condition(text: str | None) -> str | None:
    """The condition stated in ``text``: the listing vocabulary (``new_with_tags`` ... ``satisfactory``)
    when a precise phrase is there, ``used`` for a generic second-hand marker, ``new_without_tags``
    for a generic "new"; ``None`` when nothing (or both new and used) is said."""
    t = fold(text or "")
    if not t:
        return None
    precise = normalize_condition(t)
    if precise != Condition.UNKNOWN:
        return precise.value
    for pattern, value in _PRECISE:
        if pattern.search(t):
            return value
    used, new = bool(_USED.search(t)), bool(_NEW.search(t))
    if used and not new:
        return "used"
    if new and not used:
        return Condition.NEW_WITHOUT_TAGS.value
    return None


_SIZE_CONTEXT = re.compile(
    r"\b(?:taglia|tg\.?|size|misura|numero|n\.|gr\.|grosse|talla|taille|eu|eur|uk|us)\s*:?\s*"
    r"(\d{1,2}(?:[.,]5)?|xxs|xs|s|m|l|xl|xxl|xxxl|2xl|3xl)\b",
    re.IGNORECASE,
)


def extract_size(text: str | None, category_slug: str | None) -> str | None:
    """The size when the text names it explicitly ("taglia 42", "EU 42.5", "UK 8", "size M")."""
    for m in _SIZE_CONTEXT.finditer(text or ""):
        size = normalize_size(m.group(0), category_slug)
        if size:
            return size[:20]
    return None


# ---------------------------------------------------------------------------- offers
SECOND_HAND_SITES = (
    "ebay.it",
    "ebay.com",
    "vestiairecollective.com",
    "depop.com",
    "grailed.com",
    "subito.it",
    "wallapop.com",
)
# Shops whose offers are second-hand asking prices (not retail "new" prices).
SECOND_HAND_SHOPS = (
    "ebay",
    "vestiaire",
    "depop",
    "grailed",
    "subito",
    "wallapop",
    "kleinanzeigen",
    "leboncoin",
    "milanuncios",
    "marktplaats",
    "tise",
    "rebelle",
    "videdressing",
)
# Resale platforms of new, unworn items: asking prices, not retail.
RESALE_SHOPS = ("stockx", "goat", "klekt", "laced", "restocks", "wethenew", "alias", "sneakit")
# Vinted is FlipFinder's own source: never stored as an external reference.
EXCLUDED_SHOPS = ("vinted",)
# Item pages of the second-hand marketplaces (search, category and catalogue pages are not items).
_ITEM_PATHS = (
    ("ebay.", re.compile(r"/itm/")),
    ("vestiairecollective.", re.compile(r"-\d{5,}\.shtml")),
    ("depop.", re.compile(r"/products/[^/]+")),
    ("grailed.", re.compile(r"/listings/\d+")),
    ("subito.", re.compile(r"-\d{6,}\.htm")),
    ("wallapop.", re.compile(r"/item/[^/]+")),
)


def domain(url: str | None) -> str:
    """``https://www.ebay.it/itm/1`` -> ``ebay.it`` ('' when not a URL)."""
    host = (urlparse(url or "").hostname or "").lower()
    for prefix in ("www.", "m.", "it.", "uk.", "de.", "fr.", "es."):
        if host.startswith(prefix) and host.count(".") > 1:
            host = host[len(prefix) :]
            break
    return host


def _is_google(host: str) -> bool:
    return host.startswith("google.") or ".google." in host or host == "google.com"


def is_item_url(url: str) -> bool:
    """Whether ``url`` is a single item of a known second-hand marketplace."""
    host = domain(url)
    path = urlparse(url).path
    return any(host.startswith(site) and pattern.search(path) for site, pattern in _ITEM_PATHS)


@dataclass
class Offer:
    """One search result read into a price candidate (kept or rejected by the matcher)."""

    endpoint: str  # shopping | search
    purpose: str  # shopping | used | sold (the query that found it)
    query: str
    position: int | None
    title: str
    url: str
    source: str  # domain, or the shop name when the link is a Google page
    snippet: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    price_text: str | None = None
    price_from: str | None = None  # field | attributes | title | snippet
    sold: bool = False
    sold_marker: str | None = None
    source_date: datetime | None = None
    condition: str | None = None  # stated condition, None if not stated
    is_item: bool = True
    second_hand: bool = False
    resale: bool = False
    excluded: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return f"{self.title} {self.snippet or ''}".strip()


def _shop_kind(source: str, shop: str | None) -> tuple[bool, bool, bool]:
    names = f"{source} {fold(shop or '')}"
    return (
        any(s in names for s in SECOND_HAND_SHOPS),
        any(s in names for s in RESALE_SHOPS),
        any(s in names for s in EXCLUDED_SHOPS),
    )


def _source(url: str, shop: str | None) -> str:
    host = domain(url)
    if host and not _is_google(host):
        return host[:80]
    return (fold(shop or "") or host or "sconosciuto")[:80]


def parse_shopping(payload: dict[str, Any], query: str, purpose: str = "shopping") -> list[Offer]:
    """``shopping[]`` of a ``/shopping`` response."""
    out: list[Offer] = []
    for item in payload.get("shopping") or []:
        title, url = str(item.get("title") or "").strip(), str(item.get("link") or "").strip()
        if not title or not url:
            continue
        shop = str(item.get("source") or "").strip() or None
        source = _source(url, shop)
        second_hand, resale, excluded = _shop_kind(source, shop)
        price_text = str(item.get("price") or "").strip() or None
        hit = parse_price(price_text)
        offer = Offer(
            endpoint="shopping",
            purpose=purpose,
            query=query,
            position=item.get("position"),
            title=title,
            url=url,
            source=source,
            price=hit.amount if hit else None,
            currency=hit.currency if hit else None,
            price_text=price_text,
            price_from="field" if hit else None,
            # "€45.00 used" / "59,99 € usato": the price row may state the condition.
            condition=detect_condition(f"{title} {price_text or ''}"),
            second_hand=second_hand,
            resale=resale,
            excluded=excluded,
            extra={"shop": shop, "product_id": item.get("productId"), "delivery": item.get("delivery")},
        )
        out.append(offer)
    return out


def _structured_price(item: dict[str, Any]) -> tuple[PriceHit | None, str | None]:
    """Price from the structured fields of an organic result (``price``/``currency``,
    ``attributes``), the most reliable place."""
    price, currency = item.get("price"), item.get("currency")
    if isinstance(price, int | float) and price > 0:
        cur = _currency(str(currency or "")) if currency else None
        text = f"{currency or ''} {price}".strip()
        return PriceHit(Decimal(str(price)), cur, text, 0, len(text)), "field"
    if isinstance(price, str) and (hit := parse_price(f"{price} {currency or ''}".strip())):
        return hit, "field"
    attributes = item.get("attributes") or {}
    if isinstance(attributes, dict):
        for key, value in attributes.items():
            if re.search(r"prezzo|price|prix|preis|precio", fold(str(key))) and (
                hit := parse_price(str(value))
            ):
                return hit, "attributes"
    return None, None


def parse_organic(payload: dict[str, Any], query: str, purpose: str, now: datetime) -> list[Offer]:
    """``organic[]`` of a ``/search`` response restricted to second-hand marketplaces."""
    out: list[Offer] = []
    for item in payload.get("organic") or []:
        title, url = str(item.get("title") or "").strip(), str(item.get("link") or "").strip()
        if not title or not url:
            continue
        snippet = str(item.get("snippet") or "").strip() or None
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        attr_text = " ".join(f"{k}: {v}" for k, v in attributes.items())
        source = _source(url, None)
        second_hand, resale, excluded = _shop_kind(source, None)
        hit, where = _structured_price(item)
        if hit is None and (hit := item_price(title)):
            where = "title"
        if hit is None and (hit := item_price(snippet)):
            where = "snippet"
        full = " ".join(x for x in (title, snippet or "", attr_text) if x)
        # Each part on its own: the end of the title is not the context of the snippet's marker.
        parts = (find_sold(x, now) for x in (title, snippet, attr_text) if x)
        sold = next((m for m in parts if m.sold), SoldMarker(False))
        stated = parse_date(str(item.get("date") or ""), now) if item.get("date") else None
        out.append(
            Offer(
                endpoint="search",
                purpose=purpose,
                query=query,
                position=item.get("position"),
                title=title,
                url=url,
                source=source,
                snippet=snippet,
                price=hit.amount if hit else None,
                currency=hit.currency if hit else None,
                price_text=hit.text
                if hit
                else (str(item.get("priceRange")) if item.get("priceRange") else None),
                price_from=where if hit else None,
                sold=sold.sold,
                sold_marker=sold.marker,
                source_date=sold.date or stated,
                condition=detect_condition(full),
                is_item=is_item_url(url),
                second_hand=second_hand or is_item_url(url),
                resale=resale,
                excluded=excluded,
                extra={"date": item.get("date"), "attributes": attributes or None},
            )
        )
    return out
