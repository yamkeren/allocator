"""Stack smoke: the manager is up, the stub registered an ONLINE node, and the
manager assigned the expected per-class generic names."""

from allocator_client.client import AllocatorClient


def test_node_is_online(manager_url):
    with AllocatorClient(manager_url=manager_url, client_id="e2e-smoke") as client:
        nodes = client.list_nodes()
    names = {n.name: n for n in nodes.items}
    assert "stub-1" in names
    assert names["stub-1"].status == "ONLINE"


def test_devices_named_by_manager(manager_url):
    with AllocatorClient(manager_url=manager_url, client_id="e2e-smoke") as client:
        devices = client.list_devices()
    logical = {d.logical_name for d in devices.items}
    # one WIFI + one HID synced -> manager assigns wifi_0 and hid_0
    assert "wifi_0" in logical
    assert "hid_0" in logical
