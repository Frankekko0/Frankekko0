"""Product knowledge base: brands, product lines, categories and multilingual vocabularies.

This is the identification engine's "domain knowledge". It is plain data so it can be
unit-tested, versioned and extended. Brands and categories are mirrored into the database at
startup (``app.seed.sync_catalog``); brands added only in the database are merged back in by
``Taxonomy.with_extra_brands``.

Vocabularies cover Italian, English, French, Spanish and German because Vinted is a
pan-European marketplace and sellers write in their own language.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property, lru_cache


@lru_cache(maxsize=65536)
def fold(text: str) -> str:
    """Lowercase + strip accents + collapse whitespace (stable matching key). Pure, so memoized."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().replace("’", "'").split())


@dataclass(frozen=True)
class LineSpec:
    name: str
    category: str  # category slug
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class BrandSpec:
    name: str
    slug: str
    aliases: tuple[str, ...]
    tier: str
    counterfeit_risk: Decimal
    lines: tuple[LineSpec, ...] = ()
    # Aliases that are also ordinary words or names ("lee", "guess", "boss", "ralph"): read in the brand field,
    # but in a title only as a hint and never in a description. Aliases of up to three letters count as weak too.
    weak_aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CategorySpec:
    slug: str
    name: str
    name_it: str
    parent: str | None
    keywords: tuple[str, ...] = ()
    baseline_days_to_sell: int = 14


def _b(
    name: str,
    slug: str,
    aliases: list[str],
    tier: str,
    risk: str,
    lines: list[tuple[str, str, list[str]]] | None = None,
    weak: list[str] | None = None,
) -> BrandSpec:
    return BrandSpec(
        name=name,
        slug=slug,
        aliases=tuple(aliases),
        tier=tier,
        counterfeit_risk=Decimal(risk),
        lines=tuple(LineSpec(n, c, tuple(k)) for n, c, k in (lines or [])),
        weak_aliases=tuple(weak or ()),
    )


def _c(
    slug: str, name: str, name_it: str, parent: str | None, keywords: tuple[str, ...] = (), days: int = 14
) -> CategorySpec:
    return CategorySpec(slug, name, name_it, parent, keywords, days)


# Keywords are matched on the folded text, longest first, as whole words: plurals and the common stems are listed
# one by one. A word that only says "some kind of that group" ("scarpe", "shoes") is a GENERIC keyword below and
# weighs less than a specific one ("stivali").
CATEGORIES: tuple[CategorySpec, ...] = (
    _c("tops", "Tops", "Top", None),
    _c("outerwear", "Outerwear", "Capispalla", None),
    _c("bottoms", "Bottoms", "Pantaloni e tute", None),
    _c("footwear", "Footwear", "Scarpe", None),
    _c("accessories", "Accessories", "Accessori", None),
    _c("clothing-other", "Other clothing", "Altro abbigliamento", None),
    _c(
        "hoodies",
        "Hoodies",
        "Felpe con cappuccio",
        "tops",
        (
            "felpa con cappuccio",
            "felpe con cappuccio",
            "felpa cappuccio",
            "hoodie",
            "hoodies",
            "hoody",
            "hooded",
            "kapuzenpullover",
            "sudadera con capucha",
            "sweat a capuche",
            "cappuccio",
        ),
        10,
    ),
    _c(
        "sweatshirts",
        "Sweatshirts",
        "Felpe",
        "tops",
        (
            "felpa",
            "felpe",
            "sweatshirt",
            "sweatshirts",
            "crewneck",
            "crew neck",
            "sweat",
            "sudadera",
            "half zip",
            "quarter zip",
            "mezza zip",
            "1/4 zip",
        ),
        12,
    ),
    _c("polo-shirts", "Polo shirts", "Polo", "tops", ("polo shirt", "polo", "polos"), 12),
    _c(
        "t-shirts",
        "T-shirts",
        "T-shirt",
        "tops",
        (
            "t-shirt",
            "t-shirts",
            "t shirt",
            "tshirt",
            "tee",
            "maglietta",
            "magliette",
            "camiseta",
            "maglia maniche corte",
        ),
        14,
    ),
    _c(
        "shirts",
        "Shirts",
        "Camicie",
        "tops",
        (
            "camicia",
            "camicie",
            "shirt",
            "shirts",
            "chemise",
            "camisa",
            "hemd",
            "oxford",
            "overshirt",
            "sovracamicia",
        ),
        16,
    ),
    _c(
        "knitwear",
        "Knitwear",
        "Maglieria",
        "tops",
        (
            "maglione",
            "maglioni",
            "pullover",
            "cardigan",
            "jumper",
            "sweater",
            "knit",
            "cable knit",
            "trecce",
            "pull",
            "jersey de punto",
            "strickpullover",
            "maglioncino",
            "maglieria",
            "dolcevita",
            "lupetto",
        ),
        14,
    ),
    _c(
        "football-shirts",
        "Football shirts",
        "Maglie da calcio",
        "tops",
        (
            "maglia calcio",
            "maglia da calcio",
            "football shirt",
            "soccer jersey",
            "maillot",
            "camiseta futbol",
            "trikot",
            "home kit",
            "away kit",
            "maglia gara",
            "match worn",
            "player issue",
        ),
        9,
    ),
    _c(
        "jackets",
        "Jackets",
        "Giacche",
        "outerwear",
        (
            "giacca",
            "giacche",
            "giubbotto",
            "giubbotti",
            "giubbino",
            "giubbini",
            "jacket",
            "jackets",
            "veste",
            "chaqueta",
            "jacke",
            "bomber",
            "harrington",
            "windbreaker",
            "k-way",
            "softshell",
            "shell",
            "wax jacket",
            "overshirt jacket",
            "giacca di jeans",
            "giacca jeans",
            "giubbotto di jeans",
            "denim jacket",
            "trucker",
            "gilet",
            "impermeabile",
        ),
        12,
    ),
    _c(
        "coats",
        "Coats",
        "Cappotti",
        "outerwear",
        (
            "cappotto",
            "cappotti",
            "coat",
            "manteau",
            "abrigo",
            "mantel",
            "trench",
            "parka",
            "montgomery",
            "loden",
            "peacoat",
        ),
        18,
    ),
    _c(
        "puffer-jackets",
        "Puffer jackets",
        "Piumini",
        "outerwear",
        (
            "piumino",
            "piumini",
            "puffer",
            "down jacket",
            "doudoune",
            "plumifero",
            "daunenjacke",
            "nuptse",
            "piumone",
        ),
        9,
    ),
    _c(
        "fleece",
        "Fleece",
        "Pile",
        "outerwear",
        ("pile", "fleece", "polaire", "forro polar", "sherpa", "micropile"),
        10,
    ),
    _c("jeans", "Jeans", "Jeans", "bottoms", ("jeans", "jean", "denim", "vaqueros"), 16),
    _c(
        "trousers",
        "Trousers",
        "Pantaloni",
        "bottoms",
        (
            "pantaloni",
            "pantalone",
            "trousers",
            "pants",
            "chino",
            "chinos",
            "cargo",
            "pantalon",
            "hose",
            "work pant",
            "double knee",
            "leggings",
        ),
        18,
    ),
    _c(
        "shorts",
        "Shorts",
        "Pantaloncini",
        "bottoms",
        (
            "pantaloncini",
            "pantaloncino",
            "shorts",
            "bermuda",
            "bermudas",
            "calzoncini",
            "pantalones cortos",
        ),
        14,
    ),
    _c(
        "tracksuits",
        "Tracksuits",
        "Tute",
        "bottoms",
        (
            "tuta",
            "tute",
            "tracksuit",
            "track pants",
            "track jacket",
            "track top",
            "survetement",
            "chandal",
            "trainingsanzug",
            "joggers",
            "jogger",
            "pantaloni tuta",
        ),
        12,
    ),
    _c(
        "sneakers",
        "Sneakers",
        "Sneakers",
        "footwear",
        (
            "sneakers",
            "sneaker",
            "scarpe da ginnastica",
            "scarpe da corsa",
            "scarpe sportive",
            "trainers",
            "baskets",
            "zapatillas",
            "turnschuhe",
            "running shoes",
            "air max",
            "air force",
            "af1",
            "air jordan",
            "dunk",
            "yeezy",
            "stan smith",
            "old skool",
            "all star",
            "chuck taylor",
            "air huarache",
            "vapormax",
            "ultraboost",
            "scarpe",
            "scarpa",
            "shoes",
            "shoe",
        ),
        9,
    ),
    _c(
        "boots",
        "Boots",
        "Stivali",
        "footwear",
        (
            "stivali",
            "stivale",
            "stivaletti",
            "stivaletto",
            "anfibi",
            "boots",
            "boot",
            "bottes",
            "botas",
            "stiefel",
            "chelsea boot",
            "desert boot",
            "scarponi",
            "scarponcini",
        ),
        14,
    ),
    _c(
        "loafers",
        "Loafers and dress shoes",
        "Mocassini e scarpe eleganti",
        "footwear",
        (
            "mocassini",
            "mocassino",
            "loafers",
            "loafer",
            "mocasines",
            "mocassins",
            "stringate",
            "scarpe stringate",
            "scarpe eleganti",
            "francesine",
            "brogue",
            "brogues",
            "derby",
        ),
        16,
    ),
    _c(
        "sandals",
        "Sandals",
        "Sandali e ciabatte",
        "footwear",
        (
            "sandali",
            "sandalo",
            "sandals",
            "sandal",
            "ciabatte",
            "ciabatta",
            "infradito",
            "slides",
            "flip flop",
            "flip flops",
            "sandalias",
            "sandalen",
            "zoccoli",
            "clogs",
        ),
        14,
    ),
    _c(
        "dresses",
        "Dresses",
        "Abiti",
        "clothing-other",
        ("vestito", "abito", "abiti", "dress", "dresses", "robe", "vestido", "kleid", "tubino", "miniabito"),
        16,
    ),
    _c(
        "skirts",
        "Skirts",
        "Gonne",
        "clothing-other",
        ("gonna", "gonne", "skirt", "skirts", "jupe", "falda", "minigonna"),
        16,
    ),
    _c(
        "swimwear",
        "Swimwear",
        "Costumi",
        "clothing-other",
        (
            "costume da bagno",
            "costumi da bagno",
            "costume mare",
            "boxer mare",
            "swimsuit",
            "bikini",
            "swim shorts",
            "swim trunks",
            "boardshorts",
            "maillot de bain",
            "bañador",
            "badehose",
        ),
        18,
    ),
    _c(
        "bags",
        "Bags",
        "Borse",
        "accessories",
        (
            "borsa",
            "borse",
            "bag",
            "bags",
            "zaino",
            "backpack",
            "sac",
            "bolso",
            "tracolla",
            "marsupio",
            "pochette",
            "clutch",
            "tote",
            "shopper",
            "handtasche",
            "rucksack",
            "borsone",
        ),
        16,
    ),
    _c(
        "belts",
        "Belts",
        "Cinture",
        "accessories",
        ("cintura", "cinture", "belt", "ceinture", "cinturon", "gurtel"),
        18,
    ),
    _c(
        "scarves",
        "Scarves",
        "Sciarpe",
        "accessories",
        ("sciarpa", "sciarpe", "scarf", "scarves", "echarpe", "bufanda", "schal", "foulard", "stola"),
        20,
    ),
    _c(
        "caps",
        "Caps & hats",
        "Cappelli",
        "accessories",
        (
            "cappello",
            "cappelli",
            "cappellino",
            "cap",
            "beanie",
            "berretto",
            "casquette",
            "gorra",
            "bucket hat",
            "snapback",
        ),
        14,
    ),
    _c(
        "watches",
        "Watches",
        "Orologi",
        "accessories",
        ("orologio", "orologi", "watch", "watches", "montre", "reloj", "armbanduhr"),
        30,
    ),
)

# Words that name a whole group rather than one kind ("scarpe" is any shoe): they count, but weigh less.
GENERIC_CATEGORY_KEYWORDS = frozenset(
    {"scarpe", "scarpa", "shoes", "shoe", "sac", "bag", "bags", "pull", "cap"}
)

BRANDS: tuple[BrandSpec, ...] = (
    _b(
        "Ralph Lauren",
        "ralph-lauren",
        ["ralph lauren", "polo ralph lauren", "polo by ralph lauren", "ralph", "rl"],
        "premium",
        "0.12",
        weak=["ralph"],
        lines=[
            ("Custom Slim Fit", "polo-shirts", ["custom slim fit", "custom fit"]),
            ("Classic Fit", "polo-shirts", ["classic fit"]),
            ("Big Pony", "polo-shirts", ["big pony", "pony grande"]),
            ("Polo Bear", "knitwear", ["polo bear", "orsetto"]),
            ("Cable Knit", "knitwear", ["cable knit", "trecce", "cable-knit"]),
            ("Oxford", "shirts", ["oxford"]),
            ("Harrington", "jackets", ["harrington"]),
            ("Half Zip", "sweatshirts", ["half zip", "quarter zip", "mezza zip", "1/4 zip"]),
        ],
    ),
    _b(
        "Nike",
        "nike",
        ["nike", "nike sportswear", "nsw"],
        "sport",
        "0.25",
        [
            ("Air Force 1", "sneakers", ["air force 1", "air force one", "af1", "air force"]),
            ("Dunk Low", "sneakers", ["dunk low"]),
            ("Air Max 90", "sneakers", ["air max 90", "am90"]),
            ("Air Max 95", "sneakers", ["air max 95", "am95"]),
            ("Air Jordan 1", "sneakers", ["air jordan 1", "jordan 1", "aj1"]),
            ("Tech Fleece", "tracksuits", ["tech fleece"]),
            ("Center Swoosh", "sweatshirts", ["center swoosh", "centre swoosh", "middle swoosh"]),
            ("ACG", "jackets", ["acg"]),
        ],
    ),
    _b(
        "Adidas",
        "adidas",
        ["adidas", "adidas originals"],
        "sport",
        "0.15",
        [
            ("Samba", "sneakers", ["samba"]),
            ("Gazelle", "sneakers", ["gazelle"]),
            ("Spezial", "sneakers", ["spezial"]),
            ("Superstar", "sneakers", ["superstar"]),
            ("Firebird", "tracksuits", ["firebird"]),
            ("Trefoil", "sweatshirts", ["trefoil"]),
        ],
    ),
    _b(
        "The North Face",
        "the-north-face",
        ["the north face", "north face", "northface", "tnf"],
        "outdoor",
        "0.25",
        [
            ("Nuptse 700", "puffer-jackets", ["nuptse 700", "nuptse", "retro nuptse", "1996 retro"]),
            ("Himalayan", "puffer-jackets", ["himalayan"]),
            ("Denali", "fleece", ["denali"]),
            ("Mountain Jacket", "jackets", ["mountain jacket", "mountain light"]),
        ],
    ),
    _b(
        "Carhartt WIP",
        "carhartt",
        ["carhartt wip", "carhartt"],
        "streetwear",
        "0.08",
        [
            ("Detroit Jacket", "jackets", ["detroit"]),
            ("Active Jacket", "jackets", ["active jacket"]),
            ("Double Knee", "trousers", ["double knee"]),
            ("Chase", "sweatshirts", ["chase"]),
            ("Michigan Coat", "jackets", ["michigan"]),
        ],
    ),
    _b(
        "Stone Island",
        "stone-island",
        ["stone island", "stoneisland"],
        "premium",
        "0.35",
        [
            ("Ghost Piece", "jackets", ["ghost piece", "ghost"]),
            ("Shadow Project", "jackets", ["shadow project"]),
            ("Soft Shell-R", "jackets", ["soft shell-r", "soft shell r", "softshell-r"]),
            ("Overshirt", "shirts", ["overshirt", "sovracamicia"]),
        ],
    ),
    _b(
        "Patagonia",
        "patagonia",
        ["patagonia"],
        "outdoor",
        "0.05",
        [
            ("Retro-X", "fleece", ["retro-x", "retro x"]),
            ("Synchilla", "fleece", ["synchilla", "snap-t", "snap t"]),
            ("Better Sweater", "fleece", ["better sweater"]),
            ("Torrentshell", "jackets", ["torrentshell"]),
            ("Down Sweater", "puffer-jackets", ["down sweater"]),
        ],
    ),
    _b(
        "Levi's",
        "levis",
        ["levi's", "levis", "levi strauss", "levi"],
        "mid",
        "0.03",
        weak=["levi"],
        lines=[
            ("501", "jeans", ["501"]),
            ("505", "jeans", ["505"]),
            ("511", "jeans", ["511"]),
            ("Trucker Jacket", "jackets", ["trucker"]),
        ],
    ),
    _b(
        "Tommy Hilfiger",
        "tommy-hilfiger",
        ["tommy hilfiger", "tommy jeans", "hilfiger", "tommy"],
        "mid",
        "0.06",
        weak=["tommy"],
    ),
    _b(
        "Lacoste",
        "lacoste",
        ["lacoste"],
        "premium",
        "0.15",
        [
            ("L.12.12", "polo-shirts", ["l.12.12", "l1212", "l 12 12"]),
        ],
    ),
    _b(
        "Arc'teryx",
        "arcteryx",
        ["arc'teryx", "arcteryx", "arc teryx"],
        "outdoor",
        "0.15",
        [
            ("Beta", "jackets", ["beta lt", "beta ar", "beta sl", "beta"]),
            ("Atom", "jackets", ["atom lt", "atom hoody", "atom"]),
            ("Alpha SV", "jackets", ["alpha sv", "alpha"]),
        ],
    ),
    _b(
        "Moncler",
        "moncler",
        ["moncler"],
        "luxury",
        "0.55",
        [
            ("Maya", "puffer-jackets", ["maya"]),
            ("Grenoble", "puffer-jackets", ["grenoble"]),
        ],
    ),
    _b(
        "Stüssy",
        "stussy",
        ["stussy", "stüssy"],
        "streetwear",
        "0.20",
        [("8 Ball", "sweatshirts", ["8 ball", "8-ball"])],
    ),
    _b(
        "New Balance",
        "new-balance",
        ["new balance", "nb"],
        "sport",
        "0.08",
        [
            ("550", "sneakers", ["550"]),
            ("2002R", "sneakers", ["2002r", "2002 r"]),
            ("990", "sneakers", ["990", "990v5", "990v6"]),
            ("9060", "sneakers", ["9060"]),
            ("574", "sneakers", ["574"]),
        ],
    ),
    _b(
        "Burberry",
        "burberry",
        ["burberry", "burberrys"],
        "luxury",
        "0.45",
        [
            ("Trench", "coats", ["trench", "kensington", "chelsea trench"]),
            ("Nova Check", "scarves", ["nova check", "check"]),
        ],
    ),
    _b(
        "C.P. Company",
        "cp-company",
        ["c.p. company", "cp company", "c.p company", "c.p.company", "cpc"],
        "premium",
        "0.25",
        [
            ("Goggle", "jackets", ["goggle", "mille miglia"]),
            ("Lens", "sweatshirts", ["lens"]),
        ],
    ),
    _b(
        "Champion",
        "champion",
        ["champion"],
        "sport",
        "0.05",
        [("Reverse Weave", "sweatshirts", ["reverse weave"])],
    ),
    _b("Dickies", "dickies", ["dickies"], "mid", "0.03", [("874", "trousers", ["874"])]),
    _b(
        "Barbour",
        "barbour",
        ["barbour"],
        "premium",
        "0.12",
        [
            ("Bedale", "jackets", ["bedale"]),
            ("Beaufort", "jackets", ["beaufort"]),
            ("International", "jackets", ["international"]),
        ],
    ),
    _b(
        "Napapijri",
        "napapijri",
        ["napapijri"],
        "mid",
        "0.08",
        [("Skidoo", "jackets", ["skidoo", "rainforest"])],
    ),
    _b(
        "Fred Perry",
        "fred-perry",
        ["fred perry"],
        "mid",
        "0.06",
        [("M12", "polo-shirts", ["m12", "twin tipped"])],
    ),
    _b(
        "Supreme",
        "supreme",
        ["supreme"],
        "streetwear",
        "0.60",
        [("Box Logo", "hoodies", ["box logo", "bogo"])],
    ),
    _b(
        "Gucci",
        "gucci",
        ["gucci"],
        "luxury",
        "0.70",
        [
            ("GG Marmont", "belts", ["marmont", "gg marmont"]),
            ("Ace", "sneakers", ["ace sneaker", "gucci ace"]),
        ],
    ),
    _b("Puma", "puma", ["puma"], "sport", "0.05"),
    _b("Umbro", "umbro", ["umbro"], "sport", "0.05"),
    _b("Kappa", "kappa", ["kappa"], "sport", "0.05"),
    _b("Zara", "zara", ["zara"], "fast_fashion", "0.00"),
    _b("H&M", "h-m", ["h&m", "h & m", "hm", "h and m"], "fast_fashion", "0.00"),
)

# Brands beyond the first dozens, without product lines: enough for each to have its own comparables pool and a
# counterfeit prior. The priors are editorial assumptions (how often a fake of the brand turns up on the Italian
# second-hand market), not measurements: docs/LIMITATIONS.md says so. A brand missing from here is "unknown brand".
_EXTENDED_BRANDS: tuple[BrandSpec, ...] = (
    # ---- luxury and designer, the ones copied most
    _b("Louis Vuitton", "louis-vuitton", ["louis vuitton", "lv"], "luxury", "0.75"),
    _b("Prada", "prada", ["prada"], "luxury", "0.60"),
    _b("Balenciaga", "balenciaga", ["balenciaga"], "luxury", "0.65"),
    _b("Dior", "dior", ["dior", "christian dior"], "luxury", "0.60"),
    _b("Chanel", "chanel", ["chanel"], "luxury", "0.75"),
    _b("Hermès", "hermes", ["hermes", "hermès"], "luxury", "0.70"),
    _b("Fendi", "fendi", ["fendi"], "luxury", "0.55"),
    _b("Versace", "versace", ["versace", "versace jeans couture", "versace jeans"], "luxury", "0.55"),
    _b("Giorgio Armani", "giorgio-armani", ["giorgio armani"], "luxury", "0.50"),
    _b("Emporio Armani", "emporio-armani", ["emporio armani", "ea7"], "premium", "0.45"),
    _b("Armani Jeans", "armani-jeans", ["armani jeans"], "premium", "0.40"),
    _b("Armani Exchange", "armani-exchange", ["armani exchange", "a|x", "ax armani exchange"], "mid", "0.35"),
    _b("Armani", "armani", ["armani"], "premium", "0.45"),
    _b("Off-White", "off-white", ["off-white", "off white", "offwhite"], "luxury", "0.65"),
    _b("Saint Laurent", "saint-laurent", ["saint laurent", "yves saint laurent", "ysl"], "luxury", "0.60"),
    _b("Valentino", "valentino", ["valentino", "valentino garavani"], "luxury", "0.50"),
    _b(
        "Dolce & Gabbana",
        "dolce-gabbana",
        ["dolce & gabbana", "dolce e gabbana", "dolce gabbana", "dolce&gabbana", "d&g"],
        "luxury",
        "0.60",
    ),
    _b("Givenchy", "givenchy", ["givenchy"], "luxury", "0.55"),
    _b("Bottega Veneta", "bottega-veneta", ["bottega veneta"], "luxury", "0.50"),
    _b("Celine", "celine", ["celine", "céline"], "luxury", "0.50"),
    _b("Alexander McQueen", "alexander-mcqueen", ["alexander mcqueen", "mcqueen"], "luxury", "0.55"),
    _b("Kenzo", "kenzo", ["kenzo"], "luxury", "0.40"),
    _b("Loewe", "loewe", ["loewe"], "luxury", "0.40"),
    _b("Goyard", "goyard", ["goyard"], "luxury", "0.60"),
    _b("Maison Margiela", "maison-margiela", ["maison margiela", "margiela", "mm6"], "premium", "0.40"),
    _b("Philipp Plein", "philipp-plein", ["philipp plein", "plein sport"], "premium", "0.50"),
    _b("Dsquared2", "dsquared2", ["dsquared2", "dsquared", "d squared"], "premium", "0.50"),
    _b("Michael Kors", "michael-kors", ["michael kors", "mk"], "mid", "0.45"),
    _b("Coach", "coach", ["coach"], "mid", "0.45", weak=["coach"]),
    _b("Longchamp", "longchamp", ["longchamp"], "mid", "0.45"),
    _b("Furla", "furla", ["furla"], "mid", "0.35"),
    _b("Tory Burch", "tory-burch", ["tory burch"], "mid", "0.30"),
    _b("Kate Spade", "kate-spade", ["kate spade"], "mid", "0.30"),
    _b("Tod's", "tods", ["tod's", "tods"], "premium", "0.35"),
    _b("Hogan", "hogan", ["hogan"], "premium", "0.30"),
    _b("Moschino", "moschino", ["moschino"], "premium", "0.35"),
    _b("Roberto Cavalli", "roberto-cavalli", ["roberto cavalli", "just cavalli"], "premium", "0.35"),
    _b("Ami Paris", "ami-paris", ["ami paris", "ami alexandre mattiussi"], "premium", "0.30"),
    _b("Acne Studios", "acne-studios", ["acne studios"], "premium", "0.20"),
    _b("Peuterey", "peuterey", ["peuterey"], "premium", "0.30"),
    _b("Pinko", "pinko", ["pinko"], "mid", "0.25"),
    _b("Liu Jo", "liu-jo", ["liu jo", "liu-jo"], "mid", "0.20"),
    _b("Max Mara", "max-mara", ["max mara", "maxmara", "weekend max mara"], "premium", "0.20"),
    _b("Brunello Cucinelli", "brunello-cucinelli", ["brunello cucinelli"], "luxury", "0.20"),
    _b("Zegna", "zegna", ["zegna", "ermenegildo zegna"], "luxury", "0.15"),
    _b("Fay", "fay", ["fay"], "premium", "0.15", weak=["fay"]),
    _b("Herno", "herno", ["herno"], "premium", "0.15"),
    _b("Aspesi", "aspesi", ["aspesi"], "premium", "0.10"),
    _b("Woolrich", "woolrich", ["woolrich"], "premium", "0.20"),
    _b("Colmar", "colmar", ["colmar"], "mid", "0.10"),
    _b("Belstaff", "belstaff", ["belstaff"], "premium", "0.25"),
    _b("Paul & Shark", "paul-shark", ["paul & shark", "paul and shark", "paul&shark"], "premium", "0.20"),
    _b("Ray-Ban", "ray-ban", ["ray-ban", "ray ban", "rayban"], "mid", "0.40"),
    _b("Oakley", "oakley", ["oakley"], "sport", "0.30"),
    _b("Rolex", "rolex", ["rolex"], "luxury", "0.70"),
    # ---- sport and streetwear
    _b(
        "Jordan",
        "jordan",
        ["air jordan", "jumpman", "jordan"],
        "sport",
        "0.45",
        weak=["jordan"],
        lines=[
            ("Jordan 1", "sneakers", ["air jordan 1", "jordan 1", "aj1"]),
            ("Jordan 3", "sneakers", ["air jordan 3", "jordan 3", "aj3"]),
            ("Jordan 4", "sneakers", ["air jordan 4", "jordan 4", "aj4"]),
            ("Jordan 5", "sneakers", ["air jordan 5", "jordan 5", "aj5"]),
            ("Jordan 11", "sneakers", ["air jordan 11", "jordan 11", "aj11"]),
        ],
    ),
    _b("Ugg", "ugg", ["ugg", "ugg australia"], "mid", "0.40"),
    _b(
        "Converse",
        "converse",
        ["converse", "converse all star", "all star"],
        "sport",
        "0.30",
        weak=["all star"],
        lines=[("All Star", "sneakers", ["all star", "chuck taylor", "chuck 70"])],
    ),
    _b(
        "Vans",
        "vans",
        ["vans", "vans off the wall"],
        "streetwear",
        "0.25",
        lines=[("Old Skool", "sneakers", ["old skool", "sk8-hi", "sk8 hi"])],
    ),
    _b("Timberland", "timberland", ["timberland"], "mid", "0.30"),
    _b("Dr. Martens", "dr-martens", ["dr. martens", "dr martens", "doc martens", "drmartens"], "mid", "0.25"),
    _b(
        "Reebok",
        "reebok",
        ["reebok"],
        "sport",
        "0.10",
        lines=[("Club C", "sneakers", ["club c", "classic leather"])],
    ),
    _b("Fila", "fila", ["fila"], "sport", "0.15", weak=["fila"]),
    _b("Ellesse", "ellesse", ["ellesse"], "sport", "0.12"),
    _b("Diadora", "diadora", ["diadora"], "sport", "0.10"),
    _b("Sergio Tacchini", "sergio-tacchini", ["sergio tacchini"], "sport", "0.12"),
    _b("Lotto Sport", "lotto", ["lotto sport", "lotto leggenda"], "sport", "0.05"),
    _b("Hummel", "hummel", ["hummel"], "sport", "0.05"),
    _b("Le Coq Sportif", "le-coq-sportif", ["le coq sportif"], "sport", "0.05"),
    _b("Mizuno", "mizuno", ["mizuno"], "sport", "0.05"),
    _b("Asics", "asics", ["asics"], "sport", "0.05"),
    _b("Salomon", "salomon", ["salomon"], "outdoor", "0.10"),
    _b("Columbia", "columbia", ["columbia", "columbia sportswear"], "outdoor", "0.05", weak=["columbia"]),
    _b("Helly Hansen", "helly-hansen", ["helly hansen"], "outdoor", "0.08"),
    _b("Jack Wolfskin", "jack-wolfskin", ["jack wolfskin"], "outdoor", "0.05"),
    _b("Mammut", "mammut", ["mammut"], "outdoor", "0.05"),
    _b("Fjällräven", "fjallraven", ["fjallraven", "fjällräven"], "outdoor", "0.10"),
    _b("Under Armour", "under-armour", ["under armour"], "sport", "0.08"),
    _b("Lululemon", "lululemon", ["lululemon"], "sport", "0.10"),
    _b("Gymshark", "gymshark", ["gymshark"], "sport", "0.05"),
    _b("North Sails", "north-sails", ["north sails"], "mid", "0.08"),
    _b("K-Way", "k-way", ["k-way", "kway", "k way"], "mid", "0.15"),
    _b("Alpha Industries", "alpha-industries", ["alpha industries"], "streetwear", "0.10"),
    _b("Birkenstock", "birkenstock", ["birkenstock"], "mid", "0.25"),
    _b("Crocs", "crocs", ["crocs"], "mid", "0.15"),
    _b("Hunter", "hunter", ["hunter"], "mid", "0.05", weak=["hunter"]),
    _b("Superga", "superga", ["superga"], "mid", "0.10"),
    _b("Geox", "geox", ["geox"], "mid", "0.05"),
    # ---- denim, casual and high street
    _b("Calvin Klein", "calvin-klein", ["calvin klein", "calvin klein jeans", "ck"], "mid", "0.20"),
    _b(
        "Hugo Boss",
        "hugo-boss",
        ["hugo boss", "boss orange", "boss green", "boss black", "boss"],
        "premium",
        "0.20",
        weak=["boss"],
    ),
    _b("Diesel", "diesel", ["diesel"], "mid", "0.20", weak=["diesel"]),
    _b("Guess", "guess", ["guess", "guess jeans"], "mid", "0.15", weak=["guess"]),
    _b("Replay", "replay", ["replay"], "mid", "0.08", weak=["replay"]),
    _b("Pepe Jeans", "pepe-jeans", ["pepe jeans"], "mid", "0.08"),
    _b("Wrangler", "wrangler", ["wrangler"], "mid", "0.06"),
    _b("Lee", "lee", ["lee", "lee jeans"], "mid", "0.05", weak=["lee"]),
    _b("Superdry", "superdry", ["superdry"], "mid", "0.10"),
    _b("Hollister", "hollister", ["hollister"], "mid", "0.08"),
    _b(
        "Abercrombie & Fitch",
        "abercrombie-fitch",
        ["abercrombie & fitch", "abercrombie and fitch", "abercrombie"],
        "mid",
        "0.10",
    ),
    _b("Gap", "gap", ["gap"], "fast_fashion", "0.02", weak=["gap"]),
    _b("Gant", "gant", ["gant"], "premium", "0.08"),
    _b(
        "Jack & Jones", "jack-jones", ["jack & jones", "jack and jones", "jack&jones"], "fast_fashion", "0.02"
    ),
    _b("Ben Sherman", "ben-sherman", ["ben sherman"], "mid", "0.05"),
    _b("Lyle & Scott", "lyle-scott", ["lyle & scott", "lyle and scott", "lyle&scott"], "mid", "0.10"),
    _b("Pretty Green", "pretty-green", ["pretty green"], "mid", "0.10"),
    _b(
        "Harmont & Blaine",
        "harmont-blaine",
        ["harmont & blaine", "harmont and blaine", "harmont&blaine"],
        "mid",
        "0.08",
    ),
    _b("Frankie Morello", "frankie-morello", ["frankie morello"], "premium", "0.20"),
    _b("Twinset", "twinset", ["twinset", "twin set"], "mid", "0.12"),
    _b("Patrizia Pepe", "patrizia-pepe", ["patrizia pepe"], "mid", "0.10"),
    _b("Elisabetta Franchi", "elisabetta-franchi", ["elisabetta franchi"], "premium", "0.20"),
    _b("Sandro", "sandro", ["sandro", "sandro paris"], "premium", "0.10", weak=["sandro"]),
    _b("Maje", "maje", ["maje"], "premium", "0.10"),
    _b("A.P.C.", "apc", ["a.p.c.", "apc"], "premium", "0.10"),
    _b("Mango", "mango", ["mango"], "fast_fashion", "0.00", weak=["mango"]),
    _b("Bershka", "bershka", ["bershka"], "fast_fashion", "0.00"),
    _b("Pull&Bear", "pull-bear", ["pull&bear", "pull & bear", "pull and bear"], "fast_fashion", "0.00"),
    _b("Stradivarius", "stradivarius", ["stradivarius"], "fast_fashion", "0.00"),
    _b("Massimo Dutti", "massimo-dutti", ["massimo dutti"], "mid", "0.00"),
    _b("Uniqlo", "uniqlo", ["uniqlo"], "fast_fashion", "0.02"),
    _b("Benetton", "benetton", ["benetton", "united colors of benetton"], "mid", "0.02"),
    _b(
        "Ralph Lauren Purple Label",
        "ralph-lauren-purple-label",
        ["purple label", "ralph lauren purple label"],
        "luxury",
        "0.25",
    ),
    _b("RRL", "rrl", ["rrl", "double rl"], "premium", "0.20"),
    _b(
        "Lauren Ralph Lauren",
        "lauren-ralph-lauren",
        ["lauren ralph lauren", "lauren by ralph lauren"],
        "mid",
        "0.05",
    ),
)
BRANDS = BRANDS + _EXTENDED_BRANDS

COLORS: dict[str, tuple[str, ...]] = {
    "black": ("nero", "nera", "neri", "nere", "black", "noir", "noire", "noirs", "negro", "negra", "schwarz"),
    "white": (
        "bianco",
        "bianca",
        "bianchi",
        "bianche",
        "white",
        "blanc",
        "blanche",
        "blanco",
        "blanca",
        "weiss",
        "weiß",
        "panna",
        "ecru",
        "crema",
        "cream",
    ),
    "navy": (
        "blu navy",
        "navy",
        "blu marino",
        "blu scuro",
        "marine",
        "bleu marine",
        "dark blue",
        "blu notte",
        "azul marino",
        "marineblau",
    ),
    "blue": ("blu", "blue", "bleu", "azul", "blau", "royal"),
    "light_blue": (
        "azzurro",
        "azzurra",
        "azzurri",
        "azzurre",
        "celeste",
        "celesti",
        "light blue",
        "sky blue",
        "bleu ciel",
    ),
    "turquoise": ("turchese", "turchesi", "turquoise", "turquesa", "turkis"),
    "red": ("rosso", "rossa", "rossi", "rosse", "red", "rouge", "rojo", "roja", "rot"),
    "burgundy": ("bordeaux", "bordeau", "burgundy", "granata", "vinaccia"),
    "green": ("verde", "verdi", "green", "vert", "verte", "grun", "grün", "forest", "bottle green"),
    "khaki": ("khaki", "kaki", "cachi", "verde militare", "olive", "oliva"),
    "grey": (
        "grigio",
        "grigia",
        "grigi",
        "grigie",
        "grey",
        "gray",
        "gris",
        "grise",
        "grau",
        "antracite",
        "melange",
    ),
    "silver": ("argento", "argentato", "silver", "argent", "plata", "silber"),
    "gold": ("oro", "dorato", "dorata", "gold", "dore", "doree", "dorado", "golden"),
    "beige": ("beige", "sabbia", "sand", "camel", "tan", "cammello"),
    "brown": ("marrone", "marroni", "brown", "marron", "braun", "cioccolato", "moka"),
    "yellow": (
        "giallo",
        "gialla",
        "gialli",
        "gialle",
        "yellow",
        "jaune",
        "amarillo",
        "gelb",
        "senape",
        "mustard",
    ),
    "orange": ("arancione", "arancioni", "arancio", "orange", "naranja", "corallo", "coral"),
    "pink": ("rosa", "pink", "rose", "fucsia", "fuchsia", "magenta"),
    "purple": ("viola", "viole", "purple", "violet", "lilla", "lilac", "morado"),
    "multi": ("multicolore", "multicolor", "multicolour", "fantasia", "tie dye"),
}

MATERIALS: dict[str, tuple[str, ...]] = {
    "cashmere": ("cashmere", "cachemire", "kaschmir"),
    "wool": ("lana", "wool", "laine", "merino", "lambswool", "wolle"),
    "cotton": ("cotone", "cotton", "coton", "algodon", "baumwolle", "pique", "piqué", "jersey di cotone"),
    "linen": ("lino", "linen", "lin"),
    "silk": ("seta", "silk", "soie"),
    # Imitation leather first: "pelle ecologica" must not read as leather (qualifiers are also handled
    # by ``normalize_material``).
    "faux_leather": (
        "pelle ecologica",
        "pelle sintetica",
        "ecopelle",
        "eco pelle",
        "simil pelle",
        "similpelle",
        "finta pelle",
        "similicuir",
        "faux leather",
        "vegan leather",
        "cuir synthetique",
        "kunstleder",
        "piel sintetica",
        "ecopiel",
    ),
    "leather": ("pelle", "leather", "cuir", "cuero", "leder", "vera pelle"),
    "suede": ("camoscio", "suede", "daim", "scamosciato"),
    # "piumino" is the garment, not the fill: it is not a material word.
    "down": ("piuma d'oca", "piuma", "piume", "down", "duvet", "goose down", "plumon", "daunen"),
    "denim": ("denim",),
    "fleece": ("fleece", "pile", "polaire"),
    "nylon": ("nylon", "poliammide", "polyamide"),
    "polyester": ("poliestere", "polyester", "poliester"),
    "acrylic": ("acrilico", "acrylic", "acrylique", "acryl"),
    "viscose": ("viscosa", "viscose", "viskose", "rayon"),
    "elastane": ("elastan", "elastane", "elastam", "elastano", "spandex", "lycra", "elasthan"),
    "gore-tex": ("gore-tex", "goretex", "gore tex"),
}

GENDER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "kids": (
        "bambino",
        "bambina",
        "bambini",
        "bambine",
        "bimbo",
        "bimba",
        "kids",
        "kid",
        "enfant",
        "enfants",
        "nino",
        "nina",
        "junior",
        "boys",
        "girls",
        "ragazzo",
        "ragazza",
        "ragazzi",
        "ragazze",
    ),
    "women": (
        "donna",
        "donne",
        "women",
        "womens",
        "woman",
        "femme",
        "mujer",
        "damen",
        "femminile",
        "lady",
        "ladies",
    ),
    "men": ("uomo", "uomini", "men", "mens", "man", "homme", "hombre", "herren", "maschile"),
    "unisex": ("unisex",),
}

# First element of the marketplace's own category path ("Uomo > Vestiti > ..."): the audience it files the item under.
CRUMB_ROOT_GENDER: dict[str, str] = {
    "uomo": "men",
    "uomini": "men",
    "men": "men",
    "homme": "men",
    "hombre": "men",
    "herren": "men",
    "donna": "women",
    "donne": "women",
    "women": "women",
    "femme": "women",
    "mujer": "women",
    "damen": "women",
    "bambini": "kids",
    "bambino": "kids",
    "kids": "kids",
    "enfants": "kids",
    "ninos": "kids",
    "kinder": "kids",
    "hommes": "men",
    "hombres": "men",
    "manner": "men",
    "femmes": "women",
    "mujeres": "women",
    "frauen": "women",
}

# Phrases that say a condition on their own, in a label or in running text (matched on the folded text).
CONDITION_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "new_with_tags",
        (
            "nuovo con cartellino",
            "nuovo con etichetta",
            "new with tags",
            "neuf avec etiquette",
            "nuevo con etiquetas",
            "neu mit etikett",
            "nieuw met kaartje",
            "nieuw met prijskaartje",
            "nwt",
            "con cartellino",
        ),
    ),
    (
        "new_without_tags",
        (
            "nuovo senza cartellino",
            "nuovo senza etichetta",
            "new without tags",
            "neuf sans etiquette",
            "nuevo sin etiquetas",
            "neu ohne etikett",
            "nieuw zonder kaartje",
            "nieuw zonder prijskaartje",
            "nwot",
            "mai indossato",
            "mai indossata",
            "never worn",
        ),
    ),
    (
        "very_good",
        (
            "ottime condizioni",
            "ottimo stato",
            "ottime condizione",
            "very good",
            "tres bon etat",
            "muy bueno",
            "sehr gut",
            "heel goed",
            "zeer goed",
            "come nuovo",
            "like new",
            "perfette condizioni",
            "pari al nuovo",
        ),
    ),
    (
        "satisfactory",
        (
            "discrete condizioni",
            "condizioni discrete",
            "condizioni soddisfacenti",
            "soddisfacenti condizioni",
            "satisfactory",
            "satisfaisant",
            "satisfactorio",
            "zufriedenstellend",
            "usurato",
            "da sistemare",
        ),
    ),
    ("good", ("buone condizioni", "buono stato", "good condition", "bon etat", "gut erhalten")),
)

# What the condition field itself prints, whole (the other languages' labels are in the shared parser
# configuration, ``vinted_parser.json``, which is read first). Only ever applied to a whole label: a bare
# "good" or "gut" inside a description ("Good brand", "gut verpackt") says nothing about the condition.
CONDITION_LABELS: dict[str, str] = {
    "ottime": "very_good",
    "ottimo": "very_good",
    "ottima": "very_good",
    "buone": "good",
    "buono": "good",
    "buona": "good",
    "soddisfacenti": "satisfactory",
    "soddisfacente": "satisfactory",
    "discrete": "satisfactory",
    "discreto": "satisfactory",
    "very good": "very_good",
    "good": "good",
    "sehr gut": "very_good",
    "gut": "good",
    "tres bon etat": "very_good",
    "bon etat": "good",
    "muy bueno": "very_good",
    "bueno": "good",
    "heel goed": "very_good",
    "zeer goed": "very_good",
    "goed": "good",
    "redelijk": "satisfactory",
}

# Replica / counterfeit wording (IT / EN / FR / DE / ES). Regexes on the folded text, written in the syntax Python
# and JavaScript share: the extension's instant verdict reads the same list. Wording that only *sounds* like a
# replica warning ("simile al nuovo", "ispirato al design anni 90", "copia dello scontrino") is not here; the
# engine also ignores a term inside a negation ("non è un fake") or next to a document word (see
# ``NEGATION_WORDS`` and ``DOCUMENT_WORDS``), and reads "tipo/stile <brand>" apart (``CLAIM_CUES``).
SUSPICIOUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brepl(?:ica|iche|icas)\b|\breplique[s]?\b|\breplicas?\b", "replica"),
    (r"\bfakes?\b", "fake"),
    (
        r"\bfals[oaie]\b|\bfalsificat[oaie]\b|\bfalsification\b|\bfalsificacion\b|\bfalschung\b|\bgefalscht\b",
        "falso",
    ),
    (
        r"\bimitazion[ei]\b|\bimitation\b|\bimitacion\b|\bimitiert\b|\bnachgemacht\b|\bnachahmung\b",
        "imitazione",
    ),
    (
        r"\bcontraffatt[oaie]\b|\bcontraffazion[ei]\b|\bcounterfeit\b|\bcontrefacon\b|\bcontrefait[es]?\b|\bplagiat\b",
        "contraffatto",
    ),
    (r"\bdupes?\b|\bknock\s?-?off\b|\blook\s?-?alikes?\b", "dupe"),
    (r"\b1\s?:\s?1\b", "1:1"),
    (r"\ba{3,}\b", "AAA"),
    (
        r"\bua\s+(?:quality|version|batch|level)\b|\b(?:super|master|first)\s+(?:copy|copies)\b|\bmirror\s+quality\b",
        "copia di alta qualità",
    ),
    (
        r"\bnon\s+original[ei]\b|\bnot\s+original\b|\bnon\s+autentic[oaie]\b|\bnot\s+authentic\b|\bpas\s+original\b|\bnicht\s+original\b",
        "non originale",
    ),
    (r"\bcopia\b|\bcopie\b|\bcopy\b|\bkopie\b", "copia"),
)
# A suspicious term is not read when one of these comes right before it ("non è un fake", "no replica").
NEGATION_WORDS = frozenset(
    [
        "non",
        "no",
        "not",
        "senza",
        "mai",
        "nessuna",
        "nessuno",
        "niente",
        "pas",
        "ne",
        "sans",
        "ni",
        "kein",
        "keine",
        "keinen",
        "nicht",
        "ohne",
        "sin",
        "without",
        "never",
        "nothing",
    ]
)
# "copia" next to one of these is a copy of a document ("allego copia dello scontrino"), not a copy of the item.
DOCUMENT_WORDS = frozenset(
    [
        "scontrino",
        "ricevuta",
        "fattura",
        "receipt",
        "ticket",
        "certificato",
        "certificate",
        "garanzia",
        "warranty",
        "documento",
        "documenti",
        "invoice",
        "bill",
        "contratto",
        "autenticita",
        "authenticity",
        "quittung",
        "beleg",
        "rechnung",
        "facture",
        "recu",
        "justificante",
    ]
)
# "tipo Nike", "stile Stone Island", "inspired by Gucci": the seller says it is not the brand. Matched in front of a
# brand alias; the brand is then a claim, never the item's brand.
CLAIM_CUES = (
    "tipo",
    "stile",
    "style",
    "like",
    "inspired by",
    "inspired",
    "ispirata a",
    "ispirato a",
    "ispirata",
    "ispirato",
    "simile a",
    "simili a",
    "simil",
    "modello",
    "look",
    "effetto",
    "alla maniera di",
    "in the style of",
    "dans le style de",
    "im stil von",
    "al estilo de",
)

# Model names distinctive enough to imply the brand when the seller omits it
# ("Piumino Nuptse nero" -> The North Face). Generic words (beta, check, oxford...) are excluded.
DISTINCTIVE_MODEL_KEYWORDS = frozenset(
    {
        "custom slim fit",
        "big pony",
        "polo bear",
        "air force 1",
        "air force one",
        "af1",
        "aj1",
        "dunk low",
        "air max 90",
        "air max 95",
        "air jordan 1",
        "tech fleece",
        "center swoosh",
        "firebird",
        "gazelle",
        "spezial",
        "nuptse",
        "nuptse 700",
        "retro nuptse",
        "himalayan",
        "denali",
        "double knee",
        "ghost piece",
        "shadow project",
        "soft shell-r",
        "retro-x",
        "synchilla",
        "better sweater",
        "torrentshell",
        "l.12.12",
        "2002r",
        "9060",
        "reverse weave",
        "bedale",
        "beaufort",
        "skidoo",
        "box logo",
        "gg marmont",
        "nova check",
        "goggle",
    }
)

# Of the distinctive names, the ones that are also ordinary words or shared by a whole genre: they hint at a brand
# but do not identify it on their own ("Sciarpa Himalayan", "Maschera da sci goggle", "T-shirt box logo").
WEAK_MODEL_KEYWORDS = frozenset(
    {
        "gazelle",
        "spezial",
        "himalayan",
        "denali",
        "double knee",
        "goggle",
        "box logo",
        "nova check",
        "firebird",
    }
)

VINTAGE_PATTERN = re.compile(
    r"\bvintage\b|\b(?:19)?[6-9]0'?s\b|\banni\s?['’]?[6-9]0\b|\by2k\b|\bold\s?school\b"
)

# (team, aliases, club names that identify the team even without football words). Plain place names and countries
# ("roma", "napoli", "italia", "inter", "milan", "lazio") are also ordinary words of every other listing ("Made in Italy",
# "Spedisco da Napoli"): they name a team only next to a football word or a season (``FOOTBALL_CONTEXT``).
FOOTBALL_TEAMS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("AC Milan", ("ac milan", "milan"), ("ac milan",)),
    ("Inter", ("inter", "internazionale"), ("internazionale", "inter milan", "fc inter")),
    ("Juventus", ("juventus", "juve"), ("juventus",)),
    ("AS Roma", ("as roma", "roma"), ("as roma",)),
    ("Napoli", ("napoli", "ssc napoli"), ("ssc napoli",)),
    ("Lazio", ("lazio", "ss lazio"), ("ss lazio",)),
    ("Fiorentina", ("fiorentina",), ("fiorentina",)),
    ("Italia", ("italia", "italy", "nazionale"), ("nazionale italiana", "nazionale italia")),
    ("Arsenal", ("arsenal",), ("arsenal",)),
    ("Manchester United", ("manchester united", "man utd", "man united"), ("manchester united", "man utd")),
    ("Liverpool", ("liverpool",), ("liverpool fc",)),
    ("Chelsea", ("chelsea",), ("chelsea fc",)),
    ("Barcelona", ("barcelona", "barca", "barça"), ("fc barcelona",)),
    ("Real Madrid", ("real madrid",), ("real madrid",)),
    (
        "PSG",
        ("psg", "paris saint germain", "paris saint-germain"),
        ("paris saint germain", "paris saint-germain"),
    ),
    ("Bayern", ("bayern", "bayern munich", "bayern monaco"), ("bayern munich", "bayern monaco")),
    ("Ajax", ("ajax",), ("afc ajax",)),
)
# Words that say a text is about a football shirt: without one of them (or a season) a team name is not read.
FOOTBALL_CONTEXT = re.compile(
    r"(?<!\w)(?:maglia|maglie|maglietta|jersey|jerseys|maillot|trikot|camiseta|kit|calcio|football|soccer|fussball|"
    r"futbol|serie a|champions|home|away|third|gara|match|authentic|player issue|replica|ultras|curva|stadio|stagione)(?!\w)"
    r"|(?:19|20)\d{2}\s*[/-]\s*(?:19|20)?\d{2}"
)

SEASON_PATTERN = re.compile(r"\b((?:19|20)\d{2})\s*[/-]\s*((?:19|20)?\d{2})\b")
# A style code needs a digit (and letters or a hyphen): "Articolo", "Style casual", "Reference photos" are words.
PRODUCT_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b([A-Z]{2}\d{4}-\d{3})\b"),  # Nike style code e.g. CW2288-111
    re.compile(
        r"(?i)\b(?:sku|style|art\.?|cod(?:ice)?\.?|ref\.?|codice articolo|articolo)(?=[\s:#.])\s*[:#.]?\s*"
        r"(?:articolo\s*[:#]?\s*)?([A-Z0-9][A-Z0-9-]{4,19})\b"
    ),
    re.compile(r"\b([A-Z]{1,2}\d{5})\b"),  # adidas-like e.g. B75806, GZ12345
)


def plausible_product_code(code: str) -> bool:
    """A code has a digit and either a letter or a hyphen ("CW2288-111", "B75806", "ABC-12345"); "ARTICOLO" or
    "CASUAL" are words, and a bare number is not a style code."""
    has_digit = any(c.isdigit() for c in code)
    return has_digit and (any(c.isalpha() for c in code) or "-" in code)


# Brands that own or contain each other: a field saying one and a title saying the other is not a disagreement
# ("Nike" and "Air Jordan 1", "Ralph Lauren" and "Polo Ralph Lauren", "Armani" and "Emporio Armani").
RELATED_BRANDS: tuple[frozenset[str], ...] = (
    frozenset({"nike", "jordan"}),
    frozenset({"ralph-lauren", "rrl", "ralph-lauren-purple-label", "lauren-ralph-lauren"}),
    frozenset({"armani", "giorgio-armani", "emporio-armani", "armani-jeans", "armani-exchange"}),
    frozenset({"versace", "versace-jeans"}),
    frozenset({"dolce-gabbana"}),
    frozenset({"adidas"}),
)


# Brand aliases that are also the name of the product: "Air Jordan 1", "Converse All Star". They stay in the title when
# the brand is taken out of it, because the model and the kind of item are read from them.
PRODUCT_ALIASES = frozenset({"air jordan", "jordan", "jumpman", "all star", "converse all star"})


def related_brands(a: str, b: str) -> bool:
    return a == b or any(a in group and b in group for group in RELATED_BRANDS)


# ---------------------------------------------------------------------------------------------------------------
# The marketplace's own category path ("Uomo > Vestiti > Camicie e t-shirt > Polo > Polo Ralph Lauren Polo"). The
# deepest entry that names one of our categories decides it: what a seller wrote in the title never overrides it.
# Patterns run on the folded entry; plural forms and stems are written out. The first matching rule wins, so the
# specific rules come first ("giacca di jeans" before "jeans", "piumini" before "giacche").
CRUMB_RULES: tuple[tuple[str, str], ...] = (
    (
        r"giacch\w* di jeans|giubbott\w* di jeans|denim jacket|jeans jacket|veste en jean|jeansjacke",
        "jackets",
    ),
    (r"piumin|puffer|doudoune|daunenjacke|down jacket|plumifer", "puffer-jackets"),
    (r"parka|trench|cappott|\bcoats?\b|manteau|abrigo|\bmantel\b|montgomery|loden|peacoat", "coats"),
    (r"\bpile\b|fleece|polaire|forro polar|micropile", "fleece"),
    (
        r"giacch|giubbott|giubbin|jacket|\bveste|chaqueta|jacke\b|bomber|gilet|blazer|k-way|softshell|windbreaker|impermeabil",
        "jackets",
    ),
    (r"con cappuccio|hoodie|hooded|kapuzen|capuche|capucha", "hoodies"),
    (r"felp|sweatshirt|sudadera|\bsweat\b", "sweatshirts"),
    (
        r"maglion|cardigan|pullover|maglieria|dolcevita|\bpull\b|sweater|jumper|strick|girocollo|scollo a v",
        "knitwear",
    ),
    (r"\bpolo\b|\bpolos\b|polo shirt", "polo-shirts"),
    (r"t-shirt|t shirt|tshirt|magliett|camiset|canott|maniche corte|maniche lunghe", "t-shirts"),
    (r"camici|\bcamisa|chemise|\bhemd|blusa|\bshirts?\b", "shirts"),
    (r"jeans|denim|vaquer", "jeans"),
    (r"pantaloncin|\bshorts\b|bermuda", "shorts"),
    (
        r"\btut[ae]\b|joggers?|tracksuit|pantaloni sportiv|pantaloni della tuta|survet|chandal|felpa e pantaloni",
        "tracksuits",
    ),
    (r"pantalon|chino|cargo|leggings|trousers|\bpants\b|\bhose\b", "trousers"),
    (r"gonn|skirt|\bjupe|falda", "skirts"),
    (r"\babit[oi]\b|\bdress|\brobe\b|vestido|\bkleider?\b", "dresses"),
    (r"costum\w* da bagno|\bswim|bikini|beachwear|maillot de bain|banador|badehose|costumi", "swimwear"),
    (r"stival|stivalett|anfibi|\bboots?\b|\bbottes\b|\bbotas\b|stiefel|scarponi", "boots"),
    (r"mocassin|loafer|stringate|eleganti|francesine|\bderby|brogue|pantofol", "loafers"),
    (r"sandal|ciabatt|infradit|\bslides\b|flip.?flop|zocco|clogs", "sandals"),
    (
        r"sneaker|scarpe (?:da )?(?:ginnastica|corsa|sportive|running)|trainers|baskets|zapatillas|turnschuhe|running",
        "sneakers",
    ),
    (
        r"bors[ae]\b|\bbags?\b|\bzain|backpack|\bsac\b|bolso|tracolla|marsupio|pochette|clutch|\btote\b|rucksack|handtasche",
        "bags",
    ),
    (r"cintur|\bbelts?\b|ceinture|cinturon|gurtel", "belts"),
    (r"sciarp|scarf|scarves|echarpe|bufanda|\bschal\b|foulard|stola", "scarves"),
    (r"cappell|berrett|beanie|\bcaps?\b|casquette|gorra|\bhats?\b|snapback", "caps"),
    (r"orolog|\bwatch|montre|reloj|armbanduhr", "watches"),
)
# Path entries that name a whole group (several of our categories) or an audience: they decide nothing.
CRUMB_GROUPS = re.compile(
    r"^(?:pullover|uomo|donna|bambini|men|women|kids|vestiti|abbigliamento|clothing|accessori|accessories|scarpe|shoes|"
    r"top e t-shirt|camicie e t-shirt|maglioni e felpe|cappotti e giacche|abbigliamento sportivo|"
    r"maglioni e cardigan|pantaloni e leggings|giacche e cappotti|intimo|altro|casa|sportswear)$"
)
# The same path as the marketplace's catalogue links give it: ``/catalog/5492-polo-shirts`` is the entry id and an
# English slug that is the same on every domain, so it does not depend on the language of the page.
CATALOG_SLUG_RULES: tuple[tuple[str, str], ...] = (
    (r"denim-jackets?|jean-jackets?", "jackets"),
    (r"puffer|down-jackets?|padded-jackets?|quilted", "puffer-jackets"),
    (r"parkas?|trench|(?:^|-)coats?(?:$|-)|overcoats?|peacoats?", "coats"),
    (r"fleece", "fleece"),
    (r"jackets?|blazers?|bombers?|gilets?|waistcoats?|windbreakers?|raincoats?", "jackets"),
    (r"hoodies|hoodie|hooded", "hoodies"),
    (r"sweatshirts?", "sweatshirts"),
    (r"jumpers?|sweaters?|cardigans?|knitwear|pullovers?|turtlenecks?", "knitwear"),
    (r"polo-shirts?|polos?", "polo-shirts"),
    (r"t-shirts?|tank-tops?|vests?", "t-shirts"),
    (r"(?:^|-)shirts?(?:$|-)|blouses?", "shirts"),
    (r"jeans|denim", "jeans"),
    (r"shorts|bermudas?", "shorts"),
    (r"tracksuits?|joggers?|jogging|sweatpants|track-?pants", "tracksuits"),
    (r"trousers|pants|chinos?|cargo|leggings", "trousers"),
    (r"skirts?", "skirts"),
    (r"dress|dresses|gowns?", "dresses"),
    (r"swim|bikini|beachwear|swimsuits?", "swimwear"),
    (r"boots?|booties|wellies|ankle-boots", "boots"),
    (r"loafers?|moccasins?|oxfords?|derbies|brogues?|formal-shoes|dress-shoes|ballerinas?|pumps", "loafers"),
    (r"sandals?|flip-flops|slides|clogs|slippers", "sandals"),
    (r"sneakers?|trainers|sports-shoes|running-shoes", "sneakers"),
    (r"bags?|backpacks?|handbags?|clutch|wallets?|purses?|totes?", "bags"),
    (r"belts?", "belts"),
    (r"scarves|scarf|shawls?|foulards?", "scarves"),
    (r"hats?|caps?|beanies|beanie", "caps"),
    (r"watch|watches", "watches"),
)
# Catalogue ids seen on real pages (tests/fixtures/vinted/real) whose slug alone would not tell: they are listed
# here so the mapping is checked against the real pages. A group id (men, clothing, tops-and-t-shirts) maps to nothing.
CATALOG_ID_CATEGORIES: dict[str, str | None] = {
    "5": None,  # men
    "2050": None,  # clothing
    "76": None,  # tops-and-t-shirts
    "257": "jeans",
    "1819": "jeans",  # straight-fit-jeans
    "5492": "polo-shirts",
}


def catalog_code(href_or_code: str) -> tuple[str, str] | None:
    """``/catalog/5492-polo-shirts?referrer=...`` or ``5492-polo-shirts`` -> ``("5492", "polo-shirts")``."""
    m = re.search(
        r"(?:^|/catalog/)(\d{1,6})-([a-z0-9][a-z0-9-]{0,80})(?=$|[/?#])", href_or_code.strip().lower()
    )
    return (m.group(1), m.group(2)) if m else None


@dataclass
class Taxonomy:
    brands: tuple[BrandSpec, ...] = BRANDS
    categories: tuple[CategorySpec, ...] = CATEGORIES
    extra_brands: list[BrandSpec] = field(default_factory=list)

    @cached_property
    def all_brands(self) -> tuple[BrandSpec, ...]:
        known = {b.slug for b in self.brands}
        return self.brands + tuple(b for b in self.extra_brands if b.slug not in known)

    @cached_property
    def brand_by_slug(self) -> dict[str, BrandSpec]:
        return {b.slug: b for b in self.all_brands}

    @cached_property
    def category_by_slug(self) -> dict[str, CategorySpec]:
        return {c.slug: c for c in self.categories}

    @cached_property
    def brand_alias_patterns(self) -> list[tuple[re.Pattern[str], BrandSpec, str]]:
        """Alias regexes sorted longest-first so 'polo ralph lauren' wins over 'polo'."""
        entries: list[tuple[str, BrandSpec]] = []
        for brand in self.all_brands:
            for alias in {*brand.aliases, brand.name}:
                entries.append((fold(alias), brand))
        entries.sort(key=lambda e: len(e[0]), reverse=True)
        return [(re.compile(rf"(?<![\w&]){re.escape(a)}(?![\w&])"), b, a) for a, b in entries]

    @cached_property
    def category_keyword_patterns(self) -> list[tuple[re.Pattern[str], CategorySpec, str]]:
        entries: list[tuple[str, CategorySpec]] = []
        for cat in self.categories:
            for kw in cat.keywords:
                entries.append((fold(kw), cat))
        entries.sort(key=lambda e: len(e[0]), reverse=True)
        return [(re.compile(rf"(?<!\w){re.escape(k)}(?!\w)"), c, k) for k, c in entries]

    def leaf_categories(self) -> list[CategorySpec]:
        return [c for c in self.categories if c.parent is not None]

    def parent_of(self, slug: str | None) -> str | None:
        if not slug:
            return None
        spec = self.category_by_slug.get(slug)
        return spec.parent if spec else None

    def with_extra_brands(self, extra: list[BrandSpec]) -> Taxonomy:
        return Taxonomy(brands=self.brands, categories=self.categories, extra_brands=extra)


DEFAULT_TAXONOMY = Taxonomy()
