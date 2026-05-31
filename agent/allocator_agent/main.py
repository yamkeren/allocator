import socket
from contextlib import asynccontextmanager
from typing import Any

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()

    node_name = settings.node_name or socket.gethostname()
    log.info("allocator_agent_starting", node_name=node_name, version="0.1.0")

    # Initialise inventory DB
    from allocator_agent.components.inventory_manager import InventoryManager
    mgr = await InventoryManager.get_instance()
    await mgr.initialize()

    # Run initial device scan
    from allocator_agent.components.device_discoverer import DeviceDiscoverer
    discoverer = DeviceDiscoverer()
    discovered = await discoverer.scan()
    log.info("initial_scan_complete", device_count=len(discovered))
    for device_info in discovered:
        await mgr.handle_add(device_info)

    # Start udev monitor
    from allocator_agent.components.udev_monitor import UdevMonitor
    monitor = UdevMonitor(inventory_manager=mgr)
    monitor.start()

    # Start heartbeat reporter
    from allocator_agent.components.heartbeat_reporter import HeartbeatReporter
    reporter = HeartbeatReporter(node_name=node_name, inventory_manager=mgr)
    reporter_task = reporter.start()

    log.info("allocator_agent_ready", port=settings.agent_port)
    yield

    log.info("allocator_agent_shutting_down")
    monitor.stop()
    reporter_task.cancel()

    log.info("allocator_agent_stopped")


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
