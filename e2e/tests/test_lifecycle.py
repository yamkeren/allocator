"""Full lifecycle over real HTTP: create -> ACTIVE -> the manager called the
stub agent's bind -> release -> the manager called unbind -> session RELEASED.
"""

import httpx

from allocator_client.client import AllocatorClient


def test_session_lifecycle_drives_agent(manager_url, stub_url):
    with AllocatorClient(manager_url=manager_url, client_id="e2e-life") as client:
        session = client.create_session(["wifi_0"])
        assert session.status == "ACTIVE"
        assert session.node_name == "stub-1"
        # the session carries usbip coordinates from the agent
        dev = next(d for d in session.devices if d.logical_name == "wifi_0")
        assert dev.usbip_bus_id  # non-empty bus id returned by the stub's bind

        # the manager actually called the stub agent's bind endpoint
        binds = httpx.get(f"{stub_url}/_calls", timeout=5).json()["binds"]
        assert "wifi_0" in binds

        client.release_session(session.session_id)
        assert client.get_session(session.session_id).status == "RELEASED"

        # release drove the stub agent's unbind endpoint
        unbinds = httpx.get(f"{stub_url}/_calls", timeout=5).json()["unbinds"]
        assert "wifi_0" in unbinds


def test_device_is_free_again_after_release(manager_url):
    with AllocatorClient(manager_url=manager_url, client_id="e2e-life2") as client:
        session = client.create_session(["wifi_0"])
        assert session.status == "ACTIVE"
        client.release_session(session.session_id)
        # wifi_0 must be FREE again -> a fresh session allocates immediately
        again = client.create_session(["wifi_0"])
        assert again.status == "ACTIVE"
        client.release_session(again.session_id)
