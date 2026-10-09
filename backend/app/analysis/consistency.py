"""P11 - the consistency matrix: does everything the listing says agree with itself and the photos?

Every check returns ``ok``, ``discrepancy`` or ``not_verifiable`` with a severity and, when there
is a discrepancy, an estimate of what it costs. A check that cannot be made says why: it is never
reported ``ok`` for lack of evidence.

The economic impact is an *estimate* in euros from fixed rules of thumb (stated in ``IMPACT``), to
rank problems and to tell what matters, not a price. The measures table is a generic one for tops
and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.analysis.condition import ConditionReport
from app.analysis.labels import LabelReport, materials_in
from app.analysis.text import TextSignals, analyze_text
from app.identification.taxonomy import fold
from app.ingestion.normalizer import normalize_color, normalize_size, size_distance

SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
STATUS_LABEL = {"ok": "coerente", "discrepancy": "discrepanza", "not_verifiable": "non verificabile"}

# Share of the price a discrepancy is estimated to cost (rules of thumb, not measurements).
IMPACT = {
    "size_one_step": Decimal("0.08"),
    "size_far": Decimal("0.15"),
    "material_value_driver": Decimal("0.25"),
    "material_other": Decimal("0.08"),
    "brand_contradicted": Decimal("0.50"),
    "title_description_size": Decimal("0.08"),
    "title_description_color": Decimal("0.05"),
    "color": Decimal("0.05"),
    "category": Decimal("0.20"),
    "measures": Decimal("0.08"),
    "tags_not_shown": Decimal("0.10"),
}
# Materials that are what a garment is worth: declaring them without the label is a big claim.
VALUE_MATERIALS = {"cashmere", "wool", "silk", "leather", "down", "suede", "linen"}
COLOR_FAMILIES = [
    {"blue", "navy", "light_blue"},
    {"grey", "black"},
    {"beige", "khaki", "brown"},
    {"red", "burgundy", "pink"},
    {"white", "beige"},
]
# Generic flat chest width (cm, armpit to armpit) per letter size for tops. Indicative only.
TOPS_CHEST_FLAT = {
    "XS": (46, 49),
    "S": (49, 52),
    "M": (52, 55),
    "L": (55, 58),
    "XL": (58, 61),
    "XXL": (61, 65),
}
CLAIM_FIELDS = ("tags", "box", "receipt")


@dataclass
class Check:
    code: str
    label: str
    status: str
    severity: str = "none"
    detail: str = ""
    impact_eur: float | None = None
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "status": self.status,
            "status_label": STATUS_LABEL[self.status],
            "severity": self.severity,
            "detail": self.detail,
            "impact_eur": self.impact_eur,
            "evidence": self.evidence,
        }


@dataclass
class ConsistencyInput:
    title: str
    description: str
    price: Decimal
    declared_brand: str | None
    declared_size: str | None  # normalised
    declared_material: str | None
    declared_color: str | None  # normalised
    declared_condition: str
    declared_category: str | None  # slug
    declared_parent_category: str | None
    fair_market_value: Decimal | None
    brand_counterfeit_risk: float
    text: TextSignals
    vision: dict[str, Any] | None
    labels: LabelReport
    condition: ConditionReport
    roles_seen: set[str]
    photo_count: int
    photos_reused: bool = False
    analysed: bool = False


def _eur(price: Decimal, share: Decimal) -> float:
    return float((price * share).quantize(Decimal("0.01")))


def _vision_text(vision: dict[str, Any] | None, key: str) -> str | None:
    f = (vision or {}).get(key)
    if isinstance(f, dict) and f.get("value") and f.get("certainty") in ("certain", "probable"):
        return str(f["value"])
    return None


def _same_family(a: str, b: str) -> bool:
    return a == b or any(a in fam and b in fam for fam in COLOR_FAMILIES)


def _no(code: str, label: str, why: str) -> Check:
    return Check(code, label, "not_verifiable", "none", why)


def _ok(code: str, label: str, detail: str = "") -> Check:
    return Check(code, label, "ok", "none", detail)


def check_title_description(i: ConsistencyInput) -> Check:
    label = "Titolo e descrizione"
    if not i.text.available:
        return _no(
            "title_description",
            label,
            "descrizione non disponibile in questa lettura (es. card della ricerca)",
        )
    t, d = analyze_text(i.title, None), analyze_text(None, i.description)
    issues: list[tuple[str, str, Decimal, str]] = []
    if t.size and d.size and t.size != d.size:
        issues.append(
            (
                "size",
                f"taglia {t.size} nel titolo, {d.size} nella descrizione",
                IMPACT["title_description_size"],
                "medium",
            )
        )
    if t.color and d.color and not _same_family(t.color, d.color):
        issues.append(
            (
                "color",
                f"colore {t.color} nel titolo, {d.color} nella descrizione",
                IMPACT["title_description_color"],
                "low",
            )
        )
    mt, md = set(t.materials), set(d.materials)
    if mt and md and not (mt & md):
        issues.append(
            (
                "material",
                f"materiale {', '.join(sorted(mt))} nel titolo, {', '.join(sorted(md))} nella descrizione",
                IMPACT["material_other"],
                "medium",
            )
        )
    if not issues:
        return _ok("title_description", label)
    worst = max(issues, key=lambda x: SEVERITY_ORDER[x[3]])
    return Check(
        "title_description", label, "discrepancy", worst[3], "; ".join(x[1] for x in issues),
        _eur(i.price, max(x[2] for x in issues)), [x[0] for x in issues],
    )  # fmt: skip


def check_title_photos(i: ConsistencyInput) -> Check:
    label = "Titolo e foto"
    if not i.analysed:
        return _no("title_photos", label, "foto non analizzate dal modello visivo")
    issues: list[str] = []
    seen_cat = _vision_text(i.vision, "category")
    if seen_cat and i.declared_category and fold(i.declared_category).split("-")[0] not in fold(seen_cat):
        parent = fold(i.declared_parent_category or "")
        if not parent or parent not in fold(seen_cat):
            issues.append(f"le foto sembrano mostrare «{seen_cat}», l'annuncio è «{i.declared_category}»")
    if not issues:
        return _ok("title_photos", label)
    return Check(
        "title_photos",
        label,
        "discrepancy",
        "medium",
        "; ".join(issues),
        _eur(i.price, IMPACT["category"]),
        issues,
    )


def check_brand(i: ConsistencyInput) -> Check:
    label = "Marca: dichiarata, etichetta, logo"
    seen = [
        (s, t)
        for s, t in (
            ("etichetta", i.labels.brand_text),
            ("logo", _vision_text(i.vision, "logo")),
            ("foto", _vision_text(i.vision, "brand")),
        )
        if t
    ]
    if not seen:
        why = (
            "etichetta e logo non visibili o foto non analizzate"
            if i.analysed
            else "foto non analizzate dal modello visivo"
        )
        return _no("brand", label, why)
    if not i.declared_brand:
        s, t = seen[0]
        return Check(
            "brand", label, "discrepancy", "low",
            f"Nessuna marca dichiarata ma da {s} si legge «{t}»: possibile occasione nascosta, da verificare.",
            None, [f"{s}: {t}"],
        )  # fmt: skip
    declared = fold(i.declared_brand)
    bad = [(s, t) for s, t in seen if fold(t) not in declared and declared not in fold(t)]
    if not bad:
        return _ok("brand", label, "marca coerente con " + ", ".join(s for s, _ in seen))
    s, t = bad[0]
    return Check(
        "brand", label, "discrepancy", "high",
        f"Dichiarato «{i.declared_brand}», ma da {s} si legge «{t}».",
        _eur(i.price, IMPACT["brand_contradicted"]), [f"{s}: {t}"],
    )  # fmt: skip


def check_size(i: ConsistencyInput) -> Check:
    label = "Taglia: dichiarata, etichetta, misure"
    issues: list[str] = []
    severity, share = "none", Decimal("0")
    label_size = normalize_size(i.labels.size_text) if i.labels.size_text else None
    if label_size and i.declared_size:
        dist = size_distance(i.declared_size, label_size)
        if label_size != i.declared_size:
            far = dist is None or dist >= 2
            issues.append(f"dichiarata {i.declared_size}, etichetta {label_size}")
            severity = "high" if far else "medium"
            share = IMPACT["size_far"] if far else IMPACT["size_one_step"]
    flat = i.text.measures_cm.get("chest_flat") or (
        i.text.measures_cm["chest_circ"] / 2 if "chest_circ" in i.text.measures_cm else None
    )
    ref = label_size or i.declared_size
    if (
        flat
        and ref in TOPS_CHEST_FLAT
        and (i.declared_parent_category in (None, "tops") or i.declared_category == "tops")
    ):
        lo, hi = TOPS_CHEST_FLAT[ref]
        gap = lo - flat if flat < lo else flat - hi if flat > hi else 0
        if gap > 3:
            issues.append(
                f"larghezza {flat:g} cm, per una {ref} generica ci si aspetta {lo}-{hi} cm (tabella indicativa)"
            )
            if SEVERITY_ORDER["high" if gap > 6 else "medium"] > SEVERITY_ORDER[severity]:
                severity = "high" if gap > 6 else "medium"
            share = max(share, IMPACT["measures"])
    if issues:
        return Check("size", label, "discrepancy", severity, "; ".join(issues), _eur(i.price, share), issues)
    if label_size or flat:
        return _ok(
            "size", label, "taglia coerente" + (" con l'etichetta" if label_size else " con le misure")
        )
    return _no("size", label, "né etichetta della taglia né misure leggibili")


def check_material(i: ConsistencyInput) -> Check:
    label = "Materiale: dichiarato ed etichetta"
    declared = set(materials_in(i.declared_material or "")) | set(i.text.materials) | set(i.text.composition)
    if not i.labels.composition:
        why = (
            "composizione non leggibile sull'etichetta"
            if i.analysed
            else "foto non analizzate dal modello visivo"
        )
        return _no("material", label, why)
    if not declared:
        return _ok("material", label, "nessun materiale dichiarato da confrontare")
    on_label = set(i.labels.composition)
    claimed_missing = declared - on_label
    value_missing = claimed_missing & VALUE_MATERIALS
    # A claim of 100% X with other fibres on the label is also a contradiction.
    declared_pure = [
        m for m, pct in i.text.composition.items() if pct == 100 and i.labels.composition.get(m, 0) < 100
    ]
    if not claimed_missing and not declared_pure:
        return _ok("material", label, "composizione coerente con l'etichetta")
    shown = ", ".join(f"{p}% {m}" for m, p in i.labels.composition.items())
    parts = [f"dichiarato {', '.join(sorted(declared))}", f"etichetta {shown}"]
    big = bool(value_missing) or bool(declared_pure)
    return Check(
        "material", label, "discrepancy", "high" if big else "medium", "; ".join(parts),
        _eur(i.price, IMPACT["material_value_driver"] if big else IMPACT["material_other"]), parts,
    )  # fmt: skip


def check_color(i: ConsistencyInput) -> Check:
    label = "Colore: dichiarato e foto"
    seen = _vision_text(i.vision, "color")
    if not seen or not i.declared_color:
        return _no(
            "color",
            label,
            "colore non determinabile dalle foto" if i.analysed else "foto non analizzate dal modello visivo",
        )
    canon = normalize_color(seen)
    if canon is None:
        return _no("color", label, f"colore visto «{seen}» non riconducibile a un colore noto")
    if _same_family(canon, i.declared_color):
        return _ok("color", label)
    return Check(
        "color",
        label,
        "discrepancy",
        "low",
        f"dichiarato {i.declared_color}, dalle foto {canon}",
        _eur(i.price, IMPACT["color"]),
        [seen],
    )


def check_condition(i: ConsistencyInput) -> Check:
    label = "Condizione: dichiarata e difetti visibili"
    if i.condition.condition_class == "undeterminable":
        return _no("condition", label, "foto non analizzate dal modello visivo")
    bad = [c for c in i.condition.contradictions if c["code"] == "declared_better_than_seen"]
    if bad:
        c = bad[0]
        impact = (i.condition.price_impact_pct or 0) / 100
        return Check(
            "condition",
            label,
            "discrepancy",
            c["severity"],
            c["detail"],
            round(float(i.price) * impact, 2),
            [c["detail"]],
        )
    tags = [c for c in i.condition.contradictions if c["code"] == "declared_tags_not_shown"]
    if tags:
        return Check(
            "condition",
            label,
            "discrepancy",
            "low",
            tags[0]["detail"],
            _eur(i.price, IMPACT["tags_not_shown"]),
            [tags[0]["detail"]],
        )
    return _ok("condition", label)


def check_category(i: ConsistencyInput) -> Check:
    label = "Categoria e oggetto"
    seen = _vision_text(i.vision, "category")
    if not seen or not i.declared_category:
        return _no(
            "category",
            label,
            "categoria non determinabile dalle foto"
            if i.analysed
            else "foto non analizzate dal modello visivo",
        )
    fs, fd = fold(seen), fold(i.declared_category)
    if fd in fs or fs in fd or fold(i.declared_parent_category or "~") in fs:
        return _ok("category", label)
    return Check(
        "category",
        label,
        "discrepancy",
        "medium",
        f"le foto mostrano «{seen}», categoria dichiarata «{i.declared_category}»",
        _eur(i.price, IMPACT["category"]),
        [seen],
    )


def check_price(i: ConsistencyInput) -> Check:
    label = "Prezzo, condizione e marca"
    fmv = i.fair_market_value
    if fmv is None or fmv <= 0:
        return _no("price", label, "valore di mercato non stimabile")
    ratio = float(i.price / fmv)
    risky = i.brand_counterfeit_risk >= 0.3
    if ratio < 0.35 and risky:
        return Check(
            "price",
            label,
            "discrepancy",
            "high",
            f"prezzo al {ratio:.0%} del mercato per una marca spesso contraffatta",
            float(i.price),
            [f"{ratio:.0%} del valore"],
        )
    if ratio < 0.45:
        return Check(
            "price",
            label,
            "discrepancy",
            "medium" if risky else "low",
            f"prezzo al {ratio:.0%} del mercato: verifica perché costa così poco",
            float(i.price) if risky else None,
            [f"{ratio:.0%} del valore"],
        )
    if ratio > 1.3:
        return Check(
            "price",
            label,
            "discrepancy",
            "low",
            f"prezzo al {ratio:.0%} del mercato",
            None,
            [f"{ratio:.0%} del valore"],
        )
    return _ok("price", label)


def check_season(i: ConsistencyInput) -> Check:
    return _no(
        "season", "Stagione o anno e codici", "nessun decodificatore di codici stagionali: non verificabile"
    )


def check_lot(i: ConsistencyInput) -> Check:
    label = "Numero di pezzi e foto"
    if not i.text.lot_pieces:
        return (
            _ok("lot", label, "articolo singolo")
            if i.text.available
            else _no("lot", label, "descrizione non disponibile")
        )
    return _no(
        "lot", label, f"il testo parla di {i.text.lot_pieces} pezzi: non si possono contare dalle foto"
    )


def check_closet(i: ConsistencyInput) -> Check:
    label = "Foto e closet del venditore"
    if i.photos_reused:
        return Check(
            "closet",
            label,
            "discrepancy",
            "high",
            "foto già usate in annunci di altri venditori",
            None,
            ["foto riutilizzate"],
        )
    return _no(
        "closet", label, "stesso ambiente delle foto del venditore non verificabile senza il suo closet"
    )


def check_claims(i: ConsistencyInput) -> Check:
    label = "Descrizione e foto: accessori, scatola, cartellini"
    claims = {"tags": i.text.mentions_tags, "box": i.text.mentions_box, "receipt": i.text.mentions_receipt}
    claimed = [k for k in CLAIM_FIELDS if claims[k]]
    if not claimed:
        return (
            _ok("claims", label, "nessun accessorio dichiarato")
            if i.text.available
            else _no("claims", label, "descrizione non disponibile")
        )
    if not i.analysed:
        return _no("claims", label, "foto non analizzate dal modello visivo")
    tag_state = i.labels.state_of("paper_tag")
    not_shown = [
        c
        for c in claimed
        if (c == "tags" and tag_state != "present_readable")
        or (c == "box" and "packshot" not in i.roles_seen)
        or c == "receipt"
    ]
    if not not_shown:
        return _ok("claims", label)
    names = {"tags": "cartellino", "box": "scatola", "receipt": "scontrino"}
    return Check(
        "claims",
        label,
        "not_verifiable",
        "low",
        "dichiarati ma non mostrati nelle foto: " + ", ".join(names[c] for c in not_shown),
        None,
        not_shown,
    )


CHECKS = (
    check_title_description,
    check_title_photos,
    check_brand,
    check_size,
    check_material,
    check_color,
    check_condition,
    check_category,
    check_season,
    check_price,
    check_lot,
    check_closet,
    check_claims,
)


@dataclass
class Matrix:
    checks: list[Check]

    @property
    def discrepancies(self) -> list[Check]:
        return sorted(
            (c for c in self.checks if c.status == "discrepancy"),
            key=lambda c: (-SEVERITY_ORDER[c.severity], -(c.impact_eur or 0)),
        )

    @property
    def not_verifiable(self) -> list[Check]:
        return [c for c in self.checks if c.status == "not_verifiable"]

    @property
    def total_impact_eur(self) -> float:
        return round(sum(c.impact_eur or 0 for c in self.discrepancies), 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.as_dict() for c in self.checks],
            "discrepancies": len(self.discrepancies),
            "not_verifiable": len(self.not_verifiable),
            "ok": sum(1 for c in self.checks if c.status == "ok"),
            "total_impact_eur": self.total_impact_eur,
            "impact_basis": "stima con regole fisse (quota del prezzo), non una misura",
        }


def build_matrix(i: ConsistencyInput) -> Matrix:
    return Matrix([fn(i) for fn in CHECKS])
