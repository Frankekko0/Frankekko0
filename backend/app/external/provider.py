"""Search provider (official API, never scraping) and the query budget.

``SerperProvider`` calls Serper.dev (Google results as JSON): ``POST https://google.serper.dev/search``
and ``/shopping`` with the key in the ``X-API-KEY`` header, country/language from the settings,
10 results per query (= 1 credit), a 10 s timeout and one retry on 5xx or network errors. The key
is never logged nor put in an error message.

``QueryBudget`` counts queries per day and per month in ``system_state`` (key
``external_search_budget``) and is charged BEFORE every call: a query that would exceed
``EXTERNAL_SEARCH_DAILY_MAX`` or ``EXTERNAL_SEARCH_MONTHLY_BUDGET`` is never sent. Days and months
are UTC.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.market.state import get_state, set_state

log = get_logger(__name__)

BUDGET_KEY = "external_search_budget"
SERPER_URL = "https://google.serper.dev"
RESULTS_PER_QUERY = 10  # Serper: up to 10 results = 1 credit
TIMEOUT_SECONDS = 10.0
COST_PER_QUERY_USD = 0.001  # $50 for 50,000 credits (Starter pack)
FREE_QUERIES = 2500  # free credits of a new account, no card


class ProviderError(Exception):
    """A failed search. ``fatal`` errors (key refused, credits exhausted, rate limited) stop the
    whole run; the others only the model being searched."""

    def __init__(self, message: str, *, status: int | None = None, fatal: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.fatal = fatal


@dataclass
class SearchResponse:
    endpoint: str  # shopping | search
    query: str
    payload: dict[str, Any]
    credits: int = 1


class SearchProvider(Protocol):
    name: str

    async def search(self, endpoint: str, query: str) -> SearchResponse: ...


class SerperProvider:
    """Serper.dev client (``endpoint``: ``"search"`` or ``"shopping"``)."""

    name = "serper"

    def __init__(
        self,
        api_key: str,
        *,
        country: str = "it",
        language: str = "it",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
        retry_delay: float = 1.0,
    ) -> None:
        self._key = api_key
        self.country = country
        self.language = language
        self.transport = transport
        self.timeout = timeout
        self.retry_delay = retry_delay

    def __repr__(self) -> str:  # never shows the key
        return f"SerperProvider(country={self.country!r}, language={self.language!r})"

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> SerperProvider | None:
        if settings.external_search_provider != "serper" or not settings.external_search_enabled:
            return None
        assert settings.serper_api_key is not None
        return cls(
            settings.serper_api_key.get_secret_value().strip(),
            country=settings.external_search_country,
            language=settings.external_search_language,
            transport=transport,
        )

    async def search(self, endpoint: str, query: str) -> SearchResponse:
        if endpoint not in ("search", "shopping"):
            raise ValueError(f"unknown endpoint {endpoint!r}")
        body = {"q": query, "gl": self.country, "hl": self.language, "num": RESULTS_PER_QUERY}
        headers = {"X-API-KEY": self._key, "Content-Type": "application/json"}
        last: ProviderError | None = None
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            for attempt in range(2):
                if attempt and self.retry_delay:
                    await asyncio.sleep(self.retry_delay)
                try:
                    resp = await client.post(f"{SERPER_URL}/{endpoint}", json=body, headers=headers)
                except httpx.HTTPError as exc:
                    last = ProviderError(f"Errore di rete verso Serper ({type(exc).__name__})")
                    continue
                if resp.status_code >= 500:
                    last = ProviderError(
                        f"Serper non disponibile (HTTP {resp.status_code})", status=resp.status_code
                    )
                    continue
                if resp.status_code >= 400:
                    raise _client_error(resp)
                try:
                    payload = resp.json()
                except ValueError:
                    raise ProviderError("Risposta di Serper non leggibile (JSON non valido)") from None
                if not isinstance(payload, dict):
                    raise ProviderError("Risposta di Serper inattesa")
                credits = payload.get("credits")
                return SearchResponse(
                    endpoint, query, payload, credits if isinstance(credits, int) and credits > 0 else 1
                )
        assert last is not None
        raise last


def _client_error(resp: httpx.Response) -> ProviderError:
    """4xx: the key, the credits or the rate limit stop the run; anything else only this query."""
    try:
        detail = str((resp.json() or {}).get("message") or "")[:120]
    except ValueError:
        detail = ""
    code = resp.status_code
    if code in (401, 403):
        return ProviderError(
            f"Chiave Serper rifiutata (HTTP {code}): controlla SERPER_API_KEY", status=code, fatal=True
        )
    if code == 402 or "credit" in detail.lower():
        return ProviderError(f"Crediti Serper esauriti (HTTP {code})", status=code, fatal=True)
    if code == 429:
        return ProviderError("Limite di richieste Serper raggiunto (HTTP 429)", status=code, fatal=True)
    return ProviderError(
        f"Richiesta rifiutata da Serper (HTTP {code}{': ' + detail if detail else ''})", status=code
    )


# ---------------------------------------------------------------------------- budget
@dataclass
class QueryBudget:
    """Queries used today and this month (UTC), checked and charged before every call."""

    daily_max: int
    monthly_budget: int
    now: datetime
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def day(self) -> str:
        return self.now.astimezone(UTC).date().isoformat()

    @property
    def month(self) -> str:
        return self.day[:7]

    @property
    def today_used(self) -> int:
        return int(self.state.get("today_used", 0)) if self.state.get("day") == self.day else 0

    @property
    def month_used(self) -> int:
        return int(self.state.get("month_used", 0)) if self.state.get("month") == self.month else 0

    def remaining(self) -> int:
        return max(0, min(self.daily_max - self.today_used, self.monthly_budget - self.month_used))

    def allows(self, n: int = 1) -> bool:
        return self.remaining() >= n

    def charge(self, n: int = 1) -> None:
        """Count ``n`` queries (credits), whether the call then succeeds or not."""
        self.state.update(
            day=self.day,
            month=self.month,
            today_used=self.today_used + n,
            month_used=self.month_used + n,
            total_used=int(self.state.get("total_used", 0)) + n,
        )

    def reserve(self, n: int = 1) -> bool:
        """Charge ``n`` queries if the budget allows them; ``False`` (nothing charged) otherwise."""
        if not self.allows(n):
            return False
        self.charge(n)
        return True

    @classmethod
    async def load(
        cls, session: AsyncSession, settings: Settings, now: datetime | None = None
    ) -> QueryBudget:
        return cls(
            daily_max=settings.external_search_daily_max,
            monthly_budget=settings.external_search_monthly_budget,
            now=now or datetime.now(UTC),
            state=await get_state(session, BUDGET_KEY) or {},
        )

    async def save(self, session: AsyncSession, **extra: Any) -> None:
        self.state.update(extra)
        await set_state(session, BUDGET_KEY, self.state)


async def charged_search(
    provider: SearchProvider, budget: QueryBudget, endpoint: str, query: str
) -> SearchResponse:
    """One query, already reserved in ``budget``; credits beyond the one reserved (a provider
    charging more for a query) are added afterwards."""
    response = await provider.search(endpoint, query)
    if response.credits > 1:
        budget.charge(response.credits - 1)
    log.info("external.search", provider=provider.name, endpoint=endpoint, credits=response.credits)
    return response
