from dataclasses import dataclass

import httpx
import structlog

from allocator_manager.config import settings

log = structlog.get_logger(__name__)


@dataclass
class BindResult:
    bus_id: str


@dataclass
class UnbindResult:
    unbound: bool


class NodeAgentClient:
    def __init__(self, node_url: str) -> None:
        self._base_url = node_url.rstrip("/")

    async def bind(self, bus_id: str, logical_name: str, session_id: str) -> BindResult:
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=settings.agent_request_timeout,
            headers={settings.agent_secret_header: settings.agent_secret},
        ) as client:
            resp = await client.post(
                "/api/v1/usbip/bind",
                json={"bus_id": bus_id, "logical_name": logical_name, "session_id": session_id},
            )
            resp.raise_for_status()
            data = resp.json()
            return BindResult(bus_id=data.get("bus_id", bus_id))

    async def unbind(self, bus_id: str, logical_name: str) -> UnbindResult:
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=settings.agent_request_timeout,
            headers={settings.agent_secret_header: settings.agent_secret},
        ) as client:
            resp = await client.post(
                "/api/v1/usbip/unbind",
                json={"bus_id": bus_id, "logical_name": logical_name},
            )
            resp.raise_for_status()
            return UnbindResult(unbound=True)
