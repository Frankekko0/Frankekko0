"""Vinted page parsing for the server side (opt-in public fetch) and notification emails.

Every selector, label and pattern lives in ``vinted_parser.json``, the same file the browser
extension uses: when Vinted changes its pages, that file is the one place to fix. This module
only holds the extraction logic (standard library only, no HTML engine):

1. structured data: JSON-LD ``Product``, Open Graph / product meta tags;
2. JSON embedded in the page scripts (favourites, views, status, seller rating, photos);
3. visible "label / value" pairs (Brand, Taglia, Condizioni, Colore, Materiale, Caricato…).

No personal data is kept: the seller is reduced to an opaque hash, its rating and review count.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.domain.enums import CaptureLevel, ListingStatus
from app.identification.taxonomy import fold
from app.marketplace.base import ProviderImage, ProviderListing, ProviderSeller

DEFAULT_CONFIG = Path(__file__).with_name("vinted_parser.json")


# ------------------------------------------------------------------ configuration
@dataclass(frozen=True)
class ParserConfig:
    raw: dict[str, Any]
    patterns: dict[str, re.Pattern[str]]
    labels: dict[str, frozenset[str]]
    conditions: tuple[tuple[str, re.Pattern[str]], ...]
    currencies: tuple[tuple[str, re.Pattern[str]], ...]
    units: dict[str, str]

    @property
    def version(self) -> str:
        return str(self.raw.get("version", "?"))


def _compile(source: str, flags: str = "") -> re.Pattern[str]:
    return re.compile(source, re.IGNORECASE if "i" in flags else 0)


def build_config(raw: dict[str, Any]) -> ParserConfig:
    units: dict[str, str] = {}
    for unit, words in raw.get("relative_units", {}).items():
        for w in words:
            units[fold(w)] = unit
    return ParserConfig(
        raw=raw,
        patterns={k: _compile(p["source"], p.get("flags", "")) for k, p in raw["patterns"].items()},
        labels={k: frozenset(fold(w) for w in v) for k, v in raw["labels"].items()},
        conditions=tuple((name, _compile(rx, "i")) for name, rx in raw["conditions"]),
        currencies=tuple((code, _compile(rx, "i")) for code, rx in raw["currencies"]),
        units=units,
    )


@lru_cache(maxsize=1)
def load_config() -> ParserConfig:
    """The parser configuration: ``PARSER_CONFIG_PATH`` when set (e.g. a mounted, updated file),
    otherwise the copy shipped with the application."""
    override = get_settings().parser_config_path
    path = Path(override) if override else DEFAULT_CONFIG
    return build_config(json.loads(path.read_text(encoding="utf-8")))


def config_json() -> dict[str, Any]:
    return load_config().raw


# ------------------------------------------------------------------ small helpers
def parse_price(value: Any) -> Decimal | None:
    """ "18,00 €", "€1.234,50", "18.5", 18 -> Decimal; None when not a price."""
    if isinstance(value, int | float | Decimal):
        d = Decimal(str(value))
        return d.quantize(Decimal("0.01")) if d > 0 else None
    if not isinstance(value, str):
        return None
    s = re.sub(r"[^\d.,]", "", value)
    if not s:
        return None
    last_comma, last_dot = s.rfind(","), s.rfind(".")
    if last_comma > -1 and last_dot > -1:
        decimal_sep = "," if last_comma > last_dot else "."
        thousands = "." if decimal_sep == "," else ","
        s = s.replace(thousands, "").replace(decimal_sep, ".")
    elif last_comma > -1:
        s = s.replace(".", "").replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    elif last_dot > -1 and not re.search(r"\.\d{1,2}$", s):
        s = s.replace(".", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d.quantize(Decimal("0.01")) if d > 0 else None


def find_price(text: str, cfg: ParserConfig) -> tuple[Decimal, str] | None:
    m = cfg.patterns["price_token"].search(text or "")
    if not m:
        return None
    token = re.sub(r"[\s  ](?=\d{3}\b)", "", m.group(0))
    price = parse_price(token)
    if price is None:
        return None
    currency = next((code for code, rx in cfg.currencies if rx.search(m.group(0))), "")
    return price, currency


def normalize_condition(text: str | None, cfg: ParserConfig) -> str | None:
    if not text:
        return None
    for name, rx in cfg.conditions:
        if rx.search(text):
            return name
    return None


def label_key(label: str, cfg: ParserConfig) -> str | None:
    key = fold(label).rstrip(":：").strip()
    for name, words in cfg.labels.items():
        if key in words:
            return name
    return None


def relative_time(text: str | None, now: datetime, cfg: ParserConfig) -> datetime | None:
    """ "3 giorni fa", "2 hours ago", "ieri", "il y a 5 minutes" -> approximate datetime."""
    if not text:
        return None
    t = fold(text)
    if cfg.patterns["relative_now"].search(t):
        return now
    if cfg.patterns["relative_yesterday"].search(t):
        return now - timedelta(days=1)
    m = cfg.patterns["relative_amount"].search(t)
    if not m:
        return None
    amount, word = int(m.group(1)), m.group(2)
    unit = cfg.units.get(word) or next(
        (u for w, u in cfg.units.items() if word.startswith(w) and len(w) >= 3), None
    )
    delta = {
        "minute": timedelta(minutes=amount),
        "hour": timedelta(hours=amount),
        "day": timedelta(days=amount),
        "week": timedelta(weeks=amount),
        "month": timedelta(days=30 * amount),
        "year": timedelta(days=365 * amount),
    }.get(unit or "")
    return now - delta if delta else None


def seller_key(member_id: str) -> str:
    """One-way hash of the Vinted member id: groups a seller's listings, reveals nothing."""
    return "h:" + hashlib.sha256(f"vinted-member:{member_id}".encode()).hexdigest()[:24]


def _unescape_json_string(s: str) -> str:
    """Undo the JSON escaping of strings found inside script payloads (possibly nested)."""
    return re.sub(r"\\+/", "/", s).replace("\\u0026", "&")


# ------------------------------------------------------------------ HTML collection
class _Collector(HTMLParser):
    """Collects what the parser needs from a page: JSON-LD blocks, scripts, meta tags, canonical
    link, member links, the first heading and the visible text in document order."""

    SKIP = frozenset({"script", "style", "noscript", "template", "svg"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.scripts: list[str] = []
        self.meta: dict[str, list[str]] = {}
        self.canonical: str | None = None
        self.member_links: list[str] = []
        self.texts: list[str] = []
        self.heading: str | None = None
        self._stack: list[str] = []
        self._script_kind: str | None = None
        self._buf: list[str] = []
        self._in_h1 = False
        self._h1: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "script":
            self._script_kind = "jsonld" if "ld+json" in a.get("type", "") else "script"
            self._buf = []
        elif tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop")
            if key and "content" in a:
                self.meta.setdefault(key, []).append(a["content"])
        elif tag == "link" and "canonical" in a.get("rel", ""):
            self.canonical = a.get("href")
        elif tag == "a" and "/member/" in a.get("href", ""):
            self.member_links.append(a["href"])
        elif tag == "h1" and self.heading is None:
            self._in_h1 = True
        if tag not in ("meta", "link", "img", "br", "input", "hr"):
            self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_kind:
            body = "".join(self._buf)
            (self.jsonld if self._script_kind == "jsonld" else self.scripts).append(body)
            self._script_kind = None
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.heading = " ".join("".join(self._h1).split()) or None
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._script_kind:
            self._buf.append(data)
            return
        if any(t in self.SKIP for t in self._stack):
            return
        if self._in_h1:
            self._h1.append(data)
        text = " ".join(data.split())
        if text:
            self.texts.append(text)


def _products(blocks: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for n in node:
                visit(n)
        elif isinstance(node, dict):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if any(isinstance(t, str) and "product" in t.lower() for t in types):
                out.append(node)
            if "@graph" in node:
                visit(node["@graph"])

    for b in blocks:
        try:
            visit(json.loads(b))
        except (ValueError, TypeError):
            continue
    return out


def _breadcrumbs(blocks: list[str]) -> list[str]:
    for b in blocks:
        try:
            data = json.loads(b)
        except (ValueError, TypeError):
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") == "BreadcrumbList":
                items = node.get("itemListElement") or []
                names = [
                    str((i.get("item") or {}).get("name") or i.get("name") or "")
                    for i in items
                    if isinstance(i, dict)
                ]
                return [n for n in names if n][:6]
    return []


# ------------------------------------------------------------------ item page
@dataclass
class ParsedItem:
    vinted_id: str | None
    url: str
    title: str = ""
    description: str = ""
    price: Decimal | None = None
    currency: str = "EUR"
    brand: str | None = None
    size: str | None = None
    condition_label: str | None = None
    condition: str | None = None
    color: str | None = None
    material: str | None = None
    category_path: list[str] = field(default_factory=list)
    favourite_count: int | None = None
    view_count: int | None = None
    published_at: datetime | None = None
    status: ListingStatus = ListingStatus.ACTIVE
    status_source: str = "default"
    buyer_protection_fee: Decimal | None = None
    shipping_fee: Decimal | None = None
    images: list[str] = field(default_factory=list)
    seller_key: str | None = None
    seller_rating: Decimal | None = None
    seller_review_count: int | None = None
    sources: list[str] = field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        return [k for k in ("title", "price") if not getattr(self, k)]

    @property
    def complete(self) -> bool:
        return not self.missing and self.vinted_id is not None

    def to_provider_listing(self) -> ProviderListing:
        if not self.complete:
            raise ValueError(f"incomplete item: missing {', '.join(self.missing) or 'id'}")
        seller = (
            ProviderSeller(
                external_id=self.seller_key,
                rating=self.seller_rating,
                review_count=self.seller_review_count or 0,
            )
            if self.seller_key
            else None
        )
        return ProviderListing(
            external_id=self.vinted_id or "",
            url=self.url,
            title=self.title[:300],
            description=self.description,
            price=self.price or Decimal(0),
            currency=self.currency,
            brand=self.brand,
            category=" > ".join(self.category_path) if self.category_path else None,
            size=self.size,
            condition=self.condition_label or self.condition,
            color=self.color,
            material=self.material,
            images=[ProviderImage(url=u) for u in self.images[:20]],
            seller=seller,
            published_at=self.published_at,
            status=self.status,
            buyer_protection_fee=self.buyer_protection_fee,
            shipping_fee=self.shipping_fee,
            favourite_count=self.favourite_count,
            view_count=self.view_count,
            capture_level=CaptureLevel.FULL,
            raw={"source": "vinted_page", "parser": load_config().version, "fields": self.sources},
        )


def _first(meta: dict[str, list[str]], key: str) -> str | None:
    values = meta.get(key) or []
    return values[0] if values else None


def _label_pairs(texts: list[str], cfg: ParserConfig) -> dict[str, str]:
    """ "Brand" followed by "Ralph Lauren" in reading order -> {"brand": "Ralph Lauren"}."""
    found: dict[str, str] = {}
    for i, t in enumerate(texts[:-1]):
        if len(t) > 30:
            continue
        key = label_key(t, cfg)
        if key and key not in found:
            value = texts[i + 1]
            if 0 < len(value) <= 120 and label_key(value, cfg) is None:
                found[key] = value
    return found


def parse_item_html(html: str, url: str, now: datetime | None = None) -> ParsedItem:
    cfg = load_config()
    now = now or datetime.now(UTC)
    c = _Collector()
    c.feed(html)
    c.close()
    id_rx = cfg.patterns["item_id"]
    m = id_rx.search(url) or (id_rx.search(c.canonical or "") if c.canonical else None)
    item = ParsedItem(vinted_id=m.group(1) if m else None, url=url.split("?")[0].split("#")[0])
    product = (_products(c.jsonld) or [None])[0]
    offer: dict[str, Any] = {}
    if product:
        offers = product.get("offers")
        offer = (offers[0] if isinstance(offers, list) and offers else offers) or {}
        item.sources.append("jsonld")
    scripts = "\n".join(c.scripts)

    def emb(name: str) -> str | None:
        hit = cfg.patterns[name].search(scripts)
        return hit.group(1) if hit else None

    # Title, price, currency.
    title = (product or {}).get("name") or c.heading or _first(c.meta, "og:title") or ""
    item.title = cfg.patterns["site_suffix"].sub("", " ".join(str(title).split()))[:300]
    price = parse_price(offer.get("price")) if offer.get("price") is not None else None
    price = (
        price
        or parse_price(_first(c.meta, "product:price:amount"))
        or parse_price(_first(c.meta, "og:price:amount"))
    )
    if price is None:
        hit = next((p for t in c.texts[:400] if (p := find_price(t, cfg))), None)
        if hit:
            price, item.currency = hit
    item.price = price
    currency = offer.get("priceCurrency") or _first(c.meta, "product:price:currency")
    if currency:
        item.currency = str(currency).upper()[:3]

    # Description, brand, attributes.
    item.description = str(
        (product or {}).get("description") or _first(c.meta, "og:description") or ""
    ).strip()[:5000]
    brand = (product or {}).get("brand")
    pairs = _label_pairs(c.texts, cfg)
    if pairs:
        item.sources.append("labels")
    item.brand = (brand.get("name") if isinstance(brand, dict) else brand) or pairs.get("brand")
    item.size = pairs.get("size")
    item.condition_label = pairs.get("condition")
    item.condition = normalize_condition(item.condition_label, cfg)
    item.color = pairs.get("color") or (product or {}).get("color")
    item.material = pairs.get("material") or (product or {}).get("material")
    if item.material is None and (mat := emb("embedded_material")):
        item.material = _unescape_json_string(mat)
    item.category_path = _breadcrumbs(c.jsonld)

    # Demand signals.
    fav = emb("embedded_favourites") or re.sub(r"\D", "", pairs.get("favourites", "")) or None
    views = emb("embedded_views") or re.sub(r"\D", "", pairs.get("views", "")) or None
    item.favourite_count = int(fav) if fav else None
    item.view_count = int(views) if views else None
    if any(x is not None for x in (item.favourite_count, item.view_count)):
        item.sources.append("demand")

    # Publication date.
    created = emb("embedded_created")
    if created:
        try:
            item.published_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            item.published_at = None
    if item.published_at is None:
        item.published_at = relative_time(pairs.get("uploaded"), now, cfg)

    # Fees: "€19,60 include la Protezione acquisti" -> protection = total - price.
    if item.price:
        for t in c.texts[:600]:
            if cfg.patterns["protection_included"].search(t) and (hit := find_price(t, cfg)):
                total = hit[0]
                diff = total - item.price
                if Decimal("0") < diff <= item.price * Decimal("0.2") + Decimal("5"):
                    item.buyer_protection_fee = diff.quantize(Decimal("0.01"))
                    break
        if item.buyer_protection_fee is None and (fee := emb("embedded_service_fee")):
            item.buyer_protection_fee = parse_price(fee)
    if (ship := emb("embedded_shipping")) is not None:
        item.shipping_fee = parse_price(ship)

    # Status: embedded flags, schema.org availability, then visible badges.
    action, closed, reserved = (
        emb("embedded_closing_action"),
        emb("embedded_closed"),
        emb("embedded_reserved"),
    )
    availability = str(offer.get("availability") or "")
    page_text = " ".join(c.texts[:250])
    if action == "sold" or (closed == "true" and action in (None, "sold")):
        item.status, item.status_source = ListingStatus.SOLD, "embedded"
    elif closed == "true":
        item.status, item.status_source = ListingStatus.REMOVED, "embedded"
    elif reserved == "true":
        item.status, item.status_source = ListingStatus.RESERVED, "embedded"
    elif "SoldOut" in availability:
        item.status, item.status_source = ListingStatus.SOLD, "jsonld"
    elif product is None and cfg.patterns["status_removed"].search(page_text):
        item.status, item.status_source = ListingStatus.REMOVED, "text"
    elif closed == "false" or "InStock" in availability:
        item.status, item.status_source = ListingStatus.ACTIVE, "embedded" if closed else "jsonld"
    elif cfg.patterns["status_sold"].search(" ".join(c.texts[:60])):
        item.status, item.status_source = ListingStatus.SOLD, "text"
    elif cfg.patterns["status_reserved"].search(" ".join(c.texts[:60])):
        item.status, item.status_source = ListingStatus.RESERVED, "text"

    # Photos, in the page's order.
    images: list[str] = []
    for raw_url in cfg.patterns["embedded_photo"].findall(scripts):
        images.append(_unescape_json_string(raw_url))
    ld_images = (product or {}).get("image")
    for img in ld_images if isinstance(ld_images, list) else [ld_images]:
        u = img.get("url") if isinstance(img, dict) else img
        if isinstance(u, str):
            images.append(u)
    images += c.meta.get("og:image", [])
    item.images = list(dict.fromkeys(u for u in images if u.startswith(("http://", "https://"))))[:20]

    # Seller: opaque key, rating, review count. Nothing else.
    member = next(
        (mm.group(1) for href in c.member_links if (mm := cfg.patterns["member_id"].search(href))), None
    )
    item.seller_key = seller_key(member) if member else None
    rep = emb("embedded_feedback_reputation")
    if rep:
        try:
            value = Decimal(rep)
            item.seller_rating = (value * 5 if value <= 1 else value).quantize(Decimal("0.01"))
        except InvalidOperation:
            pass
    count = emb("embedded_feedback_count")
    item.seller_review_count = int(count) if count else None
    item.title = unescape(item.title)
    return item


# ------------------------------------------------------------------ notification emails
@dataclass(frozen=True)
class EmailItem:
    kind: str  # sold | price_drop | new_item | other
    vinted_id: str
    url: str
    price: Decimal | None = None
    title: str | None = None


_HREF = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")


def classify_email(subject: str, body_text: str) -> str:
    cfg = load_config()
    text = f"{subject}\n{body_text[:2000]}"
    if cfg.patterns["email_sold"].search(text):
        return "sold"
    if cfg.patterns["email_price_drop"].search(text):
        return "price_drop"
    if cfg.patterns["email_new_items"].search(text):
        return "new_item"
    return "other"


def items_in_email(sender: str, subject: str, html: str, text: str) -> list[EmailItem]:
    """Vinted item links in a notification email, with the price written next to each when any."""
    cfg = load_config()
    if not cfg.patterns["email_sender"].search(sender or ""):
        return []
    plain = text or unescape(_TAGS.sub(" ", html or ""))
    kind = classify_email(subject, plain)
    # (link, position in the html) - the price is usually written right after the item's link.
    links = [(unescape(m.group(1)), m.end()) for m in _HREF.finditer(html or "")]
    links += [(u, -1) for u in re.findall(r"https?://[^\s<>\"']+", text or "")]
    out: dict[str, EmailItem] = {}
    for link, pos in links:
        m = cfg.patterns["item_id"].search(link)
        if not m or "vinted." not in link:
            continue
        vid = m.group(1)
        if vid in out:
            continue
        price = None
        if pos >= 0:
            window = unescape(_TAGS.sub(" ", html[pos : pos + 1500]))
            if hit := find_price(window, cfg):
                price = hit[0]
        clean = link.split("?")[0].split("#")[0]
        out[vid] = EmailItem(kind=kind, vinted_id=vid, url=clean, price=price)
    return list(out.values())
