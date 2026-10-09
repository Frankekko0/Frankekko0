"""When to lower or raise the asking price of a listed item, from what the listing is doing."""

from __future__ import annotations

from dataclasses import dataclass

from app.intelligence.survival import MarkdownStep


@dataclass(frozen=True)
class RepriceAdvice:
    action: str  # lower | raise | hold | improve_listing
    new_price: float | None
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {"action": self.action, "new_price": self.new_price, "reason": self.reason}


def advise(
    *,
    days_listed: float,
    asking: float,
    floor: float,
    markdowns: list[MarkdownStep],
    views: int | None = None,
    favourites: int | None = None,
    offers_received: int = 0,
    sold_within_days: float | None = None,
) -> RepriceAdvice:
    """The plan decides when; the listing's own numbers say whether the price or the listing is the problem."""
    if sold_within_days is not None and sold_within_days <= 2 and asking > floor:
        return RepriceAdvice(
            "raise", round(asking * 1.08, 2), "Si è venduto in meno di 2 giorni: il prezzo era basso."
        )
    due = [m for m in markdowns if m.day <= days_listed and m.price < asking - 0.005]
    per_day = (views or 0) / max(days_listed, 1.0)
    if views is not None and days_listed >= 5 and per_day < 1.5:
        return RepriceAdvice(
            "improve_listing",
            None,
            "Pochissime visualizzazioni: il problema è l'annuncio (titolo, foto di copertina, categoria), non il prezzo.",
        )
    if due:
        step = due[-1]
        new = round(max(floor, step.price), 2)
        if new < asking - 0.005:
            interest = ""
            if (
                views
                and favourites is not None
                and views >= 20
                and favourites / views >= 0.05
                and offers_received == 0
            ):
                interest = " Molti preferiti ma nessuna offerta: il prezzo è l'ostacolo."
            return RepriceAdvice("lower", new, f"{step.why}.{interest}".strip())
    if asking <= floor + 0.005 and days_listed >= (markdowns[-1].day if markdowns else 30):
        return RepriceAdvice(
            "hold", None, "Già al prezzo minimo: sotto non resta il margine. Valuta un lotto o la rimozione."
        )
    return RepriceAdvice("hold", None, "Nessun ribasso previsto adesso: il piano non lo richiede ancora.")
