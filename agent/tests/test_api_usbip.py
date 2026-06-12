"""Agent FastAPI routes via ASGITransport — controller monkeypatched.

ASGITransport does not run the lifespan, so no registration/heartbeat
side effects happen.
"""

import httpx
import pytest

from allocator_agent.components.usbip_controller import BindResult, UsbipController, UsbipError
from allocator_agent.main import create_app


@pytest.fixture
async def api():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        yield client


async def test_health(api):
    resp = await api.get("/api/v1/health")
    assert resp.status_code == 200


async def test_bind_success(api, monkeypatch):
    async def ok(self, bus_id):
        return BindResult(bus_id=bus_id)
    monkeypatch.setattr(UsbipController, "bind", ok)
    resp = await api.post("/api/v1/usbip/bind", json={
        "bus_id": "1-1.2", "logical_name": "wifi_0", "session_id": "s1",
    })
    assert resp.status_code == 200
    assert resp.json() == {"bound": True, "bus_id": "1-1.2"}


async def test_bind_failure_maps_to_500(api, monkeypatch):
    async def boom(self, bus_id):
        raise UsbipError("bind exploded")
    monkeypatch.setattr(UsbipController, "bind", boom)
    resp = await api.post("/api/v1/usbip/bind", json={
        "bus_id": "1-1.2", "logical_name": "wifi_0", "session_id": "s1",
    })
    assert resp.status_code == 500
    assert "bind exploded" in resp.json()["detail"]


async def test_unbind_success(api, monkeypatch):
    async def ok(self, bus_id):
        from allocator_agent.components.usbip_controller import UnbindResult
        return UnbindResult()
    monkeypatch.setattr(UsbipController, "unbind", ok)
    resp = await api.post("/api/v1/usbip/unbind", json={
        "bus_id": "1-1.2", "logical_name": "wifi_0",
    })
    assert resp.status_code == 200
    assert resp.json() == {"unbound": True}


async def test_bind_validation_error_is_422(api):
    resp = await api.post("/api/v1/usbip/bind", json={"bus_id": "1-1.2"})
    assert resp.status_code == 422
