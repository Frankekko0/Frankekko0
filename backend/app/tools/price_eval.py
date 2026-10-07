"""Price evidence report: concluded sales before/after the sync, models resting on real sales,
accuracy of the estimates with and without the extra evidence, and the gate decision.

    python -m app.tools.price_eval              # sync, measure, store the gate, print a report
    python -m app.tools.price_eval --json       # the same as JSON
    python -m app.tools.price_eval --dry-run    # measure without writing anything

Accuracy is measured on the newer half of the sales (same time split as the calibration), each
sale re-estimated with only what was known before it (see ``app.analytics.evidence``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.accuracy import MAX_SUBJECTS, aware
from app.analytics.backtest import load_rows, time_split
from app.analytics.evidence import decide_gate, load_evidence_pool, report, run_evidence_backtest, store_gate
from app.core.logging import configure_logging
from app.db.session import dispose_engine, get_sessionmaker
from app.ingestion.catalog import load_catalog
from app.market.jobs import sync_price_evidence
from app.market.negotiation import current_negotiation_discount
from app.market.sold_sales import SOURCES, sold_sales_summary

SOURCE_LABELS = {
    "own_sale": "tue vendite (prezzo incassato)",
    "own_purchase": "tuoi acquisti (prezzo pagato)",
    "vinted_sold": "Vinted venduti (ultimo prezzo rilevato)",
    "external_sold": "venduti su altri mercati",
}
VARIANT_LABELS = {
    "baseline": "stima precedente (senza regola dei venduti)",
    "listings": "solo annunci (prima dei dati extra)",
    "own_sales": "+ tue vendite",
    "own": "+ tue vendite e acquisti",
    "external_sales": "+ tue vendite + altri mercati",
    "external_own": "+ tuoi dati + altri mercati",
    "own_sales_cap": "+ tue vendite, tetto prezzo nuovo",
    "own_cap": "+ tuoi dati, tetto prezzo nuovo",
    "external_sales_cap": "+ vendite + mercati, tetto nuovo",
    "external_own_cap": "+ tutto, tetto prezzo nuovo",
}


async def evaluate_prices(
    session: AsyncSession, *, sync: bool = True, store: bool = True, max_subjects: int = MAX_SUBJECTS
) -> dict[str, Any]:
    """Sync the evidence, backtest every variant, decide (and store) the gate; the full report."""
    start = time.perf_counter()
    before = await sold_sales_summary(session)
    synced = await sync_price_evidence(session, full=True) if sync else None
    after = await sold_sales_summary(session)
    catalog = await load_catalog(session)
    rows = await load_rows(session, catalog)
    split = aware(time_split(rows, 0.5))
    pool = await load_evidence_pool(session, catalog)
    bt = run_evidence_backtest(rows, catalog, pool, split, max_subjects=max_subjects)
    gate = decide_gate(bt)
    if store:
        await store_gate(session, gate)
    return {
        "sold_sales": {"before": before, "after": after, "synced": synced["synced"] if synced else None},
        "models_with_5_sales": {
            "before": before["models_with_5_sales"],
            "after": after["models_with_5_sales"],
        },
        "negotiation": await current_negotiation_discount(session),
        "accuracy": {
            "split_at": split.isoformat(),
            "test_subjects": gate["n_subjects"],
            "test_own_sales": sum(1 for c in bt.test("listings") if c.kind == "own_sale"),
            "variants": report(bt),
        },
        "gate": gate,
        "duration_s": round(time.perf_counter() - start, 1),
    }


def _pct(v: float | None) -> str:
    return "  n.d." if v is None else f"{v * 100:5.1f}%"


def _eur(v: float | None) -> str:
    return "   n.d." if v is None else f"€{v:6.2f}"


def format_report(r: dict[str, Any], title: str = "Dati di prezzo") -> str:
    lines = [title, "=" * len(title), "", "Vendite concluse nel database   prima   dopo"]
    b, a = r["sold_sales"]["before"]["by_source"], r["sold_sales"]["after"]["by_source"]
    for s in SOURCES:
        lines.append(f"  {SOURCE_LABELS[s]:<38}{b[s]:>6} {a[s]:>6}")
    lines.append(
        f"  {'totale':<38}{r['sold_sales']['before']['total']:>6} {r['sold_sales']['after']['total']:>6}"
    )
    m5 = r["models_with_5_sales"]
    lines.append(f"Modelli con almeno 5 vendite reali: {m5['before']} -> {m5['after']}")
    neg = r["negotiation"]
    lines.append(f"Sconto da trattativa: {neg['note']}")
    acc = r["accuracy"]
    lines += [
        "",
        f"Errore delle stime: vendite dopo {acc['split_at'][:10]} ({acc['test_subjects']} casi, "
        f"di cui {acc['test_own_sales']} tue vendite), ognuna stimata solo con i dati precedenti",
        f"  {'variante':<44}{'stimati':>8}{'MAE':>9}{'MAPE':>8}{'APE med':>8}{'bias':>8}{'in range':>9}",
    ]
    for name, label in VARIANT_LABELS.items():
        m = acc["variants"][name]["all"]
        lines.append(
            f"  {label:<44}{m['estimated']:>8}{_eur(m['mae_eur']):>9}{_pct(m['mape']):>8}"
            f"{_pct(m['median_ape']):>8}{_pct(m['bias']):>8}{_pct(m['in_range']):>9}"
        )
    g = r["gate"]
    chosen = acc["variants"][g["variant"]]
    before = acc["variants"]["listings"]
    lines += [
        "",
        "Decisione (usata dalle analisi):",
        f"  dati di altri mercati: {'sì' if g['use_external'] else 'no'} · tuoi acquisti: "
        f"{'sì' if g['use_own_purchases'] else 'no'} · tetto prezzo nuovo: {'sì' if g['use_new_cap'] else 'no'}",
        f"  {g['note']}",
        f"  senza dati esterni: MAE {_eur(g['without_external']['mae_eur'])} · con dati esterni: "
        f"MAE {_eur(g['with_external']['mae_eur'])} · solo annunci: MAE {_eur(g['listings_only']['mae_eur'])}",
        f"  prima (solo annunci) -> ora ({VARIANT_LABELS[g['variant']].lstrip('+ ')}): "
        f"MAE {_eur(before['all']['mae_eur'])} -> {_eur(chosen['all']['mae_eur'])}; "
        f"articoli Vinted {_eur(before['vinted']['mae_eur'])} -> {_eur(chosen['vinted']['mae_eur'])}; "
        f"tue vendite {_eur(before['own_sales']['mae_eur'])} -> {_eur(chosen['own_sales']['mae_eur'])}",
    ]
    return "\n".join(lines)


async def main(as_json: bool, dry_run: bool, max_subjects: int) -> None:
    session = get_sessionmaker()()
    try:
        result = await evaluate_prices(session, sync=True, store=not dry_run, max_subjects=max_subjects)
        if dry_run:
            await session.rollback()
        else:
            await session.commit()
    finally:
        await session.close()
        await dispose_engine()
    print(json.dumps(result, indent=1, ensure_ascii=False, default=str) if as_json else format_report(result))


if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--dry-run", action="store_true", help="measure without writing anything")
    parser.add_argument("--max-subjects", type=int, default=MAX_SUBJECTS)
    args = parser.parse_args()
    asyncio.run(main(args.json, args.dry_run, args.max_subjects))
