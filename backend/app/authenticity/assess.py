"""Authenticity assessment from every available piece of evidence.

The outcome is one of four verdicts, always with a confidence and never "100% authentic":

* ``probably_authentic`` - ONLY with proof from outside the photos that the user entered (the result of an
  authentication service or app, a verified receipt: ``AuthInput.external_proof``, recorded as declared). Photos
  never get here: labels, codes, QR codes, holograms, certificates and logos are copied by counterfeiters, so a
  photo that looks right is not evidence of the original;
* ``uncertain`` - mixed or weak evidence, including photos whose key details look consistent (said in
  ``photos_consistent``: they remove nothing from the list of what to ask the seller, and raise the probability by
  at most ``PHOTO_POSITIVE_CAP``);
* ``counterfeit_risk`` - concrete red flags (photos, text, price far below the market, seller
  patterns typical of fakes);
* ``not_verifiable`` - the evidence needed is missing (key photos absent, blurry, cropped or too
  far): absence of red flags is *not* turned into a positive result.

Method: start from the brand's counterfeit prior and update the odds with each piece of evidence
(likelihood ratios listed in ``authenticity_rules.json``, per brand when needed). A new seller or
zero reviews only weigh a little and never produce a "counterfeit" verdict on their own.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

RULES_FILE = Path(__file__).with_name("authenticity_rules.json")
MAX_P = 0.97  # never "certainly authentic"
# Details that look right on the photos can raise the probability of authenticity by this much at most (absolute):
# a good fake copies the label, the code and the logo. Evidence against has no such limit.
PHOTO_POSITIVE_CAP = 0.05
# A proof from outside the photos (entered by the user, with their note) is worth this likelihood ratio.
EXTERNAL_PROOF_LR = 8.0
VERDICT_LABELS = {
    "probably_authentic": "Probabilmente autentico",
    "uncertain": "Incerto",
    "counterfeit_risk": "A rischio falso",
    "not_verifiable": "Non verificabile",
}


@lru_cache(maxsize=1)
def load_rules(path: str | None = None) -> dict[str, Any]:
    return json.loads(Path(path or RULES_FILE).read_text(encoding="utf-8"))


def brand_rules(brand_slug: str | None, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    rules = rules or load_rules()
    base = dict(rules["default"])
    specific = rules.get("brands", {}).get(brand_slug or "", {})
    merged = {**base, **{k: v for k, v in specific.items() if k not in ("checks", "key_photos")}}
    merged["key_photos"] = list(dict.fromkeys([*base.get("key_photos", []), *specific.get("key_photos", [])]))
    merged["checks"] = [*specific.get("checks", []), *base.get("checks", [])]
    return merged


@dataclass
class PhotoFinding:
    """One observation on one photo (from the photo analysis)."""

    photo: int  # 0-based position in the gallery, -1 when the analysis did not say which photo
    kind: str  # label | care_tag | code | logo | stitching | zip | button | material | overall
    verdict: str  # consistent | concern | unreadable
    certainty: str = "probable"  # certain | probable | unverifiable
    detail: str = ""
    box: list[float] | None = None  # [x, y, w, h] in 0..1 of the photo, the detail to look at


@dataclass
class PhotoQuality:
    photo: int
    usable: bool
    reason: str | None = None  # blurry, too small, cropped, too far


@dataclass
class AuthInput:
    brand_slug: str | None
    brand_name: str | None
    brand_counterfeit_risk: float
    price: float
    market_value: float | None
    photo_count: int
    suspicious_terms: list[str] = field(default_factory=list)
    seller_reviews: int | None = None
    seller_rating: float | None = None
    seller_multi_size_same_item: bool = False
    photos_reused_by_other_seller: bool = False
    reused_photos: list[int] = field(default_factory=list)  # which photos (when known)
    catalog_photos: list[int] = field(default_factory=list)
    stock_or_catalog_photos: int = 0
    screenshots: int = 0
    foreign_watermarks: int = 0
    edited_or_generated: int = 0
    findings: list[PhotoFinding] = field(default_factory=list)
    quality: list[PhotoQuality] = field(default_factory=list)
    photos_analyzed: bool = False  # a photo analysis ran (otherwise photos are "not checked")
    # Label kinds whose absence was verified on a photo of the place where they would be (brand_label, care_label).
    absent_labels: list[str] = field(default_factory=list)
    # A proof from outside the photos, entered by the user (never inferred): the note they wrote. The only way to
    # "probably authentic".
    external_proof: str | None = None


@dataclass
class Evidence:
    direction: str  # "+" supports authenticity, "-" against, "?" not verifiable
    label: str
    weight: float  # log likelihood ratio used
    photo: int | None = None
    box: list[float] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "label": self.label,
            "weight": round(self.weight, 3),
            "photo": self.photo if self.photo is not None and self.photo >= 0 else None,
            "box": self.box,
        }


@dataclass
class AuthAssessment:
    verdict: str
    label: str
    p_authentic: float
    confidence: int
    evidence: list[Evidence]
    missing_photos: list[str]
    seller_message: str | None
    checks: list[str]
    # The key details looked consistent on the photos. Not a proof (see the module doc): shown as what it is.
    photos_consistent: bool = False
    external_proof: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "label": self.label,
            "p_authentic": round(self.p_authentic, 3),
            "confidence": self.confidence,
            "evidence": [e.as_dict() for e in self.evidence],
            "missing_photos": self.missing_photos,
            "seller_message": self.seller_message,
            "checks": self.checks,
            "photos_consistent": self.photos_consistent,
            "external_proof": self.external_proof,
        }


def _where(photo: int | None) -> str:
    return f"Foto {photo + 1}: " if photo is not None and photo >= 0 else ""


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def assess(inp: AuthInput, rules: dict[str, Any] | None = None) -> AuthAssessment:
    r = brand_rules(inp.brand_slug, rules)
    lr = (rules or load_rules())["likelihood_ratios"]
    prior_risk = min(0.9, max(0.01, float(r.get("counterfeit_prior", inp.brand_counterfeit_risk) or 0.05)))
    score = _logit(1 - prior_risk)
    ev: list[Evidence] = []

    def add(
        direction: str, label: str, ratio: float, photo: int | None = None, box: list[float] | None = None
    ) -> None:
        nonlocal score
        w = math.log(ratio)
        score += w
        ev.append(Evidence(direction, label, w, photo, box))

    # ---- price far below the market (fakes are cheap) -----------------------------------------
    if inp.market_value and inp.market_value > 0 and inp.price > 0:
        ratio = inp.price / inp.market_value
        floor = float(r.get("price_floor_ratio", 0.45))
        if ratio < floor:
            add(
                "-",
                f"Prezzo al {ratio:.0%} del valore di mercato (sotto la soglia del {floor:.0%} per questo brand)",
                lr["price_far_below"],
            )
        elif ratio < floor + 0.15:
            add("-", f"Prezzo al {ratio:.0%} del valore di mercato", lr["price_below"])

    # ---- text -----------------------------------------------------------------------------------
    for term in inp.suspicious_terms:
        add("-", f"Nel testo: {term}", lr["suspicious_text"])

    # ---- seller (weak signals, never decisive on their own) -------------------------------------
    if inp.seller_multi_size_same_item:
        add("-", "Lo stesso articolo nuovo in più taglie dallo stesso venditore", lr["multi_size_same_item"])
    if inp.seller_reviews is not None and inp.seller_reviews >= 50 and (inp.seller_rating or 0) >= 4.7:
        add(
            "+",
            f"Venditore con {inp.seller_reviews} recensioni, {inp.seller_rating:.1f}★",
            lr["established_seller"],
        )

    # ---- photo provenance -------------------------------------------------------------------
    if inp.photos_reused_by_other_seller or inp.reused_photos:
        first = inp.reused_photos[0] if inp.reused_photos else None
        add(
            "-",
            f"{_where(first)}foto identica in un annuncio di un altro venditore (foto riciclata)",
            lr["reused_photos"],
            first,
        )
    if inp.stock_or_catalog_photos or inp.catalog_photos:
        first = inp.catalog_photos[0] if inp.catalog_photos else None
        add(
            "-",
            f"{_where(first)}foto da catalogo o di stock, non dell'articolo reale",
            lr["stock_photos"],
            first,
        )
    if inp.screenshots:
        add("-", "Screenshot al posto di foto dell'articolo", lr["screenshot"])
    if inp.foreign_watermarks:
        add("-", "Filigrana di un altro sito nelle foto", lr["foreign_watermark"])
    if inp.edited_or_generated:
        add("-", "Foto ritoccate o generate", lr["edited_or_generated"])

    # ---- photo findings (label, codes, logo, stitching...) --------------------------------------
    usable = {q.photo for q in inp.quality if q.usable}
    unusable = {q.photo: q.reason for q in inp.quality if not q.usable}
    verified_kinds: set[str] = set()
    certain_kinds: set[str] = set()
    positive = 0.0
    for f in inp.findings:
        if f.photo in unusable or f.verdict == "unreadable" or f.certainty == "unverifiable":
            ev.append(
                Evidence(
                    "?",
                    f"{_where(f.photo)}{KIND_LABELS.get(f.kind, f.kind)} non leggibile"
                    + (f" ({unusable[f.photo]})" if f.photo in unusable else ""),
                    0.0,
                    f.photo,
                    f.box,
                )
            )
            continue
        if f.verdict == "concern":
            ratio = lr["photo_concern_certain"] if f.certainty == "certain" else lr["photo_concern_probable"]
            add("-", f"{_where(f.photo)}{f.detail or KIND_LABELS.get(f.kind, f.kind)}", ratio, f.photo, f.box)
        elif f.verdict == "consistent":
            verified_kinds.add(f.kind)
            if f.certainty == "certain":
                certain_kinds.add(f.kind)
            ratio = (
                lr["photo_consistent_certain"]
                if f.certainty == "certain"
                else lr["photo_consistent_probable"]
            )
            # Consistent details add up, but only so far: a good fake can look right. They are summed apart and enter
            # the probability through a cap (``PHOTO_POSITIVE_CAP``) below.
            room = math.log(lr["max_positive_total"]) - positive
            w = min(math.log(ratio), max(0.0, room))
            if w > 0:
                positive += w
            ev.append(
                Evidence(
                    "+", f"{_where(f.photo)}{f.detail or KIND_LABELS.get(f.kind, f.kind)}", w, f.photo, f.box
                )
            )

    # Unusable photos with nothing read on them: say which ones and why (to ask for new ones).
    referenced = {f.photo for f in inp.findings}
    for photo, reason in sorted(unusable.items()):
        if photo not in referenced:
            ev.append(
                Evidence(
                    "?", f"{_where(photo)}{reason or 'non leggibile'}, dettagli non verificabili", 0.0, photo
                )
            )

    # ---- a label missing where it should be (an at-risk brand without its label is a classic fake) -----------
    if (
        inp.absent_labels
        and float(r.get("counterfeit_prior", inp.brand_counterfeit_risk) or 0) >= AT_RISK_PRIOR
    ):
        what = ", ".join(
            KIND_LABELS.get(k, k) for k in ("label", "care_tag") if _LABEL_OF_KIND[k] in inp.absent_labels
        )
        add(
            "-",
            f"Mancano {what} nel punto in cui dovrebbero stare: chiedi una foto (marca spesso contraffatta)",
            lr["label_absent_at_risk_brand"],
        )

    # ---- proof from outside the photos (entered by the user, never inferred) -----------------------------------
    if inp.external_proof:
        add("+", f"Prova esterna indicata dall'utente: {inp.external_proof[:120]}", EXTERNAL_PROOF_LR)

    # ---- coverage of the key photos ---------------------------------------------------------
    key = r["key_photos"]
    missing = [k for k in key if k not in verified_kinds]
    coverage = (len(key) - len(missing)) / len(key) if key else 0.0
    p_without_photos = _sigmoid(score)
    # Photo details that look right lift the probability a little, never to a certificate: a counterfeiter copies them.
    p = min(MAX_P, _sigmoid(score + positive), p_without_photos + PHOTO_POSITIVE_CAP)
    negatives = sum(
        1 for e in ev if e.direction == "-" and e.weight <= math.log(lr["photo_concern_probable"])
    )
    strong_negative = any(e.direction == "-" and e.weight <= math.log(0.5) for e in ev)

    # A flaw seen on a key detail (label, code, logo...) is direct evidence: certain -> at risk;
    # probable -> at risk unless the rest of the evidence is clearly favourable.
    key_concerns = [
        f
        for f in inp.findings
        if f.verdict == "concern"
        and f.kind in key
        and f.photo not in unusable
        and f.certainty != "unverifiable"
    ]
    if (
        (p < 0.5 and strong_negative)
        or any(f.certainty == "certain" for f in key_concerns)
        or (key_concerns and p < 0.7)
    ):
        verdict = "counterfeit_risk"
    elif (
        not inp.photos_analyzed
        or coverage < float(r.get("min_coverage", 0.5))
        or (usable == set() and inp.quality)
    ):
        verdict = "not_verifiable"
    elif inp.external_proof and p >= 0.85 and negatives == 0:
        # The only way here: a proof from outside the photos that the user entered. Photos never certify.
        verdict = "probably_authentic"
    else:
        verdict = "uncertain"
    photos_consistent = (
        verdict == "uncertain"
        and negatives == 0
        and len(certain_kinds & set(key)) >= min(int(r.get("min_certain_key_photos", 2)), len(key))
    )
    confidence = round(
        100 * (0.35 + 0.45 * coverage + 0.2 * min(1.0, len(ev) / 6)) * (1.0 if inp.photos_analyzed else 0.6)
    )
    confidence = max(5, min(95, confidence))
    missing_labels = [KIND_LABELS.get(k, k) for k in missing]
    message = seller_message(missing_labels, inp.brand_name) if missing_labels else None
    return AuthAssessment(
        verdict=verdict,
        label=VERDICT_LABELS[verdict],
        p_authentic=p,
        confidence=confidence,
        evidence=ev,
        missing_photos=missing_labels,
        seller_message=message,
        checks=list(r.get("checks", [])),
        photos_consistent=photos_consistent,
        external_proof=inp.external_proof[:200] if inp.external_proof else None,
    )


AT_RISK_PRIOR = (
    0.3  # the counterfeit prior from which a brand counts as "at risk" (same as the decision engine's)
)
# The kind of photo finding that stands for each kind of label whose absence can be verified.
_LABEL_OF_KIND = {"label": "brand_label", "care_tag": "care_label"}

KIND_LABELS = {
    "label": "etichetta interna con il marchio",
    "care_tag": "etichetta di lavaggio e composizione",
    "code": "codice prodotto / articolo",
    "logo": "logo o ricamo in primo piano",
    "stitching": "cuciture",
    "zip": "cerniera (marca e tiretto)",
    "button": "bottoni",
    "material": "tessuto da vicino",
    "overall": "articolo intero",
    "sole": "suola",
    "insole": "etichetta interna della scarpa (taglia e codice)",
    "box_label": "etichetta della scatola",
    "hardware": "minuteria metallica (fibbie, anelli)",
}


def seller_message(missing: list[str], brand: str | None) -> str:
    items = "\n".join(f"- {m}" for m in missing)
    what = f" {brand}" if brand else ""
    return (
        f"Ciao! Mi interessa il tuo articolo{what}. Prima di acquistarlo potresti mandarmi qualche "
        f"foto in più, nitida e da vicino?\n{items}\nGrazie mille!"
    )


def photo_evidence(vision: dict[str, Any] | None) -> dict[str, Any]:
    """Photo findings, photo quality and provenance flags stored by the photo analysis.

    Per-photo findings (``photo_findings``) come with the detail's position; older analyses only
    have overall concerns and positive signals, which are used without a photo reference.
    """
    v = vision or {}
    findings: list[PhotoFinding] = []
    for f in v.get("photo_findings") or []:
        try:
            findings.append(
                PhotoFinding(
                    photo=int(f.get("photo", -1)),
                    kind=str(f.get("kind") or "overall"),
                    verdict=str(f.get("verdict") or "unreadable"),
                    certainty=str(f.get("certainty") or "probable"),
                    detail=str(f.get("detail") or "")[:200],
                    box=[float(x) for x in f["box"]][:4] if f.get("box") else None,
                )
            )
        except (TypeError, ValueError):
            continue
    if not findings:
        # Only concerns: what the model calls "positive signals" is never scored (a good fake looks right).
        findings += [
            PhotoFinding(-1, "overall", "concern", "probable", str(c))
            for c in v.get("authenticity_concerns") or []
        ]
    quality = [
        PhotoQuality(int(q["photo"]), bool(q.get("usable", True)), q.get("reason"))
        for q in v.get("photo_checks") or []
        if isinstance(q, dict) and "photo" in q
    ]
    prov = v.get("provenance") or {}
    return {
        "findings": findings,
        "quality": quality,
        "stock_or_catalog_photos": max(
            int(prov.get("stock_or_catalog", 0) or 0), len(prov.get("catalog_photos") or [])
        ),
        "reused_photos": [int(i) for i in prov.get("reused_photos") or []],
        "catalog_photos": [int(i) for i in prov.get("catalog_photos") or []],
        "screenshots": int(prov.get("screenshots", 0) or 0),
        "foreign_watermarks": int(prov.get("foreign_watermarks", 0) or 0),
        "edited_or_generated": int(prov.get("edited_or_generated", 0) or 0),
        "photos_analyzed": bool(v) and v.get("analyzer") not in (None, "heuristic"),
        "absent_labels": sorted(
            {
                str(lab.get("type"))
                for lab in v.get("labels") or []
                if lab.get("state") == "absence_verifiable" and lab.get("photo") is not None
            }
            & {"brand_label", "care_label"}
        ),
    }
