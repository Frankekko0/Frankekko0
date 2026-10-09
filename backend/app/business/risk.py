"""Business risks: platform dependency, reserves for returns and disputes, insurance, margins sliding down."""

from __future__ import annotations

from collections.abc import Sequence

CONTINGENCY = [
    "Esporta ogni mese i dati (registro, acquisti, vendite, spese): l'app li esporta in CSV e JSON",
    "Non dipendere da funzioni non ufficiali della piattaforma: se cambiano le regole, l'attività non si ferma",
    "Se l'account viene sospeso: ferma gli acquisti, conserva le prove di ogni vendita e segui la procedura di ricorso",
    "Diversifica i canali di vendita solo dove è autorizzato e con integrazioni ufficiali",
    "Tieni una riserva di liquidità per resi, dispute e perdite di spedizione",
]


def should_insure(item_value: float, threshold: float = 50.0, tracked: bool = True) -> tuple[bool, str]:
    if not tracked:
        return True, "spedizione non tracciata: assicurare o cambiare servizio"
    if item_value >= threshold:
        return True, f"valore {item_value:.0f} € a partire da {threshold:.0f} €: la regola dice di assicurare"
    return False, f"valore sotto la soglia di {threshold:.0f} €: rischio accettato"


def reserve_needed(
    monthly_sales_value: float, return_rate: float, dispute_rate: float, loss_rate: float, months: float = 1.0
) -> float:
    return round(monthly_sales_value * (return_rate + dispute_rate + loss_rate) * months, 2)


def margin_alarm(margins: Sequence[float], window: int = 3, drop: float = 0.25) -> dict[str, object]:
    """Margins per period, oldest first: alarm when the latest ``window`` average is ``drop`` below the previous."""
    if len(margins) < 2 * window:
        return {"alarm": False, "note": f"servono almeno {2 * window} periodi"}
    prev = sum(margins[-2 * window : -window]) / window
    last = sum(margins[-window:]) / window
    if prev <= 0:
        return {"alarm": False, "note": "margine precedente non positivo: nessun confronto"}
    change = (last - prev) / prev
    return {
        "alarm": change <= -drop,
        "change": round(change, 3),
        "previous": round(prev, 2),
        "latest": round(last, 2),
    }


def platform_dependency(revenue_by_platform: dict[str, float], alarm_at: float = 0.9) -> dict[str, object]:
    total = sum(revenue_by_platform.values())
    if total <= 0:
        return {"alarm": False, "top_share": None, "note": "nessuna vendita registrata"}
    name, top = max(revenue_by_platform.items(), key=lambda kv: kv[1])
    share = top / total
    return {
        "alarm": share >= alarm_at,
        "top_platform": name,
        "top_share": round(share, 3),
        "note": f"{share:.0%} del fatturato passa da «{name}»: dipendenza alta"
        if share >= alarm_at
        else "fatturato distribuito",
        "contingency": CONTINGENCY,
    }
