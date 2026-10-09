from fastapi import APIRouter, Depends

from app.api.v1 import (
    acquisition,
    agent,
    analytics,
    auth,
    autonomy,
    business,
    extension,
    items,
    listings,
    media,
    monitoring,
    opportunities,
    plan,
    portfolio,
    pricing,
    selling,
    settings,
    system,
)
from app.core.rate_limit import RateLimit

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
_limited = [Depends(RateLimit("api"))]
for module in (
    opportunities,
    listings,
    items,
    acquisition,
    extension,
    media,
    monitoring,
    portfolio,
    plan,
    pricing,
    selling,
    settings,
    analytics,
    agent,
    autonomy,
    business,
):
    api_router.include_router(module.router, dependencies=_limited)
