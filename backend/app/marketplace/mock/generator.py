"""Deterministic simulation of a second-hand fashion marketplace.

The simulated market is a *pure function of (seed, listing index)*: every process (API,
worker, tests) reconstructs exactly the same listings, sellers, price drops and sales without
shared state. Listing ``k`` is published at a deterministic time; its whole lifecycle (price
drops, sale, removal) is precomputed from its own random stream, and ``state_at(now)`` reveals
only what an observer could see at ``now``.

The stream contains the situations a reseller meets in real life: fairly priced items,
underpriced deals, overpriced items that later drop, counterfeits priced too good to be true,
absurd outlier prices, reposts of the same item, stolen photos, incomplete titles ("Polo blu
uomo") and declared defects.
"""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache

from app.domain.enums import ListingStatus
from app.identification.taxonomy import DEFAULT_TAXONOMY, FOOTBALL_TEAMS
from app.marketplace.base import ProviderImage, ProviderListing, ProviderSeller
from app.marketplace.mock.archetypes import ARCHETYPES, FOOTBALL_KITS, Archetype

MASK64 = (1 << 64) - 1
EXTERNAL_ID_BASE = 4_100_000_000
SELLER_POOL = 2500

COLOR_IT = {
    "black": ["nero", "nera"],
    "white": ["bianco", "bianca"],
    "navy": ["blu navy", "blu"],
    "blue": ["blu", "blu royal"],
    "light_blue": ["azzurro", "celeste"],
    "red": ["rosso", "rossa"],
    "burgundy": ["bordeaux"],
    "green": ["verde"],
    "khaki": ["verde militare", "khaki"],
    "grey": ["grigio", "grigia"],
    "beige": ["beige"],
    "brown": ["marrone"],
    "yellow": ["giallo", "gialla"],
    "orange": ["arancione"],
    "pink": ["rosa"],
    "purple": ["viola"],
    "multi": ["multicolore", "fantasia"],
}
CATEGORY_WORDS = {
    "polo-shirts": ["Polo", "Polo", "Maglia polo"],
    "hoodies": ["Felpa con cappuccio", "Hoodie", "Felpa cappuccio"],
    "sweatshirts": ["Felpa", "Sweatshirt", "Felpa girocollo"],
    "knitwear": ["Maglione", "Pullover", "Maglione lana"],
    "shirts": ["Camicia", "Camicia", "Shirt"],
    "t-shirts": ["T-shirt", "Maglietta", "Tee"],
    "football-shirts": ["Maglia", "Maglia calcio", "Football shirt"],
    "jackets": ["Giacca", "Giubbotto", "Jacket"],
    "coats": ["Cappotto", "Trench"],
    "puffer-jackets": ["Piumino", "Puffer", "Piumino"],
    "fleece": ["Pile", "Fleece", "Pile"],
    "jeans": ["Jeans", "Jeans"],
    "trousers": ["Pantaloni", "Pantaloni", "Pants"],
    "tracksuits": ["Tuta", "Giacca tuta", "Track jacket"],
    "sneakers": ["Sneakers", "Scarpe", "Sneakers"],
    "belts": ["Cintura"],
    "scarves": ["Sciarpa"],
}
CONDITION_RAW = {
    "new_with_tags": "Nuovo con cartellino",
    "new_without_tags": "Nuovo senza cartellino",
    "very_good": "Ottime condizioni",
    "good": "Buone condizioni",
    "satisfactory": "Discrete condizioni",
}
CONDITION_MULT = {
    "new_with_tags": 1.18,
    "new_without_tags": 1.08,
    "very_good": 1.0,
    "good": 0.86,
    "satisfactory": 0.66,
}
COUNTRIES = (("IT", 45), ("FR", 20), ("ES", 15), ("DE", 10), ("BE", 4), ("NL", 3), ("PT", 3))
LETTER_SIZES = (("XS", 5), ("S", 20), ("M", 32), ("L", 26), ("XL", 12), ("XXL", 5))
SHOE_SIZES = tuple(
    (str(s), w)
    for s, w in ((38, 3), (39, 5), (40, 9), (41, 12), (42, 15), (43, 14), (44, 11), (45, 7), (46, 4))
)
WAIST_SIZES = tuple(
    (f"W{s}", w) for s, w in ((28, 6), (29, 7), (30, 12), (31, 12), (32, 15), (33, 10), (34, 10), (36, 6))
)
SIZE_LABEL = {
    "XS": "XS / 34 / 6",
    "S": "S / 36 / 8",
    "M": "M / 38 / 10",
    "L": "L / 40 / 12",
    "XL": "XL / 42 / 14",
    "XXL": "XXL / 44 / 16",
}

DEFECT_LINES = (
    "Piccola macchia sulla manica, visibile in foto.",
    "Leggero pilling sui fianchi.",
    "Piccolo buco vicino al polsino.",
    "Colore leggermente sbiadito dai lavaggi.",
    "Lieve difetto sulla cucitura interna.",
)
FAKE_LINES = (
    "Qualità AAA, identica all'originale.",
    "Simile all'originale, ottima fattura.",
    "Replica 1:1 di ottima qualità.",
    "Non originale ma identica, prezzo affare.",
)
FILLER_LINES = (
    "Spedisco entro 24 ore.",
    "Prezzo leggermente trattabile.",
    "Disponibile per foto aggiuntive.",
    "Proveniente da casa senza animali e non fumatori.",
    "Lavato e pronto da indossare.",
    "Sconto se acquisti più articoli.",
)
EN_LINES = ("Great condition, barely worn.", "Ships fast, well packaged.", "Authentic, bought in store.")


def splitmix64(x: int) -> int:
    x = (x + 0x9E3779B97F4A7C15) & MASK64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & MASK64
    return x ^ (x >> 31)


def unit(seed: int, *parts: int) -> float:
    h = seed & MASK64
    for p in parts:
        h = splitmix64(h ^ (p & MASK64))
    return (h >> 11) / float(1 << 53)


def _weighted(r: random.Random, options: tuple[tuple[str, float], ...] | list[tuple[str, float]]) -> str:
    total = sum(w for _, w in options)
    x = r.random() * total
    for value, w in options:
        x -= w
        if x <= 0:
            return value
    return options[-1][0]


def _round_price(value: float, r: random.Random) -> Decimal:
    if value < 5:
        return Decimal(str(max(1.0, round(value * 2) / 2)))
    if value < 20:
        return Decimal(str(round(value * 2) / 2)).quantize(Decimal("0.01"))
    base = round(value)
    style = r.random()
    if style < 0.35 and base >= 30:
        base = int(round(value / 5) * 5)
    elif style < 0.5:
        base = max(1, base - 1) if str(base).endswith("0") else base
    return Decimal(base).quantize(Decimal("0.01"))


@dataclass
class SimSeller:
    index: int
    username: str
    rating: Decimal | None
    review_count: int
    account_created_at: datetime
    item_count: int
    sold_count: int
    country: str
    is_new: bool

    def to_provider(self) -> ProviderSeller:
        return ProviderSeller(
            external_id=str(9_000_000 + self.index),
            rating=self.rating,
            review_count=self.review_count,
            account_created_at=self.account_created_at,
            item_count=self.item_count,
            sold_count=self.sold_count,
            country=self.country,
        )


@dataclass
class SimListing:
    k: int
    archetype: Archetype
    behaviour: str
    published_at: datetime
    initial_price: Decimal
    true_value: float
    title: str
    description: str
    brand_field: str | None
    category_field: str
    size_raw: str | None
    condition: str
    color_raw: str
    country: str
    seller: SimSeller
    image_seeds: list[int]
    category_slug: str
    shipping_fee: Decimal
    demand_rate: float
    price_drops: list[tuple[datetime, Decimal]] = field(default_factory=list)
    sold_at: datetime | None = None
    removed_at: datetime | None = None
    repost_of: int | None = None

    @property
    def external_id(self) -> str:
        return str(EXTERNAL_ID_BASE + self.k)

    def price_at(self, now: datetime) -> Decimal:
        price = self.initial_price
        for when, new_price in self.price_drops:
            if when <= now:
                price = new_price
        return price

    def status_at(self, now: datetime) -> ListingStatus:
        if self.sold_at and self.sold_at <= now:
            return ListingStatus.SOLD
        if self.removed_at and self.removed_at <= now:
            return ListingStatus.REMOVED
        return ListingStatus.ACTIVE

    def price_history(self, now: datetime) -> list[tuple[datetime, Decimal]]:
        points = [(self.published_at, self.initial_price)]
        points += [(t, p) for t, p in self.price_drops if t <= now]
        return points

    def to_provider(self, now: datetime) -> ProviderListing | None:
        if self.published_at > now:
            return None
        status = self.status_at(now)
        price = self.price_at(now)
        visible_until = min(t for t in (now, self.sold_at, self.removed_at) if t is not None)
        age_days = max(0.0, (visible_until - self.published_at).total_seconds() / 86400)
        favourites = int(age_days * self.demand_rate * (0.6 + unit(self.k, 77) * 0.8))
        views = int(favourites * (8 + unit(self.k, 78) * 12) + age_days * 24 * (1 + unit(self.k, 79) * 2))
        bp_fee = (Decimal("0.70") + price * Decimal("0.05")).quantize(Decimal("0.01"))
        image_url = f"/api/v1/demo/images/{self.category_slug}.svg?color={self.archetype_color}"
        images = [
            ProviderImage(
                url=f"{image_url}&v={seed % 1000}&i={i}",
                phash=f"{seed & MASK64:016x}",
                width=800,
                height=1066,
            )
            for i, seed in enumerate(self.image_seeds)
        ]
        return ProviderListing(
            external_id=self.external_id,
            url=f"https://www.vinted.it/items/{self.external_id}-demo",
            title=self.title,
            description=self.description,
            price=price,
            currency="EUR",
            brand=self.brand_field,
            category=self.category_field,
            size=self.size_raw,
            condition=CONDITION_RAW.get(self.condition),
            color=self.color_raw,
            images=images,
            seller=self.seller.to_provider(),
            country=self.country,
            published_at=self.published_at,
            status=status,
            sold_at=self.sold_at if status == ListingStatus.SOLD else None,
            buyer_protection_fee=bp_fee,
            shipping_fee=self.shipping_fee,
            favourite_count=favourites,
            view_count=views,
            raw={"source": "mock", "demo": True},
        )

    @property
    def archetype_color(self) -> str:
        return self.color_raw.split()[0] if self.color_raw else "grey"


class MarketSimulator:
    """Generates the deterministic demo market."""

    def __init__(
        self,
        seed: int,
        epoch: datetime,
        history_days: int = 60,
        history_minutes_per_listing: float = 8.0,
        live_seconds_per_listing: float = 20.0,
    ) -> None:
        self.seed = seed
        self.epoch = epoch
        self.history_start = epoch - timedelta(days=history_days)
        self.hist_slot = history_minutes_per_listing * 60
        self.live_slot = live_seconds_per_listing
        self.n_hist = int(history_days * 86400 / self.hist_slot)
        weights = [a.weight for a in ARCHETYPES]
        self._cum = list(_accumulate(weights))
        self._total = self._cum[-1]
        self._brand_risk = {b.slug: float(b.counterfeit_risk) for b in DEFAULT_TAXONOMY.brands}
        self._brand_name = {b.slug: b.name for b in DEFAULT_TAXONOMY.brands}
        self._listing_cache = lru_cache(maxsize=40_000)(self._build_listing)
        self._seller_cache = lru_cache(maxsize=SELLER_POOL)(self._build_seller)

    # ----------------------------------------------------------------- timeline
    def publish_time(self, k: int) -> datetime:
        jitter = unit(self.seed, k, 1)
        if k < self.n_hist:
            return self.history_start + timedelta(seconds=(k + jitter) * self.hist_slot)
        return self.epoch + timedelta(seconds=(k - self.n_hist + jitter) * self.live_slot)

    def index_at(self, t: datetime) -> int:
        """Smallest k whose slot starts at or after ``t`` minus one slot (safe lower bound)."""
        if t <= self.history_start:
            return 0
        if t < self.epoch:
            return max(0, int((t - self.history_start).total_seconds() // self.hist_slot) - 1)
        return self.n_hist + max(0, int((t - self.epoch).total_seconds() // self.live_slot) - 1)

    def indices_between(self, since: datetime | None, until: datetime) -> range:
        start = self.index_at(since) if since else 0
        end = self.index_at(until) + 3
        return range(start, end)

    # ------------------------------------------------------------------ sellers
    def seller(self, index: int) -> SimSeller:
        return self._seller_cache(index % SELLER_POOL)

    def _build_seller(self, index: int) -> SimSeller:
        r = random.Random(self.seed * 7919 + index)
        is_new = r.random() < 0.12
        if is_new:
            reviews = r.randint(0, 2)
            rating = Decimal(str(round(r.uniform(4.0, 5.0), 2))) if reviews else None
            created = self.epoch - timedelta(days=r.uniform(1, 30))
            items = r.randint(1, 6)
            sold = reviews
        else:
            reviews = int(math.exp(r.uniform(1.0, 7.2)))
            rating = Decimal(str(round(min(5.0, max(2.8, r.gauss(4.75, 0.25))), 2)))
            created = self.epoch - timedelta(days=r.uniform(60, 3000))
            items = r.randint(3, 400)
            sold = int(reviews * r.uniform(0.8, 1.3))
        country = _weighted(r, COUNTRIES)
        handle = r.choice(
            ["vintage", "closet", "style", "shop", "armadio", "second", "drip", "retro", "outfit"]
        )
        return SimSeller(
            index=index,
            username=f"{handle}_{r.randint(10, 9999)}",
            rating=rating,
            review_count=reviews,
            account_created_at=created,
            item_count=items,
            sold_count=sold,
            country=country,
            is_new=is_new,
        )

    # ----------------------------------------------------------------- listings
    def listing(self, k: int) -> SimListing:
        return self._listing_cache(k)

    def _pick_archetype(self, r: random.Random) -> Archetype:
        return ARCHETYPES[bisect.bisect_left(self._cum, r.random() * self._total)]

    def _build_listing(self, k: int) -> SimListing:
        r = random.Random(splitmix64(self.seed * 1_000_003 + k))
        published = self.publish_time(k)

        if k > 400 and r.random() < 0.03:
            original = self.listing(k - r.randint(20, 300))
            if original.repost_of is None and original.behaviour not in ("fake", "outlier"):
                return self._repost(k, original, published, r)

        arch = self._pick_archetype(r)
        brand_risk = self._brand_risk.get(arch.brand, 0.05)
        behaviour = _weighted(
            r,
            (
                ("normal", 0.72),
                ("deal", 0.085),
                ("overpriced", 0.10),
                ("dropper", 0.04),
                ("fake", 0.01 + brand_risk * 0.07),
                ("outlier", 0.025),
            ),
        )
        is_fake = behaviour == "fake"

        seller_index = r.randint(0, SELLER_POOL - 1)
        if is_fake and r.random() < 0.7:
            for _ in range(40):  # counterfeit sellers are mostly fresh accounts
                if self.seller(seller_index).is_new:
                    break
                seller_index = r.randint(0, SELLER_POOL - 1)
        seller = self.seller(seller_index)

        condition = _weighted(
            r,
            (("new_with_tags", 0.6), ("new_without_tags", 0.25), ("very_good", 0.15))
            if is_fake
            else (
                ("new_with_tags", 0.10),
                ("new_without_tags", 0.12),
                ("very_good", 0.45),
                ("good", 0.25),
                ("satisfactory", 0.08),
            ),
        )
        size_norm, size_raw, size_mult = self._size(arch, r)
        color = r.choice(arch.colors)
        vintage = r.random() < arch.vintage_share
        team, season, season_year = None, None, None
        if arch.category == "football-shirts":
            team = r.choice(FOOTBALL_KITS.get(arch.brand, tuple(t for t, _ in FOOTBALL_TEAMS)))
            season_year = r.randint(1994, 2024)
            season = f"{season_year}/{str(season_year + 1)[-2:]}"
            vintage = season_year < 2010

        noise = math.exp(r.gauss(0, 0.10))
        vintage_mult = 1.18 if vintage else 1.0
        if arch.category == "football-shirts" and season_year is not None:
            vintage_mult = 1.45 if season_year < 2005 else 1.15 if season_year < 2012 else 1.0
        true_value = arch.median * CONDITION_MULT[condition] * size_mult * vintage_mult * noise

        ratio = {
            "normal": math.exp(r.gauss(0.08, 0.13)),
            "deal": r.uniform(0.38, 0.66),
            "overpriced": r.uniform(1.3, 1.9),
            "dropper": r.uniform(1.0, 1.2),
            "fake": r.uniform(0.15, 0.38),
            "outlier": r.uniform(3.0, 4.2),
        }[behaviour]
        price = _round_price(true_value * ratio, r)

        quality = _weighted(r, (("complete", 0.55), ("partial", 0.30), ("poor", 0.15)))
        brand_name = self._brand_name.get(arch.brand, arch.brand)
        title = self._title(arch, brand_name, color, size_norm, quality, vintage, team, season, r)
        brand_field: str | None = brand_name
        if quality == "poor" and r.random() < 0.5:
            brand_field = None
        description = self._description(arch, brand_name, condition, quality, is_fake, brand_field is None, r)

        n_images = {"complete": r.randint(3, 8), "partial": r.randint(2, 6), "poor": r.randint(1, 3)}[quality]
        if is_fake:
            n_images = r.randint(1, 2)
        image_seeds = [splitmix64(self.seed + k * 131 + i) for i in range(n_images)]
        if is_fake and k > 50 and r.random() < 0.3:
            stolen_from = self.listing(r.randint(0, k - 1))
            if stolen_from.image_seeds:
                image_seeds[0] = stolen_from.image_seeds[0]

        heavy = arch.category in ("sneakers", "jackets", "coats", "puffer-jackets", "jeans")
        shipping = (
            Decimal("4.69")
            if heavy
            else Decimal("2.99")
            if arch.category in ("belts", "scarves")
            else Decimal("3.49")
        )
        demand_rate = arch.sell_through * 6 * max(0.3, 1.5 - 0.6 * ratio)

        sim = SimListing(
            k=k,
            archetype=arch,
            behaviour=behaviour,
            published_at=published,
            initial_price=price,
            true_value=true_value,
            title=title,
            description=description,
            brand_field=brand_field,
            category_field=DEFAULT_TAXONOMY.category_by_slug[arch.category].name_it,
            size_raw=size_raw,
            condition=condition,
            color_raw=COLOR_IT.get(color, ["grigio"])[0],
            country=seller.country,
            seller=seller,
            image_seeds=image_seeds,
            category_slug=arch.category,
            shipping_fee=shipping,
            demand_rate=demand_rate,
        )
        self._lifecycle(sim, ratio, r)
        return sim

    def _repost(self, k: int, original: SimListing, published: datetime, r: random.Random) -> SimListing:
        price = (original.initial_price * Decimal(str(r.choice([1.0, 0.95, 0.9])))).quantize(Decimal("1"))
        sim = SimListing(
            k=k,
            archetype=original.archetype,
            behaviour=original.behaviour,
            published_at=published,
            initial_price=Decimal(price).quantize(Decimal("0.01")),
            true_value=original.true_value,
            title=original.title,
            description=original.description,
            brand_field=original.brand_field,
            category_field=original.category_field,
            size_raw=original.size_raw,
            condition=original.condition,
            color_raw=original.color_raw,
            country=original.country,
            seller=original.seller,
            image_seeds=list(original.image_seeds),
            category_slug=original.category_slug,
            shipping_fee=original.shipping_fee,
            demand_rate=original.demand_rate,
            repost_of=original.k,
        )
        self._lifecycle(sim, float(sim.initial_price) / max(original.true_value, 1.0), r)
        return sim

    def _lifecycle(self, sim: SimListing, ratio: float, r: random.Random) -> None:
        arch = sim.archetype
        p_sell = min(0.97, max(0.02, arch.sell_through * (1.5 - 0.6 * ratio)))
        if sim.behaviour == "fake":
            p_sell *= 0.5
            if r.random() < 0.45:
                sim.removed_at = sim.published_at + timedelta(days=r.uniform(0.5, 5))
        elif sim.behaviour == "outlier":
            p_sell = 0.03

        if sim.behaviour == "dropper":
            drop_at = sim.published_at + timedelta(hours=r.uniform(3, 48))
            new_price = _round_price(sim.true_value * r.uniform(0.52, 0.7), r)
            sim.price_drops.append((drop_at, new_price))
            sale_mean = arch.days_to_sale * 0.35
            sim.sold_at = drop_at + timedelta(days=min(60.0, r.expovariate(1 / max(sale_mean, 0.1))))
            return

        if r.random() < p_sell:
            mean_days = arch.days_to_sale * (ratio**1.6)
            days = min(90.0, max(0.03, r.expovariate(1 / max(mean_days, 0.05))))
            sim.sold_at = sim.published_at + timedelta(days=days)
        elif sim.removed_at is None and r.random() < 0.15:
            sim.removed_at = sim.published_at + timedelta(days=r.uniform(2, 45))

        if ratio > 1.05 and r.random() < 0.45:
            t1 = sim.published_at + timedelta(days=r.uniform(3, 12))
            p1 = _round_price(float(sim.initial_price) * (1 - r.uniform(0.08, 0.2)), r)
            if (sim.sold_at is None or t1 < sim.sold_at) and (sim.removed_at is None or t1 < sim.removed_at):
                sim.price_drops.append((t1, p1))
                if r.random() < 0.3:
                    t2 = t1 + timedelta(days=r.uniform(5, 12))
                    p2 = _round_price(float(p1) * (1 - r.uniform(0.05, 0.15)), r)
                    if (sim.sold_at is None or t2 < sim.sold_at) and (
                        sim.removed_at is None or t2 < sim.removed_at
                    ):
                        sim.price_drops.append((t2, p2))

    def _size(self, arch: Archetype, r: random.Random) -> tuple[str | None, str | None, float]:
        if arch.category == "sneakers":
            s = _weighted(r, SHOE_SIZES)
            mult = 0.92 if s in ("38", "46") else 1.0
            return f"EU{s}", s, mult
        if arch.category in ("jeans", "trousers"):
            s = _weighted(r, WAIST_SIZES)
            return s, f"{s} | L32" if r.random() < 0.5 else s, 0.95 if s in ("W28", "W36") else 1.0
        if arch.category in ("belts", "scarves"):
            return "ONESIZE", "Taglia unica", 1.0
        s = _weighted(r, LETTER_SIZES)
        return s, SIZE_LABEL[s], 0.9 if s in ("XS", "XXL") else 1.0

    def _title(
        self,
        arch: Archetype,
        brand_name: str,
        color: str,
        size: str | None,
        quality: str,
        vintage: bool,
        team: str | None,
        season: str | None,
        r: random.Random,
    ) -> str:
        cat_word = r.choice(CATEGORY_WORDS.get(arch.category, ["Articolo"]))
        color_word = r.choice(COLOR_IT.get(color, ["grigio"]))
        size_tag = (
            ""
            if not size or size == "ONESIZE"
            else r.choice([f" tg {size}", f" taglia {size}", f" {size}", ""])
        )
        size_tag = size_tag.replace("EU", "")
        gender_word = {"men": "uomo", "women": "donna"}.get(
            arch.gender, r.choice(["uomo", "donna", "unisex"])
        )
        brand_title = "Polo Ralph Lauren" if arch.brand == "ralph-lauren" and r.random() < 0.4 else brand_name
        line = arch.line or ""
        vintage_prefix = "Vintage " if vintage and r.random() < 0.7 else ""
        if arch.category == "football-shirts" and team:
            kit = r.choice(["home", "away", "third", ""])
            if quality == "poor":
                return f"Maglia calcio {team} {r.choice(['vintage', ''])}".strip()
            return " ".join(f"{vintage_prefix}Maglia {team} {season} {kit} {brand_name}{size_tag}".split())
        if quality == "complete":
            parts = r.choice(
                [
                    f"{brand_title} {line} {cat_word} {color_word}{size_tag}",
                    f"{cat_word} {brand_title} {line} {color_word}{size_tag}",
                    f"{brand_title} {cat_word} {line}{size_tag}",
                ]
            )
            return " ".join(f"{vintage_prefix}{parts}".split())[:300]
        if quality == "partial":
            parts = r.choice(
                [
                    f"{cat_word} {brand_title} {color_word}",
                    f"{cat_word} {line or color_word} {gender_word}",
                    f"{brand_title} {color_word}{size_tag}",
                ]
            )
            return " ".join(f"{vintage_prefix}{parts}".split())[:300]
        return " ".join(f"{cat_word} {color_word} {gender_word}".split()).capitalize()

    def _description(
        self,
        arch: Archetype,
        brand_name: str,
        condition: str,
        quality: str,
        fake: bool,
        brand_missing: bool,
        r: random.Random,
    ) -> str:
        lines: list[str] = []
        if r.random() < 0.12 and not fake:
            lines.append(r.choice(EN_LINES))
        lines.append(
            {
                "new_with_tags": "Nuovo con cartellino, mai indossato.",
                "new_without_tags": "Nuovo senza cartellino, mai usato.",
                "very_good": "In ottime condizioni, indossato poche volte.",
                "good": "In buone condizioni, normali segni d'uso.",
                "satisfactory": "Condizioni discrete: presenta segni di usura.",
            }[condition]
        )
        if condition in ("good", "satisfactory") and r.random() < 0.45:
            lines.append(r.choice(DEFECT_LINES))
        if brand_missing and r.random() < 0.6:
            lines.append(
                r.choice([f"Originale {brand_name}.", f"Marca: {brand_name}.", f"{brand_name} originale."])
            )
        if quality != "poor" and arch.line and r.random() < 0.5:
            lines.append(f"Modello {arch.line}.")
        if fake and r.random() < 0.55:
            lines.append(r.choice(FAKE_LINES))
        if arch.category not in ("sneakers", "belts", "scarves") and r.random() < 0.5:
            lines.append(f"Misure: ascella-ascella {r.randint(48, 64)} cm, lunghezza {r.randint(64, 80)} cm.")
        lines.append(r.choice(FILLER_LINES))
        return " ".join(lines)


def _accumulate(values: list[float]) -> list[float]:
    total = 0.0
    out = []
    for v in values:
        total += v
        out.append(total)
    return out
