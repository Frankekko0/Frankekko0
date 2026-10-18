"""Measure product extraction (identification, text, sizes, conditions, consistency) on the labelled set.

Run it against any checkout to compare before and after on identical inputs::

    cd <checkout>/backend && PYTHONPATH=. python /path/to/measure_extraction.py > out.json

It imports ``app`` from the current directory and loads the labelled tables by path from the repository
that holds this file (``tests/fixtures/extraction``), so the same tables are used for every checkout.
Every section is independent: one that fails on an older checkout is reported as ``error`` and the rest
still run. No database, no network.
"""

from __future__ import annotations

import collections
import dataclasses
import importlib.util
import json
import sys
import time
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "extraction"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"_extraction_{name}", FIXTURES / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CASES = _load("cases").CASES
B = _load("batteries")

from app.identification.engine import IdentificationEngine, ListingText  # noqa: E402
from app.identification.taxonomy import DEFAULT_TAXONOMY  # noqa: E402

ENGINE = IdentificationEngine(DEFAULT_TAXONOMY)
LT_FIELDS = {f.name for f in dataclasses.fields(ListingText)}
ATTRS = ["brand", "model", "category", "gender", "size", "color", "material", "team"]


def listing_text(c: dict[str, Any]) -> ListingText:
    kw: dict[str, Any] = {"title": c["title"], "brand_field": c.get("brand"), "size_field": c.get("size")}
    kw["condition"] = "Ottime"
    if c["kind"] == "item":
        kw.update(
            description=c.get("desc", ""),
            category_field=c.get("cat"),
            color_field=c.get("color"),
            material_field=c.get("mat"),
        )
    for extra in ("category_codes", "brand_code"):
        if extra in c and extra in LT_FIELDS:
            kw[extra] = c[extra]
    return ListingText(**{k: v for k, v in kw.items() if k in LT_FIELDS})


def counts() -> dict[str, collections.Counter[str]]:
    return collections.defaultdict(collections.Counter)


def finish(st: collections.Counter[str]) -> dict[str, Any]:
    ok, wrong, miss, tn = st["ok"], st["wrong"], st["miss"], st["tn"]
    out: dict[str, Any] = {"ok": ok, "wrong": wrong, "miss": miss, "tn": tn}
    out["precision"] = round(ok / (ok + wrong), 3) if ok + wrong else None
    out["strict_recall"] = round(ok / (ok + wrong + miss), 3) if ok + wrong + miss else None
    return out


def section(fn: Any) -> Any:
    def run() -> Any:
        try:
            return fn()
        except Exception:  # noqa: BLE001 - a section that cannot run on this checkout is reported, not fatal
            return {"error": traceback.format_exc(limit=3).strip().splitlines()[-1]}

    return run


@section
def labelled_cases() -> dict[str, Any]:
    stats = counts()
    wrong_cases: dict[str, list[str]] = collections.defaultdict(list)
    certain_wrong = 0
    high_conf_wrong = 0
    for c in CASES:
        r = ENGINE.identify(listing_text(c))
        t = c["t"]
        pred = {
            "brand": r.brand.value,
            "model": r.model.value,
            "category": r.category.value,
            "gender": r.gender.value,
            "size": r.size.value,
            "color": r.color.value,
            "material": r.material.value,
            "team": r.team.value,
        }
        keymap = {"category": "cat"}
        core_wrong = False
        for a in ATTRS:
            k = keymap.get(a, a)
            if k not in t:
                continue
            truth = t[k]
            st = stats[a]
            ok = pred[a] in truth if isinstance(truth, set) else pred[a] == truth
            if truth is None or (isinstance(truth, set) and not truth):
                if pred[a] is None:
                    st["tn"] += 1
                else:
                    st["wrong"] += 1
                    wrong_cases[a].append(c["id"])
                    core_wrong |= a in ("brand", "model", "category", "size")
            elif pred[a] is None:
                st["miss"] += 1
                wrong_cases[a].append(c["id"])
            elif ok:
                st["ok"] += 1
            else:
                st["wrong"] += 1
                wrong_cases[a].append(c["id"])
                core_wrong |= a in ("brand", "model", "category", "size")
                if a == "category" and r.category.certainty.value == "certain":
                    certain_wrong += 1
        if "susp" in t:
            ok = bool(r.suspicious_terms) == t["susp"]
            stats["suspicious"]["ok" if ok else "wrong"] += 1
            if not ok:
                wrong_cases["suspicious"].append(c["id"])
        if core_wrong and r.confidence >= 70:
            high_conf_wrong += 1
    out = {a: finish(stats[a]) for a in [*ATTRS, "suspicious"] if a in stats}
    out["_certain_but_wrong_category"] = certain_wrong
    out["_wrong_core_with_confidence_ge_70"] = high_conf_wrong
    out["_cases"] = len(CASES)
    out["_wrong_ids"] = {k: v for k, v in wrong_cases.items()}
    return out


@section
def defects() -> dict[str, Any]:
    from app.analysis.text import analyze_text

    def ident(t: str) -> bool:
        return bool(ENGINE.identify(ListingText(title="Giacca", description=t)).defect_terms)

    def text(t: str) -> bool:
        return bool(analyze_text("Giacca", t).declared_defects)

    pos = [p for p, _ in B.DEFECT_POS]
    neg = B.DEFECT_NEG
    out: dict[str, Any] = {
        "positives": len(pos),
        "reassurances": len(neg),
        "ident_recall": sum(map(ident, pos)),
        "ident_false_alarms": sum(map(ident, neg)),
        "text_recall": sum(map(text, pos)),
        "text_false_alarms": sum(map(text, neg)),
        "ident_missed": [p for p in pos if not ident(p)],
        "ident_false_alarm_phrases": [p for p in neg if ident(p)],
    }
    return out


@section
def defect_severity() -> dict[str, Any]:
    from app.analysis.defects import extract_defects

    order = {"none": 0, "minor": 1, "moderate": 2, "severe": 3}
    right = 0
    detail = []
    for phrase, expected in B.DEFECT_POS:
        rep = extract_defects(phrase)
        worst = max((order[h.severity] for h in rep.hits), default=0)
        ok = worst >= order[expected] if expected == "severe" else worst == order[expected]
        right += ok
        if not ok:
            detail.append((phrase, expected, worst))
    return {"severity_ok": right, "of": len(B.DEFECT_POS), "wrong": detail}


@section
def suspicious() -> dict[str, Any]:
    def susp(t: str, d: str = "") -> list[str]:
        return ENGINE.identify(ListingText(title=t, description=d, brand_field="Nike")).suspicious_terms

    pos = B.SUSP_POS
    benign = B.SUSP_BENIGN
    return {
        "positives": len(pos),
        "detected": sum(bool(susp(p)) for p in pos),
        "benign": len(benign),
        "benign_flagged": sum(bool(susp("Felpa Nike", p)) for p in benign),
        "missed": [p for p in pos if not susp(p)],
        "false_positives": [p for p in benign if susp("Felpa Nike", p)],
    }


@section
def conditions() -> dict[str, Any]:
    from app.ingestion.normalizer import condition_from_text, normalize_condition

    wrong = [(s, exp, normalize_condition(s).value) for s, exp in B.CONDITION_LABELS if normalize_condition(s).value != exp]
    unknown_ok = sum(normalize_condition(s).value == "unknown" for s in B.CONDITION_UNKNOWN)
    text_wrong = [(s, exp, condition_from_text(s).value) for s, exp in B.CONDITION_TEXT if condition_from_text(s).value != exp]
    return {
        "labels": len(B.CONDITION_LABELS),
        "labels_ok": len(B.CONDITION_LABELS) - len(wrong),
        "labels_wrong": wrong,
        "unknown_stay_unknown": f"{unknown_ok}/{len(B.CONDITION_UNKNOWN)}",
        "text_ok": len(B.CONDITION_TEXT) - len(text_wrong),
        "text_of": len(B.CONDITION_TEXT),
        "text_wrong": text_wrong,
    }


@section
def sizes() -> dict[str, Any]:
    from app.ingestion.normalizer import normalize_size

    bad = []
    for raw, cat, gender, exp in B.SIZES:
        got = normalize_size(raw, cat, gender)
        if got != exp:
            bad.append((raw, cat, gender, exp, got))
    return {"rows": len(B.SIZES), "ok": len(B.SIZES) - len(bad), "mismatches": bad}


@section
def footwear_tokens() -> dict[str, Any]:
    fw: collections.Counter[str] = collections.Counter()
    for c in CASES:
        want = c["t"].get("size")
        if isinstance(want, str) and want.startswith("EU"):
            got = ENGINE.identify(listing_text(c)).size.value
            fw[f"{c['kind']}_{'ok' if got == want else 'wrong'}"] += 1
    return dict(fw)


@section
def colors_materials() -> dict[str, Any]:
    from app.ingestion.normalizer import normalize_color, normalize_material

    out: dict[str, Any] = {}
    cw = [(s, e, normalize_color(s)) for s, e in B.COLORS if normalize_color(s) != e]
    out["colors"] = {"ok": len(B.COLORS) - len(cw), "of": len(B.COLORS), "wrong": cw}
    tw = []
    for t, e in B.COLOR_TITLES:
        got = ENGINE.identify(ListingText(title=t)).color.value
        if got != e:
            tw.append((t, e, got))
    out["colors_in_titles"] = {"ok": len(B.COLOR_TITLES) - len(tw), "of": len(B.COLOR_TITLES), "wrong": tw}
    mw = [(s, e, normalize_material(s)) for s, e in B.MATERIALS if normalize_material(s) != e]
    out["materials"] = {"ok": len(B.MATERIALS) - len(mw), "of": len(B.MATERIALS), "wrong": mw}
    xw = []
    for t, e in B.MATERIAL_TEXTS:
        got = ENGINE.identify(ListingText(title="Capo", description=t)).material.value
        if got != e:
            xw.append((t, e, got))
    out["materials_in_text"] = {"ok": len(B.MATERIAL_TEXTS) - len(xw), "of": len(B.MATERIAL_TEXTS), "wrong": xw}
    return out


@section
def product_codes() -> dict[str, Any]:
    wrong = []
    for d, exp in B.PRODUCT_CODES:
        got = ENGINE.identify(ListingText(title="Felpa", description=d)).product_code.value
        if got != exp:
            wrong.append((d, exp, got))
    return {"ok": len(B.PRODUCT_CODES) - len(wrong), "of": len(B.PRODUCT_CODES), "wrong": wrong}


@section
def paid_price() -> dict[str, Any]:
    from app.analysis.text import analyze_text

    wrong = []
    for d, exp in B.PAID:
        got = analyze_text("Giacca", d).original_price
        if got != exp:
            wrong.append((d, exp, got))
    return {"ok": len(B.PAID) - len(wrong), "of": len(B.PAID), "wrong": wrong}


@section
def brand_coverage() -> dict[str, Any]:
    ref = list(dict.fromkeys(B.BRAND_REFERENCE))
    hit = [b for b in ref if ENGINE.identify(ListingText(title=f"Giacca {b} nera", brand_field=b)).brand.value]
    heavy = [b for b in B.COUNTERFEIT_HEAVY if b in hit]
    risk_ok = 0
    for b in B.COUNTERFEIT_HEAVY:
        r = ENGINE.identify(ListingText(title=f"Borsa {b}", brand_field=b))
        spec = ENGINE.tax.brand_by_slug.get(r.brand.value or "")
        risk_ok += bool(spec and float(spec.counterfeit_risk) >= 0.3)
    return {
        "reference": len(ref),
        "recognised": len(hit),
        "recognised_pct": round(100 * len(hit) / len(ref), 1),
        "counterfeit_heavy": len(B.COUNTERFEIT_HEAVY),
        "counterfeit_heavy_recognised": len(heavy),
        "counterfeit_heavy_with_prior_ge_0.3": risk_ok,
        "not_recognised": [b for b in ref if b not in hit],
    }


@section
def consistency() -> dict[str, Any]:
    from app.analysis.condition import ConditionReport
    from app.analysis.consistency import (
        ConsistencyInput,
        check_brand,
        check_material,
        check_title_description,
    )
    from app.analysis.labels import LabelReport
    from app.analysis.text import analyze_text

    def mk(title: str = "x", desc: str = "", brand: str | None = None, vision: Any = None, labels: Any = None) -> Any:
        return ConsistencyInput(
            title=title, description=desc, price=Decimal("50"), declared_brand=brand, declared_size=None,
            declared_material=None, declared_color=None, declared_condition="very_good",
            declared_category="jeans", declared_parent_category="bottoms", fair_market_value=Decimal("80"),
            brand_counterfeit_risk=0.1, text=analyze_text(title, desc), vision=vision,
            labels=labels or LabelReport([]), condition=ConditionReport("undeterminable", None, 0),
            roles_seen=set(), photo_count=4, analysed=True,
        )  # fmt: skip

    same_bad = [
        (d, s)
        for d, s in B.BRAND_SAME
        if check_brand(mk(brand=d, vision={"brand": {"value": s, "certainty": "certain"}})).status == "discrepancy"
    ]
    diff_ok = [
        (d, s)
        for d, s in B.BRAND_DIFFERENT
        if check_brand(mk(brand=d, vision={"brand": {"value": s, "certainty": "certain"}})).status == "discrepancy"
    ]
    size_bad = [
        (t, d)
        for t, d in B.SIZE_TEXT_NOISE
        if check_title_description(mk(title=t, desc=d)).status == "discrepancy"
    ]
    mat_bad = []
    for title, desc, comp in B.MATERIAL_NOISE:
        c = check_material(mk(title=title, desc=desc, labels=LabelReport([], composition=comp)))
        if c.status == "discrepancy":
            mat_bad.append((title, c.detail))
    return {
        "brand_false_discrepancies": f"{len(same_bad)}/{len(B.BRAND_SAME)}",
        "brand_real_contradictions_kept": f"{len(diff_ok)}/{len(B.BRAND_DIFFERENT)}",
        "size_false_discrepancies": f"{len(size_bad)}/{len(B.SIZE_TEXT_NOISE)}",
        "material_false_discrepancies": f"{len(mat_bad)}/{len(B.MATERIAL_NOISE)}",
        "brand_false": same_bad,
    }


@section
def speed() -> dict[str, Any]:
    cases = [listing_text(c) for c in CASES]
    t0 = time.perf_counter()
    n = 0
    for _ in range(10):
        for lt in cases:
            ENGINE.identify(lt)
            n += 1
    return {"identify_ms_per_call": round(1000 * (time.perf_counter() - t0) / n, 3)}


def main() -> None:
    sections = {
        "labelled_cases": labelled_cases,
        "defects": defects,
        "defect_severity": defect_severity,
        "suspicious": suspicious,
        "conditions": conditions,
        "sizes": sizes,
        "footwear_tokens": footwear_tokens,
        "colors_materials": colors_materials,
        "product_codes": product_codes,
        "paid_price": paid_price,
        "brand_coverage": brand_coverage,
        "consistency": consistency,
        "speed": speed,
    }
    only = set(sys.argv[1:])
    out = {name: fn() for name, fn in sections.items() if not only or name in only}
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
