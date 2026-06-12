"""AllocatorSession lifecycle: attach on enter, detach+release on exit,
rollback when an attach fails mid-way. Client and usbip are fakes.
"""

import pytest

from allocator_client import session_context
from allocator_client.session_context import AllocatorSession

NOW = "2026-06-12T10:00:00Z"


def _session_response(status="ACTIVE", n_devices=2, failure_reason=None):
    from allocator_contract.session import SessionDeviceAttachInfo, SessionResponse
    devices = [
        SessionDeviceAttachInfo(
            logical_name=f"dev_{i}", node_id="n1", node_hostname="lab-1.local",
            node_ip="192.168.1.5", usbip_bus_id=f"1-1.{i}",
            usbip_attach_command=None, device_class="GENERIC",
        )
        for i in range(n_devices)
    ]
    return SessionResponse(
        session_id="s1", client_id="host-a",
        requested_devices=[d.logical_name for d in devices],
        status=status, node_name="lab-1", failure_reason=failure_reason,
        devices=devices, created_at=NOW,
    )


class FakeClient:
    def __init__(self, response):
        self._response = response
        self.released = []

    def create_session(self, devices, node=None):
        return self._response

    def release_session(self, session_id):
        self.released.append(session_id)


@pytest.fixture
def usbip_log(monkeypatch):
    log = {"attached": [], "detached": []}
    ports = iter(range(10))

    def fake_attach(node_ip, bus_id, timeout=15, *, sudo=False):
        log["attached"].append(bus_id)
        return next(ports)

    def fake_detach(port, timeout=10, *, sudo=False):
        log["detached"].append(port)

    monkeypatch.setattr(session_context.usbip_helper, "attach", fake_attach)
    monkeypatch.setattr(session_context.usbip_helper, "detach", fake_detach)
    return log


def test_happy_path_attaches_then_detaches_and_releases(usbip_log):
    client = FakeClient(_session_response())
    with AllocatorSession(client, ["dev_0", "dev_1"]) as info:
        assert [d.local_port for d in info.devices] == [0, 1]
        assert usbip_log["attached"] == ["1-1.0", "1-1.1"]
    assert usbip_log["detached"] == [1, 0]  # reverse order
    assert client.released == ["s1"]


def test_attach_failure_rolls_back_and_releases(usbip_log, monkeypatch):
    calls = {"n": 0}

    def flaky_attach(node_ip, bus_id, timeout=15, *, sudo=False):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("vhci full")
        usbip_log["attached"].append(bus_id)
        return 7

    monkeypatch.setattr(session_context.usbip_helper, "attach", flaky_attach)
    client = FakeClient(_session_response())
    with pytest.raises(RuntimeError, match="Failed to attach dev_1"):
        with AllocatorSession(client, ["dev_0", "dev_1"]):
            pass
    assert usbip_log["detached"] == [7]      # first device detached again
    assert client.released == ["s1"]          # session released on rollback


def test_non_active_status_raises(usbip_log):
    client = FakeClient(_session_response(status="PENDING"))
    with pytest.raises(RuntimeError, match="Unexpected session status"):
        with AllocatorSession(client, ["dev_0"]):
            pass
    assert client.released == []  # nothing attached -> enter failed before session set


def test_failed_status_raises_with_reason(usbip_log):
    client = FakeClient(_session_response(status="FAILED", failure_reason="no node"))
    with pytest.raises(RuntimeError, match="no node"):
        with AllocatorSession(client, ["dev_0"]):
            pass
