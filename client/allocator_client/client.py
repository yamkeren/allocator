"""AllocatorClient — synchronous HTTP client for the Central Manager API.

Blocking client built on httpx.Client (no event loop). Methods return typed
models from the shared `allocator_contract` package.
"""

from __future__ import annotations

import httpx
import structlog

from allocator_client.config import Config
from allocator_contract.device import DeviceListResponse, DeviceRename, DeviceResponse
from allocator_contract.group import GroupCreate, GroupListResponse, GroupResponse
from allocator_contract.node import NodeListResponse, NodeResponse
from allocator_contract.session import (
    SessionCreate,
    SessionListResponse,
    SessionResponse,
)

log = structlog.get_logger(__name__)


class NameConflict(Exception):
    """Raised when renaming a device to a name already taken on the node."""

    def __init__(self, detail: dict) -> None:
        self.detail = detail
        self.name = detail.get("name", "")
        self.node = detail.get("node", "")
        self.holder = detail.get("holder_logical_name", "")
        super().__init__(f"name {self.name!r} already used by {self.holder!r} on {self.node!r}")


def _parse(resp: httpx.Response, model):
    resp.raise_for_status()
    return model.model_validate(resp.json())


class AllocatorClient:
    def __init__(
        self,
        manager_url: str | None = None,
        client_id: str | None = None,
        timeout: float = 30.0,
        config: Config | None = None,
    ) -> None:
        # Values come from the JSON config file; args override them. `config`
        # is exposed so callers can read/persist values:
        #   client.config.set("url", "http://allocator.local")
        self.config = config or Config()
        self._base_url = (manager_url or self.config.get("url")).rstrip("/")
        self._client_id = client_id or self.config.get("client_id")
        # There is no client authentication — identity is just the hostname.
        self._http = httpx.Client(
            base_url=self._base_url,
            headers={"X-Client-Id": self._client_id},
            timeout=timeout,
        )

    # Sessions

    def create_session(self, group_name: str, node: str | None = None) -> SessionResponse:
        body = SessionCreate(group_name=group_name, node=node)
        return _parse(self._http.post("/api/v1/sessions", json=body.model_dump()), SessionResponse)

    def get_session(self, session_id: str) -> SessionResponse:
        return _parse(self._http.get(f"/api/v1/sessions/{session_id}"), SessionResponse)

    def freeze_session_node(self, session_id: str) -> None:
        self._http.post(f"/api/v1/sessions/{session_id}/freeze").raise_for_status()

    def release_session(self, session_id: str) -> None:
        self._http.delete(f"/api/v1/sessions/{session_id}").raise_for_status()

    def list_sessions(
        self, status: str | None = None, group_name: str | None = None, limit: int = 50
    ) -> SessionListResponse:
        params: dict = {"limit": limit}
        if status:
            params["session_status"] = status
        if group_name:
            params["group_name"] = group_name
        return _parse(self._http.get("/api/v1/sessions", params=params), SessionListResponse)

    # Groups

    def create_group(self, name: str, devices: list[str], description: str = "") -> GroupResponse:
        body = GroupCreate(name=name, devices=devices, description=description or None)
        return _parse(self._http.post("/api/v1/groups", json=body.model_dump()), GroupResponse)

    def get_group(self, name: str) -> GroupResponse:
        return _parse(self._http.get(f"/api/v1/groups/{name}"), GroupResponse)

    def list_groups(self) -> GroupListResponse:
        return _parse(self._http.get("/api/v1/groups"), GroupListResponse)

    def delete_group(self, name: str) -> None:
        self._http.delete(f"/api/v1/groups/{name}").raise_for_status()

    # Devices

    def register_device(
        self, node_id: str, logical_name: str, vendor_id: str, product_id: str, **kwargs
    ) -> DeviceResponse:
        resp = self._http.post(
            "/api/v1/devices",
            json={"node_id": node_id, "logical_name": logical_name, "vendor_id": vendor_id, "product_id": product_id, **kwargs},
        )
        return _parse(resp, DeviceResponse)

    def list_devices(
        self, node_id: str | None = None, status: str | None = None, device_class: str | None = None
    ) -> DeviceListResponse:
        params = {}
        if node_id:
            params["node_id"] = node_id
        if status:
            params["device_status"] = status
        if device_class:
            params["device_class"] = device_class
        return _parse(self._http.get("/api/v1/devices", params=params), DeviceListResponse)

    def get_device(self, node: str, logical_name: str) -> DeviceResponse:
        return _parse(self._http.get(f"/api/v1/devices/{node}/{logical_name}"), DeviceResponse)

    def rename_device(
        self, node: str, logical_name: str, new_name: str, force: bool = False
    ) -> DeviceResponse:
        body = DeviceRename(name=new_name, force=force)
        resp = self._http.post(
            f"/api/v1/devices/{node}/{logical_name}/rename",
            json=body.model_dump(),
        )
        if resp.status_code == 409:
            detail = resp.json().get("detail")
            if isinstance(detail, dict) and detail.get("error") == "name_conflict":
                raise NameConflict(detail)
        return _parse(resp, DeviceResponse)

    def delete_device(self, node: str, logical_name: str) -> None:
        self._http.delete(f"/api/v1/devices/{node}/{logical_name}").raise_for_status()

    # Nodes

    def list_nodes(self) -> NodeListResponse:
        return _parse(self._http.get("/api/v1/nodes"), NodeListResponse)

    def get_node(self, node_id: str) -> NodeResponse:
        return _parse(self._http.get(f"/api/v1/nodes/{node_id}"), NodeResponse)

    def unfreeze_node(self, node: str) -> NodeResponse:
        return _parse(self._http.post(f"/api/v1/nodes/{node}/unfreeze"), NodeResponse)

    # Lifecycle

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AllocatorClient:
        return self

    def __exit__(self, *_) -> None:
        self.close()
