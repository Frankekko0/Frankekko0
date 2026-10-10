"""Unchanged photos are never analysed twice (measured), and a listing with 12 photos is read whole (test T)."""

import base64
import copy
from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.ai import service
from app.ai.budget import AiBudget
from app.ai.llm import LLMClient
from app.analysis.dossier import build_dossier
from app.core.config import get_settings
from app.db.models import AiUsage, Listing, ListingImage
from app.db.session import session_scope
from app.domain.enums import AcquisitionMode
from app.ingestion.service import IngestionService
from app.media import archive
from app.vision import analyzer as az
from app.vision import cache as vc
from app.vision.analyzer import ClaudeVisionAnalyzer, PhotoInput, VisionDeferred
from app.vision.cache import CachedImageAnalyzer, VisionCacheStore, cache_key
from app.vision.types import ImageAnalysis, PhotoCheck, PhotoQuality
from tests.conftest import NOW
from tests.integration.test_ai_budget import FakeAnthropic, text_response
from tests.integration.test_ai_budget import settings as ai_settings
from tests.photos import jpeg, photo, photos
from tests.unit.test_dossier import facts
from tests.unit.test_vision_parse import ANSWER, StubLLM


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def heuristic(self: Any, photos: list[PhotoInput], ctx: dict[str, Any]) -> ImageAnalysis:
        n = len(photos)
        return ImageAnalysis(
            analyzer="heuristic",
            photo_quality=PhotoQuality(photo_count=n, analyzed_count=n),
            photo_hashes=[f"{i:016x}" for i in range(n)],
            photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.8) for i in range(n)],
        )

    monkeypatch.setattr(az.HeuristicImageAnalyzer, "analyze", heuristic)


CTX = {"category_slugs": ["sweatshirts", "jackets"], "brand_rules": {"key_photos": ["label"]}}


def make(stub: StubLLM, model: str = "model-a") -> CachedImageAnalyzer:
    return CachedImageAnalyzer(ClaudeVisionAnalyzer(stub), model)  # type: ignore[arg-type]


async def test_the_same_photos_cost_one_model_call(clean_db: None) -> None:
    stub = StubLLM(ANSWER)
    a = make(stub)
    first = await a.analyze(photos(5), CTX)
    second = await a.analyze(photos(5), CTX)
    again = await a.analyze(photos(5), CTX)
    assert len(stub.calls) == 1  # the other two came from the cache
    assert first.model_dump() == second.model_dump() == again.model_dump()
    assert (await VisionCacheStore().stats()) == {"entries": 1, "hits": 2}


async def test_a_failed_model_call_is_not_remembered_and_the_next_run_asks_the_model_again(
    clean_db: None,
) -> None:
    stub = StubLLM(None)  # the provider refuses, is out of quota, answers nonsense: no answer
    a = make(stub)
    with pytest.raises(VisionDeferred) as e:
        await a.analyze(photos(5), CTX)
    assert e.value.partial.analyzer == "heuristic"  # the local measures exist but are not the analysis
    assert (await VisionCacheStore().stats()) == {"entries": 0, "hits": 0}  # nothing was cached
    stub.answer = ANSWER
    out = await a.analyze(photos(5), CTX)
    assert out.analyzer == "claude_vision" and len(stub.calls) == 2  # asked again, not served the failure
    assert (await VisionCacheStore().stats()) == {"entries": 1, "hits": 0}


async def test_the_local_measures_are_never_stored_where_a_model_answer_belongs(clean_db: None) -> None:
    class Sloppy(az.ImageAnalyzer):
        name = "claude_vision"

        async def analyze(self, photos: list[PhotoInput], ctx: dict[str, Any]) -> ImageAnalysis:
            return await az.HeuristicImageAnalyzer().analyze(photos, ctx)

    out = await CachedImageAnalyzer(Sloppy(), "model-a").analyze(photos(3), CTX)
    assert out.analyzer == "heuristic" and (await VisionCacheStore().stats())["entries"] == 0
    # The heuristic analyzer's own result is what that cache is for.
    await CachedImageAnalyzer(az.HeuristicImageAnalyzer(), "heuristic").analyze(photos(3), CTX)
    assert (await VisionCacheStore().stats())["entries"] == 1


async def test_a_photo_is_its_content_not_the_address_it_was_uploaded_from(clean_db: None) -> None:
    stub = StubLLM(ANSWER)
    a = make(stub)
    await a.analyze(photos(5), CTX)
    # The same bytes under other gallery keys (another host, a signed link): the same photos.
    renamed = [photo(i, seed=i, key=f"other-key-{i}") for i in range(5)]
    await a.analyze(renamed, CTX)
    assert len(stub.calls) == 1
    # A photo the browser has not uploaded yet is not part of what the model sees.
    assert cache_key([*photos(5), PhotoInput(5, "k5")], "m", CTX) == cache_key(photos(5), "m", CTX)


@pytest.mark.parametrize(
    "change",
    [
        "one more photo",
        "other order",
        "other photo",
        "other model",
        "other prompt version",
        "other brand rules",
        "other categories",
    ],
)
async def test_anything_that_changes_what_the_model_would_see_is_a_new_analysis(
    clean_db: None, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    stub = StubLLM(ANSWER)
    base = photos(5)
    await make(stub).analyze(base, CTX)
    ctx = copy.deepcopy(CTX)
    model, shown = "model-a", list(base)
    if change == "one more photo":
        shown = photos(6)
    elif change == "other order":
        shown = [photo(i, p.data) for i, p in enumerate(reversed(base))]
    elif change == "other photo":
        shown[2] = photo(2, jpeg(999))
    elif change == "other model":
        model = "model-b"
    elif change == "other prompt version":
        monkeypatch.setattr(vc, "VISION_PROMPT_VERSION", "vision-next")
    elif change == "other brand rules":
        ctx["brand_rules"] = {"key_photos": ["label", "zip"]}
    elif change == "other categories":
        ctx["category_slugs"] = ["sweatshirts"]
    await make(stub, model).analyze(shown, ctx)
    assert len(stub.calls) == 2, change


async def test_the_title_is_not_part_of_the_key_because_the_model_is_not_told_it() -> None:
    assert cache_key(photos(3), "m", {**CTX, "title": "a"}) == cache_key(
        photos(3), "m", {**CTX, "title": "b", "brand": "x"}
    )
    llm = StubLLM(ANSWER)
    await ClaudeVisionAnalyzer(llm).analyze(  # type: ignore[arg-type]
        photos(3), {"title": "TITOLO SEGRETO", "brand": "MARCA SEGRETA"}
    )
    prompt = " ".join(c["text"] for c in llm.calls[0]["content"] if c["type"] == "text")
    assert "TITOLO SEGRETO" not in prompt and "MARCA SEGRETA" not in prompt


async def test_a_broken_cache_costs_a_call_not_the_analysis(clean_db: None) -> None:
    class Broken(VisionCacheStore):
        async def get(self, key: str) -> Any:
            raise RuntimeError("down")

        async def put(self, *a: Any) -> None:
            raise RuntimeError("down")

    stub = StubLLM(ANSWER)
    out = await CachedImageAnalyzer(ClaudeVisionAnalyzer(stub), "m", Broken()).analyze(  # type: ignore[arg-type]
        photos(3), CTX
    )
    assert out.analyzer == "claude_vision" and len(stub.calls) == 1


async def test_old_unused_entries_are_pruned(clean_db: None) -> None:
    store = VisionCacheStore()
    await store.put("old", "m", 3, {"analyzer": "x"})
    await store.put("fresh", "m", 3, {"analyzer": "x"})
    async with session_scope() as s:
        await s.execute(
            text("UPDATE vision_cache SET created_at = now() - interval '100 days' WHERE key = 'old'")
        )
    assert await store.prune() == 1 and (await store.stats())["entries"] == 1


# ------------------------------------------------------------------ end to end through run_vision (measured)
async def test_two_analyses_of_unchanged_photos_make_one_paid_call(
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    media = get_settings().model_copy(update={"media_dir": str(tmp_path)})
    monkeypatch.setattr(archive, "get_settings", lambda: media)
    cfg = ai_settings()
    fake = FakeAnthropic(text_response(ANSWER, tokens=(6000, 1500)))
    llm = LLMClient(cfg, budget=AiBudget(cfg))
    llm._client = fake  # type: ignore[assignment]
    monkeypatch.setattr(service, "get_llm", lambda: llm)

    async with session_scope() as s:
        res = await IngestionService(s, "vinted", AcquisitionMode.EXTENSION_ITEM).ingest(
            [make_listing(photos=5, published_days_ago=0.05)], now=NOW
        )
        listing_id = res.new_ids[0]
    async with session_scope() as s:  # the browser uploads the five photos
        listing = await s.get(Listing, listing_id)
        assert listing is not None
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
        assert len(keys) == 5
        for i, key in enumerate(keys):
            up = await archive.register_upload(s, listing.external_id, key, jpeg(i), "image/jpeg", media)
            assert up.stored, up.error

    async with session_scope() as s:
        await service.run_vision(s, listing_id)
    async with session_scope() as s:
        await service.run_vision(s, listing_id)  # a later re-check of the same photos
    async with session_scope() as s:
        await service.run_vision(s, listing_id)

    assert len(fake.calls) == 1
    async with session_scope() as s:
        paid = (
            await s.execute(select(func.count()).select_from(AiUsage).where(AiUsage.purpose == "vision"))
        ).scalar_one()
        listing = await s.get(Listing, listing_id)
    assert paid == 1
    assert (await VisionCacheStore().stats())["hits"] == 2
    assert listing is not None and listing.identification["vision"]["analyzer"] == "claude_vision"
    assert listing.identification["vision"]["labels"][0]["type"] == "brand_label"


# ------------------------------------------------------------------ test T: 12 photos, every one read
ROLES = [
    "front",
    "back",
    "label",
    "care_label",
    "detail",
    "detail",
    "worn",
    "worn",
    "flat_lay",
    "detail",
    "inside",
    "other",
]


async def test_case_t_twelve_photos_are_all_sent_all_get_a_role_and_the_gaps_are_listed() -> None:
    answer = copy.deepcopy(ANSWER)
    answer["photo_roles"] = [{"photo": i + 1, "role": r} for i, r in enumerate(ROLES)]
    answer["defects"] = []
    stub = StubLLM(answer)
    gallery = photos(12)
    out = await ClaudeVisionAnalyzer(stub).analyze(gallery, CTX)  # type: ignore[arg-type]
    sent = [c for c in stub.calls[0]["content"] if c["type"] == "image"]
    # Every photo goes to the model as the bytes the browser uploaded, none ignored, none reordered.
    assert [base64.b64decode(c["source"]["data"]) for c in sent] == [p.data for p in gallery]
    assert all(c["source"]["type"] == "base64" for c in sent)
    assert [r.photo for r in out.photo_roles] == list(range(12))
    assert len(out.photo_checks) == 12  # the technical check covers them all too

    vision = out.model_dump(mode="json")
    d = build_dossier(facts(vision=vision, photo_count=12))
    assert d["inspection_coverage"] == 100 and d["photo_quality"] is not None
    assert d["analysis_coverage"] > 0 and isinstance(d["not_analysable"], list)
    for gap in d["not_analysable"]:
        assert gap["reason"], gap  # every pass that could not run says why
    assert "P2" in {p["code"] for p in d["passes"] if p["status"] == "done"}


async def test_case_t_the_passes_that_cannot_run_are_listed_with_the_reason() -> None:
    d = build_dossier(
        facts(
            description="", vision=None, seller={"known": False, "score": 50, "level": "new"}, photo_count=12
        )
    )
    gaps = {g["code"]: g for g in d["not_analysable"]}
    assert {"P1", "P4", "P5", "P9"} <= set(gaps)
    assert all(gaps[c]["reason"] and gaps[c]["needs"] for c in ("P1", "P4", "P5", "P9"))
    assert d["coverage_note"].startswith("quota dei passaggi applicabili")
