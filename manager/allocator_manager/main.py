import asyncio
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


async def _run_periodic(fn, interval: int) -> None:
    """Run an async function repeatedly every `interval` seconds."""
    while True:
        await asyncio.sleep(interval)
        try:
            await fn()
        except Exception as exc:
            log.error("background_task_error", task=fn.__name__, error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("allocator_manager_starting", version="0.1.0")

    from allocator_manager.tasks.heartbeat_reaper import reap_stale_nodes
    from allocator_manager.tasks.queue_processor import process_queue
    from allocator_manager.tasks.session_expiry import expire_stale_sessions
    from allocator_manager.tasks.zombie_cleanup import cleanup_zombies

    tasks = [
        asyncio.create_task(_run_periodic(reap_stale_nodes, settings.heartbeat_reaper_interval)),
        asyncio.create_task(_run_periodic(expire_stale_sessions, settings.session_expiry_interval)),
        asyncio.create_task(_run_periodic(cleanup_zombies, settings.zombie_cleanup_interval)),
        asyncio.create_task(_run_periodic(process_queue, settings.queue_processor_interval)),
    ]

    log.info("allocator_manager_ready", host=settings.host, port=settings.port)
    yield

    log.info("allocator_manager_shutting_down")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
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
