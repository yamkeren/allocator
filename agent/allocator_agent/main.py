import asyncio
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from fastapi import FastAPI

from allocator_contract.node import (
    DeviceSyncPayload,
    NodeHeartbeatPayload,
    NodeRegisterPayload,
)
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


def _detect_advertise_ip() -> str:
    """The IP the manager should use to reach this agent.

    `socket.gethostbyname(gethostname())` returns 127.0.1.1 on Debian-style
    hosts, which the manager (often on another host or in a container) can't
    reach. Instead we read the source IP the OS would use for an outbound
    route — no packets are actually sent by a UDP connect.
    """
    if settings.advertise_ip:
        return settings.advertise_ip
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    # Fallbacks: hostname resolution (skip loopback), then localhost.
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return "127.0.0.1"


async def _register_with_manager() -> str | None:
    node_name = settings.node_name or socket.gethostname()
    ip_address = _detect_advertise_ip()
    try:
        async with httpx.AsyncClient(base_url=settings.manager_url, timeout=10.0) as client:
            resp = await client.post(
                "/internal/v1/nodes/register",
                json=NodeRegisterPayload(
                    name=node_name,
                    hostname=socket.gethostname(),
                    ip_address=ip_address,
                    agent_port=settings.agent_port,
                    agent_version="0.1.0",
                ).model_dump(),
                headers={settings.agent_secret_header: settings.agent_secret},
            )
            resp.raise_for_status()
            data = resp.json()
            log.info("node_registered", node_id=data["node_id"], name=node_name, ip=ip_address)
            return data["node_id"]
    except Exception as exc:
        log.warning("manager_registration_failed", error=str(exc))
        return None


async def _scan_and_sync(node_id: str) -> None:
    """Scan local USB devices and push the inventory to the manager."""
    from allocator_agent.components.device_discoverer import scan_usb_devices
    devices = scan_usb_devices()
    if not devices:
        return
    try:
        async with httpx.AsyncClient(base_url=settings.manager_url, timeout=10.0) as client:
            resp = await client.post(
                f"/internal/v1/nodes/{node_id}/devices/sync",
                json=DeviceSyncPayload(devices=devices).model_dump(),
                headers={settings.agent_secret_header: settings.agent_secret},
            )
            resp.raise_for_status()
            result = resp.json()
            log.info("devices_synced", node_id=node_id, **result)
    except Exception as exc:
        log.warning("device_sync_failed", error=str(exc))


async def _heartbeat_loop(node_id: str) -> None:
    while True:
        await asyncio.sleep(settings.heartbeat_interval)
        try:
            async with httpx.AsyncClient(base_url=settings.manager_url, timeout=5.0) as client:
                await client.post(
                    f"/internal/v1/nodes/{node_id}/heartbeat",
                    json=NodeHeartbeatPayload(timestamp=datetime.now(UTC)).model_dump(mode="json"),
                    headers={settings.agent_secret_header: settings.agent_secret},
                )
        except Exception as exc:
            log.warning("heartbeat_failed", error=str(exc))

        # Re-scan on every heartbeat so newly plugged/unplugged devices are picked up
        await _scan_and_sync(node_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    node_name = settings.node_name or socket.gethostname()
    log.info("allocator_agent_starting", node_name=node_name, version="0.1.0")

    node_id = await _register_with_manager()

    heartbeat_task = None
    if node_id:
        # Initial device sync immediately after registration
        await _scan_and_sync(node_id)
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
