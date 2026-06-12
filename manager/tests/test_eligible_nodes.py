"""eligible_nodes: ONLINE + not frozen + all names FREE; ranked fewest-devices."""

from allocator_manager.models.device import DeviceClass, DeviceStatus
from allocator_manager.models.node import NodeStatus
from allocator_manager.services.allocation import eligible_nodes


async def test_excludes_offline(db, make_node, make_device):
    node = await make_node(name="n1", status=NodeStatus.OFFLINE)
    await make_device(node, "wifi_0")
    assert await eligible_nodes(db, ["wifi_0"]) == []


async def test_excludes_frozen(db, make_node, make_device):
    node = await make_node(name="n1", frozen=True)
    await make_device(node, "wifi_0")
    assert await eligible_nodes(db, ["wifi_0"]) == []


async def test_excludes_node_missing_a_name(db, make_node, make_device):
    node = await make_node(name="n1")
    await make_device(node, "wifi_0")
    # No hid_0 on the node -> cannot satisfy {wifi_0, hid_0}
    assert await eligible_nodes(db, ["wifi_0", "hid_0"]) == []


async def test_excludes_node_with_allocated_device(db, make_node, make_device):
    node = await make_node(name="n1")
    await make_device(node, "wifi_0", status=DeviceStatus.ALLOCATED)
    assert await eligible_nodes(db, ["wifi_0"]) == []


async def test_ranks_fewest_total_devices_first(db, make_node, make_device):
    big = await make_node(name="big", ip="10.0.0.1")
    small = await make_node(name="small", ip="10.0.0.2")
    # both can satisfy wifi_0, but `big` has more total hardware
    await make_device(big, "wifi_0", fingerprint="fp-big-wifi")
    await make_device(big, "hid_0", device_class=DeviceClass.HID, fingerprint="fp-big-hid")
    await make_device(big, "hid_1", device_class=DeviceClass.HID, fingerprint="fp-big-hid1")
    await make_device(small, "wifi_0", fingerprint="fp-small-wifi")

    ranked = await eligible_nodes(db, ["wifi_0"])
    assert [n.name for n, _ in ranked] == ["small", "big"]
    # the returned mapping points at the right device
    first_node, matched = ranked[0]
    assert matched["wifi_0"].node_id == small.id


async def test_pins_to_requested_node(db, make_node, make_device):
    n1 = await make_node(name="n1", ip="10.0.0.1")
    n2 = await make_node(name="n2", ip="10.0.0.2")
    await make_device(n1, "wifi_0", fingerprint="fp-n1")
    await make_device(n2, "wifi_0", fingerprint="fp-n2")
    ranked = await eligible_nodes(db, ["wifi_0"], requested_node_id=n2.id)
    assert [n.name for n, _ in ranked] == ["n2"]
