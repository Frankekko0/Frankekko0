"""What the model said about the photos, checked before anything is stored as a fact.

The model's answer is a *claim*, not a measurement. This module sits between the model and
:class:`~app.vision.types.ImageAnalysis`:

* an answer that is empty, or that lacks the parts every analysis needs (``defects``, ``labels``, ``photo_roles``),
  is **no analysis** (:class:`BadAnswer`): it is never read as "photos checked, no defects, label seen";
* a state that says something positive (``present_readable``, "consistent", a paper tag) is kept only with the
  evidence that backs it: the photo it was read on (one that was really sent) and what was read there. Otherwise it
  is downgraded to what the evidence supports (``insufficient_information``, ``unverifiable``) and counted;
* "a label was seen" is *derived* here from those validated readings (and the local OCR), never copied from the
  boolean the model gives;
* a reading the model calls certain but the local OCR does not confirm on the same photo is capped at ``probable``;
* looser outputs (missing severity, a null list, a count that is a word) are repaired with conservative defaults
  instead of being dropped or crashing the job, and every repair or drop is listed in ``parse_issues``;
* text in the photos or in the answer that addresses the reader is flagged (``injection_suspected``) and caps the
  gallery's claims at ``probable``: it is data to report, never an instruction.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.guardrails import injection_suspected, neutralise
from app.domain.enums import Certainty
from app.identification.taxonomy import fold
from app.vision.types import (
    DEFECT_KINDS,
    LABEL_TYPES,
    DefectFinding,
    ImageAnalysis,
    LabelObservation,
    PhotoEvidence,
    PhotoRole,
    VisualFinding,
)

VALIDATION_VERSION = 1
CORE_KEYS = ("defects", "labels", "photo_roles")
FINDING_KEYS = (
    "brand",
    "logo",
    "model",
    "size_label",
    "composition",
    "product_code",
    "category",
    "color",
    "condition_estimate",
)
MAX_DEFECTS = 20
MAX_LABELS = 24
MAX_FINDINGS = 40
MAX_ISSUES = 30
# The kinds of label that make "a label photo exists" (a hang tag or a receipt is not the item's own label).
LABEL_PHOTO_TYPES = frozenset({"brand_label", "care_label", "size_label", "composition", "sku_code"})
LABEL_STATES = (
    "present_readable",
    "present_unreadable",
    "not_visible",
    "absence_verifiable",
    "insufficient_information",
)
ROLES = frozenset(
    {
        "front",
        "back",
        "label",
        "care_label",
        "detail",
        "worn",
        "flat_lay",
        "packshot",
        "sole",
        "inside",
        "other",
    }
)
# Photos on which the place of a missing label can really be seen.
ABSENCE_ROLES = frozenset({"label", "care_label", "inside"})
SEVERITY_RANK = {"minor": 0, "moderate": 1, "severe": 2}
CERTAINTY_RANK = {"certain": 2, "probable": 1, "unverifiable": 0}
# Local reasons that are statistics of a thumbnail, not facts: two readings of the content can overrule them.
SOFT_REASONS = frozenset({"sfocata", "sovraesposta", "troppo scura"})
OCR_SURE = 0.8  # a line read at least this surely counts as a legible photo
# What each finding says about a kind of label: a reading of the size without any label showing a size is a contradiction.
FINDING_LABEL_TYPE = {
    "size_label": "size_label",
    "composition": "composition",
    "product_code": "sku_code",
}
# The condition scale of the marketplace. Free wording the model may use for it maps onto the scale; anything else
# is dropped (an unknown value is not a condition).
CONDITION_ALIASES: dict[str, str] = {
    "new_with_tags": "new_with_tags",
    "nwt": "new_with_tags",
    "new_without_tags": "new_without_tags",
    "nwot": "new_without_tags",
    "new": "new_without_tags",
    "mint": "new_without_tags",
    "like_new": "very_good",
    "excellent": "very_good",
    "very_good": "very_good",
    "great": "very_good",
    "good": "good",
    "used": "good",
    "fair": "satisfactory",
    "acceptable": "satisfactory",
    "satisfactory": "satisfactory",
    "worn": "satisfactory",
    "poor": "satisfactory",
    "bad": "satisfactory",
    "damaged": "satisfactory",
}
# Words that only restate a claim about the item ("new with tags", "no defects"): a text made of them is the seller's
# assertion, not the reading of a tag or a label.
_CLAIM_WORDS = frozenset(
    {
        "new", "with", "tags", "tag", "nuovo", "nuova", "con", "cartellino", "cartellini", "senza", "no", "not",
        "difetti", "difetto", "defects", "defect", "mint", "nwt", "nwot", "condition", "condizioni", "perfetto",
        "perfetta", "perfect", "visible", "visibile", "label", "etichetta", "ottime", "come", "like", "original",
        "originale", "authentic", "autentico", "autentica", "100",
    }
)  # fmt: skip
_TOKEN = re.compile(r"[a-z0-9]+")


class BadAnswer(Exception):
    """The answer is not an analysis (empty, or lacking what every analysis needs): ask again, at most twice."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


@dataclass
class OcrContext:
    """What the local OCR read, per photo (0-based): the second, independent reading the model is checked against.

    ``ran`` is False when no OCR engine was available: then nothing is compared and nothing is capped."""

    ran: bool = False
    tokens: dict[int, set[str]] = field(default_factory=dict)
    compact: dict[int, str] = field(default_factory=dict)
    confident: set[int] = field(default_factory=set)  # photos with at least one line read surely
    injection: bool = False  # a line read in the photos addresses the reader
    label_photos: list[int] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    size: str | None = None
    codes: list[str] = field(default_factory=list)

    @classmethod
    def from_analysis(cls, base: ImageAnalysis) -> OcrContext:
        ctx = cls(ran=base.ocr_engine is not None)
        for p in base.ocr:
            text = fold(" ".join(x.text for x in p.lines))
            ctx.tokens[p.photo] = set(_TOKEN.findall(text))
            ctx.compact[p.photo] = "".join(_TOKEN.findall(text))
            if any(x.confidence >= OCR_SURE for x in p.lines):
                ctx.confident.add(p.photo)
            if any(injection_suspected(x.text) for x in p.lines):
                ctx.injection = True
        facts = base.ocr_facts or {}
        ctx.label_photos = [int(i) for i in facts.get("label_photos") or [] if isinstance(i, int)]
        ctx.brands = [str(b) for b in [*(facts.get("brands") or []), *(facts.get("logo_text") or [])]]
        ctx.size = str(facts["size"]) if facts.get("size") else None
        ctx.codes = [str(c) for c in (facts.get("codes") or {}).values()]
        return ctx

    def agrees(self, text: str | None, photo: int | None) -> bool | None:
        """Does the OCR of ``photo`` contain what the model says it read there? ``None``: no OCR to compare with."""
        if not self.ran or photo is None:
            return None
        tokens = set(_TOKEN.findall(fold(text or "")))
        if not tokens:
            return False
        seen = self.tokens.get(photo, set())
        compact = self.compact.get(photo, "")
        return any(t in seen or (len(t) >= 3 and t in compact) for t in tokens)


@dataclass
class Validated:
    """The model's answer after the checks: typed, bounded, with only the claims its evidence supports."""

    findings: dict[str, VisualFinding | None] = field(default_factory=dict)
    defects: list[DefectFinding] = field(default_factory=list)
    labels: list[LabelObservation] = field(default_factory=list)
    roles: list[PhotoRole] = field(default_factory=list)
    unobserved_parts: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    photo_findings: list[PhotoEvidence] = field(default_factory=list)
    # Per photo (0-based): (usable, reason) as the model judged it.
    checks: dict[int, tuple[bool, str | None]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    prov_notes: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    injection: bool = False
    label_evidence: list[dict[str, Any]] = field(default_factory=list)
    unobserved_reported: bool = True
    sent: tuple[int, ...] = ()
    more_defects: int = 0


class _Ctx:
    def __init__(self) -> None:
        self.issues: list[str] = []
        self.injection = False

    def issue(self, text: str) -> None:
        if text not in self.issues:
            self.issues.append(text)

    def text(self, raw: Any, limit: int) -> str | None:
        """A model string made safe to store: control characters and delimiters neutralised, bounded, flagged when it
        addresses the reader. ``None`` for anything that is not text."""
        if raw is None or isinstance(raw, bool | dict | list):
            return None
        clean = neutralise(str(raw)).strip()
        if not clean:
            return None
        if injection_suspected(clean):
            self.injection = True
        return clean[:limit]


def _photo(raw: Any, sent: set[int]) -> tuple[int | None, bool]:
    """(0-based photo, cited): ``photo`` is None when the answer cites no photo or one that was not sent."""
    if raw is None or raw == "" or isinstance(raw, bool):
        return None, False
    try:
        n = int(raw)  # "2" and 2.0 are read; the gallery is numbered from 1 in the request
    except (TypeError, ValueError, OverflowError):
        return None, True
    return (n - 1, True) if (n - 1) in sent else (None, True)


def _certainty(raw: Any) -> Certainty:
    try:
        return Certainty(str(raw).strip().lower())
    except ValueError:
        return Certainty.UNVERIFIABLE


def _confidence(raw: Any) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError, OverflowError):
        return 0.5
    return 0.5 if math.isnan(v) else max(0.0, min(1.0, v))


def _box(raw: Any) -> list[float] | None:
    """[x, y, w, h] clamped to the photo, or None when malformed."""
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    if any(math.isnan(v) or math.isinf(v) for v in (x, y, w, h)):
        return None
    x, y, w, h = (max(0.0, min(1.0, v)) for v in (x, y, w, h))
    if w <= 0 or h <= 0:
        return None
    return [round(x, 4), round(y, 4), round(min(w, 1 - x), 4), round(min(h, 1 - y), 4)]


def _lower(c: Certainty) -> Certainty:
    return Certainty.PROBABLE if c == Certainty.CERTAIN else c


def claim_only(text: str | None) -> bool:
    """A text that only restates a claim about the item ("NEW WITH TAGS - no defects"): not the reading of a tag."""
    tokens = _TOKEN.findall(fold(text or ""))
    return bool(tokens) and all(t in _CLAIM_WORDS for t in tokens)


def _same(a: str, b: str) -> bool:
    """Do two short texts name the same thing (a brand and what a label says)?"""
    x, y = "".join(_TOKEN.findall(fold(a))), "".join(_TOKEN.findall(fold(b)))
    if not x or not y:
        return False
    return x == y or (min(len(x), len(y)) >= 3 and (x in y or y in x))


# ------------------------------------------------------------------ the parts
def _roles(data: dict[str, Any], sent: set[int], ctx: _Ctx) -> list[PhotoRole]:
    out: list[PhotoRole] = []
    seen: set[int] = set()
    for r in data.get("photo_roles") or []:
        if not isinstance(r, dict):
            ctx.issue("ruolo di una foto non valido: ignorato")
            continue
        photo, _ = _photo(r.get("photo"), sent)
        role = str(r.get("role") or "").strip().lower()
        if photo is None or role not in ROLES:
            ctx.issue("ruolo riferito a una foto non inviata o sconosciuto: ignorato")
            continue
        if photo not in seen:
            seen.add(photo)
            out.append(PhotoRole(photo=photo, role=role))  # type: ignore[arg-type]
    return out


def _labels(
    data: dict[str, Any], sent: set[int], roles: list[PhotoRole], ocr: OcrContext, ctx: _Ctx
) -> list[LabelObservation]:
    role_of = {r.photo: r.role for r in roles}
    out: list[LabelObservation] = []
    unconfirmed = 0
    for lab in data.get("labels") or []:
        if not isinstance(lab, dict):
            ctx.issue("etichetta non valida: ignorata")
            continue
        ltype = str(lab.get("type") or "")
        if ltype not in LABEL_TYPES:
            ctx.issue(f"tipo di etichetta sconosciuto «{ltype[:30]}»: ignorata")
            continue
        state = str(lab.get("state") or "")
        if state not in LABEL_STATES:
            ctx.issue(f"stato dell'etichetta «{ltype}» non valido: informazione insufficiente")
            state = "insufficient_information"
        photo, _cited = _photo(lab.get("photo"), sent)
        text = ctx.text(lab.get("text"), 160)
        certainty = _certainty(lab.get("certainty"))
        source: str | None = "model"
        if state == "present_readable":
            if photo is None:
                ctx.issue(f"«{ltype}» letta senza una foto inviata: non documentata")
                state, certainty, text = "insufficient_information", Certainty.UNVERIFIABLE, None
            elif not text and not ocr.tokens.get(photo):
                ctx.issue(
                    f"«{ltype}» dichiarata leggibile sulla foto {photo + 1} senza testo letto: non documentata"
                )
                state, certainty = "insufficient_information", Certainty.UNVERIFIABLE
            elif text and claim_only(text):
                ctx.issue(
                    f"«{ltype}»: il testo «{text[:40]}» è un'affermazione, non la lettura di un'etichetta"
                )
                state, certainty = "insufficient_information", Certainty.UNVERIFIABLE
            elif certainty == Certainty.UNVERIFIABLE:
                # Readable but "cannot be verified" contradicts itself: the claim is not documented.
                state = "insufficient_information"
            else:
                agree = ocr.agrees(text, photo) if text else None
                if agree is True:
                    source = "model+ocr"
                elif agree is False:
                    unconfirmed += 1
                    certainty = _lower(certainty)
                elif not text:
                    source = "ocr"
                    text = None
        elif state == "present_unreadable":
            if photo is None:
                ctx.issue(f"«{ltype}» presente senza una foto inviata: non documentata")
                state, certainty = "insufficient_information", Certainty.UNVERIFIABLE
            text = None  # an unreadable label has no text: whatever came with it is not a reading
        elif state == "absence_verifiable":
            if photo is None or role_of.get(photo) not in ABSENCE_ROLES:
                ctx.issue(
                    f"assenza di «{ltype}» senza una foto del punto in cui starebbe: solo «non visibile»"
                )
                state, certainty, photo = "not_visible", Certainty.UNVERIFIABLE, None
            text = None
        else:
            photo = None if state in ("not_visible", "insufficient_information") else photo
            if state in ("not_visible", "insufficient_information"):
                certainty = Certainty.UNVERIFIABLE
        out.append(
            LabelObservation(
                type=ltype,  # type: ignore[arg-type]
                state=state,  # type: ignore[arg-type]
                photo=photo,
                text=text,
                box=_box(lab.get("box")),
                certainty=certainty,
                source=source if state == "present_readable" else None,
            )
        )
        if len(out) >= MAX_LABELS:
            ctx.issue(f"più di {MAX_LABELS} etichette: le altre non sono elencate")
            break
    if unconfirmed:
        ctx.issue(
            f"{unconfirmed} lettura/e del modello senza riscontro dell'OCR sulla stessa foto: certezza ridotta a «probable»"
        )
    return out


def _defects(data: dict[str, Any], sent: set[int], ctx: _Ctx) -> tuple[list[DefectFinding], int]:
    rows: list[DefectFinding] = []
    for d in data.get("defects") or []:
        if not isinstance(d, dict):
            ctx.issue("difetto non valido: ignorato")
            continue
        kind = str(d.get("kind") or "")
        if kind not in DEFECT_KINDS:
            ctx.issue(f"difetto di tipo sconosciuto «{kind[:30]}»: ignorato")
            continue
        severity = str(d.get("severity") or "").strip().lower()
        if severity not in SEVERITY_RANK:
            ctx.issue(f"difetto «{kind}» senza gravità valida: considerato «moderate»")
            severity = "moderate"
        raw_cert = d.get("certainty")
        certainty = _certainty(raw_cert)
        if str(raw_cert or "").strip().lower() not in CERTAINTY_RANK:
            ctx.issue(f"difetto «{kind}» senza certezza valida: «unverifiable»")
        photo, _ = _photo(d.get("photo"), sent)
        if photo is None and certainty == Certainty.CERTAIN:
            ctx.issue(f"difetto «{kind}» senza una foto inviata: certezza ridotta a «probable»")
            certainty = Certainty.PROBABLE
        if (d.get("confidence") is None) or not isinstance(d.get("confidence"), int | float):
            ctx.issue(f"difetto «{kind}» senza confidenza valida: 0,5")
        rows.append(
            DefectFinding(
                kind=kind,  # type: ignore[arg-type]
                severity=severity,  # type: ignore[arg-type]
                certainty=certainty,
                description=ctx.text(d.get("description"), 200),
                zone=ctx.text(d.get("zone"), 60),
                photo=photo,
                box=_box(d.get("box")),
                confidence=_confidence(d.get("confidence")),
            )
        )
    # The worst first, so that the cap never cuts a hole in favour of a crease.
    rows.sort(key=lambda r: (SEVERITY_RANK[r.severity], CERTAINTY_RANK[r.certainty.value]), reverse=True)
    more = max(0, len(rows) - MAX_DEFECTS)
    if more:
        ctx.issue(f"{more} difetti meno gravi non elencati (oltre i {MAX_DEFECTS} riportati)")
    return rows[:MAX_DEFECTS], more


def _finding(key: str, raw: Any, ctx: _Ctx, slugs: set[str]) -> VisualFinding | None:
    if raw is None:
        return None
    if isinstance(raw, str):  # a bare value, without certainty: kept, not trusted
        raw = {"value": raw, "certainty": "unverifiable", "confidence": 0.3}
        ctx.issue(f"«{key}» senza certezza: «unverifiable»")
    if not isinstance(raw, dict):
        ctx.issue(f"«{key}» in un formato non valido: ignorato")
        return None
    value = ctx.text(raw.get("value"), 120)
    if not value:
        return None
    certainty = _certainty(raw.get("certainty"))
    if str(raw.get("certainty") or "").strip().lower() not in CERTAINTY_RANK:
        ctx.issue(f"«{key}» senza certezza valida: «unverifiable»")
    if key == "condition_estimate":
        mapped = CONDITION_ALIASES.get(fold(value).replace(" ", "_").replace("-", "_"))
        if mapped is None:
            ctx.issue(f"condizione stimata «{value[:30]}» fuori dalla scala: ignorata")
            return None
        value = mapped
    elif key == "category" and slugs and value not in slugs:
        ctx.issue(f"categoria «{value[:30]}» fuori dall'elenco ammesso: ignorata")
        return None
    return VisualFinding(
        value=value,
        certainty=certainty,
        confidence=_confidence(raw.get("confidence")),
        evidence=ctx.text(raw.get("evidence"), 160),
    )


def _photo_findings(data: dict[str, Any], sent: set[int], ocr: OcrContext, ctx: _Ctx) -> list[PhotoEvidence]:
    out: list[PhotoEvidence] = []
    unconfirmed = 0
    for f in data.get("photo_findings") or []:
        if not isinstance(f, dict):
            ctx.issue("riscontro su una foto non valido: ignorato")
            continue
        photo, _ = _photo(f.get("photo"), sent)
        if photo is None:
            ctx.issue("riscontro riferito a una foto non inviata: ignorato")
            continue
        verdict = str(f.get("verdict") or "").strip().lower()
        if verdict not in ("consistent", "concern", "unreadable"):
            ctx.issue("riscontro con esito non valido: «unreadable»")
            verdict = "unreadable"
        certainty = _certainty(f.get("certainty"))
        detail = ctx.text(f.get("detail"), 200) or ""
        kind = ctx.text(f.get("kind"), 30) or "overall"
        if verdict == "consistent":
            # "Looks right" is worth something only with what was seen: no detail, no claim.
            if not detail:
                ctx.issue(f"riscontro «{kind}» coerente senza dettaglio letto: non documentato")
                certainty = Certainty.UNVERIFIABLE
            elif kind in ("label", "care_tag", "code") and ocr.agrees(detail, photo) is False:
                unconfirmed += 1
                certainty = _lower(certainty)
        out.append(
            PhotoEvidence(
                photo=photo,
                kind=kind,
                verdict=verdict,  # type: ignore[arg-type]
                certainty=certainty,
                detail=detail,
                box=_box(f.get("box")),
            )
        )
        if len(out) >= MAX_FINDINGS:
            break
    if unconfirmed:
        ctx.issue(
            f"{unconfirmed} riscontro/i «coerente» senza riscontro dell'OCR: certezza ridotta a «probable»"
        )
    return out


def _count(raw: Any, cap: int, key: str, ctx: _Ctx) -> int:
    try:
        n = int(raw or 0)
    except (TypeError, ValueError, OverflowError):
        ctx.issue(f"conteggio «{key}» non valido: 0")
        return 0
    if n < 0 or n > cap:
        ctx.issue(f"conteggio «{key}» fuori misura ({n}): limitato a 0..{cap}")
    return max(0, min(cap, n))


def _string_list(raw: Any, limit: int, size: int, ctx: _Ctx) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        text = ctx.text(item, size)
        if text:
            out.append(text)
    return out[:limit]


def _checks(raw: Any, sent: set[int]) -> dict[int, tuple[bool, str | None]]:
    out: dict[int, tuple[bool, str | None]] = {}
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        photo, _ = _photo(c.get("photo"), sent)
        if photo is None:
            continue
        reason = c.get("reason")
        out[photo] = (bool(c.get("usable", True)), str(reason)[:40] if reason else None)
    return out


# ------------------------------------------------------------------ the whole answer
def validate_answer(
    data: Any, sent: set[int], ocr: OcrContext | None = None, slugs: set[str] | None = None
) -> Validated:
    """The answer checked, or :class:`BadAnswer` when it is no analysis. ``sent`` are the 0-based positions of the
    photos the model was really given; ``ocr`` the local reading of the same photos."""
    if not isinstance(data, dict) or not data:
        raise BadAnswer("risposta vuota")
    ocr = ocr or OcrContext()
    ctx = _Ctx()
    ctx.injection = ocr.injection
    bad = [k for k in CORE_KEYS if not isinstance(data.get(k), list)]
    if bad:
        raise BadAnswer("mancano " + ", ".join(bad))
    out = Validated(sent=tuple(sorted(sent)))
    roles = _roles(data, sent, ctx)
    labels = _labels(data, sent, roles, ocr, ctx)
    defects, more = _defects(data, sent, ctx)
    photo_findings = _photo_findings(data, sent, ocr, ctx)
    if not (roles or labels or defects or photo_findings):
        # Valid in form, empty in substance: not even the roles of the photos were given.
        raise BadAnswer("nessun contenuto utilizzabile")
    out.roles, out.labels, out.defects, out.photo_findings = roles, labels, defects, photo_findings
    out.more_defects = more
    for key in FINDING_KEYS:
        out.findings[key] = _finding(key, data.get(key), ctx, slugs or set())
    _reconcile_findings(out, labels, ocr, ctx)
    out.unobserved_reported = isinstance(data.get("unobserved_parts"), list)
    out.unobserved_parts = _string_list(data.get("unobserved_parts"), 12, 80, ctx)
    out.concerns = _string_list(data.get("authenticity_concerns"), 6, 160, ctx)
    prov = data.get("provenance")
    prov = prov if isinstance(prov, dict) else {}
    if data.get("provenance") is not None and not isinstance(data.get("provenance"), dict):
        ctx.issue("provenienza delle foto in un formato non valido: ignorata")
    n = len(sent)
    out.counts = {
        k: _count(prov.get(k), n, k, ctx)
        for k in ("stock_or_catalog", "screenshots", "foreign_watermarks", "edited_or_generated")
    }
    out.prov_notes = _string_list(prov.get("notes"), 6, 160, ctx)
    out.checks = _checks(data.get("photo_checks"), sent)
    # "A label was seen" is derived from the readings that were kept, never copied from the model's boolean.
    out.label_evidence = label_evidence(labels, ocr)
    if bool(data.get("has_label_photo")) and not out.label_evidence:
        ctx.issue("il modello dichiara un'etichetta visibile ma nessuna lettura la documenta: non conta")
    if ctx.injection:
        _cap_for_injection(out, ctx)
    out.injection = ctx.injection
    out.issues = ctx.issues[:MAX_ISSUES]
    return out


def label_evidence(labels: list[LabelObservation], ocr: OcrContext) -> list[dict[str, Any]]:
    """The readings that make "a label was seen": readable labels on a photo that was sent, with the text read, and the
    photos where the local OCR read label-like text (size, composition, RN/CA code, barcode)."""
    out: list[dict[str, Any]] = []
    for lab in labels:
        if lab.type in LABEL_PHOTO_TYPES and lab.state == "present_readable" and lab.photo is not None:
            out.append(
                {"type": lab.type, "photo": lab.photo, "source": lab.source or "model", "text": lab.text}
            )
    have = {e["photo"] for e in out}
    for p in ocr.label_photos:
        if p not in have:
            out.append({"type": "ocr_text", "photo": p, "source": "ocr", "text": None})
    return out


def _reconcile_findings(out: Validated, labels: list[LabelObservation], ocr: OcrContext, ctx: _Ctx) -> None:
    """A reading of the size, composition or code needs a label that shows it: when the model's own label list says
    that kind of label is not visible or unreadable, the reading has no source and is not trusted. Where the local OCR
    read the same thing, the two are compared and the finding says whether they agree."""
    for key, ltype in FINDING_LABEL_TYPE.items():
        f = out.findings.get(key)
        if f is None:
            continue
        mine = [lab for lab in labels if lab.type == ltype]
        if mine and not any(lab.state == "present_readable" for lab in mine):
            if f.certainty != Certainty.UNVERIFIABLE:
                ctx.issue(
                    f"«{key}» letta ma l'elenco delle etichette dice che non è leggibile: non documentata"
                )
            f.certainty = Certainty.UNVERIFIABLE
            f.corroborated = False
        if key == "size_label" and ocr.size and f.certainty != Certainty.UNVERIFIABLE:
            same = _same(f.value, ocr.size)
            f.corroborated = same
            if not same:
                ctx.issue(f"taglia: il modello legge «{f.value[:12]}», l'OCR «{ocr.size}»: non concordano")
                f.certainty = _lower(f.certainty)
        if key == "product_code" and ocr.codes and f.certainty != Certainty.UNVERIFIABLE:
            f.corroborated = any(_same(f.value, c) for c in ocr.codes)
    brand = out.findings.get("brand")
    if brand is not None:
        label_texts = [
            lab.text
            for lab in labels
            if lab.type == "brand_label" and lab.state == "present_readable" and lab.text
        ]
        read = any(_same(brand.value, t) for t in label_texts) or any(
            _same(brand.value, b) for b in ocr.brands
        )
        brand.corroborated = True if read else (False if (label_texts or ocr.brands) else None)


def _cap_for_injection(out: Validated, ctx: _Ctx) -> None:
    ctx.issue(
        "testo che dà ordini nelle foto o nella risposta: le certezze della galleria sono ridotte a «probable»"
    )
    for lab in out.labels:
        lab.certainty = _lower(lab.certainty)
    for d in out.defects:
        d.certainty = _lower(d.certainty)
    for f in out.findings.values():
        if f is not None:
            f.certainty = _lower(f.certainty)
    for pf in out.photo_findings:
        pf.certainty = _lower(pf.certainty)


# ------------------------------------------------------------------ writing it on the analysis
def _rank(f: VisualFinding | None) -> tuple[int, float]:
    return (CERTAINTY_RANK[f.certainty.value], f.confidence) if f else (-1, 0.0)


def merge(a: Validated, b: Validated) -> Validated:
    """Two answers about two halves of one gallery as one (a blocked gallery is read in halves, see the analyzer)."""
    out = Validated(sent=tuple(sorted({*a.sent, *b.sent})))
    for key in FINDING_KEYS:
        fa, fb = a.findings.get(key), b.findings.get(key)
        best = fa if _rank(fa) >= _rank(fb) else fb
        if fa is not None and fb is not None and not _same(fa.value, fb.value):
            # The two halves read different things for the same field: neither is trusted.
            best = best.model_copy(update={"certainty": Certainty.UNVERIFIABLE, "corroborated": False})
            out.issues.append(f"le due metà della galleria leggono «{key}» in modo diverso: non verificabile")
        out.findings[key] = best
    out.defects = sorted(
        [*a.defects, *b.defects],
        key=lambda r: (SEVERITY_RANK[r.severity], CERTAINTY_RANK[r.certainty.value]),
        reverse=True,
    )[:MAX_DEFECTS]
    out.labels = [*a.labels, *b.labels][:MAX_LABELS]
    out.roles = [*a.roles, *b.roles]
    out.unobserved_reported = a.unobserved_reported and b.unobserved_reported
    # A part is unobserved only when neither half shows it.
    both = [p for p in a.unobserved_parts if p in b.unobserved_parts]
    out.unobserved_parts = both[:12]
    out.concerns = [*a.concerns, *b.concerns][:6]
    out.photo_findings = [*a.photo_findings, *b.photo_findings][:MAX_FINDINGS]
    out.checks = {**a.checks, **b.checks}
    out.counts = {k: a.counts.get(k, 0) + b.counts.get(k, 0) for k in {*a.counts, *b.counts}}
    out.prov_notes = [*a.prov_notes, *b.prov_notes][:6]
    out.issues = [*out.issues, *a.issues, *[i for i in b.issues if i not in a.issues]][:MAX_ISSUES]
    out.injection = a.injection or b.injection
    out.label_evidence = [*a.label_evidence, *b.label_evidence]
    out.more_defects = a.more_defects + b.more_defects
    return out


def apply(
    base: ImageAnalysis, v: Validated, *, complete: bool = True, excluded: list[int] | None = None
) -> ImageAnalysis:
    """Write the validated answer on the analysis that holds the local measures; returns it."""
    f = v.findings
    base.brand, base.logo, base.model = f.get("brand"), f.get("logo"), f.get("model")
    base.size_label, base.composition = f.get("size_label"), f.get("composition")
    base.product_code, base.category = f.get("product_code"), f.get("category")
    base.color, base.condition_estimate = f.get("color"), f.get("condition_estimate")
    base.defects, base.labels, base.photo_roles = v.defects, v.labels, v.roles
    base.unobserved_parts = v.unobserved_parts
    base.authenticity_concerns = v.concerns
    base.authenticity_positive_signals = []  # no longer asked for, never scored: a good fake looks right
    base.photo_findings = v.photo_findings
    base.photo_quality.has_label_photo = bool(v.label_evidence)
    local = {c.photo: c for c in base.photo_checks}
    ocr = OcrContext.from_analysis(base)
    for i, (usable, reason) in v.checks.items():
        check = local.get(i)
        if check is None:
            continue
        if not usable and check.usable:
            check.usable, check.reason = False, reason or "non leggibile"
        elif usable and not check.usable and check.reason in SOFT_REASONS and i in ocr.confident:
            # Borderline for the local measure, but the model read the photo and so did the OCR: the local gate
            # (a thumbnail statistic) does not overrule two readings of the content.
            check.usable, check.reason = True, None
            v.issues.append(
                f"foto {i + 1}: giudicata «{reason or 'sfocata'}» dalla misura locale ma letta da modello e OCR"
            )
    pv = base.provenance
    pv.stock_or_catalog = max(pv.stock_or_catalog, v.counts.get("stock_or_catalog", 0))
    pv.screenshots = max(pv.screenshots, v.counts.get("screenshots", 0))
    pv.foreign_watermarks = v.counts.get("foreign_watermarks", 0)
    pv.edited_or_generated = v.counts.get("edited_or_generated", 0)
    pv.notes += v.prov_notes
    base.parse_issues = v.issues[:MAX_ISSUES]
    base.injection_suspected = bool(v.injection or base.injection_suspected)
    base.excluded_photos = sorted(set(excluded or []))
    base.validation = {
        "version": VALIDATION_VERSION,
        "complete": bool(complete and not base.excluded_photos),
        "reported": {
            "defects": True,
            "labels": True,
            "roles": bool(v.roles),
            "unobserved": v.unobserved_reported,
        },
        "label_evidence": v.label_evidence[:12],
        "sent": list(v.sent),
        "issues": len(v.issues),
    }
    return base
