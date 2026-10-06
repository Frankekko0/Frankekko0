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
) -> BrandSpec:
    return BrandSpec(
        name=name,
        slug=slug,
        aliases=tuple(aliases),
        tier=tier,
        counterfeit_risk=Decimal(risk),
        lines=tuple(LineSpec(n, c, tuple(k)) for n, c, k in (lines or [])),
    )


CATEGORIES: tuple[CategorySpec, ...] = (
    CategorySpec("tops", "Tops", "Top", None),
    CategorySpec("outerwear", "Outerwear", "Capispalla", None),
    CategorySpec("bottoms", "Bottoms", "Pantaloni e tute", None),
    CategorySpec("footwear", "Footwear", "Scarpe", None),
    CategorySpec("accessories", "Accessories", "Accessori", None),
    CategorySpec(
        "hoodies",
        "Hoodies",
        "Felpe con cappuccio",
        "tops",
        (
            "felpa con cappuccio",
            "hoodie",
            "hoody",
            "hooded",
            "kapuzenpullover",
            "sudadera con capucha",
            "sweat a capuche",
            "cappuccio",
        ),
        10,
    ),
    CategorySpec(
        "sweatshirts",
        "Sweatshirts",
        "Felpe",
        "tops",
        (
            "felpa",
            "sweatshirt",
            "crewneck",
            "crew neck",
            "girocollo",
            "sweat",
            "sudadera",
            "half zip",
            "quarter zip",
            "mezza zip",
        ),
        12,
    ),
    CategorySpec("polo-shirts", "Polo shirts", "Polo", "tops", ("polo shirt", "polo", "polos"), 12),
    CategorySpec(
        "t-shirts",
        "T-shirts",
        "T-shirt",
        "tops",
        ("t-shirt", "t shirt", "tshirt", "tee", "maglietta", "camiseta", "maglia maniche corte"),
        14,
    ),
    CategorySpec(
        "shirts",
        "Shirts",
        "Camicie",
        "tops",
        ("camicia", "shirt", "chemise", "camisa", "hemd", "oxford", "overshirt", "sovracamicia"),
        16,
    ),
    CategorySpec(
        "knitwear",
        "Knitwear",
        "Maglieria",
        "tops",
        (
            "maglione",
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
        ),
        14,
    ),
    CategorySpec(
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
            "jersey",
        ),
        9,
    ),
    CategorySpec(
        "jackets",
        "Jackets",
        "Giacche",
        "outerwear",
        (
            "giacca",
            "giubbotto",
            "giubbino",
            "jacket",
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
        ),
        12,
    ),
    CategorySpec(
        "coats",
        "Coats",
        "Cappotti",
        "outerwear",
        ("cappotto", "coat", "manteau", "abrigo", "mantel", "trench", "parka", "montgomery"),
        18,
    ),
    CategorySpec(
        "puffer-jackets",
        "Puffer jackets",
        "Piumini",
        "outerwear",
        ("piumino", "puffer", "down jacket", "doudoune", "plumifero", "daunenjacke", "nuptse", "piumone"),
        9,
    ),
    CategorySpec(
        "fleece", "Fleece", "Pile", "outerwear", ("pile", "fleece", "polaire", "forro polar", "sherpa"), 10
    ),
    CategorySpec("jeans", "Jeans", "Jeans", "bottoms", ("jeans", "jean", "denim", "vaqueros"), 16),
    CategorySpec(
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
        ),
        18,
    ),
    CategorySpec(
        "tracksuits",
        "Tracksuits",
        "Tute",
        "bottoms",
        (
            "tuta",
            "tracksuit",
            "track pants",
            "track jacket",
            "track top",
            "survetement",
            "chandal",
            "trainingsanzug",
            "joggers",
            "pantaloni tuta",
        ),
        12,
    ),
    CategorySpec(
        "sneakers",
        "Sneakers",
        "Sneakers",
        "footwear",
        (
            "sneakers",
            "sneaker",
            "scarpe",
            "scarpe da ginnastica",
            "trainers",
            "baskets",
            "zapatillas",
            "turnschuhe",
            "shoes",
            "scarpa",
        ),
        9,
    ),
    CategorySpec(
        "bags", "Bags", "Borse", "accessories", ("borsa", "bag", "zaino", "backpack", "sac", "bolso"), 16
    ),
    CategorySpec(
        "belts", "Belts", "Cinture", "accessories", ("cintura", "belt", "ceinture", "cinturon", "gurtel"), 18
    ),
    CategorySpec(
        "scarves",
        "Scarves",
        "Sciarpe",
        "accessories",
        ("sciarpa", "scarf", "echarpe", "bufanda", "schal"),
        20,
    ),
    CategorySpec(
        "caps",
        "Caps & hats",
        "Cappelli",
        "accessories",
        ("cappello", "cappellino", "cap", "beanie", "berretto", "casquette", "gorra", "bucket hat"),
        14,
    ),
)

BRANDS: tuple[BrandSpec, ...] = (
    _b(
        "Ralph Lauren",
        "ralph-lauren",
        ["ralph lauren", "polo ralph lauren", "polo by ralph lauren", "ralph", "rl"],
        "premium",
        "0.12",
        [
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
            ("Dunk Low", "sneakers", ["dunk low", "dunk"]),
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
        ["the north face", "north face", "tnf"],
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
        [
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
        ["napapijri", "napa"],
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

COLORS: dict[str, tuple[str, ...]] = {
    "black": ("nero", "nera", "neri", "black", "noir", "negro", "schwarz"),
    "white": (
        "bianco",
        "bianca",
        "white",
        "blanc",
        "blanco",
        "weiss",
        "weiß",
        "panna",
        "ecru",
        "crema",
        "cream",
    ),
    "navy": ("blu navy", "navy", "blu scuro", "marine", "bleu marine", "dark blue", "blu notte"),
    "blue": ("blu", "blue", "bleu", "azul", "blau", "royal"),
    "light_blue": ("azzurro", "azzurra", "celeste", "light blue", "sky blue", "bleu ciel"),
    "red": ("rosso", "rossa", "red", "rouge", "rojo", "rot"),
    "burgundy": ("bordeaux", "bordeau", "burgundy", "granata", "vinaccia"),
    "green": ("verde", "green", "vert", "grun", "grün", "forest", "bottle green"),
    "khaki": ("khaki", "kaki", "cachi", "verde militare", "olive", "oliva"),
    "grey": ("grigio", "grigia", "grey", "gray", "gris", "grau", "antracite", "melange"),
    "beige": ("beige", "sabbia", "sand", "camel", "tan", "cammello"),
    "brown": ("marrone", "brown", "marron", "braun", "cioccolato", "moka"),
    "yellow": ("giallo", "gialla", "yellow", "jaune", "amarillo", "gelb", "senape", "mustard"),
    "orange": ("arancione", "arancio", "orange", "naranja"),
    "pink": ("rosa", "pink", "rose"),
    "purple": ("viola", "purple", "violet", "lilla", "lilac", "morado"),
    "multi": ("multicolore", "multicolor", "multicolour", "fantasia", "tie dye"),
}

MATERIALS: dict[str, tuple[str, ...]] = {
    "cashmere": ("cashmere", "cachemire", "kaschmir"),
    "wool": ("lana", "wool", "laine", "merino", "lambswool", "wolle"),
    "cotton": ("cotone", "cotton", "coton", "algodon", "baumwolle", "pique", "piqué", "jersey di cotone"),
    "linen": ("lino", "linen", "lin"),
    "silk": ("seta", "silk", "soie"),
    "leather": ("pelle", "leather", "cuir", "cuero", "leder", "vera pelle"),
    "suede": ("camoscio", "suede", "daim", "scamosciato"),
    "down": ("piuma d'oca", "piume", "down", "duvet", "goose down"),
    "denim": ("denim",),
    "fleece": ("fleece", "pile", "polaire"),
    "nylon": ("nylon", "poliammide", "polyamide"),
    "polyester": ("poliestere", "polyester", "polyester"),
    "gore-tex": ("gore-tex", "goretex", "gore tex"),
}

GENDER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "kids": (
        "bambino",
        "bambina",
        "bimbo",
        "bimba",
        "kids",
        "kid",
        "enfant",
        "nino",
        "junior",
        "boys",
        "girls",
        "ragazzo",
        "ragazza",
    ),
    "women": ("donna", "women", "womens", "woman", "femme", "mujer", "damen", "femminile", "lady", "ladies"),
    "men": ("uomo", "men", "mens", "man", "homme", "hombre", "herren", "maschile"),
    "unisex": ("unisex",),
}

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
            "nwot",
            "mai indossato",
            "never worn",
        ),
    ),
    (
        "very_good",
        (
            "ottime condizioni",
            "ottimo stato",
            "very good",
            "tres bon etat",
            "muy bueno",
            "sehr gut",
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
            "satisfactory",
            "satisfaisant",
            "satisfactorio",
            "zufriedenstellend",
            "usurato",
            "da sistemare",
        ),
    ),
    ("good", ("buone condizioni", "buono stato", "good", "bon etat", "bueno", "gut")),
)

SUSPICIOUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\breplica\b", "replica"),
    (r"\bfake\b", "fake"),
    (r"\bfals[oai]\b", "falso"),
    (r"\bimitazione\b", "imitazione"),
    (r"\bsimil[ei]?\b", "simile/simil"),
    (r"\bispirat[oa]\b|\binspired\b", "ispirato a"),
    (r"\bdupe\b", "dupe"),
    (r"\b1\s?:\s?1\b", "1:1"),
    (r"\ba{3,}\b(?:\s*quality)?", "AAA"),
    (r"\bnon\s+original[ei]\b|\bnot\s+original\b|\bnon\s+autentic[oa]\b", "non originale"),
    (r"\bcopia\b|\bfirst copy\b|\bmirror quality\b", "copia"),
    (r"\bsenza\s+marchio\b|\bunbranded\b", "senza marchio"),
)

DEFECT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bmacchi[ae]\b|\bmacchiolin[ae]\b|\bstain(?:s|ed)?\b|\btaches?\b|\bmanchas?\b", "macchie"),
    (r"\bbuc(?:o|hi|hino)\b|\bholes?\b|\btrous?\b|\bagujeros?\b|\bforellin[oi]\b", "buchi"),
    (r"\bstrapp(?:o|i|ato)\b|\btears?\b|\bripped\b|\bdechir", "strappi"),
    (r"\bscolorit[oaie]\b|\bsbiadit[oaie]\b|\bfaded\b|\bdelave", "scolorimento"),
    (r"\bpilling\b|\bpallin[io]\b|\bboulochee?\b", "pilling"),
    (r"\bdifett[oi]\b|\bdefects?\b|\bdefauts?\b|\bimperfezion[ei]\b|\bflaws?\b", "difetti dichiarati"),
    (r"\bingiallit[oaie]\b|\byellowing\b", "ingiallimento"),
    (r"\bzip\s+rott[ao]\b|\bbroken\s+zip\b|\brott[oaie]\b", "parti rotte"),
    (r"\busurat[oaie]\b|\bsegni\s+di\s+usura\b|\bsigns\s+of\s+wear\b|\bworn\s+out\b", "usura"),
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

VINTAGE_PATTERN = re.compile(
    r"\bvintage\b|\b(?:19)?[6-9]0'?s\b|\banni\s?['’]?[6-9]0\b|\by2k\b|\bold\s?school\b"
)

FOOTBALL_TEAMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AC Milan", ("ac milan", "milan")),
    ("Inter", ("inter", "internazionale")),
    ("Juventus", ("juventus", "juve")),
    ("AS Roma", ("as roma", "roma")),
    ("Napoli", ("napoli", "ssc napoli")),
    ("Lazio", ("lazio",)),
    ("Fiorentina", ("fiorentina",)),
    ("Italia", ("italia", "italy", "nazionale")),
    ("Arsenal", ("arsenal",)),
    ("Manchester United", ("manchester united", "man utd", "man united")),
    ("Liverpool", ("liverpool",)),
    ("Chelsea", ("chelsea",)),
    ("Barcelona", ("barcelona", "barca", "barça")),
    ("Real Madrid", ("real madrid",)),
    ("PSG", ("psg", "paris saint germain", "paris saint-germain")),
    ("Bayern", ("bayern", "bayern munich", "bayern monaco")),
    ("Ajax", ("ajax",)),
)

SEASON_PATTERN = re.compile(r"\b((?:19|20)\d{2})\s*[/-]\s*((?:19|20)?\d{2})\b")
PRODUCT_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b([A-Z]{2}\d{4}-\d{3})\b"),  # Nike style code e.g. CW2288-111
    re.compile(r"(?i)\b(?:sku|style|art\.?|cod(?:ice)?\.?|ref\.?)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{4,19})\b"),
    re.compile(r"\b([A-Z]{1,2}\d{5})\b"),  # adidas-like e.g. B75806, GZ12345
)


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
