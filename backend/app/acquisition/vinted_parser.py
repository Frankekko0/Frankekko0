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

from app.acquisition.embedded import find_item, find_plugins, gallery, pick, profile_photo_urls
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


DEFAULT_ITEM_JSON: dict[str, list[str]] = {
    "photos": ["photos"],
    "photo_url": ["full_size_url", "url"],
    "profile_photo": ["photo", "avatar"],
    "markers": ["photos", "title", "favourite_count", "view_count", "is_closed", "price"],
    "favourites": ["favourite_count", "favorite_count"],
    "views": ["view_count"],
    "reserved": ["is_reserved"],
    "closed": ["is_closed"],
    "closing_action": ["item_closing_action"],
    "created": ["created_at_ts", "created_at"],
    "material": ["material", "material_title"],
    "service_fee": ["service_fee.amount", "service_fee"],
    "shipping": ["shipping_price.amount", "shipping_fee.amount", "shipping_price"],
    "seller": ["user"],
    "seller_id": ["id"],
    "seller_rating": ["feedback_reputation"],
    "seller_reviews": ["feedback_count"],
    "favourite_by_me": ["is_favourite", "is_favorite", "is_favourited"],
    # Current layout: the item object names its seller by id; the rest is in page sections
    # ("plugins") whose data carries the item's id.
    "item_seller_id": ["seller_id", "user_id"],
    "can_buy": ["can_buy"],
    "plugin_name": ["name"],
    "plugin_data": ["data"],
    "plugin_item_id": ["item_id"],
    "favourite_plugins": ["favourite"],
    "seller_plugins": ["user_info_header"],
    "buy_plugins": ["ask_seller", "buy_actions", "buy"],
    "status_plugins": ["buyer_item_status"],
    "status_text": ["title", "text"],
}

DEFAULT_ITEM_DOM: dict[str, Any] = {
    "favourite_testids": ["favourite-button", "item-favourite-button"],
    "total_price_testids": ["total-combined-price"],
    "status_testids": ["item-status"],
    "summary_testids": ["item-page-summary-plugin"],
    "exclude_testid_prefixes": ["product-item-id-"],
    "zone_tags": ["aside"],
    "zone_id_prefixes": ["sidebar", "S:"],
    "badge_max_length": 40,
}


def item_json_keys(cfg: ParserConfig) -> dict[str, list[str]]:
    """Key names of the embedded item object (``item_json`` in the shared configuration)."""
    return {**DEFAULT_ITEM_JSON, **(cfg.raw.get("item_json") or {})}


def item_dom_config(cfg: ParserConfig) -> dict[str, Any]:
    """Item-page elements read from the HTML by ``data-testid`` (``item_dom`` in the configuration)."""
    return {**DEFAULT_ITEM_DOM, **(cfg.raw.get("item_dom") or {})}


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
@dataclass(eq=False)
class _Frame:
    """An open element: whether it hides other items (cards) or the site chrome, where its text
    starts (for zones), and the text being captured for an item element."""

    tag: str
    excluded: bool
    zone_start: int | None = None
    capture: str | None = None
    buf: list[str] | None = None


class _Collector(HTMLParser):
    """Collects what the parser needs from a page: JSON-LD blocks, scripts, meta tags, canonical
    link, member links, the first heading and the visible text in document order; and, by
    ``data-testid`` (``item_dom``), the item's favourite button, its total price and its status
    badges - never inside another item's card or the site header/nav/footer."""

    SKIP = frozenset({"script", "style", "noscript", "template", "svg"})
    VOID = frozenset(
        {"meta", "link", "img", "br", "input", "hr", "source", "wbr", "area", "base", "col", "embed", "param", "track"}
    )
    CHROME = frozenset({"header", "nav", "footer"})

    def __init__(self, dom: dict[str, Any] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        dom = {**DEFAULT_ITEM_DOM, **(dom or {})}
        self.jsonld: list[str] = []
        self.scripts: list[str] = []
        self.meta: dict[str, list[str]] = {}
        self.canonical: str | None = None
        self.member_links: list[str] = []
        self.texts: list[str] = []
        self.heading: str | None = None
        self.favourites: str | None = None
        self.total_price: str | None = None
        self.status_texts: list[str] = []
        self._stack: list[str] = []
        self._frames: list[_Frame] = []
        self._capturing: list[_Frame] = []
        self._excluded_texts: set[int] = set()
        self._script_kind: str | None = None
        self._buf: list[str] = []
        self._in_h1 = False
        self._h1: list[str] = []
        self._summary_seen = False
        self._fav_ids = frozenset(dom["favourite_testids"])
        self._total_ids = frozenset(dom["total_price_testids"])
        self._status_ids = frozenset(dom["status_testids"])
        self._summary_ids = frozenset(dom["summary_testids"])
        self._exclude_prefixes = tuple(dom["exclude_testid_prefixes"])
        self._zone_tags = frozenset(dom["zone_tags"])
        self._zone_ids = tuple(dom["zone_id_prefixes"])
        self._badge_max = int(dom["badge_max_length"])

    def _badges_before_summary(self) -> None:
        """Short texts right before the item's summary in its sidebar: the status banner
        ("Venduto", "Riservato") of the current layout. Taken once, from the innermost zone."""
        self._summary_seen = True
        zone = next((f for f in reversed(self._frames) if f.zone_start is not None), None)
        if zone is None or zone.zone_start is None:
            return
        own = [
            t
            for i, t in enumerate(self.texts[zone.zone_start :], zone.zone_start)
            if i not in self._excluded_texts and len(t) <= self._badge_max
        ]
        self.status_texts.extend(own[-3:])

    def _item_element(self, testid: str, a: dict[str, str], frame: _Frame) -> None:
        if testid in self._fav_ids and self.favourites is None:
            label = a.get("aria-label", "")
            if re.search(r"\d", label):
                self.favourites = label
            else:
                frame.capture, frame.buf = "favourites", []
        elif testid in self._total_ids and self.total_price is None:
            frame.capture, frame.buf = "total_price", []
        elif testid in self._status_ids:
            frame.capture, frame.buf = "status", []
        if testid in self._summary_ids and not self._summary_seen:
            self._badges_before_summary()

    def _close(self, frame: _Frame) -> None:
        if frame.capture is None or frame.buf is None:
            return
        text = " ".join("".join(frame.buf).split())
        if frame.capture == "favourites" and self.favourites is None and re.search(r"\d", text):
            self.favourites = text
        elif frame.capture == "total_price" and self.total_price is None and text:
            self.total_price = text
        elif frame.capture == "status" and text and len(text) <= self._badge_max:
            self.status_texts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        testid = a.get("data-testid", "")
        excluded = (
            (bool(self._frames) and self._frames[-1].excluded)
            or tag in self.CHROME
            or (bool(testid) and testid.startswith(self._exclude_prefixes))
        )
        frame = _Frame(tag, excluded)
        if tag in self._zone_tags or a.get("id", "").startswith(self._zone_ids):
            frame.zone_start = len(self.texts)
        if testid and not excluded:
            self._item_element(testid, a, frame)
        if tag == "script":
            self._script_kind = "jsonld" if "ld+json" in a.get("type", "") else "script"
            self._buf = []
        elif tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop")
            if key and "content" in a:
                self.meta.setdefault(key, []).append(a["content"])
        elif tag == "link" and "canonical" in a.get("rel", ""):
            self.canonical = a.get("href")
        elif (
            tag == "a"
            and "/member/" in a.get("href", "")
            and not any(t in self._stack for t in ("header", "nav", "footer"))
        ):
            # A member link in the page header is the signed-in user, never the seller.
            self.member_links.append(a["href"])
        elif tag == "h1" and self.heading is None:
            self._in_h1 = True
        if tag not in self.VOID:
            self._stack.append(tag)
            self._frames.append(frame)
            if frame.capture is not None:
                self._capturing.append(frame)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_kind:
            body = "".join(self._buf)
            (self.jsonld if self._script_kind == "jsonld" else self.scripts).append(body)
            self._script_kind = None
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.heading = " ".join("".join(self._h1).split()) or None
        # Close up to the matching open element (tolerates an unclosed child).
        for depth in range(len(self._stack) - 1, -1, -1):
            if self._stack[depth] == tag:
                while len(self._stack) > depth:
                    self._stack.pop()
                    frame = self._frames.pop()
                    if frame.capture is not None:
                        self._capturing.remove(frame)
                        self._close(frame)
                break

    def handle_data(self, data: str) -> None:
        if self._script_kind:
            self._buf.append(data)
            return
        if any(t in self.SKIP for t in self._stack):
            return
        if self._in_h1:
            self._h1.append(data)
        for frame in self._capturing:
            if frame.buf is not None:
                frame.buf.append(data)
        text = " ".join(data.split())
        if text:
            if self._frames and self._frames[-1].excluded:
                self._excluded_texts.add(len(self.texts))
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
    # Whether the page offers the item for sale to the visitor. Informational only: it is also
    # false for your own items, when signed out or reserved - never a sign of a sale.
    can_buy: bool | None = None
    images: list[str] = field(default_factory=list)
    # Where the photos come from: item_json (the item's gallery) is authoritative.
    images_source: str = "none"
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
            images_authoritative=self.images_source == "item_json" and bool(self.images),
            raw={
                "source": "vinted_page",
                "parser": load_config().version,
                "fields": self.sources,
                "images_source": self.images_source,
            },
        )


def _protection_fee(total: Decimal, price: Decimal) -> Decimal | None:
    """Buyer protection = total - price, when plausible (positive, at most 20% + 5)."""
    diff = total - price
    return diff.quantize(Decimal("0.01")) if Decimal("0") < diff <= price * Decimal("0.2") + Decimal("5") else None


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
    c = _Collector(item_dom_config(cfg))
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
    # The item's own embedded object (never values of the signed-in user, the seller's profile
    # or suggested items that the same scripts contain).
    keys = item_json_keys(cfg)
    obj = find_item(c.scripts, item.vinted_id or "", keys["photos"], keys["markers"])
    if obj is not None:
        item.sources.append("item_json")
    # Current layout: the item's page sections (seller header, favourites, status banner, buy
    # actions), each read only when its data carries the item's id.
    groups = ("favourite_plugins", "seller_plugins", "buy_plugins", "status_plugins")
    found = find_plugins(
        c.scripts,
        item.vinted_id or "",
        [n for g in groups for n in keys[g]],
        keys["plugin_name"],
        keys["plugin_data"],
        keys["plugin_item_id"],
    )
    plugin = {g: next((found[n] for n in keys[g] if n in found), None) for g in groups}
    if found:
        item.sources.append("plugins")

    def emb(name: str, *sources: dict[str, Any] | None) -> str | None:
        """First scalar value of ``name``'s keys in the item object (or the given sections)."""
        for src in sources or (obj,):
            v = pick(src, keys[name])
            if v is not None and not isinstance(v, dict | list):
                return str(v).lower() if isinstance(v, bool) else str(v)
        return None

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
    if item.material is None and (mat := emb("material")):
        item.material = _unescape_json_string(mat)
    item.category_path = _breadcrumbs(c.jsonld)

    # Demand signals.
    # Favourites: the item object, else the item's favourites section, else the label pair
    # (older pages) or the item's own favourite button ("Aggiunto ai preferiti da 78 utenti").
    fav = (
        emb("favourites", obj, plugin["favourite_plugins"])
        or re.sub(r"\D", "", pairs.get("favourites", ""))
        or re.sub(r"\D", "", c.favourites or "")
        or None
    )
    views = emb("views") or re.sub(r"\D", "", pairs.get("views", "")) or None
    item.favourite_count = int(fav) if fav else None
    item.view_count = int(views) if views else None
    if any(x is not None for x in (item.favourite_count, item.view_count)):
        item.sources.append("demand")

    # Publication date.
    created = emb("created")
    if created:
        try:
            item.published_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            item.published_at = None
    if item.published_at is None:
        item.published_at = relative_time(pairs.get("uploaded"), now, cfg)

    # Fees: protection = total - price. The item's total price element ("19,60 €" next to
    # "incl. la commissione Vinted"), else a text like "€19,60 include la Protezione acquisti".
    if item.price:
        if c.total_price and (hit := find_price(c.total_price, cfg)):
            item.buyer_protection_fee = _protection_fee(hit[0], item.price)
        for t in c.texts[:600] if item.buyer_protection_fee is None else []:
            if cfg.patterns["protection_included"].search(t) and (hit := find_price(t, cfg)):
                if (fee := _protection_fee(hit[0], item.price)) is not None:
                    item.buyer_protection_fee = fee
                    break
        if item.buyer_protection_fee is None and (fee := emb("service_fee")):
            item.buyer_protection_fee = parse_price(fee)
    if (ship := emb("shipping")) is not None:
        item.shipping_fee = parse_price(ship)

    # Status: embedded flags (older pages: is_closed / item_closing_action; current pages: the
    # item's status banner section, e.g. "Venduto"), schema.org availability, then the item's
    # own status badge. "can_buy" is never read as a sale: it is also false for your own items,
    # when signed out or reserved, and a false sale would corrupt the concluded sales.
    action, closed = emb("closing_action"), emb("closed")
    reserved = emb("reserved", obj, plugin["buy_plugins"])
    banner = emb("status_text", plugin["status_plugins"]) or ""
    badges = " | ".join(c.status_texts)
    availability = str(offer.get("availability") or "")
    page_text = " ".join(c.texts[:250])
    if action == "sold" or (closed == "true" and action in (None, "sold")):
        item.status, item.status_source = ListingStatus.SOLD, "embedded"
    elif closed == "true":
        item.status, item.status_source = ListingStatus.REMOVED, "embedded"
    elif cfg.patterns["status_sold"].search(banner):
        item.status, item.status_source = ListingStatus.SOLD, "embedded"
    elif reserved == "true" or cfg.patterns["status_reserved"].search(banner):
        item.status, item.status_source = ListingStatus.RESERVED, "embedded"
    elif "SoldOut" in availability:
        item.status, item.status_source = ListingStatus.SOLD, "jsonld"
    elif product is None and cfg.patterns["status_removed"].search(page_text):
        item.status, item.status_source = ListingStatus.REMOVED, "text"
    elif closed == "false" or "InStock" in availability:
        item.status, item.status_source = ListingStatus.ACTIVE, "embedded" if closed else "jsonld"
    elif cfg.patterns["status_sold"].search(badges):
        item.status, item.status_source = ListingStatus.SOLD, "text"
    elif cfg.patterns["status_reserved"].search(badges):
        item.status, item.status_source = ListingStatus.RESERVED, "text"
    can_buy = emb("can_buy", obj, plugin["buy_plugins"])
    item.can_buy = {"true": True, "false": False}.get(can_buy or "")

    # Photos: only the item's gallery, in its order. Structured data of the item first; the
    # product's JSON-LD or its preview image only when the page has no gallery object. Profile
    # photos found anywhere in the page are excluded whatever their source.
    images = gallery(obj, keys["photos"], keys["photo_url"]) if obj else []
    item.images_source = "item_json" if images else "none"
    if not images:
        ld_images = (product or {}).get("image")
        for img in ld_images if isinstance(ld_images, list) else [ld_images]:
            u = img.get("url") if isinstance(img, dict) else img
            if isinstance(u, str):
                images.append(u)
        item.images_source = "jsonld" if images else "none"
    if not images and (og := _first(c.meta, "og:image")):
        images.append(og)
        item.images_source = "meta"
    avatars = profile_photo_urls(c.scripts, keys["profile_photo"], keys["photo_url"])
    item.images = list(
        dict.fromkeys(u for u in images if u.startswith(("http://", "https://")) and u not in avatars)
    )[:20]

    # Seller: opaque key, rating, review count. Nothing else. From the item's own seller object
    # (older pages), else the item's seller id and its seller header section (current pages); a
    # member link in the page body only when the page has no structured data.
    seller = pick(obj, keys["seller"])
    seller = seller if isinstance(seller, dict) else None
    header = plugin["seller_plugins"]
    member = str(pick(seller, keys["seller_id"]) or "") or None
    if member is None:
        member = emb("item_seller_id", obj, header)
    if seller is None and header is not None:
        header_id = pick(header, keys["item_seller_id"])
        if header_id is None or member is None or str(header_id) == member:
            seller = header
    if member is None and obj is None and not found:
        member = next(
            (mm.group(1) for href in c.member_links if (mm := cfg.patterns["member_id"].search(href))), None
        )
    item.seller_key = seller_key(member) if member else None

    def semb(name: str) -> str | None:
        v = pick(seller, keys[name])
        return None if v is None or isinstance(v, dict | list | bool) else str(v)

    count = semb("seller_reviews")
    item.seller_review_count = int(count) if count and count.isdigit() else None
    rep = semb("seller_rating")
    if rep and item.seller_review_count != 0:  # no reviews yet: no rating (Vinted says 0)
        try:
            value = Decimal(rep)
            item.seller_rating = (value * 5 if value <= 1 else value).quantize(Decimal("0.01"))
        except InvalidOperation:
            pass
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
