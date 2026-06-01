from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from allocator_manager.api.v1.router import internal_router, v1_router
from allocator_manager.config import settings
from allocator_manager.database import engine
from allocator_manager.observability.logging import configure_logging

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("allocator_manager_starting", version="0.1.0")
    log.info("allocator_manager_ready", host=settings.host, port=settings.port)
    yield
    log.info("allocator_manager_shutting_down")
    await engine.dispose()
    log.info("allocator_manager_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Allocator Manager",
        description="Distributed USB resource orchestration — central manager",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(v1_router, prefix="/api/v1")
    app.include_router(internal_router, prefix="/internal/v1")

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, Any]:
        return {"status": "healthy", "version": "0.1.0"}

    return app


app = create_app()
