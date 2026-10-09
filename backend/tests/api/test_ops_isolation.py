"""One account never sees or changes another's inventory, ledger, autonomy settings, goals or experiments."""

import csv
import io
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from tests.api.test_api import API
from tests.conftest import register


@pytest.fixture
async def other(auth_client: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        await register(c, "second@example.com", "Another-pass-2026!")
        yield c


async def test_a_second_account_sees_and_changes_nothing_of_the_first(
    auth_client: httpx.AsyncClient, other: httpx.AsyncClient
) -> None:
    mine = auth_client
    # ---- the first account fills in everything
    p = await mine.post(
        f"{API}/purchases",
        json={"title": "Felpa mia", "purchase_price": 20, "shipping_cost": 0, "buyer_protection_fee": 0,
              "purchase_date": "2026-09-20"},
    )  # fmt: skip
    pid = p.json()["purchase_id"]
    assert (
        await mine.patch(f"{API}/selling/inventory/{pid}", json={"stage": "listed", "listed_price": 40})
    ).status_code == 200
    exp = await mine.post(
        f"{API}/accounting/expenses",
        json={"spent_on": "2026-10-01", "kind": "packaging", "amount": 12, "note": "x"},
    )
    assert exp.status_code == 201, exp.text
    eid = exp.json()["id"]
    assert (
        await mine.put(f"{API}/autonomy/enable", json={"limits": {"daily_budget": 40}, "skip_dry_run": False})
    ).status_code == 200
    goals = {"monthly_profit_target": 500, "initial_capital": 300, "max_capital": 900, "weekly_hours": 8, "horizon_months": 6,
             "reinvest_pct": 0.7, "min_reserve": 50, "explore_share": 0.1, "holder_status": "occasional", "tax_thresholds": []}  # fmt: skip
    assert (await mine.put(f"{API}/business/goals", json=goals)).status_code == 200
    assert (await mine.get(f"{API}/learning/report?register=true")).status_code == 200

    # ---- the second one sees none of it
    inv = (await other.get(f"{API}/selling/inventory")).json()
    assert inv["items"] == []
    for method, url, body in (
        ("patch", f"{API}/selling/inventory/{pid}", {"stage": "sold"}),
        ("get", f"{API}/selling/inventory/{pid}/plan", None),
        ("post", f"{API}/selling/inventory/{pid}/offer", {"offer": 30}),
        ("delete", f"{API}/accounting/expenses/{eid}", None),
    ):
        r = await (
            getattr(other, method)(url, json=body) if body is not None else getattr(other, method)(url)
        )
        assert r.status_code == 404, (method, url, r.status_code, r.text)
    assert (await other.get(f"{API}/accounting/expenses")).json() == []
    stolen = await other.post(
        f"{API}/accounting/expenses",
        json={"spent_on": "2026-10-02", "kind": "refurb", "amount": 5, "purchase_id": pid},
    )
    assert stolen.status_code == 404  # an expense cannot be attached to someone else's purchase
    state: dict[str, Any] = (await other.get(f"{API}/autonomy")).json()
    assert state["enabled"] is False and state["limits"].get("daily_budget") in (None, 0)
    assert (await other.get(f"{API}/autonomy/audit")).json() == []
    assert (await other.get(f"{API}/autonomy/actions")).json() == []
    assert (await other.get(f"{API}/business/goals")).json()["monthly_profit_target"] is None
    assert (await other.get(f"{API}/learning/experiments")).json() == []

    # ---- and what the second one does does not leak back
    assert (await other.post(f"{API}/autonomy/kill")).status_code in (200, 409)
    assert (await mine.get(f"{API}/autonomy")).json()["killed"] is False
    assert (await mine.get(f"{API}/accounting/expenses")).json() != []
    assert (await mine.get(f"{API}/selling/inventory")).json()["items"][0]["stage"] == "listed"


async def test_listing_link_must_be_a_web_address(auth_client: httpx.AsyncClient) -> None:
    p = await auth_client.post(
        f"{API}/purchases",
        json={"title": "Giacca", "purchase_price": 20, "shipping_cost": 0, "buyer_protection_fee": 0,
              "purchase_date": "2026-09-20"},
    )  # fmt: skip
    url = f"{API}/selling/inventory/{p.json()['purchase_id']}"
    for bad in (
        "javascript:alert(1)",
        "data:text/html,<script>1</script>",
        "//evil.example",
        "vinted.it/items/1",
    ):
        r = await auth_client.patch(url, json={"listing_url": bad})
        assert r.status_code == 422, (bad, r.status_code)
    ok = await auth_client.patch(url, json={"listing_url": " https://www.vinted.it/items/123-giacca "})
    assert ok.status_code == 200 and ok.json()["listing_url"] == "https://www.vinted.it/items/123-giacca"
    assert (await auth_client.patch(url, json={"listing_url": ""})).json()["listing_url"] is None  # cleared


def test_ledger_csv_neutralises_formulas_in_every_text_cell() -> None:
    from datetime import date
    from decimal import Decimal

    from app.selling.accounting import LedgerRow, to_csv

    rows = [
        LedgerRow(date(2026, 10, 1), "purchase", '=HYPERLINK("http://x")', "@SUM(A1)", Decimal("-20")),
        LedgerRow(date(2026, 10, 2), "sale", "ok", "", Decimal("40")),
        LedgerRow(date(2026, 10, 3), "expense", "+cmd", "-1+1", Decimal("-3")),
    ]
    cells = list(csv.reader(io.StringIO(to_csv(rows).lstrip("\ufeff")), delimiter=";"))[1:]
    assert cells[0][2].startswith("'=") and cells[0][3].startswith("'@")
    assert cells[1][3] == ""  # an empty description stays empty
    assert cells[2][2].startswith("'+") and cells[2][3].startswith("'-")
    assert cells[1][4] == "40,00"  # numbers are untouched
