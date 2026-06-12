"""Stub node agent for e2e tests.

On startup it registers itself with the manager (using its real container IP,
which the manager stores in an INET column and dials back for bind), then syncs
a fixed two-device inventory. It serves the usbip bind/unbind endpoints the
manager calls during allocation/release, recording every call so the host test
can assert the manager actually drove the agent. It never touches real usbip.
"""

import os
import socket
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from allocator_contract.node import (
    DeviceInfoPayload,
    DeviceSyncPayload,
    NodeRegisterPayload,
)
from allocator_contract.usbip import BindRequest, BindResponse, UnbindRequest, UnbindResponse

MANAGER_URL = os.environ["MANAGER_URL"]
AGENT_SECRET = os.environ.get("AGENT_SECRET", "dev-agent-secret")
NODE_NAME = os.environ.get("NODE_NAME", "stub-1")
AGENT_PORT = int(os.environ.get("AGENT_PORT", "5000"))

CALLS: dict[str, list[str]] = {"binds": [], "unbinds": []}

INVENTORY = [
    DeviceInfoPayload(vendor_id="0bda", product_id="8812", device_class="WIFI",
                      usbip_bus_id="1-1", fingerprint="e2e-wifi"),
    DeviceInfoPayload(vendor_id="046d", product_id="c52b", device_class="HID",
                      usbip_bus_id="1-2", fingerprint="e2e-hid"),
]


def _container_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ip = _container_ip()
    headers = {"X-Agent-Secret": AGENT_SECRET}
    async with httpx.AsyncClient(base_url=MANAGER_URL, headers=headers, timeout=10) as c:
        reg = await c.post("/internal/v1/nodes/register", json=NodeRegisterPayload(
            name=NODE_NAME, hostname=NODE_NAME, ip_address=ip, agent_port=AGENT_PORT,
        ).model_dump())
        reg.raise_for_status()
        node_id = reg.json()["node_id"]
        sync = await c.post(
            f"/internal/v1/nodes/{node_id}/devices/sync",
            json=DeviceSyncPayload(devices=INVENTORY).model_dump(mode="json"),
        )
        sync.raise_for_status()
    app.state.node_id = node_id
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/api/v1/usbip/bind", response_model=BindResponse)
async def bind(body: BindRequest) -> BindResponse:
    CALLS["binds"].append(body.logical_name)
    return BindResponse(bound=True, bus_id=body.bus_id)


@app.post("/api/v1/usbip/unbind", response_model=UnbindResponse)
async def unbind(body: UnbindRequest) -> UnbindResponse:
    CALLS["unbinds"].append(body.logical_name)
    return UnbindResponse(unbound=True)


@app.get("/_calls")
async def calls() -> dict:
    return CALLS


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "node_id": getattr(app.state, "node_id", None)}
