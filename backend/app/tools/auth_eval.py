"""Measure the authenticity verdicts on a labeled set: false positives and false negatives.

    python -m app.tools.auth_eval [path/to/labeled_cases.json]

* false negative: a counterfeit judged "probably authentic" (the costly error);
* missed: a counterfeit not flagged "at risk" (uncertain or not verifiable: no green light,
  but no red flag either);
* false positive: an authentic item flagged "at risk of counterfeit".
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.authenticity.assess import AuthInput, PhotoFinding, PhotoQuality, assess

DEFAULT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "authenticity" / "labeled_cases.json"
MARKET = 100.0


def to_input(case: dict[str, Any]) -> AuthInput:
    fields = {k: v for k, v in case.items() if k in AuthInput.__dataclass_fields__}
    fields["findings"] = [PhotoFinding(*f) for f in case.get("findings", [])]
    fields["quality"] = [PhotoQuality(*q) for q in case.get("quality", [])]
    return AuthInput(
        brand_name=case.get("brand_name") or case["brand_slug"],
        market_value=MARKET,
        photo_count=case.get("photo_count", 6),
        **{k: v for k, v in fields.items() if k not in ("brand_name", "market_value", "photo_count")},
    )


def evaluate(path: Path = DEFAULT) -> dict[str, Any]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    rows = []
    for c in cases:
        a = assess(to_input(c))
        rows.append(
            {
                "id": c["id"],
                "label": c["label"],
                "verdict": a.verdict,
                "p": round(a.p_authentic, 3),
                "hard": c.get("hard", False),
            }
        )
    fakes = [r for r in rows if r["label"] == "counterfeit"]
    real = [r for r in rows if r["label"] == "authentic"]
    fn = [r["id"] for r in fakes if r["verdict"] == "probably_authentic"]
    missed = [r["id"] for r in fakes if r["verdict"] in ("uncertain", "not_verifiable")]
    fp = [r["id"] for r in real if r["verdict"] == "counterfeit_risk"]
    return {
        "cases": len(rows),
        "counterfeit": len(fakes),
        "authentic": len(real),
        "false_negatives": fn,
        "false_negative_rate": round(len(fn) / len(fakes), 4) if fakes else None,
        "flagged_at_risk": round(1 - (len(fn) + len(missed)) / len(fakes), 4) if fakes else None,
        "missed_not_flagged": missed,
        "false_positives": fp,
        "false_positive_rate": round(len(fp) / len(real), 4) if real else None,
        "matrix": {
            label: dict(Counter(r["verdict"] for r in rows if r["label"] == label))
            for label in ("authentic", "counterfeit")
        },
        "rows": rows,
    }


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    result = evaluate(path)
    for r in result.pop("rows"):
        print(f"{r['id']:4} {r['label']:12} -> {r['verdict']:20} p={r['p']}{'  (hard)' if r['hard'] else ''}")
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
