from fastapi import APIRouter, Depends

from app.api.v1 import (
    acquisition,
    analytics,
    auth,
    demo,
    items,
    listings,
    monitoring,
    opportunities,
    portfolio,
    settings,
    system,
)
from app.core.rate_limit import RateLimit

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(demo.router)
api_router.include_router(auth.router)
_limited = [Depends(RateLimit("api"))]
for module in (opportunities, listings, items, acquisition, monitoring, portfolio, settings, analytics):
    api_router.include_router(module.router, dependencies=_limited)
