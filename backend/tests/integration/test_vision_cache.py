"""Unchanged photos are never analysed twice (measured), and a listing with 12 photos is read whole (test T)."""

import copy
from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.ai import service
from app.ai.budget import AiBudget
from app.ai.llm import LLMClient
from app.analysis.dossier import build_dossier
from app.db.models import AiUsage, Listing, ListingImage
from app.db.session import session_scope
from app.ingestion.service import IngestionService
from app.vision import analyzer as az
from app.vision import cache as vc
from app.vision.analyzer import ClaudeVisionAnalyzer
from app.vision.cache import CachedImageAnalyzer, VisionCacheStore, cache_key
from app.vision.types import ImageAnalysis, PhotoCheck, PhotoQuality
from tests.conftest import NOW
from tests.integration.test_ai_budget import FakeAnthropic, text_response
from tests.integration.test_ai_budget import settings as ai_settings
from tests.unit.test_dossier import facts
from tests.unit.test_vision_parse import ANSWER, StubLLM


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def heuristic(self: Any, urls: list[str], hashes: list[Any], ctx: dict[str, Any]) -> ImageAnalysis:
        n = len(urls)
        return ImageAnalysis(
            analyzer="heuristic",
            photo_quality=PhotoQuality(photo_count=n, analyzed_count=n),
            photo_hashes=[f"{i:016x}" for i in range(n)],
            photo_checks=[PhotoCheck(photo=i, usable=True, sharpness=0.8) for i in range(n)],
        )

    monkeypatch.setattr(az.HeuristicImageAnalyzer, "analyze", heuristic)
    monkeypatch.setattr(az, "is_public_https_url", lambda url: True)


def urls(n: int, host: str = "images1.vinted.net", query: str = "") -> list[str]:
    return [f"https://{host}/t/photo-{i}/f800.jpeg{query}" for i in range(n)]


CTX = {"category_slugs": ["sweatshirts", "jackets"], "brand_rules": {"key_photos": ["label"]}}


def make(stub: StubLLM, model: str = "model-a") -> CachedImageAnalyzer:
    return CachedImageAnalyzer(ClaudeVisionAnalyzer(stub), model)  # type: ignore[arg-type]


async def test_the_same_photos_cost_one_model_call(clean_db: None) -> None:
    stub = StubLLM(ANSWER)
    a = make(stub)
    first = await a.analyze(urls(5), [None] * 5, CTX)
    second = await a.analyze(urls(5), [None] * 5, CTX)
    again = await a.analyze(urls(5), [None] * 5, CTX)
    assert len(stub.calls) == 1  # the other two came from the cache
    assert first.model_dump() == second.model_dump() == again.model_dump()
    assert (await VisionCacheStore().stats()) == {"entries": 1, "hits": 2}


async def test_the_same_photos_under_other_hosts_and_signed_links_are_the_same_photos(clean_db: None) -> None:
    stub = StubLLM(ANSWER)
    a = make(stub)
    await a.analyze(urls(5), [None] * 5, CTX)
    await a.analyze(urls(5, host="images2.vinted.net", query="?s=abc123"), [None] * 5, CTX)
    assert len(stub.calls) == 1


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
    base = urls(5)
    await make(stub).analyze(base, [None] * 5, CTX)
    ctx = copy.deepcopy(CTX)
    model, photos = "model-a", list(base)
    if change == "one more photo":
        photos = urls(6)
    elif change == "other order":
        photos = list(reversed(base))
    elif change == "other photo":
        photos[2] = "https://images1.vinted.net/t/another/f800.jpeg"
    elif change == "other model":
        model = "model-b"
    elif change == "other prompt version":
        monkeypatch.setattr(vc, "VISION_PROMPT_VERSION", "vision-next")
    elif change == "other brand rules":
        ctx["brand_rules"] = {"key_photos": ["label", "zip"]}
    elif change == "other categories":
        ctx["category_slugs"] = ["sweatshirts"]
    await make(stub, model).analyze(photos, [None] * len(photos), ctx)
    assert len(stub.calls) == 2, change


async def test_the_title_is_not_part_of_the_key_because_the_model_is_not_told_it() -> None:
    assert cache_key(urls(3), "m", {**CTX, "title": "a"}) == cache_key(
        urls(3), "m", {**CTX, "title": "b", "brand": "x"}
    )
    llm = StubLLM(ANSWER)
    await ClaudeVisionAnalyzer(llm).analyze(
        urls(3), [None] * 3, {"title": "TITOLO SEGRETO", "brand": "MARCA SEGRETA"}
    )  # type: ignore[arg-type]
    prompt = " ".join(c["text"] for c in llm.calls[0]["content"] if c["type"] == "text")
    assert "TITOLO SEGRETO" not in prompt and "MARCA SEGRETA" not in prompt


async def test_a_broken_cache_costs_a_call_not_the_analysis(clean_db: None) -> None:
    class Broken(VisionCacheStore):
        async def get(self, key: str) -> Any:
            raise RuntimeError("down")

        async def put(self, *a: Any) -> None:
            raise RuntimeError("down")

    stub = StubLLM(ANSWER)
    out = await CachedImageAnalyzer(ClaudeVisionAnalyzer(stub), "m", Broken()).analyze(
        urls(3), [None] * 3, CTX
    )  # type: ignore[arg-type]
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
    clean_db: None, make_listing: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = ai_settings()
    fake = FakeAnthropic(text_response(ANSWER, tokens=(6000, 1500)))
    llm = LLMClient(cfg, budget=AiBudget(cfg))
    llm._client = fake  # type: ignore[assignment]
    monkeypatch.setattr(service, "get_llm", lambda: llm)

    async with session_scope() as s:
        res = await IngestionService(s, "test").ingest(
            [make_listing(photos=5, published_days_ago=0.05)], now=NOW
        )
        listing_id = res.new_ids[0]
    async with session_scope() as s:  # the test listing carries relative links: make them photo URLs
        await s.execute(text("UPDATE listing_images SET url = 'https://images1.vinted.net' || url"))
        n_images = (
            await s.execute(
                select(func.count()).select_from(ListingImage).where(ListingImage.listing_id == listing_id)
            )
        ).scalar_one()
    assert n_images == 5

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
    photos = urls(12)
    out = await ClaudeVisionAnalyzer(stub).analyze(photos, [None] * 12, CTX)  # type: ignore[arg-type]
    sent = [c for c in stub.calls[0]["content"] if c["type"] == "image"]
    assert [c["source"]["url"] for c in sent] == photos  # none ignored, none reordered
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
