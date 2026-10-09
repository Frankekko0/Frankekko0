"""Phase 8: the selling cycle in pure logic: stages, the truthful listing, the price plan, offers, repricing,
the ledger, forecast against reality and the negotiation assistant."""

from datetime import date
from decimal import Decimal as D
from itertools import pairwise

import pytest

from app.intelligence import survival
from app.negotiation import assistant
from app.selling import accounting, learning, listing_draft, offers, pricing, repricing, stages


# ------------------------------------------------------------------ stages
def test_the_stages_follow_the_selling_cycle_and_refuse_shortcuts() -> None:
    path = ["purchased", "arriving", "to_list", "listed", "sold"]
    for a, b in pairwise(path):
        stages.check_move(a, b)
    assert stages.can_move("listed", "to_list") and stages.can_move("sold", "returned")
    for a, b in [("purchased", "sold"), ("to_list", "sold"), ("arriving", "listed"), ("sold", "listed")]:
        with pytest.raises(stages.StageError, match="non si passa"):
            stages.check_move(a, b)
    with pytest.raises(stages.StageError, match="sconosciuto"):
        stages.check_move("listed", "teleported")
    assert set(stages.STAGES) == set(stages.TRANSITIONS) and "sold" not in stages.IN_STOCK


# ------------------------------------------------------------------ the truthful listing
def test_a_draft_says_only_what_is_known_and_asks_for_the_rest() -> None:
    f = listing_draft.ItemFacts(
        brand="Ralph Lauren", category="felpa con cappuccio", size="M", color="blu navy",
        condition="very_good", label_visible_in_photos=True, photo_roles=("front", "label"),
    )  # fmt: skip
    d = listing_draft.build_draft(f)
    assert d.title == "Ralph Lauren felpa con cappuccio blu navy tg. M"
    assert "ottime condizioni" in d.description and "Etichetta interna visibile nelle foto" in d.description
    # what it does not know is asked, not invented
    assert any("difetti" in c for c in d.to_confirm) and any("Misurare" in c for c in d.to_confirm)
    assert "Nelle foto non si vedono difetti" not in d.description  # nobody checked
    assert listing_draft.claims_not_supported(d.description) == []
    assert any("Autenticità" in n for n in d.not_claimed)
    assert not any("Davanti" in s for s in d.photo_checklist) and any(
        "composizione" in s for s in d.photo_checklist
    )


def test_defects_are_disclosed_and_get_a_close_up_when_there_is_none() -> None:
    f = listing_draft.ItemFacts(
        brand="Nike", category="felpa", size="L", condition="good", defects_checked=True,
        defects=(listing_draft.Defect("macchia", "manica sinistra", photo=4), listing_draft.Defect("pilling", "polsini")),
        measures_cm={"spalle": 52.0, "lunghezza": 70.0},
    )  # fmt: skip
    d = listing_draft.build_draft(f)
    assert "Difetto: macchia (manica sinistra), vedi foto 4." in d.description
    assert "Difetto: pilling (polsini)." in d.description
    assert "Misure: spalle 52 cm, lunghezza 70 cm." in d.description and d.to_confirm == []
    assert any("Primo piano del difetto: pilling" in s for s in d.photo_checklist)
    assert not any(
        "Primo piano del difetto: macchia" in s for s in d.photo_checklist
    )  # photo 4 already shows it


def test_untrue_claims_are_detected_in_edited_texts() -> None:
    assert listing_draft.claims_not_supported("Felpa 100% originale, come nuovo, mai usato") == [
        "originale", "100% originale", "mai usato", "come nuovo"
    ]  # fmt: skip
    long_title = listing_draft.build_draft(listing_draft.ItemFacts(brand="B" * 40, category="c" * 40)).title
    assert len(long_title) <= 70


# ------------------------------------------------------------------ price plan, offers, repricing
FIT = survival.SurvivalFit(h0=0.05, beta=5.0, n=300, events=120, reliable=True, source="measured")
COST = 20.0
profit_at = lambda price: price * 0.9 - 1.0 - COST  # noqa: E731


def test_the_resale_plan_starts_above_the_best_price_and_ends_at_the_floor() -> None:
    plan = pricing.plan_resale(reference=40.0, profit_at=profit_at, fit=FIT, min_profit=5.0)
    assert plan is not None
    assert (
        profit_at(plan.floor) >= 5.0 > profit_at(plan.floor - 0.1)
    )  # the lowest price that clears the minimum
    assert plan.floor <= plan.best_price < plan.start_price <= 40.0 * pricing.MAX_RATIO
    assert plan.markdowns[0].price == plan.start_price and plan.markdowns[-1].price == plan.floor
    assert plan.basis == "measured" and plan.reliable and "misurati" in plan.note
    prior = pricing.plan_resale(reference=40.0, profit_at=profit_at, fit=survival.prior_fit(), min_profit=5.0)
    assert prior is not None and not prior.reliable and "ipotesi" in prior.note
    # an item that cannot make the minimum profit at any price gets no plan
    assert pricing.plan_resale(reference=40.0, profit_at=lambda p: -1.0, fit=FIT, min_profit=5.0) is None
    assert pricing.plan_resale(reference=0, profit_at=profit_at, fit=FIT) is None


def decide_offer(offer: float, asking: float = 44.0, floor: float = 28.0) -> offers.OfferDecision:
    return offers.evaluate_offer(
        offer=offer, asking=asking, cost=COST, floor=floor, reference=40.0, profit_at=profit_at, fit=FIT
    )


def test_offers_are_accepted_countered_or_declined_by_what_waiting_is_worth() -> None:
    high = decide_offer(43.0)
    assert (
        high.action == "accept" and high.profit_at_offer >= high.value_of_waiting and "accetto" in high.reply
    )
    mid = decide_offer(32.0)
    assert mid.action == "counter" and 32.0 < (mid.counter_price or 0) <= 44.0
    assert (mid.counter_price or 0) >= 28.0 and "controfferta" in mid.reason
    low = decide_offer(15.0)
    assert low.action == "decline" and low.counter_price is None and "minimo" in low.reason
    # an offer just under the floor is countered half way to the asking price, never below the floor
    near = decide_offer(26.0)
    assert near.action == "counter" and near.counter_price == 35.0  # half way, and above the floor
    for d in (high, mid, low, near):
        assert assistant.pressure_phrases(d.reply) == []


def test_waiting_is_worth_less_when_the_price_is_high_and_the_capital_is_tied_up() -> None:
    cheap = offers.value_of_waiting(36.0, COST, 40.0, profit_at, FIT)
    dear = offers.value_of_waiting(60.0, COST, 40.0, profit_at, FIT)
    assert dear < cheap  # a much higher price is far less likely to sell within a month
    assert offers.value_of_waiting(
        44.0, COST, 40.0, profit_at, FIT, daily_capital_cost=0.02
    ) < offers.value_of_waiting(44.0, COST, 40.0, profit_at, FIT)


def test_repricing_follows_the_plan_and_tells_the_listing_problem_from_the_price_problem() -> None:
    plan = survival.markdown_plan(FIT, 40.0, 50.0, 30.0, steps=3)
    due = plan[1]
    lower = repricing.advise(
        days_listed=due.day, asking=50.0, floor=30.0, markdowns=plan, views=200, favourites=12
    )
    assert lower.action == "lower" and lower.new_price is not None and 30.0 <= lower.new_price < 50.0
    early = repricing.advise(days_listed=0, asking=50.0, floor=30.0, markdowns=plan, views=5)
    assert early.action == "hold" and "non lo richiede" in early.reason
    nobody = repricing.advise(days_listed=9, asking=50.0, floor=30.0, markdowns=plan, views=6)
    assert nobody.action == "improve_listing" and "annuncio" in nobody.reason
    interest = repricing.advise(
        days_listed=due.day,
        asking=50.0,
        floor=30.0,
        markdowns=plan,
        views=100,
        favourites=10,
        offers_received=0,
    )
    assert "preferiti ma nessuna offerta" in interest.reason
    fast = repricing.advise(days_listed=1, asking=40.0, floor=30.0, markdowns=plan, sold_within_days=1)
    assert fast.action == "raise" and fast.new_price == 43.2
    floor_hit = repricing.advise(days_listed=90, asking=30.0, floor=30.0, markdowns=plan, views=500)
    assert floor_hit.action == "hold" and "prezzo minimo" in floor_hit.reason


# ------------------------------------------------------------------ the ledger (test K, accounting side)
P = accounting.PurchaseRec
S = accounting.SaleRec
E = accounting.ExpenseRec


def books() -> tuple[list[P], list[S], list[E]]:
    purchases = [
        P("p1", "Felpa RL", date(2026, 9, 1), D("25.00"), sold=True),
        P("p2", "Polo Lacoste", date(2026, 9, 5), D("18.50"), sold=False),
        P("p3", "Giacca", date(2026, 8, 20), D("30.00"), sold=True),
    ]
    sales = [
        S(
            "s1",
            "p1",
            "Felpa RL",
            date(2026, 9, 8),
            D("45.00"),
            D("0"),
            D("4.00"),
            D("0.50"),
            D("0"),
            D("25.00"),
        ),
        S(
            "s3",
            "p3",
            "Giacca",
            date(2026, 8, 30),
            D("40.00"),
            D("0"),
            D("4.00"),
            D("0.50"),
            D("0"),
            D("30.00"),
        ),
    ]
    expenses = [E("e1", date(2026, 9, 2), "packaging", D("6.00"), "scatole")]
    return purchases, sales, expenses


def test_the_realised_profit_matches_the_numbers_entered_and_stock_is_not_a_loss() -> None:
    p, s, e = books()
    sm = accounting.summarize(p, s, e, date(2026, 9, 1), date(2026, 9, 30))
    assert (
        sm["revenue"] == D("45.00")
        and sm["selling_costs"] == D("4.50")
        and sm["cost_of_goods_sold"] == D("25.00")
    )
    assert sm["realized_profit"] == D("15.50")  # 45 - 4.50 - 25
    assert sm["realized_profit_after_expenses"] == D("9.50")  # minus the 6 euros of boxes
    assert sm["cash_out"] == D("25.00") + D("18.50") + D("4.50") + D("6.00") and sm["cash_in"] == D("45.00")
    assert sm["cash_flow"] == D("45.00") - D("54.00")
    assert sm["stock_items"] == 1 and sm["stock_at_cost"] == D(
        "18.50"
    )  # unsold stock is shown, not counted as loss
    # a wider period sees both sales
    year = accounting.summarize(p, s, e, date(2026, 1, 1), date(2026, 12, 31))
    assert year["realized_profit"] == D("15.50") + D("5.50") and year["sales_count"] == 2
    assert set(year["by_month"]) == {"2026-08", "2026-09"}


def test_the_ledger_rows_are_signed_ordered_and_exported_safely() -> None:
    p, s, e = books()
    rows = accounting.build_ledger(p, s, e, date(2026, 9, 1), date(2026, 9, 30))
    assert [r.kind for r in rows] == ["purchase", "expense", "purchase", "sale", "shipping"]
    assert rows[0].amount == D("-25.00") and rows[3].amount == D("45.00") and rows[4].amount == D("-4.50")
    rows.append(accounting.LedgerRow(date(2026, 9, 30), "expense", "e2", "=HYPERLINK(evil)", D("-1")))
    text = accounting.to_csv(rows)
    assert text.startswith("﻿") and "'=HYPERLINK(evil)" in text and "45,00" in text
    assert text.splitlines()[0].count(";") == 5


def test_the_accountant_report_invents_no_rate_and_shows_the_users_thresholds() -> None:
    p, s, e = books()
    sm = accounting.summarize(p, s, e, date(2026, 1, 1), date(2026, 12, 31))
    rep = accounting.accountant_report(
        sm, "private",
        [{"name": "soglia X", "amount": 2000, "source": "sito ufficiale", "as_of": "2026-01-10"}, {"name": "rotta"}],
        D("85.00"),
    )  # fmt: skip
    assert len(rep["thresholds"]) == 1  # the malformed one is dropped, not guessed
    t = rep["thresholds"][0]
    assert (
        t["source"] == "sito ufficiale" and t["as_of"] == "2026-01-10" and t["share"] == pytest.approx(0.0425)
    )
    assert any("Nessuna aliquota" in n for n in rep["notes"])


# ------------------------------------------------------------------ forecast against reality
def test_forecast_and_result_are_kept_apart_and_the_gap_is_measured() -> None:
    assert learning.price_error_pct(60, 42) == pytest.approx(-0.30)  # expected 60, sold 42
    assert learning.price_error_pct(None, 42) is None and learning.price_error_pct(0, 42) is None
    assert learning.days_error(10, 25) == 15
    row = learning.OutcomeRow
    rows = [
        row("rl", "felpe", 60, 10, 30, 42, 25, 12),
        row("rl", "felpe", 50, 12, 20, 45, 14, 17),
        row("rl", "polo", 40, 8, 15, 38, 9, 14),
        row("nike", "felpe", 30, 7, 8, 33, 5, 11),
    ]
    acc = learning.accuracy(rows)
    o = acc["overall"]
    assert (
        o["n"] == 4
        and o["actual_profit"] == 54.0
        and o["predicted_profit"] == 73.0
        and o["profit_gap"] == -19.0
    )
    assert o["price_bias_pct"] < 0  # on average we sold below the forecast
    assert set(acc["by_brand"]) == {"rl"}  # a segment needs 3 outcomes before it is reported
    assert "Nessuna vendita" in learning.accuracy([])["note"]


# ------------------------------------------------------------------ the negotiation assistant (§4.16)
def plan_for(price: float = 28.0, **kw: object) -> assistant.NegotiationPlan:
    def at(p: float) -> tuple[float, float | None]:
        profit = p * -0.0 + (
            34.0 * 0.9 - 1.0 - p * 1.05 - 3.5
        )  # resale 34 net of 10%, buyer protection and shipping
        return profit, profit / (p + 4.0)

    kwargs: dict[str, object] = dict(
        title="Felpa Ralph Lauren cappuccio blu navy taglia M", asked=price, profit_at=at, max_buy=22.0, ideal_offer=20.0,
        similar_sold_median=24.0, signals=assistant.SellerSignals(days_online=20, price_drops=1, favourites=2),
    )  # fmt: skip
    kwargs.update(kw)
    return assistant.build_plan(**kwargs)  # type: ignore[arg-type]


def test_the_plan_shows_ideal_maximum_discount_needed_and_profit_at_several_prices() -> None:
    p = plan_for()
    assert p.asked == 28.0 and p.ideal_offer == 20.0 and p.max_acceptable == 22.0
    assert p.discount_needed == 6.0 and p.discount_needed_pct == pytest.approx(0.2143, abs=1e-4)
    prices = [row["price"] for row in p.profit_table]
    assert prices == sorted(prices, reverse=True) and 22.0 in prices and 28.0 in prices
    profits = [row["profit"] for row in p.profit_table]
    assert profits == sorted(profits)  # the lower the price, the higher the profit
    assert any("simili sono stati venduti" in r for r in p.reasons)


def test_the_seller_s_willingness_comes_from_observable_facts() -> None:
    eager, _ = assistant.willingness(
        assistant.SellerSignals(days_online=30, price_drops=2, favourites=1, bundle_possible=True)
    )
    fresh, why = assistant.willingness(assistant.SellerSignals(days_online=0.5, favourites=30))
    assert eager > 70 > 50 > fresh and any("pochissimo" in w for w in why)
    assert assistant.willingness(assistant.SellerSignals())[0] == 40  # no facts, no opinion


def test_messages_are_courteous_and_never_pressure_or_invent() -> None:
    p = plan_for(signals=assistant.SellerSignals(days_online=20, bundle_possible=True))
    assert "20 €" in p.messages["first_offer"] and "Felpa Ralph Lauren" in p.messages["first_offer"]
    assert "Grazie" in p.messages["first_offer"] and "bundle" in p.messages
    for text in p.messages.values():
        assert assistant.pressure_phrases(text) == [], text
    assert assistant.pressure_phrases("Ti do l'ultimo prezzo, ci sono altri interessati!") == [
        "ultimo prezzo",
        "altri interessati",
    ]


def test_when_the_price_is_already_fine_there_is_nothing_to_negotiate_and_a_huge_gap_is_flagged() -> None:
    fine = plan_for(price=21.0)
    assert fine.discount_needed is None and "accept" in fine.messages and "già dentro" in fine.note
    bare = assistant.build_plan(
        title="Felpa", asked=21.0, profit_at=lambda p: (5.0, 0.3), max_buy=None, ideal_offer=None
    )
    assert set(bare.messages) == {"accept"} and "già dentro" in bare.note
    far = plan_for(price=60.0, max_buy=22.0, ideal_offer=19.0)
    assert (
        far.discount_needed_pct is not None and far.discount_needed_pct > 0.6 and "supera il 25%" in far.note
    )
