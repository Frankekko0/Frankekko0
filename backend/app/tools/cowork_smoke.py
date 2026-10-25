"""Collaudo del flusso minimo FlipFinder Cowork su dati di prova: nessun database, nessuna rete.

    python -m app.tools.cowork_smoke                     # report leggibile sui dati di prova inclusi
    python -m app.tools.cowork_smoke --json              # lo stesso come JSON
    python -m app.tools.cowork_smoke --input items.json  # su un file di articoli nello stesso formato

Esegue i passi 1-11 del ciclo operativo con le formule del motore (``app.profit``, ``app.scoring``) e
le soglie iniziali proposte (budget 100, spesa max 25 per articolo, profitto prudente >= 12, ROI >= 50%,
Flip Score >= 75 per l'alert, Risk Score <= 30). Non acquista, non contatta nessuno, non pubblica:
classifica e prepara le schede. L'analisi di Grok non è collegata: ogni articolo lo dichiara e nessun
risultato viene inventato al suo posto.

Regole che seguono il brief e che il motore principale non applica in questa forma:

* il prezzo di rivendita è *prudente*: primo quartile dei venduti comparabili; se ci sono solo prezzi
  richiesti, primo quartile meno ``asking_haircut`` (ipotesi da calibrare, dichiarata nel report);
* la "spesa per articolo" è il costo totale di acquisizione (prezzo + protezione + spedizione +
  ripristino), non il solo prezzo dell'annuncio;
* domanda e velocità senza dati valgono 0 e mandano l'articolo in revisione umana;
* contraffazione sospetta o rischio molto alto scartano l'articolo qualunque sia il margine.
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.acquisition.identity import listing_identity
from app.core.money import CENT, ZERO, money, to_decimal
from app.domain.enums import Condition
from app.profit.calculator import CostProfile, acquisition_cost, max_buy_price
from app.profit.evaluation import CostStatus, Restoration, evaluate_deal
from app.scoring.curves import days_score, sell_through_score
from app.scoring.flip import FlipInput, compute_flip_score
from app.scoring.risk import RiskInput, assess_risk

D = Decimal
SAMPLE = Path(__file__).with_name("cowork_sample.json")
# I cinque fattori del brief; gli altri componenti del Flip Score a peso zero (il rischio resta un punteggio a sé).
BRIEF_WEIGHTS: dict[str, float] = {
    "profit": 30,
    "roi": 25,
    "demand": 20,
    "sale_time": 15,
    "info": 10,
    "price_vs_market": 0,
    "condition": 0,
    "risk": 0,
}
VERY_HIGH_RISK = 75


class Classification(StrEnum):
    SCARTA = "SCARTA"
    MONITORA = "MONITORA"
    REVISIONE_UMANA = "REVISIONE UMANA"
    OPPORTUNITA = "OPPORTUNITÀ DA APPROVARE"


@dataclass(frozen=True)
class Thresholds:
    total_budget: Decimal = Decimal("100")
    max_spend_per_item: Decimal = Decimal("25")
    min_profit: Decimal = Decimal("12")
    min_roi: Decimal = Decimal("0.50")
    min_flip_alert: int = 75
    max_risk: int = 30
    asking_haircut: Decimal = Decimal("0.20")
    min_sold_comparables: int = 3


@dataclass
class Card:
    item_id: str
    title: str
    url: str | None
    classification: Classification
    reasons: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    grok: str = "non collegato: nessuna analisi richiesta né ricevuta"
    prudent_resale: Decimal | None = None
    resale_basis: str | None = None
    total_cost: Decimal | None = None
    net_profit: Decimal | None = None
    roi: Decimal | None = None
    unknown_costs: list[str] = field(default_factory=list)
    flip_score: int | None = None
    risk_score: int | None = None
    risk_factors: list[str] = field(default_factory=list)
    max_buy_price: Decimal | None = None
    alert: bool = False

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            out[k] = str(v) if isinstance(v, Decimal) else v
        out["classification"] = self.classification.value
        return out


def _lower_quartile(prices: list[Decimal]) -> Decimal:
    if len(prices) == 1:
        return prices[0]
    return money(D(str(statistics.quantiles([float(p) for p in prices], n=4, method="inclusive")[0])))


def _prices(comparables: list[dict[str, Any]], th: Thresholds) -> tuple[list[Decimal], Decimal, str] | None:
    """I prezzi su cui stimare, il fattore che li porta a prezzi realizzati e la loro natura."""
    sold = [to_decimal(c["price"]) for c in comparables if c.get("sold") and c.get("price") is not None]
    if sold:
        return sold, D(1), f"{len(sold)} vendite concluse"
    asking = [to_decimal(c["price"]) for c in comparables if c.get("price") is not None]
    if asking:
        return (
            asking,
            1 - th.asking_haircut,
            f"solo {len(asking)} prezzi richiesti, -{th.asking_haircut:.0%} (ipotesi da calibrare)",
        )
    return None


def prudent_resale(comparables: list[dict[str, Any]], th: Thresholds) -> tuple[Decimal | None, str | None]:
    """Prezzo prudente (primo quartile) e su cosa si basa. I prezzi richiesti non sono prova di vendita."""
    found = _prices(comparables, th)
    if found is None:
        return None, None
    prices, factor, basis = found
    return money(_lower_quartile(prices) * factor), f"primo quartile di {basis}"


def market_value(comparables: list[dict[str, Any]], th: Thresholds) -> Decimal | None:
    """Valore di mercato centrale (mediana): misura lo sconto e il rischio, non il profitto."""
    found = _prices(comparables, th)
    if found is None:
        return None
    prices, factor, _ = found
    return money(D(str(statistics.median(prices))) * factor)


def _info_score(item: dict[str, Any], sold_count: int, th: Thresholds) -> int:
    checks = [
        bool(item.get("url")),
        bool(item.get("captured_at")),
        item.get("price") is not None,
        bool(item.get("brand")),
        bool(item.get("size")),
        item.get("condition", Condition.UNKNOWN) != Condition.UNKNOWN,
        int(item.get("photo_count") or 0) >= 3,
        sold_count >= th.min_sold_comparables,
    ]
    return round(100 * sum(checks) / len(checks))


def evaluate_item(
    item: dict[str, Any], profile: CostProfile, th: Thresholds, *, profile_confirmed: bool = False
) -> Card:
    item_id = str(item.get("id") or "")
    card = Card(
        item_id, item.get("title") or "(senza titolo)", item.get("url"), Classification.REVISIONE_UMANA
    )

    # 2. Dati essenziali.
    for key, label in (("url", "URL"), ("captured_at", "data di rilevazione"), ("price", "prezzo")):
        if item.get(key) in (None, ""):
            card.missing.append(label)
    if item.get("captured_at"):
        try:
            datetime.fromisoformat(str(item["captured_at"]))
        except ValueError:
            card.missing.append("data di rilevazione valida")
    if card.missing:
        card.reasons.append("dati essenziali mancanti: calcolo non eseguito")
        return card

    # 7. Profitto prudente e ROI, con lo stato di ogni costo.
    comparables = item.get("comparables") or []
    card.prudent_resale, card.resale_basis = prudent_resale(comparables, th)
    sold_count = sum(1 for c in comparables if c.get("sold"))
    price = to_decimal(item["price"])
    restoration_raw = item.get("restoration", 0)
    restoration = (
        Restoration(None, CostStatus.UNKNOWN)
        if restoration_raw is None
        else Restoration(to_decimal(restoration_raw))
    )
    if restoration_raw is None:
        card.missing.append("costo di pulizia/riparazione")
    shipping = item.get("shipping")
    protection = item.get("buyer_protection")
    economics_ok = False
    if card.prudent_resale is None:
        card.missing.append("comparabili di prezzo")
    else:
        ev = evaluate_deal(
            price,
            card.prudent_resale,
            profile,
            listing_shipping=None if shipping is None else to_decimal(shipping),
            listing_buyer_protection=None if protection is None else to_decimal(protection),
            restoration=restoration,
            profile_saved=profile_confirmed,
        )
        card.total_cost, card.net_profit, card.roi = ev.total_acquisition_cost, ev.net_profit, ev.roi
        card.unknown_costs = list(ev.unknown_costs)
        economics_ok = (
            ev.net_profit >= th.min_profit
            and ev.roi >= th.min_roi
            and ev.total_acquisition_cost <= th.max_spend_per_item
        )
        if ev.net_profit < th.min_profit:
            card.reasons.append(f"profitto prudente {ev.net_profit} < {th.min_profit}")
        if ev.roi < th.min_roi:
            card.reasons.append(f"ROI prudente {ev.roi:.0%} < {th.min_roi:.0%}")
        if ev.total_acquisition_cost > th.max_spend_per_item:
            card.reasons.append(f"spesa totale {ev.total_acquisition_cost} > {th.max_spend_per_item}")
        card.max_buy_price = _max_buy(card.prudent_resale, profile, th, item)

    # 8. Flip Score (pesi del brief) e Risk Score indipendente.
    sell_through = item.get("sell_through")
    days = item.get("median_days_to_sell")
    if sell_through is None:
        card.missing.append("domanda documentata (sell-through)")
    if days is None:
        card.missing.append("tempi di vendita documentati")
    fmv = market_value(comparables, th)
    discount = float((fmv - price) / fmv) if fmv and fmv > 0 else None
    condition = item.get("condition", Condition.UNKNOWN)
    risk = assess_risk(
        RiskInput(
            price=price,
            fair_market_value=fmv,
            brand_counterfeit_risk=float(item.get("brand_counterfeit_risk") or 0),
            seller=None,
            photo_count=int(item.get("photo_count") or 0),
            description_length=len(item.get("description") or ""),
            defect_terms=tuple(item.get("defects") or ()),
            identification_confidence=int(item.get("identification_confidence") or 0),
            comparables_used=len(comparables),
            market_confidence=80 if sold_count >= th.min_sold_comparables else 30,
            condition=condition,
        )
    )
    card.risk_score = risk.score
    card.risk_factors = [f.label for f in risk.factors]
    flip = compute_flip_score(
        FlipInput(
            discount_vs_market=discount,
            expected_roi=None if card.roi is None else float(card.roi),
            expected_profit=None if card.net_profit is None else float(card.net_profit),
            demand_score=0 if sell_through is None else round(sell_through_score(float(sell_through))),
            velocity_score=0 if days is None else round(days_score(float(days))),
            risk_score=risk.score,
            condition=condition,
            info_score=_info_score(item, sold_count, th),
        ),
        BRIEF_WEIGHTS,
    )
    card.flip_score = flip.score

    # 10. Classificazione: il rischio grave prevale sull'economia.
    if item.get("counterfeit_suspected"):
        card.classification = Classification.SCARTA
        card.reasons.insert(0, "contraffazione sospetta")
    elif risk.score >= VERY_HIGH_RISK:
        card.classification = Classification.SCARTA
        card.reasons.insert(0, f"rischio molto alto ({risk.score})")
    elif card.prudent_resale is None:
        card.classification = Classification.REVISIONE_UMANA
        card.reasons.append("nessun comparabile: valore non stimabile")
    elif not economics_ok:
        near = card.max_buy_price is not None and card.max_buy_price >= price * D("0.75")
        card.classification = Classification.MONITORA if near else Classification.SCARTA
        if near:
            card.reasons.append(f"interessante sotto {card.max_buy_price} (prezzo massimo d'acquisto)")
    elif review := _review_reasons(card, risk.score, sold_count, th):
        card.classification = Classification.REVISIONE_UMANA
        card.reasons.extend(review)
    elif flip.score >= th.min_flip_alert:
        card.classification = Classification.OPPORTUNITA
        card.reasons.append(f"soglie superate, Flip Score {flip.score}")
    else:
        card.classification = Classification.MONITORA
        card.reasons.append(f"Flip Score {flip.score} < {th.min_flip_alert}")
    return card


def _review_reasons(card: Card, risk_score: int, sold_count: int, th: Thresholds) -> list[str]:
    """Perché un articolo economicamente valido ha bisogno di una persona prima di diventare un'opportunità."""
    out = []
    if risk_score > th.max_risk:
        out.append(f"Risk Score {risk_score} > {th.max_risk}")
    if sold_count < th.min_sold_comparables:
        out.append(f"vendite concluse comparabili {sold_count} < {th.min_sold_comparables}")
    if card.unknown_costs:
        out.append("costi non conosciuti: " + ", ".join(card.unknown_costs))
    if card.missing:
        out.append("da verificare: " + ", ".join(card.missing))
    return out


def _max_buy(resale: Decimal, profile: CostProfile, th: Thresholds, item: dict[str, Any]) -> Decimal | None:
    """Prezzo massimo d'acquisto che rispetta profitto minimo, ROI minimo e spesa massima per articolo."""
    shipping = None if item.get("shipping") is None else to_decimal(item["shipping"])
    restoration = ZERO if item.get("restoration") is None else to_decimal(item.get("restoration", 0))
    candidate = max_buy_price(resale, profile, th.min_profit, th.min_roi, shipping, restoration)
    while candidate is not None and candidate > 0:
        if acquisition_cost(candidate, profile, shipping, None, restoration).total <= th.max_spend_per_item:
            return candidate
        candidate -= CENT
    return None


def run(
    items: list[dict[str, Any]],
    th: Thresholds | None = None,
    profile: CostProfile | None = None,
    *,
    profile_confirmed: bool = False,
) -> dict[str, Any]:
    """Passi 1-12 su un lotto di articoli; ritorna schede e report di sessione.

    ``profile_confirmed``: i costi del profilo sono cifre dell'utente. Senza conferma promozione e spedizione
    a carico del venditore restano "non conosciute" e nessun articolo supera la revisione umana."""
    th = th or Thresholds()
    profile = profile or CostProfile()
    seen: set[str] = set()
    alerted: set[str] = set()
    cards: list[Card] = []
    duplicates: list[str] = []
    for item in items:
        # 3. Deduplica per ID Vinted (uguale su tutti i domini), altrimenti per id dichiarato.
        key = listing_identity(item["url"])[1] if item.get("url") else f"id:{item.get('id')}"
        if key in seen:
            duplicates.append(str(item.get("id")))
            continue
        seen.add(key)
        cards.append(evaluate_item(item, profile, th, profile_confirmed=profile_confirmed))

    # Budget: le opportunità si contendono il capitale in ordine di Flip Score; nessuna spesa reale.
    reserved = ZERO
    for card in sorted(
        (c for c in cards if c.classification == Classification.OPPORTUNITA),
        key=lambda c: c.flip_score or 0,
        reverse=True,
    ):
        cost = card.total_cost or ZERO
        if reserved + cost > th.total_budget:
            card.classification = Classification.REVISIONE_UMANA
            card.reasons.append("budget residuo insufficiente")
            continue
        reserved += cost
        # 11. Alert una sola volta per articolo.
        if card.item_id not in alerted:
            alerted.add(card.item_id)
            card.alert = True

    def by(cls: Classification) -> list[Card]:
        return [c for c in cards if c.classification == cls]

    return {
        "thresholds": {k: str(v) for k, v in th.__dict__.items()},
        "profilo_costi": {"confermato": profile_confirmed, **profile.model_dump(mode="json")},
        "cards": [c.as_dict() for c in cards],
        "report": {
            "nuovi_articoli_analizzati": len(cards),
            "duplicati": duplicates,
            "opportunita_da_approvare": [c.item_id for c in by(Classification.OPPORTUNITA)],
            "revisione_umana": [c.item_id for c in by(Classification.REVISIONE_UMANA)],
            "monitora": [c.item_id for c in by(Classification.MONITORA)],
            "scartati": {c.item_id: c.reasons for c in by(Classification.SCARTA)},
            "alert_preparati": sorted(alerted),
            "alert_inviati": 0,
            "integrazioni_non_disponibili": [
                "Grok (nessuna connessione attiva)",
                "invio notifiche (collaudo a secco)",
            ],
            "capitale_impegnato": "0.00",
            "capitale_richiesto_dalle_opportunita": str(reserved),
            "budget_residuo": str(th.total_budget),
            "profitto_stimato_opportunita": str(
                sum((c.net_profit or ZERO) for c in by(Classification.OPPORTUNITA))
            ),
            "profitto_realizzato": "0.00",
            "prossime_azioni": [
                "approvazione umana per ogni opportunità (nessun acquisto automatico)",
                "verifica dei dati mancanti sugli articoli in revisione",
            ],
        },
    }


def _print(result: dict[str, Any]) -> None:
    for c in result["cards"]:
        print(f"[{c['classification']}] {c['item_id']} · {c['title']}")
        print(
            f"   rivendita prudente {c['prudent_resale']} ({c['resale_basis']}) · costo {c['total_cost']}"
            f" · profitto {c['net_profit']} · ROI {c['roi']} · Flip {c['flip_score']} · Risk {c['risk_score']}"
        )
        if c["max_buy_price"]:
            print(f"   prezzo massimo d'acquisto {c['max_buy_price']}")
        for r in c["reasons"]:
            print(f"   - {r}")
    print("\nREPORT DI SESSIONE")
    for k, v in result["report"].items():
        print(f"  {k}: {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=SAMPLE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    result = run(
        data["items"],
        profile=CostProfile(**data.get("cost_profile", {})),
        profile_confirmed=bool(data.get("profile_confirmed")),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print(result)


if __name__ == "__main__":
    main()
