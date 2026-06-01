"""AllocatorClient — HTTP client for the Central Manager API."""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger(__name__)


class AllocatorClient:
    def __init__(self, manager_url: str, api_key: str, timeout: float = 30.0) -> None:
        self._base_url = manager_url.rstrip("/")
        self._api_key = api_key
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-API-Key": api_key},
            timeout=timeout,
        )

    # Sessions

    async def create_session(self, group_name: str) -> dict:
        resp = await self._http.post("/api/v1/sessions", json={"group_name": group_name})
        resp.raise_for_status()
        return resp.json()

    async def get_session(self, session_id: str) -> dict:
        resp = await self._http.get(f"/api/v1/sessions/{session_id}")
        resp.raise_for_status()
        return resp.json()

    async def release_session(self, session_id: str) -> None:
        resp = await self._http.delete(f"/api/v1/sessions/{session_id}")
        resp.raise_for_status()

    async def list_sessions(self, status: str | None = None, group_name: str | None = None, limit: int = 50) -> dict:
        params: dict = {"limit": limit}
        if status:
            params["session_status"] = status
        if group_name:
            params["group_name"] = group_name
        resp = await self._http.get("/api/v1/sessions", params=params)
        resp.raise_for_status()
        return resp.json()

    # Groups

    async def create_group(self, name: str, devices: list[str], description: str = "") -> dict:
        resp = await self._http.post(
            "/api/v1/groups",
            json={"name": name, "devices": devices, "description": description},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_group(self, name: str) -> dict:
        resp = await self._http.get(f"/api/v1/groups/{name}")
        resp.raise_for_status()
        return resp.json()

    async def list_groups(self) -> dict:
        resp = await self._http.get("/api/v1/groups")
        resp.raise_for_status()
        return resp.json()

    async def delete_group(self, name: str) -> None:
        resp = await self._http.delete(f"/api/v1/groups/{name}")
        resp.raise_for_status()

    # Devices

    async def register_device(self, node_id: str, logical_name: str, vendor_id: str, product_id: str, **kwargs) -> dict:
        resp = await self._http.post(
            "/api/v1/devices",
            json={"node_id": node_id, "logical_name": logical_name, "vendor_id": vendor_id, "product_id": product_id, **kwargs},
        )
        resp.raise_for_status()
        return resp.json()

    async def list_devices(self, node_id: str | None = None, status: str | None = None, device_class: str | None = None) -> dict:
        params = {}
        if node_id:
            params["node_id"] = node_id
        if status:
            params["device_status"] = status
        if device_class:
            params["device_class"] = device_class
        resp = await self._http.get("/api/v1/devices", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_device(self, logical_name: str) -> dict:
        resp = await self._http.get(f"/api/v1/devices/{logical_name}")
        resp.raise_for_status()
        return resp.json()

    async def delete_device(self, logical_name: str) -> None:
        resp = await self._http.delete(f"/api/v1/devices/{logical_name}")
        resp.raise_for_status()

    # Nodes

    async def list_nodes(self) -> dict:
        resp = await self._http.get("/api/v1/nodes")
        resp.raise_for_status()
        return resp.json()

    async def get_node(self, node_id: str) -> dict:
        resp = await self._http.get(f"/api/v1/nodes/{node_id}")
        resp.raise_for_status()
        return resp.json()

    # Lifecycle

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AllocatorClient:
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()
