"""API of the acquisition modes: links, "update now", email upload, status page."""

from pathlib import Path

import httpx

from app.db.session import session_scope
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.marketplace.registry import set_provider
from tests.api.test_api import API, seed_deal
from tests.conftest import NOW

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "vinted"


async def test_paste_links_creates_tracked_records(auth_client: httpx.AsyncClient) -> None:
    text = (
        "guarda questi: https://www.vinted.it/items/111-polo?referrer=catalog, "
        "https://www.vinted.fr/items/111-polo (doppione), https://www.vinted.de/items/222-jacke "
        "e questo no https://example.com/items/333"
    )
    r = await auth_client.post(f"{API}/listings/import/links", json={"text": text})
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["found"], body["created"], body["existing"]) == (2, 2, 0)
    assert [i["vinted_id"] for i in body["items"]] == ["111", "222"]
    again = (await auth_client.post(f"{API}/listings/import/links", json={"text": text})).json()
    assert (again["created"], again["existing"]) == (0, 2)
    detail = (await auth_client.get(f"{API}/items/111")).json()
    assert detail["item"]["capture_level"] == "link" and detail["item"]["status"] == "unknown"
    assert detail["tracking"]["tracked"] and detail["analysis"] is None
    none = (await auth_client.post(f"{API}/listings/import/links", json={"text": "niente link qui"})).json()
    assert none["found"] == 0


async def test_update_now_uses_the_best_mode(auth_client: httpx.AsyncClient, make_listing) -> None:
    # Vinted listing, no server mode enabled: queued for the extension.
    await auth_client.post(
        f"{API}/listings/import/links", json={"text": "https://www.vinted.it/items/4242-x"}
    )
    q = (await auth_client.post(f"{API}/items/4242/refresh")).json()
    assert q["outcome"] == "queued" and q["needs_extension"] and q["mode"] == "extension_refresh"

    # Listing from the configured provider (demo market): read again from the provider.
    class OneListing:
        name = "mock"

        def __init__(self, pl):
            self.pl = pl

        async def get_listing(self, external_id: str):
            return self.pl

    pl = make_listing(price=20, external_id="9001")
    async with session_scope() as s:
        res = await IngestionService(s, "mock").ingest([pl], now=NOW)
    set_provider(OneListing(pl.model_copy(update={"price": pl.price - 2})))
    try:
        r = (await auth_client.post(f"{API}/items/{res.new_ids[0]}/refresh")).json()
    finally:
        set_provider(None)
    assert r["outcome"] == "updated" and r["mode"] == "provider_scan"
    closed = await auth_client.post(f"{API}/items/{res.new_ids[0]}/refresh")
    assert closed.status_code == 200


async def test_email_upload_and_status_page(auth_client: httpx.AsyncClient, make_listing) -> None:
    await seed_deal(make_listing)
    async with session_scope() as s:
        await IngestionService(s, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest(
            [make_listing(price=18, external_id="4242424242")], now=NOW
        )
    r = await auth_client.post(
        f"{API}/acquisition/email",
        content=(FIX / "email_sold.eml").read_bytes(),
        headers={"Content-Type": "message/rfc822"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["sold"] == 1
    detail = (await auth_client.get(f"{API}/items/4242424242")).json()
    assert detail["item"]["status"] == "sold" and detail["tracking"]["sold_at"]
    too_big = await auth_client.post(f"{API}/acquisition/email", content=b"x" * (2 * 1024 * 1024 + 1))
    assert too_big.status_code == 413 and "Traceback" not in too_big.text

    status = (await auth_client.get(f"{API}/acquisition/status")).json()
    assert status["public_fetch"]["enabled"] is False
    assert status["email"]["enabled"] is False
    assert status["parser_config_version"]
    assert status["extension"]["listings"] >= 1
