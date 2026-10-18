"""What the photos established survives a re-capture of the same listing (F1, the blocker of the ingest wave).

A page capture carries text only. Re-sending a listing (re-opening the page, a price drop, the extension queue replaying
it) used to overwrite ``listings.identification`` with a text-only identification: the stored photo analysis, the
"photos reused by another seller" signal and the brand/size read on a label were erased, the next analysis computed a
verdict without them and nothing queued the photo check again. Everything here runs with a scripted model.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, text

from app.ai import service
from app.ai.budget import AiBudget
from app.ai.llm import LLMClient
from app.core.config import get_settings
from app.db.models import Brand, Listing, ListingImage, Opportunity
from app.db.session import session_scope
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.marketplace.base import ProviderImage, ProviderListing
from app.media import archive
from app.opportunities.analysis_record import classify_trigger, listing_inputs
from app.opportunities.pipeline import AnalysisPipeline
from app.vision import analyzer as az
from app.vision.analyzer import PhotoInput
from app.vision.types import ImageAnalysis, PhotoCheck, PhotoQuality
from app.workers import tasks
from tests.conftest import NOW
from tests.integration.test_ai_budget import FakeAnthropic, text_response
from tests.integration.test_ai_budget import settings as ai_settings
from tests.photos import jpeg
from tests.unit.test_vision_parse import ANSWER


@pytest.fixture(autouse=True)
def no_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    async def heuristic(self: Any, photos: list[PhotoInput], ctx: dict[str, Any]) -> ImageAnalysis:
        n = len(photos)
        return ImageAnalysis(
            analyzer="heuristic",
            photo_quality=PhotoQuality(photo_count=n, analyzed_count=n),
            photo_hashes=[f"{i:016x}" for i in range(n)],
            photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.8) for i in range(n)],
        )

    monkeypatch.setattr(az.HeuristicImageAnalyzer, "analyze", heuristic)


class Setup:
    """A captured item page whose photos were uploaded and read by the (scripted) model."""

    def __init__(self, fake: FakeAnthropic, listing: ProviderListing, listing_id: uuid.UUID) -> None:
        self.fake = fake
        self.listing = listing
        self.listing_id = listing_id

    async def recapture(self, listing: ProviderListing | None = None, hours: float = 1.0) -> Any:
        """The same item page sent again (``hours`` later), as the extension does on every visit."""
        from datetime import timedelta

        async with session_scope() as s:
            return await IngestionService(s, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest(
                [listing or self.listing], now=NOW + timedelta(hours=hours)
            )

    async def stored(self) -> Listing:
        async with session_scope() as s:
            row = await s.get(Listing, self.listing_id)
            assert row is not None
            s.expunge(row)
            return row


async def checked_listing(
    make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, **kw: Any
) -> Setup:
    media = get_settings().model_copy(update={"media_dir": str(tmp_path)})
    monkeypatch.setattr(archive, "get_settings", lambda: media)
    cfg = ai_settings()
    fake = FakeAnthropic(text_response(ANSWER, tokens=(6000, 1500)))
    llm = LLMClient(cfg, budget=AiBudget(cfg))
    llm._client = fake  # type: ignore[assignment]
    monkeypatch.setattr(service, "get_llm", lambda: llm)
    # No brand in the text: the brand is only known from the label the model read on the photos.
    pl = make_listing(
        title="Felpa grigia con cappuccio",
        brand=None,
        category=None,
        size=None,
        photos=3,
        published_days_ago=0.05,
        external_id=kw.pop("external_id", "7101"),
        **kw,
    )
    async with session_scope() as s:
        res = await IngestionService(s, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest([pl], now=NOW)
        listing_id = res.new_ids[0]
    async with session_scope() as s:  # the browser uploads the photos
        keys = (
            (
                await s.execute(
                    select(ListingImage.image_key)
                    .where(ListingImage.listing_id == listing_id)
                    .order_by(ListingImage.position)
                )
            )
            .scalars()
            .all()
        )
        for i, key in enumerate(keys):
            up = await archive.register_upload(s, pl.external_id, key, jpeg(i), "image/jpeg", media)
            assert up.stored, up.error
    async with session_scope() as s:
        assert await service.run_vision(s, listing_id) is True  # the first model analysis is a change
    return Setup(fake, pl, listing_id)


async def nike_id() -> int:
    async with session_scope() as s:
        return (await s.execute(select(Brand.id).where(Brand.slug == "nike"))).scalar_one()


async def mark_reused(listing_id: uuid.UUID) -> None:
    """What ``photo_provenance`` records when another seller's listing shows the same photos."""
    async with session_scope() as s:
        await s.execute(
            text(
                "UPDATE listings SET identification = identification || '{\"photos_reused_by_other_seller\": true}'"
                " WHERE id = :i"
            ),
            {"i": listing_id},
        )


async def test_recapturing_the_same_page_keeps_the_photo_analysis_and_what_the_label_established(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    sx = await checked_listing(make_listing, monkeypatch, tmp_path)
    nike = await nike_id()
    before = await sx.stored()
    assert before.identification["vision"]["analyzer"] == "claude_vision"
    assert before.brand_id == nike  # read on the label, not in the text
    await mark_reused(sx.listing_id)

    await sx.recapture()  # the page is opened again: same text, same photos
    await sx.recapture(sx.listing.model_copy(update={"price": Decimal("19.00")}), hours=2)  # and after a price drop

    after = await sx.stored()
    assert after.identification["vision"] == before.identification["vision"]  # the stored analysis, untouched
    assert after.identification["photos_reused_by_other_seller"] is True
    assert after.brand_id == nike  # the brand the photos established is not erased by a text-only capture
    assert after.size_normalized == before.size_normalized
    assert after.identification_confidence == before.identification_confidence
    assert after.price == Decimal("19.00")  # ...while what the capture does bring is stored


async def test_the_verdict_after_a_recapture_still_reads_the_photos(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The audited case: a defect found on the photos lowers the verdict, and re-opening the page must not undo it."""
    sx = await checked_listing(make_listing, monkeypatch, tmp_path)

    async def analysed() -> Any:
        async with session_scope() as s:
            (outcome,) = await AnalysisPipeline(s).analyze_many([sx.listing_id])
            opp = await s.get(Opportunity, outcome.opportunity_id)
            assert opp is not None and opp.dossier is not None
            return SimpleNamespace(
                risk=outcome.result.risk.score,
                verdict=opp.decision_verdict,
                defects=[d["kind"] for d in (opp.dossier.get("condition") or {}).get("defects", [])],
                vision=(await s.get(Listing, sx.listing_id)).identification.get("vision"),  # type: ignore[union-attr]
                coverage=opp.analysis_coverage_score,
            )

    first = await analysed()
    assert first.vision is not None
    await sx.recapture()
    second = await analysed()
    assert second.vision == first.vision
    assert (second.verdict, second.risk, second.coverage) == (first.verdict, first.risk, first.coverage)
    assert second.defects == first.defects


async def test_a_gallery_that_changed_drops_the_photo_analysis_but_not_the_reuse_signal(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    sx = await checked_listing(make_listing, monkeypatch, tmp_path)
    await mark_reused(sx.listing_id)
    other = sx.listing.model_copy(
        update={
            "images": [*sx.listing.images, ProviderImage(url="/img/new/extra.jpg")],
            "images_authoritative": True,
        }
    )
    await sx.recapture(other)

    after = await sx.stored()
    assert "vision" not in after.identification  # it described other photos: the check runs again
    assert after.identification["photos_reused_by_other_seller"] is True  # an observed signal is never forgotten


async def test_a_card_never_touches_the_photo_analysis(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    sx = await checked_listing(make_listing, monkeypatch, tmp_path)
    before = await sx.stored()
    card = sx.listing.model_copy(update={"capture_level": "card", "images": sx.listing.images[:1]})
    async with session_scope() as s:
        await IngestionService(s, "vinted", AcquisitionMode.EXTENSION_CARD).ingest([card], now=NOW)
    after = await sx.stored()
    assert after.identification == before.identification and after.brand_id == before.brand_id


async def test_a_second_photo_check_of_unchanged_photos_changes_nothing_and_queues_no_analysis(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    sx = await checked_listing(make_listing, monkeypatch, tmp_path)
    async with session_scope() as s:
        # The brand the label gave changes the question asked of the model (its rules): this second run may be paid.
        await service.run_vision(s, sx.listing_id)
    calls_before = len(sx.fake.calls)
    queued: list[tuple[Any, ...]] = []

    async def spy(function: str, *args: Any, **kw: Any) -> bool:
        queued.append((function, *args))
        return True

    monkeypatch.setattr(tasks, "enqueue", spy)
    await tasks.vision_task({"job_try": 1}, str(sx.listing_id))  # the same gallery, answered from the cache
    assert len(sx.fake.calls) == calls_before  # no model call...
    assert queued == []  # ...and so no re-analysis that would compute the same numbers


async def test_the_inputs_of_an_analysis_name_the_photo_evidence_it_read() -> None:
    base = dict(
        price=Decimal("20"),
        currency="EUR",
        status="active",
        capture_level="full",
        title="t",
        description="d",
        images=[SimpleNamespace(image_key="a")],
        shipping_fee=None,
        buyer_protection_fee=None,
        brand_id=1,
        category_id=2,
        model_name=None,
        size_normalized="M",
        condition="good",
        seller_id=None,
        duplicate_of_id=None,
    )
    plain = listing_inputs(SimpleNamespace(**base, identification={"brand": {}}))
    assert "photo_evidence" not in plain  # listings without photo evidence keep their inputs (and hash) as they were
    checked = listing_inputs(
        SimpleNamespace(
            **base,
            identification={"vision": {"analyzer": "claude_vision", "photos_key": "k1"}, "photos_reused_by_other_seller": True},
        )
    )
    assert checked["photo_evidence"] == {"analyzer": "claude_vision", "photos_key": "k1", "reused_photos": True}
    # A lost photo analysis is a change of the inputs, and one that asks for the photos to be checked again.
    from app.opportunities.analysis_record import jsonable

    assert classify_trigger(jsonable(checked), plain) == "photos"
    assert classify_trigger(jsonable(plain), checked) == "data_changed"
