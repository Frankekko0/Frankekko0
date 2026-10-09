"""AI budget: every paid call is counted, and a daily and a monthly cap stop the spending.

The caps are in USD (UTC day, calendar month) and live in settings. Before a call the budget is
checked (``check``): when the spent amount plus a reserve for one more call would cross a cap, the
call is not made and the caller falls back to its rules. After a call its cost is recorded
(``record``) from the tokens the provider reports and the configured prices. The prices are
assumptions the operator sets (see ``DEPENDENCIES.md``): the provider's price list is not read.

Fail closed: if the budget cannot be read (database down), no call is made.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.events import log_event
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import AiUsage, Event
from app.db.session import session_scope

log = get_logger(__name__)
MICRO = Decimal("0.000001")


@dataclass(frozen=True)
class BudgetCheck:
    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class BudgetStatus:
    day_spent: Decimal
    month_spent: Decimal
    day_cap: Decimal
    month_cap: Decimal
    calls_today: int
    by_purpose: dict[str, Decimal] = field(default_factory=dict)

    @property
    def day_left(self) -> Decimal:
        return max(Decimal("0"), self.day_cap - self.day_spent)

    @property
    def month_left(self) -> Decimal:
        return max(Decimal("0"), self.month_cap - self.month_spent)

    @property
    def exhausted(self) -> bool:
        return self.day_left <= 0 or self.month_left <= 0

    def as_dict(self) -> dict[str, object]:
        return {
            "day_spent": str(self.day_spent),
            "day_cap": str(self.day_cap),
            "day_left": str(self.day_left),
            "month_spent": str(self.month_spent),
            "month_cap": str(self.month_cap),
            "month_left": str(self.month_left),
            "calls_today": self.calls_today,
            "exhausted": self.exhausted,
            "by_purpose": {k: str(v) for k, v in self.by_purpose.items()},
            "currency": "USD",
            "prices_are_assumptions": True,
        }


Scope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class AiBudget:
    def __init__(self, settings: Settings | None = None, scope: Scope = session_scope) -> None:
        self.settings = settings or get_settings()
        self._scope = scope

    # ---- prices -------------------------------------------------------------------------
    def cost(self, tier: str, input_tokens: int, output_tokens: int) -> Decimal:
        s = self.settings
        if tier == "cheap":
            price_in, price_out = s.ai_price_cheap_input_per_mtok, s.ai_price_cheap_output_per_mtok
        else:
            price_in, price_out = s.ai_price_strong_input_per_mtok, s.ai_price_strong_output_per_mtok
        total = (Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out) / Decimal(1_000_000)
        return total.quantize(MICRO)

    # ---- reading ------------------------------------------------------------------------
    async def status(self, now: datetime | None = None) -> BudgetStatus:
        today = (now or datetime.now(UTC)).date()
        month_start = today.replace(day=1)
        async with self._scope() as s:
            day_spent, month_spent, calls = (
                await s.execute(
                    select(
                        func.coalesce(func.sum(AiUsage.cost_usd).filter(AiUsage.day == today), 0),
                        func.coalesce(func.sum(AiUsage.cost_usd).filter(AiUsage.day >= month_start), 0),
                        func.count().filter(AiUsage.day == today),
                    ).where(AiUsage.day >= month_start)
                )
            ).one()
            rows = (
                await s.execute(
                    select(AiUsage.purpose, func.sum(AiUsage.cost_usd))
                    .where(AiUsage.day == today)
                    .group_by(AiUsage.purpose)
                )
            ).all()
        return BudgetStatus(
            Decimal(day_spent),
            Decimal(month_spent),
            self.settings.ai_daily_budget_usd,
            self.settings.ai_monthly_budget_usd,
            int(calls),
            {p: Decimal(c) for p, c in rows},
        )

    async def check(self, purpose: str, now: datetime | None = None) -> BudgetCheck:
        """May one more call be made? Never raises: an unreadable budget is a refusal."""
        try:
            st = await self.status(now)
        except Exception as exc:
            log.warning("ai.budget_unreadable", error=type(exc).__name__)
            return BudgetCheck(False, "budget illeggibile: nessuna chiamata a pagamento")
        reserve = self.settings.ai_call_reserve_usd
        if st.day_cap - st.day_spent < reserve or st.day_cap <= 0:
            return await self._stop("giornaliero", purpose, st, now)
        if st.month_cap - st.month_spent < reserve or st.month_cap <= 0:
            return await self._stop("mensile", purpose, st, now)
        return BudgetCheck(True)

    async def _stop(self, which: str, purpose: str, st: BudgetStatus, now: datetime | None) -> BudgetCheck:
        reason = f"tetto {which} di spesa AI raggiunto"
        log.warning("ai.budget_stop", cap=which, purpose=purpose)
        try:  # recorded once per day and cap, not once per refused call
            today = (now or datetime.now(UTC)).date()
            async with self._scope() as s:
                seen = (
                    await s.execute(
                        select(func.count())
                        .select_from(Event)
                        .where(
                            Event.kind == "ai.budget_stop",
                            func.date(Event.at) == today,
                            Event.payload["cap"].astext == which,
                        )
                    )
                ).scalar_one()
                if not seen:
                    await log_event(
                        s,
                        "ai.budget_stop",
                        subject_type="budget",
                        subject_id=which,
                        payload={"cap": which, "purpose": purpose, **st.as_dict()},
                        at=now,
                    )
        except Exception as exc:
            log.warning("ai.budget_event_failed", error=type(exc).__name__)
        return BudgetCheck(False, reason)

    # ---- writing ------------------------------------------------------------------------
    async def record(
        self,
        *,
        purpose: str,
        model: str,
        tier: str,
        input_tokens: int,
        output_tokens: int,
        ref: str | None = None,
        now: datetime | None = None,
    ) -> Decimal:
        cost = self.cost(tier, input_tokens, output_tokens)
        day: date = (now or datetime.now(UTC)).date()
        async with self._scope() as s:
            s.add(
                AiUsage(
                    day=day,
                    purpose=purpose[:48],
                    model=model[:64],
                    tier=tier,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                    ref=ref[:64] if ref else None,
                )
            )
        return cost


_budget: AiBudget | None = None


def get_budget() -> AiBudget:
    global _budget
    if _budget is None:
        _budget = AiBudget()
    return _budget
