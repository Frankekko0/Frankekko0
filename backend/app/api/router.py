from fastapi import APIRouter, Depends

from app.api.v1 import (
    acquisition,
    analytics,
    auth,
    extension,
    items,
    listings,
    media,
    monitoring,
    opportunities,
    portfolio,
    pricing,
    settings,
    system,
    vinted,
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
    pricing,
    settings,
    analytics,
    vinted,
):
    api_router.include_router(module.router, dependencies=_limited)
