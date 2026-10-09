"""Cleanup of photos and seller data that never belonged to a listing (profile photo bug)."""

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.seller_key import protect
from app.db.models import Listing, ListingImage, Seller
from app.db.session import session_scope
from app.media.cleanup import clean_foreign_data
from tests.api.test_api import API, seed_deal

MY_AVATAR = "https://images1.vinted.net/t/AVATAR_ME/f800/1.jpeg"
BASE = {"brand": "Ralph Lauren", "size": "M", "condition": "Ottime condizioni", "price": 15}


def stored_key(seller: int) -> str:
    """The key as stored: the extension's hash wrapped by the server (app.core.seller_key)."""
    return protect(f"h:{seller:024x}", get_settings().seller_key_secret_bytes())


def item(vid: int, seller: int, **extra) -> dict:
    return {
        **BASE,
        "url": f"https://www.vinted.it/items/{vid}-polo",
        "title": f"Polo Ralph Lauren {vid}",
        "seller_key": f"h:{seller:024x}",
        **extra,
    }


async def test_saved_analyses_lose_foreign_photos_and_copied_seller_profiles(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    await seed_deal(make_listing)
    # Saved with the old parser: my avatar first in every gallery, my rating on every seller.
    for n in range(6):
        photos = [
            MY_AVATAR,
            f"https://images1.vinted.net/t/ITEM_{n}_a/f800/1.jpeg",
            f"https://images1.vinted.net/t/ITEM_{n}_b/f800/1.jpeg",
        ]
        body = item(8100 + n, 500 + n, image_urls=photos, seller_rating=5.0, seller_review_count=999)
        r = await auth_client.post(f"{API}/capture/item", json={"item": body})
        assert r.status_code == 200, r.text
    # A genuine seller with its own figures is left alone.
    r = await auth_client.post(
        f"{API}/capture/item",
        json={
            "item": item(
                8200,
                900,
                image_urls=["https://images1.vinted.net/t/OK/f800/1.jpeg"],
                seller_rating=4.7,
                seller_review_count=31,
            )
        },
    )
    assert r.status_code == 200

    async with session_scope() as s:
        dry = await clean_foreign_data(s, dry_run=True)
    assert (dry.images_removed, dry.sellers_reset, len(dry.listing_ids)) == (6, 6, 6)

    async with session_scope() as s:
        report = await clean_foreign_data(s)
    assert report.images_removed == 6
    async with session_scope() as s:
        images = (
            await s.execute(
                select(Listing.external_id, ListingImage.position, ListingImage.url)
                .join(Listing, Listing.id == ListingImage.listing_id)
                .order_by(Listing.external_id, ListingImage.position)
            )
        ).all()
        by_item: dict[str, list[tuple[int, str]]] = {}
        for vid, pos, url in images:
            by_item.setdefault(vid, []).append((pos, url))
        assert all(MY_AVATAR not in [u for _, u in v] for v in by_item.values())
        assert [p for p, _ in by_item["8100"]] == [0, 1] and "ITEM_0_a" in by_item["8100"][0][1]
        sellers = {
            s_.external_id: (s_.rating, s_.review_count) for s_ in (await s.execute(select(Seller))).scalars()
        }
        assert sellers[stored_key(500)] == (None, 0)  # unknown again, never invented
        assert float(sellers[stored_key(900)][0]) == 4.7
        photo_counts = dict(
            (
                await s.execute(
                    select(Listing.external_id, Listing.photo_count).where(Listing.external_id.like("81%"))
                )
            ).all()
        )
        assert set(photo_counts.values()) == {2}
    # Idempotent: nothing left to remove.
    async with session_scope() as s:
        again = await clean_foreign_data(s)
    assert (again.images_removed, again.sellers_reset) == (0, 0)


async def test_a_capture_of_the_gallery_replaces_stored_photos_even_when_fewer(
    auth_client: httpx.AsyncClient, make_listing
) -> None:
    await seed_deal(make_listing)
    old = [
        MY_AVATAR,
        "https://images1.vinted.net/t/G1/f800/1.jpeg",
        "https://images1.vinted.net/t/G2/f800/1.jpeg",
    ]
    await auth_client.post(f"{API}/capture/item", json={"item": item(8300, 1, image_urls=old)})
    gallery = old[1:]
    r = await auth_client.post(
        f"{API}/capture/item", json={"item": item(8300, 1, image_urls=gallery, images_source="item_json")}
    )
    assert r.status_code == 200
    detail = (await auth_client.get(f"{API}/items/8300")).json()
    assert [i["url"] for i in detail["images"]] == gallery
    # Without the gallery marker a smaller set does not overwrite (it may be a partial capture).
    await auth_client.post(
        f"{API}/capture/item", json={"item": item(8300, 1, image_urls=gallery[:1], images_source="jsonld")}
    )
    assert len((await auth_client.get(f"{API}/items/8300")).json()["images"]) == 2
