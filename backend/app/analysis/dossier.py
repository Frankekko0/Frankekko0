"""The dossier: one versioned, verifiable record of everything known about a listing.

Twelve passes (P0-P12) each report what they did, what they found and what they could not do and
why. The *analysis coverage* is the share of the applicable passes that ran with enough data; it is
a different number from the data completeness of the listing and from the inspection coverage of
the photos. Signals are typed ``observed`` (read on the source), ``declared`` (written by the
seller) or ``inferred`` (deduced here, with its confidence). The five main reasons of the decision
come with it.

Pure and deterministic: the same facts give the same dossier. Re-running after a change is
incremental: each pass has a fingerprint of the facts it reads, and only the passes whose
fingerprint moved are marked as redone (``changed_passes``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.analysis.categories import CLOTHING, FOOTWEAR, CategoryPlugin, plugin_for
from app.analysis.condition import ConditionReport, build_condition_report
from app.analysis.consistency import ConsistencyInput, Matrix, build_matrix
from app.analysis.coverage import PhotoCoverage, is_analysed, photo_coverage
from app.analysis.labels import LabelReport, build_label_report
from app.analysis.text import TextSignals, analyze_text

DOSSIER_VERSION = 1
PASS_NAMES = {
    "P0": "Normalizzazione e duplicati",
    "P1": "Testo dell'annuncio",
    "P2": "Foto: ruolo e qualità",
    "P3": "Identificazione",
    "P4": "Etichette e codici",
    "P5": "Difetti",
    "P6": "Autenticità",
    "P7": "Misure e vestibilità",
    "P8": "Mercato",
    "P9": "Venditore",
    "P10": "Costi e logistica",
    "P11": "Matrice di coerenza",
    "P12": "Decisione",
}
DONE, PARTIAL, NOT_POSSIBLE, NOT_APPLICABLE = "done", "partial", "not_possible", "not_applicable"
CREDIT = {DONE: 1.0, PARTIAL: 0.5, NOT_POSSIBLE: 0.0}


@dataclass
class DossierFacts:
    title: str
    description: str
    price: Decimal
    declared_brand: str | None
    brand_counterfeit_risk: float
    category: str | None
    parent_category: str | None
    declared_size: str | None
    declared_color: str | None
    declared_material: str | None
    declared_condition: str
    model: str | None
    photo_count: int
    shipping_known: bool
    is_repost: bool
    identification: dict[str, Any]
    identification_confidence: int
    vision: dict[str, Any] | None
    seller: dict[str, Any]  # known, rating, reviews, anomalies, score, level
    market: dict[str, Any]  # has_value, n_used, n_sold, fair_value, confidence, data_quality, reason
    fair_market_value: Decimal | None
    authenticity: dict[str, Any] | None
    economics: dict[str, Any]
    decision: dict[str, Any]
    completeness: dict[str, Any]
    listing_age_hours: float | None = None
    price_changes: int = 0
    favourites: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _fingerprint(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _step(
    code: str, status: str, summary: str, *, reason: str | None = None, needs: str | None = None, **data: Any
) -> dict[str, Any]:
    return {
        "code": code,
        "name": PASS_NAMES[code],
        "status": status,
        "summary": summary,
        "reason": reason,
        "needs": needs,
        **({"data": data} if data else {}),
    }


def _sig(
    name: str, value: Any, provenance: str, source: str, confidence: float | None = None
) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "provenance": provenance,
        "source": source,
        "confidence": confidence,
    }


def hidden_gem(f: DossierFacts, matrix: Matrix) -> dict[str, Any]:
    """A listing that may be worth more than it looks: a brand the title does not say, a typo in it."""
    reasons: list[str] = []
    seen = [
        t
        for t in (
            (f.vision or {}).get("brand"),
            (f.vision or {}).get("logo"),
        )
        if isinstance(t, dict) and t.get("value") and t.get("certainty") in ("certain", "probable")
    ]
    if not f.declared_brand and seen:
        reasons.append(f"titolo senza marca ma dalle foto sembra «{seen[0]['value']}»")
    typo = (f.identification.get("flags") or {}).get("misspelled_brand")
    if typo:
        reasons.append(f"marca scritta «{typo.get('written')}», probabilmente «{typo.get('brand')}»")
    mismatch = (f.identification.get("flags") or {}).get("category_mismatch")
    if mismatch:
        reasons.append(
            f"categoria dichiarata «{mismatch.get('declared')}», nelle foto «{mismatch.get('seen') or mismatch.get('detected')}»"
        )
    flag = bool(reasons)
    return {
        "possibly_undervalued": flag,
        "reasons": reasons,
        "to_verify": ["modello esatto", "etichetta del marchio", "composizione"] if flag else [],
        "certainty": "inferred",
    }


def build_dossier(f: DossierFacts, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    plugin: CategoryPlugin = plugin_for(f.category, f.parent_category)
    analysed = is_analysed(f.vision)
    text: TextSignals = analyze_text(f.title, f.description)
    cov: PhotoCoverage = photo_coverage(f.vision, plugin, f.photo_count)
    labels: LabelReport = build_label_report(f.vision, plugin)
    condition: ConditionReport = build_condition_report(f.vision, f.declared_condition, plugin, f.photo_count)
    photos_reused = bool(f.identification.get("photos_reused_by_other_seller"))
    matrix: Matrix = build_matrix(
        ConsistencyInput(
            title=f.title,
            description=f.description,
            price=f.price,
            declared_brand=f.declared_brand,
            declared_size=f.declared_size,
            declared_material=f.declared_material,
            declared_color=f.declared_color,
            declared_condition=f.declared_condition,
            declared_category=f.category,
            declared_parent_category=f.parent_category,
            fair_market_value=f.fair_market_value,
            brand_counterfeit_risk=f.brand_counterfeit_risk,
            text=text,
            vision=f.vision,
            labels=labels,
            condition=condition,
            roles_seen=set(cov.roles_seen),
            photo_count=f.photo_count,
            photos_reused=photos_reused,
            analysed=analysed,
        )
    )
    gem = hidden_gem(f, matrix)

    steps: list[dict[str, Any]] = []
    steps.append(
        _step(
            "P0",
            DONE,
            "Ripubblicazione dello stesso venditore" if f.is_repost else "Annuncio unico",
            repost=f.is_repost,
            photos_reused_by_other_seller=photos_reused,
            price_changes=f.price_changes,
        )
    )
    if text.available:
        steps.append(
            _step(
                "P1",
                DONE,
                f"Lingua {text.language}; {len(text.declared_defects)} difetti dichiarati; {len(text.risk_phrases)} frasi a rischio",
                language=text.language,
                declared_defects=text.declared_defects,
                risk_phrases=text.risk_phrases,
                motivated_seller=text.motivated_seller,
                open_to_offers=text.open_to_offers,
                vague_phrases=text.vague_phrases,
                injection_suspected=text.injection_suspected,
            )
        )
    else:
        steps.append(
            _step(
                "P1",
                NOT_POSSIBLE,
                "Solo il titolo è stato letto.",
                reason="descrizione non disponibile in questa lettura (es. card della ricerca)",
                needs="aprire la pagina dell'articolo",
            )
        )
    if f.photo_count == 0:
        steps.append(
            _step(
                "P2",
                NOT_POSSIBLE,
                "Nessuna foto.",
                reason="l'annuncio non ha foto",
                needs="chiedere le foto al venditore",
            )
        )
    elif analysed:
        steps.append(
            _step(
                "P2",
                DONE,
                f"Qualità {cov.photo_quality}/100 · copertura {cov.inspection_coverage}/100",
                **cov.as_dict(),
            )
        )
    else:
        steps.append(
            _step(
                "P2",
                PARTIAL,
                "Misure tecniche sui file; ruolo e contenuto delle foto non analizzati.",
                reason="foto non analizzate dal modello visivo",
                needs="analisi delle foto (chiave AI)",
                **cov.as_dict(),
            )
        )
    steps.append(
        _step(
            "P3",
            DONE if f.identification_confidence >= 50 else PARTIAL,
            f"Identificazione al {f.identification_confidence}%"
            + (f" · modello «{f.model}»" if f.model else " · modello non identificato"),
            reason=None if f.identification_confidence >= 50 else "identificazione debole",
            confidence=f.identification_confidence,
            ensemble="un solo passaggio: nessun ensemble",
        )
    )
    readable = sum(1 for st in labels.states if st.state == "present_readable")
    ocr_only = not analysed and bool((f.vision or {}).get("ocr_facts", {}).get("found"))
    if readable:
        steps.append(
            _step(
                "P4",
                DONE if analysed else PARTIAL,
                f"{readable} etichette leggibili su {len(labels.states)}"
                + ("" if analysed else " (lettura OCR locale)"),
                reason=None
                if analysed
                else "solo OCR locale, senza modello visivo: lettura probabile, non certa",
                needs=None if analysed else "analisi delle foto (chiave AI) per confermare",
                **labels.as_dict(),
            )
        )
    elif analysed:
        steps.append(
            _step(
                "P4",
                PARTIAL,
                f"0 etichette leggibili su {len(labels.states)}",
                reason="nessuna etichetta leggibile nelle foto",
                needs="foto dell'etichetta interna e di quella di lavaggio",
                **labels.as_dict(),
            )
        )
    else:
        steps.append(
            _step(
                "P4",
                NOT_POSSIBLE,
                "Etichette non lette.",
                reason="foto non analizzate dal modello visivo e nessun testo letto dall'OCR locale",
                needs="analisi delle foto (chiave AI) o OCR locale installato",
            )
        )
    if analysed:
        thin = cov.inspection_coverage is not None and cov.inspection_coverage < 60
        steps.append(
            _step(
                "P5",
                PARTIAL if thin else DONE,
                f"{len(condition.visible_defects)} difetti visibili, {len(condition.potential_defects)} potenziali · {condition.label}",
                reason="le foto non mostrano abbastanza dell'articolo per escludere difetti"
                if thin
                else None,
                needs="le foto mancanti dell'elenco" if thin else None,
                **condition.as_dict(),
            )
        )
    else:
        steps.append(
            _step(
                "P5",
                NOT_POSSIBLE,
                "Condizioni non verificate dalle foto.",
                reason="foto non analizzate dal modello visivo"
                + (" (l'OCR legge solo il testo)" if ocr_only else ""),
                needs="analisi delle foto (chiave AI)",
            )
        )
    a = f.authenticity or {}
    steps.append(
        _step(
            "P6",
            DONE if analysed else PARTIAL,
            f"{a.get('label', 'Non valutata')} (mai «autentico al 100%»)",
            reason=None if analysed else "senza foto analizzate l'autenticità resta non verificabile",
            needs=None if analysed else "foto di etichetta, codici e logo",
            verdict=a.get("verdict"),
            p_authentic=a.get("p_authentic"),
        )
    )
    if plugin not in (CLOTHING, FOOTWEAR):
        steps.append(_step("P7", NOT_APPLICABLE, "Misure e vestibilità non applicabili a questa categoria."))
    elif text.measures_cm:
        steps.append(
            _step(
                "P7",
                DONE,
                "Misure dichiarate: " + ", ".join(f"{k} {v:g} cm" for k, v in text.measures_cm.items()),
                measures_cm=text.measures_cm,
                table="generica e indicativa",
            )
        )
    else:
        steps.append(
            _step(
                "P7",
                NOT_POSSIBLE,
                "Nessuna misura dichiarata.",
                reason="le misure non sono nel testo e dalle foto non si stimano senza un oggetto di riferimento",
                needs="chiedere larghezza e lunghezza al venditore",
            )
        )
    m = f.market
    if m.get("has_value"):
        steps.append(
            _step(
                "P8",
                DONE if m.get("data_quality") == "ok" else PARTIAL,
                f"{m['n_used']} comparabili ({m['n_sold']} venduti) · confidenza {m['confidence']}",
                reason=None if m.get("data_quality") == "ok" else m.get("reason"),
                **{k: m.get(k) for k in ("n_used", "n_sold", "confidence", "data_quality")},
            )
        )
    else:
        steps.append(
            _step(
                "P8",
                NOT_POSSIBLE,
                "Valore di mercato non stimabile.",
                reason=m.get("reason") or "comparabili insufficienti",
                needs="più annunci simili osservati",
            )
        )
    s = f.seller
    if s.get("known"):
        steps.append(
            _step(
                "P9",
                DONE,
                f"Affidabilità {s.get('score')}/100 ({s.get('level')})",
                rating=s.get("rating"),
                reviews=s.get("reviews"),
                anomalies=s.get("anomalies"),
            )
        )
    else:
        steps.append(
            _step(
                "P9",
                NOT_POSSIBLE,
                "Venditore non noto.",
                reason="profilo del venditore non disponibile in questa lettura",
                needs="aprire la pagina dell'articolo",
            )
        )
    unknown = f.economics.get("unknown_costs") or []
    steps.append(
        _step(
            "P10",
            DONE if f.shipping_known and not unknown else PARTIAL,
            "Costi confermati o stimati" + (f"; ignoti: {', '.join(unknown)}" if unknown else ""),
            reason=(
                None
                if f.shipping_known and not unknown
                else ("costi necessari non determinabili: " + ", ".join(unknown))
                if unknown
                else "spedizione non letta dall'annuncio"
            ),
            needs="il profilo dei costi dell'utente (promozione, spedizione di rivendita)"
            if unknown
            else None,
            cost_status=f.economics.get("cost_status"),
            unknown_costs=unknown,
        )
    )
    verifiable = len(matrix.checks) - len(matrix.not_verifiable)
    steps.append(
        _step(
            "P11",
            DONE if verifiable >= len(matrix.checks) // 2 else PARTIAL if verifiable else NOT_POSSIBLE,
            f"{len(matrix.discrepancies)} discrepanze, {matrix.as_dict()['ok']} coerenti, {len(matrix.not_verifiable)} non verificabili",
            reason=None
            if verifiable >= len(matrix.checks) // 2
            else "molti controlli richiedono le foto analizzate",
            **matrix.as_dict(),
        )
    )
    steps.append(
        _step(
            "P12",
            DONE,
            f"Verdetto {f.decision.get('label', f.decision.get('verdict'))}",
            verdict=f.decision.get("verdict"),
        )
    )

    applicable = [s for s in steps if s["status"] != NOT_APPLICABLE]
    coverage = round(100 * sum(CREDIT[s["status"]] for s in applicable) / len(applicable))
    not_analysable = [
        {
            "code": s["code"],
            "name": s["name"],
            "status": s["status"],
            "reason": s["reason"],
            "needs": s["needs"],
        }
        for s in applicable
        if s["status"] in (PARTIAL, NOT_POSSIBLE)
    ]

    # What each pass reads: when it moves, that pass is redone (and the others are not).
    photo_set = [f.photo_count, (f.vision or {}).get("photo_hashes")]
    inputs = {
        "P0": _fingerprint(f.is_repost, photos_reused, f.price_changes),
        "P1": _fingerprint(f.title, f.description),
        "P2": _fingerprint(
            f.photo_count, (f.vision or {}).get("photo_hashes"), (f.vision or {}).get("analyzer")
        ),
        "P3": _fingerprint(f.identification_confidence, f.model, f.declared_brand),
        # The passes that read the photos are involved whenever the set of photos moves.
        "P4": _fingerprint(
            photo_set,
            (f.vision or {}).get("ocr_facts"),
            (f.vision or {}).get("labels"),
            (f.vision or {}).get("composition"),
            (f.vision or {}).get("size_label"),
        ),
        "P5": _fingerprint(
            photo_set,
            (f.vision or {}).get("defects"),
            (f.vision or {}).get("condition_estimate"),
            f.declared_condition,
        ),
        "P6": _fingerprint(photo_set, f.authenticity, f.brand_counterfeit_risk),
        "P7": _fingerprint(text.measures_cm, f.declared_size),
        "P8": _fingerprint(m.get("n_used"), m.get("confidence"), str(f.fair_market_value)),
        "P9": _fingerprint(s.get("score"), s.get("known")),
        "P10": _fingerprint(f.economics.get("cost_status"), unknown, str(f.price), f.shipping_known),
        "P11": _fingerprint(matrix.as_dict()),
        "P12": _fingerprint(f.decision.get("verdict"), f.decision.get("scores")),
    }
    for step in steps:
        step["fingerprint"] = inputs[step["code"]]
    prev_fp = {s["code"]: s.get("fingerprint") for s in (previous or {}).get("passes", [])}
    changed = [c for c, fp in inputs.items() if previous and prev_fp.get(c) != fp]

    signals = _signals(f, text, labels, condition, matrix, gem)
    reasons = top_reasons(f, matrix, gem)
    return {
        "v": DOSSIER_VERSION,
        "category_plugin": {"key": plugin.key, "label": plugin.label, "covered": plugin.covered},
        "analysis_coverage": coverage,
        "coverage_note": "quota dei passaggi applicabili eseguiti con dati sufficienti (non è la completezza dell'annuncio né la copertura delle foto)",
        "photo_quality": cov.photo_quality,
        "inspection_coverage": cov.inspection_coverage,
        "passes": steps,
        "not_analysable": not_analysable,
        "missing_photos": list(cov.checklist),
        "signals": signals,
        "contradictions": [c.as_dict() for c in matrix.discrepancies],
        "condition": condition.as_dict(),
        "labels": labels.as_dict(),
        "text": {
            k: v
            for k, v in text.as_dict().items()
            if k
            in (
                "language",
                "available",
                "declared_defects",
                "risk_phrases",
                "motivated_seller",
                "open_to_offers",
                "vague_phrases",
                "sale_reason",
                "original_price",
                "injection_suspected",
            )
        },
        "hidden_gem": gem,
        "top_reasons": reasons,
        "changed_passes": changed if previous else list(PASS_NAMES),
    }


def _signals(
    f: DossierFacts,
    text: TextSignals,
    labels: LabelReport,
    condition: ConditionReport,
    matrix: Matrix,
    gem: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if f.declared_brand:
        out.append(_sig("brand", f.declared_brand, "declared", "titolo/campo marca"))
    if f.declared_size:
        out.append(_sig("size", f.declared_size, "declared", "campo taglia"))
    out.append(_sig("condition", f.declared_condition, "declared", "campo condizione"))
    if labels.size_text:
        out.append(_sig("size_label", labels.size_text, "observed", "etichetta della taglia"))
    if labels.composition:
        out.append(_sig("composition", labels.composition, "observed", "etichetta di composizione"))
    for d in condition.visible_defects:
        where = f", foto {d['photo'] + 1}" if d.get("photo") is not None else ""
        out.append(
            _sig(
                f"defect:{d['kind']}",
                d.get("zone") or "zona non indicata",
                "observed",
                f"foto{where}",
                d.get("confidence"),
            )
        )
    if condition.condition_class != "undeterminable":
        out.append(
            _sig(
                "condition_class",
                condition.condition_class,
                "inferred",
                "difetti e copertura delle foto",
                condition.confidence / 100,
            )
        )
    for k, v in text.measures_cm.items():
        out.append(_sig(f"measure:{k}", v, "declared", "descrizione"))
    if text.original_price is not None:
        out.append(_sig("original_price", text.original_price, "declared", "descrizione"))
    for r in text.risk_phrases:
        out.append(_sig(f"risk:{r['code']}", r["evidence"], "observed", "descrizione"))
    if gem["possibly_undervalued"]:
        out.append(_sig("possibly_undervalued", True, "inferred", "; ".join(gem["reasons"]), 0.4))
    for c in matrix.discrepancies:
        out.append(_sig(f"contradiction:{c.code}", c.detail, "inferred", "matrice di coerenza"))
    return out


def top_reasons(f: DossierFacts, matrix: Matrix, gem: dict[str, Any]) -> list[dict[str, str]]:
    """The five reasons behind the verdict, most decisive first: vetoes, then the biggest
    contradictions, then what speaks for the item, then what is still missing."""
    d = f.decision
    out: list[dict[str, str]] = []

    def add(kind: str, text: str) -> None:
        if text and all(text != o["text"] for o in out):
            out.append({"kind": kind, "text": text})

    for v in d.get("vetoes", []):
        if v.get("binding"):
            add("veto", v["label"])
    for c in matrix.discrepancies[:2]:
        if c.severity in ("medium", "high"):
            add(
                "contradiction",
                c.detail if c.impact_eur is None else f"{c.detail} (≈ {c.impact_eur:.0f} € di impatto)",
            )
    for r in d.get("reasons", [])[:3]:
        add("positive", r)
    if gem["possibly_undervalued"]:
        add("lead", "Possibile occasione nascosta: " + "; ".join(gem["reasons"]))
    for w in d.get("warnings", []):
        add("warning", w)
    for m in d.get("missing_info", []):
        if m.get("blocks") in ("strong_buy", "buy"):
            add("missing", m["label"])
    return out[:5]


def compact_dossier(d: dict[str, Any]) -> dict[str, Any]:
    """What the permanent record keeps: enough to read the analysis and see what changed, without the
    bulk (the labels, the text and the photo findings are already in the other blocks)."""
    return {
        "v": d.get("v"),
        "category_plugin": d.get("category_plugin"),
        "analysis_coverage": d.get("analysis_coverage"),
        "photo_quality": d.get("photo_quality"),
        "inspection_coverage": d.get("inspection_coverage"),
        "passes": [
            {
                "code": p["code"],
                "status": p["status"],
                "fingerprint": p.get("fingerprint"),
                "reason": p.get("reason"),
            }
            for p in d.get("passes", [])
        ],
        "not_analysable": d.get("not_analysable"),
        "missing_photos": d.get("missing_photos"),
        "contradictions": [
            {
                "code": c["code"],
                "severity": c["severity"],
                "impact_eur": c.get("impact_eur"),
                "detail": c.get("detail"),
            }
            for c in d.get("contradictions", [])
        ],
        "condition": {
            k: (d.get("condition") or {}).get(k) for k in ("class", "score", "confidence", "price_impact_pct")
        },
        "declared_defects": (d.get("text") or {}).get("declared_defects", []),
        "hidden_gem": d.get("hidden_gem"),
        "top_reasons": d.get("top_reasons"),
        "changed_passes": d.get("changed_passes"),
        "delta": d.get("delta"),
    }


def finalize_dossier(
    dossier: dict[str, Any],
    previous: dict[str, Any] | None,
    previous_inputs: dict[str, Any] | None,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Add what only the history can say: which passes moved and a readable account of the change.

    ``inputs`` are the small observable fields of the listing now and ``previous_inputs`` then (the
    ones the permanent record keeps: price, photo keys, hashes of title and description)."""
    prev_fp = {p["code"]: p.get("fingerprint") for p in (previous or {}).get("passes", [])}
    if previous:
        dossier["changed_passes"] = [
            p["code"] for p in dossier["passes"] if prev_fp.get(p["code"]) != p.get("fingerprint")
        ]
    lines: list[str] = []
    if previous_inputs and not previous_inputs.get("migrated"):
        if previous_inputs.get("price") != inputs.get("price") and previous_inputs.get("price") is not None:
            lines.append(f"Prezzo da {previous_inputs['price']} € a {inputs.get('price')} €")
        bp, ap = previous_inputs.get("photos") or [], inputs.get("photos") or []
        if len(ap) > len(bp):
            lines.append(f"{len(ap) - len(bp)} foto aggiunte")
        elif len(ap) < len(bp):
            lines.append(f"{len(bp) - len(ap)} foto rimosse")
        elif bp != ap:
            lines.append("Foto sostituite")
        if previous_inputs.get("description") != inputs.get("description"):
            now_defects = set((dossier.get("text") or {}).get("declared_defects", []))
            was = set(
                (previous or {}).get("declared_defects")
                or ((previous or {}).get("text") or {}).get("declared_defects")
                or []
            )
            added = sorted(now_defects - was)
            lines.append(
                "Descrizione cambiata"
                + (f": difetto dichiarato dopo la prima analisi «{', '.join(added)}»" if added else "")
            )
        if previous_inputs.get("title") != inputs.get("title"):
            lines.append("Titolo cambiato")
        if previous_inputs.get("status") != inputs.get("status"):
            lines.append(f"Stato da {previous_inputs.get('status')} a {inputs.get('status')}")
    dossier["delta"] = lines
    return dossier
