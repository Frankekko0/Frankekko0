"""Who the strong model reviews, and in which order (TT-06 / F2 of the ingest wave).

The review can only LOWER a verdict. Before, every row at the flip threshold waited for it whatever its verdict: on the
free tier most of the 90 daily reviews went to PASS rows that cannot change, a BUY at 78 was never reviewed, and a fresh
find waited behind the whole older backlog. Now: the verdict decides (a buy at any flip, a watchlist at the threshold
with a profit above the floor), what the user's browser showed in the last minutes goes first, and a capture of the
user's browser is kicked at once even when older rows with a higher flip score wait.
"""

# ruff: noqa: F811  (``env`` is a fixture imported from test_ai_queue and used as an argument)
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update

from app.ai import queue as queue_mod
from app.ai import service
from app.ai.queue import claim_pending
from app.db.models import Listing, Opportunity
from app.db.session import session_scope
from app.opportunities.pipeline import AnalysisPipeline
from tests.conftest import NOW
from tests.integration.test_agent import seed
from tests.integration.test_ai_budget import text_response
from tests.integration.test_ai_queue import (  # noqa: F401  (env is a fixture)
    Env,
    ai_settings,
    answer,
    env,
    row,
)

VERDICTS = {
    "strong": ("STRONG_BUY", 60, 25),
    "buy": ("BUY", 50, 18),
    "negotiate": ("NEGOTIATE", 40, 12),
    "watch": ("WATCHLIST", 85, 20),
    "dear": ("PASS", 95, 30),
    "inject": ("INSUFFICIENT_EVIDENCE", 90, 30),
}


async def set_rows(ids: dict[str, str], rows: dict[str, tuple[str, int, float]]) -> None:
    async with session_scope() as s:
        for name, (verdict, flip, rap) in rows.items():
            await s.execute(
                update(Opportunity)
                .where(Opportunity.id == uuid.UUID(ids[name]))
                .values(decision_verdict=verdict, flip_score=flip, risk_adjusted_profit=rap)
            )


async def waiting(cfg: Any) -> set[str]:
    async with session_scope() as s:
        found = (await s.execute(select(Opportunity.id).where(queue_mod.pending_clause(cfg)))).scalars().all()
    return {str(i) for i in found}


def test_the_gate_reads_the_verdict(env: Env) -> None:
    env.configure(ai_auto_analyze_min_flip_score=80, ai_review_scope="actionable")
    q = queue_mod.qualifies_for_strong_ai
    cfg = env.cfg
    for verdict in ("STRONG_BUY", "BUY", "NEGOTIATE"):  # a buy is reviewed at any flip score
        assert q(cfg, flip_score=45, data_quality="ok", decision_verdict=verdict)
    # a watchlist only at the threshold and with a profit above the floor (DEFAULT_MIN_PROFIT)
    assert q(cfg, flip_score=82, data_quality="ok", decision_verdict="WATCHLIST", risk_adjusted_profit=20)
    assert not q(cfg, flip_score=79, data_quality="ok", decision_verdict="WATCHLIST", risk_adjusted_profit=20)
    assert not q(cfg, flip_score=90, data_quality="ok", decision_verdict="WATCHLIST", risk_adjusted_profit=2)
    assert not q(cfg, flip_score=90, data_quality="ok", decision_verdict="WATCHLIST")
    # a review cannot change these: never queued, whatever their flip score
    for verdict in ("PASS", "INSUFFICIENT_EVIDENCE"):
        assert not q(cfg, flip_score=99, data_quality="ok", decision_verdict=verdict, risk_adjusted_profit=50)
    # a row without a verdict (written before decisions existed) keeps the flip rule; so does the scope "all"
    assert q(cfg, flip_score=80, data_quality="ok") and not q(cfg, flip_score=79, data_quality="ok")
    env.configure(ai_auto_analyze_min_flip_score=80, ai_review_scope="all")
    assert q(env.cfg, flip_score=90, data_quality="ok", decision_verdict="PASS")
    # "never" (101) and no key and no usable estimate still close the gate in every scope
    off = ai_settings(ai_auto_analyze_min_flip_score=101, ai_review_scope="actionable")
    assert not q(off, flip_score=100, data_quality="ok", decision_verdict="STRONG_BUY")
    assert not q(cfg, flip_score=99, data_quality="insufficient", decision_verdict="STRONG_BUY")


async def test_pass_rows_never_wait_and_a_buy_below_the_threshold_does(
    clean_db: None,
    make_listing: Any,
    env: Env,
) -> None:
    ids = await seed(make_listing)
    await set_rows(ids, VERDICTS)

    env.configure(ai_auto_analyze_min_flip_score=80, ai_review_scope="actionable")
    assert await waiting(env.cfg) == {ids[n] for n in ("strong", "buy", "negotiate", "watch")}

    env.configure(ai_auto_analyze_min_flip_score=80, ai_review_scope="all")  # the old behaviour, by switch
    assert await waiting(env.cfg) == {ids[n] for n in ("watch", "dear", "inject")}

    # the sweep claims the same rows the gate lets in
    env.configure(ai_auto_analyze_min_flip_score=80, ai_review_scope="actionable")
    claimed = {str(i) for i in await claim_pending(env.cfg, 50)}
    assert claimed == {ids[n] for n in ("strong", "buy", "negotiate", "watch")}


async def test_the_review_goes_to_what_the_browser_showed_last_then_to_the_better_verdict(
    clean_db: None,
    make_listing: Any,
    env: Env,
) -> None:
    ids = await seed(make_listing)
    await set_rows(
        ids,
        {
            "strong": ("STRONG_BUY", 99, 40),  # the best row of the backlog ...
            "buy": ("BUY", 70, 20),
            "negotiate": ("NEGOTIATE", 85, 30),
            "watch": ("WATCHLIST", 90, 25),
            "dear": ("PASS", 99, 50),
            "inject": ("PASS", 99, 50),
        },
    )
    env.configure(ai_auto_analyze_min_flip_score=60, ai_review_scope="actionable")
    async with session_scope() as s:
        # ... but the user's browser showed the BUY a minute ago and everything else yesterday
        await s.execute(update(Listing).values(last_seen_at=NOW - timedelta(days=1)))
        await s.execute(
            update(Listing)
            .where(
                Listing.id
                == (
                    await s.execute(
                        select(Opportunity.listing_id).where(Opportunity.id == uuid.UUID(ids["buy"]))
                    )
                ).scalar_one()
            )
            .values(last_seen_at=NOW - timedelta(minutes=1))
        )
    order = [str(i) for i in await claim_pending(env.cfg, 10, now=NOW)]
    assert order[0] == ids["buy"], "what the user is looking at goes first"
    assert order[1:] == [ids["strong"], ids["negotiate"], ids["watch"]], "then the verdict, then the profit"


async def test_a_capture_of_the_users_browser_is_kicked_ahead_of_an_older_backlog(
    clean_db: None,
    make_listing: Any,
    env: Env,
    monkeypatch: Any,
) -> None:
    ids = await seed(make_listing)
    env.configure(ai_rpm_strong=0, ai_rpd_strong=0, ai_sweep_batch=5, ai_review_scope="actionable")
    minute = [4000]
    monkeypatch.setattr(queue_mod, "_pace_minute", lambda: minute[0])
    await set_rows(ids, {"strong": ("STRONG_BUY", 99, 40), "buy": ("BUY", 70, 20)})  # a backlog row at 99
    fresh = [(70, uuid.UUID(ids["buy"]))]  # the item just captured: a lower flip than the waiting backlog

    assert await queue_mod.kick(fresh, env.cfg) == 0  # a worker batch keeps the order of the sweep
    assert await queue_mod.kick(fresh, env.cfg, interactive=True) == 1  # the user is looking at this one
    assert [(f, a[0]) for f, a, _ in env.queued] == [("ai_analyze_task", ids["buy"])]


# ------------------------------------------------------------------ TT-02: a price move alone keeps the review
async def reprice_and_reanalyse(env: Env, listing_id: uuid.UUID, drop: Decimal, hours: float = 1.0) -> Any:
    async with session_scope() as s:
        await s.execute(update(Listing).where(Listing.id == listing_id).values(price=Listing.price - drop))
    async with session_scope() as s:
        out = await AnalysisPipeline(s, env.cfg).analyze_many([listing_id], now=NOW + timedelta(hours=hours))
        return out[0]


async def test_a_price_drop_alone_does_not_throw_away_the_models_caution(
    clean_db: None, make_listing: Any, env: Env
) -> None:
    ids = await seed(make_listing)
    env.configure(text_response(answer("CONSIDER")))  # the model lowers a STRONG BUY to a watchlist
    await service.review_opportunity(uuid.UUID(ids["strong"]), env.cfg)
    reviewed = await row(ids["strong"])
    assert (reviewed.ai_provider, reviewed.decision_verdict) == ("claude", "WATCHLIST")
    assert reviewed.decision["reviewed_from"] == "STRONG_BUY"

    outcome = await reprice_and_reanalyse(env, reviewed.listing_id, Decimal("1"))  # 12 -> 11 EUR
    after = await row(ids["strong"])
    assert outcome.analysis_created is True and outcome.trigger == "price_change"
    assert outcome.ai_pending is False, "a euro off the price must not cost a second model call"
    assert after.ai_provider == "claude" and after.ai_for_analysis_id == after.analysis_id
    assert after.decision_verdict == "WATCHLIST", "the verdict went back to the rules' BUY for a price move"
    assert after.decision["reviewed_from"] == "STRONG_BUY" and after.ai_analyzed_at == reviewed.ai_analyzed_at
    assert after.listing_price == reviewed.listing_price - 1


async def test_a_price_move_that_moves_the_score_enough_is_reviewed_again(
    clean_db: None, make_listing: Any, env: Env
) -> None:
    ids = await seed(make_listing)
    env.configure(text_response(answer("CONSIDER")), ai_reanalyze_flip_delta=1)
    await service.review_opportunity(uuid.UUID(ids["strong"]), env.cfg)
    reviewed = await row(ids["strong"])
    outcome = await reprice_and_reanalyse(env, reviewed.listing_id, Decimal("3"))
    assert outcome.ai_pending is True and outcome.result.flip.score != reviewed.flip_score
    assert (await row(ids["strong"])).ai_provider == "rules"  # the old review is no longer the current one
