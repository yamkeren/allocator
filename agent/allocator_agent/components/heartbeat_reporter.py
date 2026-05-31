"""Sends periodic heartbeats to the manager.

On startup (or reconnect after prolonged absence), triggers a full device sync.
Uses exponential backoff when the manager is unreachable.
"""

import asyncio
import socket
from datetime import UTC, datetime

import httpx
import structlog

from allocator_agent.config import settings

log = structlog.get_logger(__name__)


class HeartbeatReporter:
    def __init__(self, node_name: str, inventory_manager) -> None:
        self._node_name = node_name
        self._mgr = inventory_manager
        self._node_id: str | None = None
        self._consecutive_failures = 0
        self._registered = False

    def start(self) -> asyncio.Task:
        return asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        delay = settings.heartbeat_interval
        while True:
            try:
                await self._tick()
                self._consecutive_failures = 0
                delay = settings.heartbeat_interval
            except Exception as exc:
                self._consecutive_failures += 1
                delay = min(
                    settings.heartbeat_interval * (2 ** self._consecutive_failures),
                    settings.heartbeat_max_backoff,
                )
                log.warning(
                    "heartbeat_failed",
                    error=str(exc),
                    next_retry_s=delay,
                    consecutive_failures=self._consecutive_failures,
                )
            await asyncio.sleep(delay)

    async def _tick(self) -> None:
        async with httpx.AsyncClient(
            base_url=settings.manager_url, timeout=5.0
        ) as client:
            if not self._registered:
                await self._register(client)

            devices = await self._mgr.list_onboarded()
            bound = [d for d in devices if d.status == "BOUND"]

            resp = await client.post(
                f"/internal/v1/nodes/{self._node_id}/heartbeat",
                json={
                    "timestamp": datetime.now(UTC).isoformat(),
                    "device_count": len(devices),
                    "bound_device_count": len(bound),
                    "agent_version": "0.1.0",
                },
                headers={settings.agent_secret_header: settings.agent_secret},
            )
            resp.raise_for_status()

            # After prolonged absence, push full device sync
            if self._consecutive_failures > 2:
                await self._mgr.push_sync_to_manager()

    async def _register(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/internal/v1/nodes/register",
            json={
                "name": self._node_name,
                "hostname": socket.gethostname(),
                "ip_address": socket.gethostbyname(socket.gethostname()),
                "agent_port": settings.agent_port,
                "agent_version": "0.1.0",
            },
            headers={settings.agent_secret_header: settings.agent_secret},
        )
        resp.raise_for_status()
        data = resp.json()
        self._node_id = data["node_id"]
        self._registered = True
        self._mgr.set_manager_coords(settings.manager_url, self._node_id)

        # Push initial device sync after registration
        await self._mgr.push_sync_to_manager()
        log.info("node_registered", node_id=self._node_id, name=self._node_name)
