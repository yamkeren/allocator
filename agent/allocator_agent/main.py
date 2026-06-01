import asyncio
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from fastapi import FastAPI

from allocator_agent.api.v1.router import v1_router
from allocator_agent.config import settings

log = structlog.get_logger(__name__)


def _configure_logging() -> None:
    import logging
    import sys
    import structlog as sl

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )
    sl.configure(
        processors=[
            sl.contextvars.merge_contextvars,
            sl.stdlib.add_log_level,
            sl.processors.TimeStamper(fmt="iso"),
            sl.dev.ConsoleRenderer() if not settings.log_json else sl.processors.JSONRenderer(),
        ],
        logger_factory=sl.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


async def _register_with_manager() -> str | None:
    node_name = settings.node_name or socket.gethostname()
    try:
        async with httpx.AsyncClient(base_url=settings.manager_url, timeout=10.0) as client:
            resp = await client.post(
                "/internal/v1/nodes/register",
                json={
                    "name": node_name,
                    "hostname": socket.gethostname(),
                    "ip_address": socket.gethostbyname(socket.gethostname()),
                    "agent_port": settings.agent_port,
                    "agent_version": "0.1.0",
                },
                headers={settings.agent_secret_header: settings.agent_secret},
            )
            resp.raise_for_status()
            data = resp.json()
            log.info("node_registered", node_id=data["node_id"], name=node_name)
            return data["node_id"]
    except Exception as exc:
        log.warning("manager_registration_failed", error=str(exc))
        return None


async def _heartbeat_loop(node_id: str) -> None:
    while True:
        await asyncio.sleep(settings.heartbeat_interval)
        try:
            async with httpx.AsyncClient(base_url=settings.manager_url, timeout=5.0) as client:
                await client.post(
                    f"/internal/v1/nodes/{node_id}/heartbeat",
                    json={"timestamp": datetime.now(UTC).isoformat()},
                    headers={settings.agent_secret_header: settings.agent_secret},
                )
        except Exception as exc:
            log.warning("heartbeat_failed", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    node_name = settings.node_name or socket.gethostname()
    log.info("allocator_agent_starting", node_name=node_name, version="0.1.0")

    node_id = await _register_with_manager()

    heartbeat_task = None
    if node_id:
        heartbeat_task = asyncio.create_task(_heartbeat_loop(node_id))

    log.info("allocator_agent_ready", port=settings.agent_port)
    yield

    log.info("allocator_agent_shutting_down")
    if heartbeat_task:
        heartbeat_task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Allocator Agent",
        description="Node agent for distributed USB resource orchestration",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(v1_router, prefix="/api/v1")
    return app


app = create_app()
