"""When an observation deserves a history row, and what its payload records (pure)."""

from datetime import timedelta
from decimal import Decimal

from app.domain.enums import CaptureLevel
from app.ingestion.observations import (
    Current,
    LastSnapshot,
    observation_payload,
    photos_changed,
    snapshot_reasons,
)
from app.marketplace.base import ProviderImage, ProviderListing
from tests.conftest import NOW

PREV = LastSnapshot(
    observed_at=NOW,
    status="active",
    price=Decimal("20"),
    favourite_count=3,
    capture_level="card",
    image_keys=("a",),
    images_complete=False,
)


def cur(**kw) -> Current:
    base = {
        "status": "active",
        "price": Decimal("20"),
        "favourite_count": 3,
        "capture_level": "card",
        "image_keys": ("a",),
        "images_complete": False,
    }
    return Current(**{**base, **kw})


def test_first_observation_always_gets_a_row() -> None:
    assert snapshot_reasons(None, cur(), NOW) == ["first"]


def test_an_unchanged_sighting_adds_nothing_until_the_heartbeat() -> None:
    assert snapshot_reasons(PREV, cur(), NOW + timedelta(minutes=5)) == []
    assert snapshot_reasons(PREV, cur(), NOW + timedelta(hours=5, minutes=59)) == []
    assert snapshot_reasons(PREV, cur(), NOW + timedelta(hours=6)) == ["heartbeat"]


def test_each_kind_of_change_is_named() -> None:
    later = NOW + timedelta(minutes=1)
    assert snapshot_reasons(PREV, cur(price=Decimal("18")), later) == ["price"]
    assert snapshot_reasons(PREV, cur(status="sold"), later) == ["status"]
    assert snapshot_reasons(PREV, cur(favourite_count=4), later) == ["favourites"]
    assert snapshot_reasons(PREV, cur(image_keys=("b",)), later) == ["photos"]
    assert snapshot_reasons(PREV, cur(price=Decimal("18"), status="sold"), later) == ["price", "status"]


def test_a_card_that_does_not_show_a_value_never_counts_as_a_change() -> None:
    later = NOW + timedelta(minutes=1)
    assert snapshot_reasons(PREV, cur(favourite_count=None), later) == []
    assert snapshot_reasons(PREV, cur(image_keys=()), later) == []
    assert snapshot_reasons(PREV, cur(price=None, status=None), later) == []


def test_a_richer_capture_is_recorded() -> None:
    later = NOW + timedelta(minutes=1)
    full = cur(capture_level="full", image_keys=("a", "b", "c"), images_complete=True)
    assert snapshot_reasons(PREV, full, later) == ["photos", "richer"]


def test_cover_only_cards_do_not_look_like_removed_photos() -> None:
    gallery = LastSnapshot(NOW, "active", Decimal("20"), 3, "full", ("a", "b", "c"), True)
    assert not photos_changed(gallery, cur(image_keys=("a",)))
    assert photos_changed(gallery, cur(image_keys=("a", "b"), images_complete=True))
    assert photos_changed(gallery, cur(image_keys=("a", "b", "c", "d"), images_complete=True))
    assert photos_changed(gallery, cur(image_keys=("z",)))


def test_payload_types_every_field_and_omits_what_was_not_seen() -> None:
    pl = ProviderListing(
        external_id="1",
        url="https://www.vinted.it/items/1",
        title="Felpa Nike",
        price=Decimal("10"),
        brand="Nike",
        condition="Buone condizioni",
        favourite_count=2,
        published_at=NOW,
        published_at_kind="relative",
        images=[ProviderImage(url="https://images1.vinted.net/t/01_a/f800/1.jpeg?s=x")],
        capture_level=CaptureLevel.CARD,
    )
    fields = observation_payload(pl, "active")["fields"]
    assert fields["price"] == {"v": "10", "t": "observed", "c": "read", "s": "card"}
    assert fields["title"]["t"] == "declared" and fields["brand"]["t"] == "declared"
    assert fields["published_at"]["t"] == "inferred" and fields["published_at"]["c"] == "approx"
    # Not in the card, so not in the record - never stored as an empty value.
    assert "view_count" not in fields and "description" not in fields and "shipping_fee" not in fields
    assert fields["photos"]["complete"] is False


def test_a_link_only_record_has_no_price_or_status() -> None:
    pl = ProviderListing(
        external_id="1",
        url="https://www.vinted.it/items/1",
        title="x",
        price=Decimal(0),
        capture_level=CaptureLevel.LINK,
    )
    fields = observation_payload(pl, "unknown")["fields"]
    assert "price" not in fields and "status" not in fields
