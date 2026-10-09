"""Standard procedures and capacity: what to do at each step, and what the volume asks of time and space."""

from __future__ import annotations

SOPS: dict[str, list[str]] = {
    "ricezione": [
        "Fotografa il pacco chiuso e l'etichetta di spedizione prima di aprirlo",
        "Apri e confronta l'articolo con le foto e la descrizione dell'annuncio",
        "Se qualcosa non corrisponde, scatta le foto del difetto e apri subito la segnalazione sulla piattaforma",
        "Registra la ricezione nell'app (stato «Da pubblicare»)",
    ],
    "controllo": [
        "Verifica etichetta, taglia e composizione; misura il capo steso",
        "Cerca macchie, fori, pilling, odori; annota tutto con la zona",
        "Decidi se serve un ripristino (vedi la valutazione del ripristino)",
    ],
    "pulizia": [
        "Lavaggio o vapore secondo l'etichetta",
        "Rimozione pilling e fili",
        "Asciugatura e stiratura leggera",
    ],
    "foto": [
        "Fondo neutro e luce naturale",
        "Scatta le foto della checklist: davanti, dietro, etichetta, composizione, dettagli, difetti",
        "La prima foto è il davanti intero: è quella che converte",
    ],
    "pubblicazione": [
        "Usa titolo e descrizione preparati e correggi ciò che è da confermare",
        "Prezzo di partenza e minimo dal piano di prezzo",
        "Registra l'annuncio nell'app (stato «Pubblicato») con il prezzo",
    ],
    "spedizione": [
        "Imballa con materiale pulito; foto del pacco chiuso",
        "Spedisci entro i tempi dichiarati e carica il tracking",
        "Assicura la spedizione se il valore supera la soglia impostata",
    ],
    "resi": [
        "Rispondi entro 24 ore con cortesia",
        "Raccogli le prove (foto dell'annuncio, del pacco, della spedizione)",
        "Segui la procedura della piattaforma per resi e dispute e registra l'esito",
    ],
}

# What each role may do. The app has a single account for now: these are the intended permissions, written down so
# that the day it has several accounts they exist (see docs/LIMITATIONS.md).
ROLE_PERMISSIONS = {
    "titolare": ["tutto"],
    "assistente_spedizioni": ["vedere gli ordini da spedire", "segnare come spedito", "caricare il tracking"],
    "assistente_foto": ["vedere la checklist foto", "caricare foto", "pubblicare bozze senza prezzo"],
}


def capacity_plan(
    items_per_month: float,
    minutes_per_item: float,
    weekly_hours: float | None,
    stock_items: int,
    items_per_shelf: int = 15,
    storage_shelves: int | None = None,
) -> dict[str, object]:
    hours_month = items_per_month * minutes_per_item / 60.0
    available = weekly_hours * 4.33 if weekly_hours else None
    by_time = available * 60.0 / minutes_per_item if available else None
    by_space = storage_shelves * items_per_shelf if storage_shelves else None
    limits = [x for x in (by_time, by_space) if x is not None]
    shelves_needed = stock_items / items_per_shelf
    helper_hours = max(0.0, hours_month - available) if available is not None else None
    return {
        "hours_per_month_needed": round(hours_month, 1),
        "hours_per_month_available": None if available is None else round(available, 1),
        "items_per_month_by_time": None if by_time is None else round(by_time, 1),
        "shelves_needed_for_stock": round(shelves_needed, 1),
        "items_per_month_by_space_turnover": None if by_space is None else by_space,
        "recommended_monthly_volume": round(min(limits), 1) if limits else None,
        "helper_hours_needed": None if helper_hours is None else round(helper_hours, 1),
        "needs_helper": bool(helper_hours and helper_hours > 0),
        "assumption": f"{minutes_per_item:g} minuti per articolo, {items_per_shelf} articoli per ripiano",
    }


def collaborator_brief(role: str) -> str:
    perms = ROLE_PERMISSIONS.get(role)
    if perms is None:
        raise ValueError(f"ruolo sconosciuto: {role}")
    steps = {
        "assistente_spedizioni": ["ricezione", "spedizione", "resi"],
        "assistente_foto": ["pulizia", "foto", "pubblicazione"],
        "titolare": list(SOPS),
    }[role]
    lines = [f"Istruzioni per il ruolo «{role}»", "", "Cosa puoi fare: " + "; ".join(perms), ""]
    for s in steps:
        lines.append(f"{s.capitalize()}:")
        lines.extend(f"  {i}. {t}" for i, t in enumerate(SOPS[s], 1))
        lines.append("")
    return "\n".join(lines).rstrip()
