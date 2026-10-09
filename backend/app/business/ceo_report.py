"""The CEO report: one page, plain language, built only from the ledger and the stock (nothing is invented).

P&L, cash flow, KPIs, what worked and what did not, the deviation from the plan, the decisions taken and the plan
for the next period. Every figure comes from the numbers passed in, so the report can be checked against the ledger.
"""

from __future__ import annotations

from datetime import date
from typing import Any


def _eur(v: float | int | None) -> str:
    if v is None:
        return "n.d."
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def build(
    *,
    period: str,
    start: date,
    end: date,
    summary: dict[str, Any],
    kpis: dict[str, Any],
    monthly_target: float | None,
    niche_best: list[dict[str, Any]],
    niche_worst: list[dict[str, Any]],
    actions_by_status: dict[str, int],
    to_list: int,
    reprice_due: int,
    budget_left: float | None,
    plan_suggestions: int,
    cash_message: str | None,
    tax_messages: list[str],
    margin_alarm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    days = max(1, (end - start).days + 1)
    realized = float(summary["realized_profit"])
    after_exp = float(summary["realized_profit_after_expenses"])
    target = None if monthly_target is None else monthly_target * days / 30.0
    deviation = None if target is None else round(after_exp - target, 2)
    lines = [
        f"REPORT {period.upper()} — dal {start.isoformat()} al {end.isoformat()}",
        "",
        "CONTO ECONOMICO",
        f"- Ricavi: {_eur(float(summary['revenue']))} su {summary['sales_count']} vendite",
        f"- Costi di vendita: {_eur(float(summary['selling_costs']))}",
        f"- Costo degli articoli venduti: {_eur(float(summary['cost_of_goods_sold']))}",
        f"- Profitto realizzato: {_eur(realized)}",
        f"- Altre spese: {_eur(float(summary['other_expenses']))}",
        f"- Profitto dopo le spese: {_eur(after_exp)}",
        "",
        "CASSA",
        f"- Entrate {_eur(float(summary['cash_in']))}, uscite {_eur(float(summary['cash_out']))}, saldo {_eur(float(summary['cash_flow']))}",
        f"- Giacenze al costo: {_eur(float(summary['stock_at_cost']))} ({summary['stock_items']} articoli): non sono una perdita",
        "",
        "INDICATORI",
        f"- Margine per articolo: {_eur(kpis.get('contribution_margin_per_item'))} · sell-through: "
        + ("n.d." if kpis.get("sell_through") is None else f"{kpis['sell_through']:.0%}")
        + f" · giorni da acquisto a incasso: {kpis.get('cash_conversion_days') if kpis.get('cash_conversion_days') is not None else 'n.d.'}",
        f"- Profitto orario: {_eur(kpis.get('hourly_profit'))} ({kpis.get('hours_basis', '')})",
    ]
    if target is not None:
        verb = "sopra" if (deviation or 0) >= 0 else "sotto"
        lines += [
            "",
            "PIANO",
            f"- Obiettivo del periodo {_eur(target)}, ottenuto {_eur(after_exp)}: {_eur(abs(deviation or 0))} {verb} il piano",
        ]
    if niche_best or niche_worst:
        lines += ["", "COSA HA FUNZIONATO E COSA NO"]
        lines += [f"- Bene: {n['niche']} ({_eur(n['profit'])})" for n in niche_best[:3]]
        lines += [f"- Male: {n['niche']} ({_eur(n['profit'])})" for n in niche_worst[:3]]
    if margin_alarm and margin_alarm.get("alarm"):
        lines += [
            "",
            f"ATTENZIONE: i margini stanno calando ({margin_alarm['change']:.0%} rispetto al periodo precedente)",
        ]
    decided = {k: v for k, v in actions_by_status.items() if v}
    lines += [
        "",
        "DECISIONI PRESE",
        "- "
        + (
            ", ".join(f"{v} {k}" for k, v in decided.items())
            if decided
            else "nessuna azione automatica registrata"
        ),
    ]
    nxt = []
    if to_list:
        nxt.append(f"pubblicare {to_list} articoli")
    if reprice_due:
        nxt.append(f"ribassare {reprice_due} annunci")
    if plan_suggestions:
        nxt.append(
            f"valutare {plan_suggestions} acquisti del piano"
            + (f" (budget residuo {_eur(budget_left)})" if budget_left is not None else "")
        )
    lines += ["", "PROSSIMO PERIODO", "- " + ("; ".join(nxt) if nxt else "nessuna azione in sospeso")]
    if cash_message:
        lines += ["", "LIQUIDITÀ", f"- {cash_message}"]
    if tax_messages:
        lines += ["", "SOGLIE FISCALI (impostate da te)"] + [f"- {m}" for m in tax_messages]
    text = "\n".join(lines)
    return {
        "period": period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "figures": {
            "revenue": float(summary["revenue"]),
            "realized_profit": realized,
            "profit_after_expenses": after_exp,
            "cash_flow": float(summary["cash_flow"]),
            "sales": summary["sales_count"],
            "stock_at_cost": float(summary["stock_at_cost"]),
            "target": None if target is None else round(target, 2),
            "deviation": deviation,
        },
        "text": text,
        "lines": len(lines),
    }
