"""AllocatorClient against httpx MockTransport: paths, headers, parsing,
NameConflict mapping. No network.
"""

import json

import httpx
import pytest

from allocator_client.client import AllocatorClient, NameConflict
from allocator_client.config import Config

NOW = "2026-06-12T10:00:00Z"

SESSION_JSON = {
    "session_id": "s1", "client_id": "host-a", "requested_devices": ["wifi_0"],
    "status": "ACTIVE", "node_name": "lab-1", "failure_reason": None,
    "devices": [{
        "logical_name": "wifi_0", "node_id": "n1", "node_hostname": "lab-1.local",
        "node_ip": "192.168.1.5", "usbip_bus_id": "1-1.2",
        "usbip_attach_command": "usbip attach -r 192.168.1.5 -b 1-1.2",
        "device_class": "WIFI",
    }],
    "created_at": NOW,
}

DEVICE_JSON = {
    "device_id": "d1", "node_id": "n1", "logical_name": "wifi_0",
    "vendor_id": "0bda", "product_id": "8812", "serial": None,
    "manufacturer": None, "product_name": None, "mac_address": None,
    "device_class": "WIFI", "status": "FREE", "usbip_bus_id": "1-1.2",
    "created_at": NOW,
}


def make_client(handler, **kwargs) -> AllocatorClient:
    cfg = Config(path="/nonexistent/never-written.json")
    return AllocatorClient(
        manager_url="http://mgr", client_id="host-a", config=cfg,
        transport=httpx.MockTransport(handler), **kwargs,
    )


def test_create_session_posts_and_parses():
    seen = {}
    def handler(request):
        seen["path"] = request.url.path
        seen["client_id"] = request.headers["X-Client-Id"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=SESSION_JSON)

    with make_client(handler) as client:
        s = client.create_session(["wifi_0"])
    assert seen["path"] == "/api/v1/sessions"
    assert seen["client_id"] == "host-a"
    assert seen["body"] == {"devices": ["wifi_0"], "node": None}
    assert s.status == "ACTIVE" and s.devices[0].usbip_bus_id == "1-1.2"


def test_release_session_deletes():
    seen = {}
    def handler(request):
        seen["method_path"] = (request.method, request.url.path)
        return httpx.Response(204)

    with make_client(handler) as client:
        client.release_session("s1")
    assert seen["method_path"] == ("DELETE", "/api/v1/sessions/s1")


def test_list_sessions_passes_status_param():
    def handler(request):
        assert request.url.params["session_status"] == "ACTIVE"
        return httpx.Response(200, json={"items": [SESSION_JSON], "total": 1})

    with make_client(handler) as client:
        out = client.list_sessions(status="ACTIVE")
    assert out.total == 1


def test_rename_conflict_raises_nameconflict():
    def handler(request):
        return httpx.Response(409, json={"detail": {
            "error": "name_conflict", "name": "cam", "node": "lab-1",
            "holder_logical_name": "video_0",
        }})

    with make_client(handler) as client:
        with pytest.raises(NameConflict) as exc:
            client.rename_device("lab-1", "video_1", "cam")
    assert exc.value.holder == "video_0"


def test_rename_success_parses_device():
    def handler(request):
        body = json.loads(request.content)
        assert body == {"name": "cam", "force": True}
        return httpx.Response(200, json=DEVICE_JSON)

    with make_client(handler) as client:
        d = client.rename_device("lab-1", "wifi_0", "cam", force=True)
    assert d.logical_name == "wifi_0"


def test_http_error_surfaces():
    def handler(request):
        return httpx.Response(500, json={"detail": "boom"})

    with make_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.get_session("s1")


def test_unfreeze_node_path():
    def handler(request):
        assert (request.method, request.url.path) == ("POST", "/api/v1/nodes/lab-1/unfreeze")
        return httpx.Response(200, json={
            "node_id": "n1", "name": "lab-1", "hostname": "lab-1.local",
            "ip_address": "192.168.1.5", "agent_port": 5000,
            "agent_url": "http://192.168.1.5:5000", "status": "ONLINE",
            "last_heartbeat": NOW, "agent_version": None, "frozen": False,
            "created_at": NOW,
        })

    with make_client(handler) as client:
        n = client.unfreeze_node("lab-1")
    assert n.frozen is False
