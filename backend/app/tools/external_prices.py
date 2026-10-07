"""External price references (Serper.dev): one model on demand, the refresh queue, the status.

    python -m app.tools.external_prices --brand nike --model "Air Max 90"            # search and store
    python -m app.tools.external_prices --brand nike --model "Air Max 90" --dry-run  # store nothing
    python -m app.tools.external_prices --due [--max-models 5]                       # models due
    python -m app.tools.external_prices --status                                     # budget, cache
    (add --json for machine-readable output)

Every query costs one credit ($0.001) and is counted in the daily/monthly budget, with --dry-run
too: dry-run searches for real but stores no price, and prints every result kept or rejected with
the reason. Needs EXTERNAL_SEARCH_PROVIDER=serper and SERPER_API_KEY (see docs/EXTERNAL_PRICES.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.redis import redis_lock
from app.db.session import dispose_engine, get_sessionmaker
from app.external.jobs import LOCK_NAME, LOCK_TTL_SECONDS
from app.external.provider import SearchProvider, SerperProvider
from app.external.service import (
    ModelSearch,
    disabled_reason,
    external_status,
    refresh_due_models,
    refresh_model,
)
from app.identification.taxonomy import Taxonomy, fold
from app.ingestion.catalog import load_catalog
from app.market.cleaning import outlier_ids

KIND_LABELS = {"new": "nuovo", "asking": "in vendita", "sold": "venduto"}
REASON_LABELS = {
    "brand": "marca assente",
    "model": "modello assente",
    "other_model": "altro modello o collaborazione",
    "replica": "replica o imitazione",
    "kids": "bambini",
    "lot": "lotto",
    "accessory": "accessorio",
    "not_item": "pagina di ricerca, non un articolo",
    "source": "fonte esclusa",
    "no_price": "senza prezzo",
    "currency": "valuta sconosciuta",
    "price": "prezzo assurdo",
    "outlier": "prezzo anomalo per il modello",
}


def resolve_brand(taxonomy: Taxonomy, value: str) -> str | None:
    """A brand slug from a slug, a name or an alias ("nike", "The North Face", "tnf")."""
    wanted = fold(value)
    for brand in taxonomy.all_brands:
        if wanted in {brand.slug, fold(brand.name), *(fold(a) for a in brand.aliases)}:
            return brand.slug
    return None


def resolve_model(taxonomy: Taxonomy, slug: str, value: str) -> tuple[str, str | None]:
    """The canonical model name (a taxonomy line when it is one) and its category slug."""
    brand = taxonomy.brand_by_slug[slug]
    for line in brand.lines:
        if fold(value) in {fold(line.name), *(fold(k) for k in line.keywords)}:
            return line.name, line.category
    return " ".join(value.split()), None


def _row(c: Any, outlier: bool) -> dict[str, Any]:
    o = c.offer
    return {
        "kind": c.kind,
        "source": o.source,
        "price": str(c.price),
        "currency": c.currency,
        "price_eur": str(c.price_eur),
        "date": o.source_date.date().isoformat() if o.source_date else None,
        "condition": c.condition,
        "size": c.size,
        "title": o.title,
        "url": o.url,
        "score": c.score,
        "outlier": outlier,
    }


def search_report(search: ModelSearch | None, summary: dict[str, Any]) -> dict[str, Any]:
    # Implausible prices among those found now (the stored ones are also checked against the
    # prices already in the database).
    kept = search.kept if search else []
    flagged = outlier_ids((i, c.kind, c.price_eur, c.condition) for i, c in enumerate(kept))
    return {
        **summary,
        "model": search.spec.model_name if search else None,
        "brand": search.spec.brand_slug if search else None,
        "brand_name": search.spec.brand_name if search else None,
        "queries": [{"endpoint": q.endpoint, "purpose": q.purpose, "q": q.q} for q in search.queries]
        if search
        else [],
        "kept_rows": [_row(c, i in flagged) for i, c in enumerate(kept)],
        "rejected_rows": [
            {"reason": reason, "detail": detail, "title": o.title, "source": o.source, "url": o.url}
            for o, reason, detail in search.rejected
        ]
        if search
        else [],
    }


def format_search(report: dict[str, Any], settings: Settings, dry_run: bool) -> str:
    title = f"{report['brand_name'] or ''} {report['model'] or ''}".strip() or "Ricerca"
    lines = [
        f"{title} - {report['queries_used']} query "
        f"(oggi {report['today_used']}/{settings.external_search_daily_max}, "
        f"mese {report['month_used']}/{settings.external_search_monthly_budget})"
        + (" - prova: nulla salvato" if dry_run else ""),
    ]
    if report.get("error"):
        lines.append(f"Errore: {report['error']}")
    if report["status"] == "skipped_budget":
        lines.append("Budget di query esaurito: nessuna ricerca eseguita.")
    kept = report["kept"]
    lines.append(
        f"Tenuti: {sum(kept.values())} (nuovo {kept['new']}, in vendita {kept['asking']}, venduti {kept['sold']})"
    )
    for r in sorted(report["kept_rows"], key=lambda r: (r["kind"], float(r["price_eur"]))):
        lines.append(
            f"  {KIND_LABELS[r['kind']]:<10} {r['source'][:22]:<22} {r['price']:>9} {r['currency']} = "
            f"{r['price_eur']:>8} EUR  {r['date'] or '-':<10}  {r['condition']:<16} {r['title'][:60]}"
            + ("  [prezzo anomalo: escluso]" if r["outlier"] else "")
        )
    lines.append(f"Scartati: {len(report['rejected_rows'])}")
    for r in report["rejected_rows"]:
        detail = f" [{r['detail']}]" if r["detail"] else ""
        lines.append(f"  {REASON_LABELS.get(r['reason'], r['reason']):<36} {r['title'][:60]}{detail}")
    if report.get("stored") is not None:
        s = report["stored"]
        lines.append(f"Salvati nuovi: nuovo {s['new']}, in vendita {s['asking']}, venduti {s['sold']}")
    return "\n".join(lines)


async def run(
    args: argparse.Namespace,
    *,
    provider: SearchProvider | None = None,
    settings: Settings | None = None,
    session: AsyncSession | None = None,
) -> tuple[int, dict[str, Any], str]:
    """Execute the command; returns ``(exit code, result, text report)``."""
    settings = settings or get_settings()
    own = session is None
    session = session or get_sessionmaker()()
    try:
        if args.status:
            status = await external_status(session)
            return 0, status, json.dumps(status, indent=1, ensure_ascii=False)
        if provider is None:
            if reason := disabled_reason(settings):
                return 2, {"status": "disabled", "reason": reason}, reason
            provider = SerperProvider.from_settings(settings)
            assert provider is not None
        if args.due:
            async with redis_lock(LOCK_NAME, LOCK_TTL_SECONDS) as acquired:
                if not acquired:
                    return (
                        1,
                        {"status": "skipped", "reason": "already running"},
                        "Aggiornamento già in corso.",
                    )
                result = await refresh_due_models(
                    session, max_models=args.max_models, provider=provider, settings=settings
                )
            return 0, result, json.dumps(result, indent=1, ensure_ascii=False, default=str)
        if not (args.brand and args.model):
            return (
                2,
                {"status": "error", "reason": "--brand e --model obbligatori"},
                "Indica --brand e --model.",
            )
        catalog = await load_catalog(session)
        slug = resolve_brand(catalog.taxonomy, args.brand)
        if slug is None:
            return 2, {"status": "error", "reason": "marca sconosciuta"}, f"Marca sconosciuta: {args.brand}"
        model, category = resolve_model(catalog.taxonomy, slug, args.model)
        search, summary = await refresh_model(
            session,
            catalog.brand_id(slug),
            catalog.category_id(category),
            model,
            slug,
            provider=provider,
            settings=settings,
            store=not args.dry_run,
        )
        report = search_report(search, summary)
        code = 1 if summary["status"] == "error" else 0
        return code, report, format_search(report, settings, args.dry_run)
    finally:
        if own:
            await session.close()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--brand", help="brand slug, name or alias (nike, 'The North Face')")
    p.add_argument("--model", help="model name ('Air Max 90')")
    p.add_argument("--dry-run", action="store_true", help="search but store nothing (queries still counted)")
    p.add_argument("--due", action="store_true", help="refresh the models due, highest demand first")
    p.add_argument("--max-models", type=int, default=None)
    p.add_argument("--status", action="store_true", help="budget, cache and last run")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    return p


async def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        code, result, text = await run(args)
    finally:
        await dispose_engine()
    print(json.dumps(result, indent=1, ensure_ascii=False, default=str) if args.json else text)
    return code


if __name__ == "__main__":
    configure_logging()
    sys.exit(asyncio.run(main()))
