"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.core.redis import close_redis
from app.db.session import dispose_engine
from app.marketplace.registry import close_provider
from app.workers.queue import close_queue

DESCRIPTION = """
**Vinted FlipFinder** - marketplace intelligence for resellers.

Finds listings priced below their realistic market value and quantifies the opportunity:
Fair Market Value from comparables, resale scenarios, all costs, net profit, ROI, maximum buy
price, demand, sales velocity, risk and an explainable **Flip Score**.

Authentication: browser sessions use an httpOnly cookie + `X-CSRF-Token` header; API clients
can log in with `?include_token=true` and send `Authorization: Bearer <token>`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    settings.validate_for_production()
    get_logger(__name__).info(
        "api.started", environment=settings.environment, provider=settings.marketplace_provider
    )
    yield
    await close_provider()
    await close_queue()
    await close_redis()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    install_exception_handlers(app)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "X-Request-ID"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
