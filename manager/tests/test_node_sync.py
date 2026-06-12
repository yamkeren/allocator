"""NodeService: fingerprint identity across reconnects, prune of vanished
devices, kick on inventory change, and register/heartbeat."""

from datetime import UTC, datetime

from sqlalchemy import select

from allocator_manager.models.device import Device
from allocator_manager.models.node import Node, NodeStatus
from allocator_manager.services.node import NodeService
from allocator_contract.node import (
    DeviceInfoPayload,
    DeviceSyncPayload,
    NodeHeartbeatPayload,
    NodeRegisterPayload,
)


def _payload(*fingerprints, bus="1-1"):
    return DeviceSyncPayload(devices=[
        DeviceInfoPayload(vendor_id="0bda", product_id="8812", device_class="WIFI",
                          usbip_bus_id=bus, fingerprint=fp)
        for fp in fingerprints
    ])


async def test_register_creates_then_updates_node(db):
    svc = NodeService(db)
    r1 = await svc.register(NodeRegisterPayload(
        name="lab-1", hostname="lab-1.local", ip_address="10.0.0.5"))
    assert r1.registered
    # Re-register same name -> same node row, updated fields, ONLINE.
    r2 = await svc.register(NodeRegisterPayload(
        name="lab-1", hostname="lab-1.local", ip_address="10.0.0.9", agent_port=5001))
    assert r2.node_id == r1.node_id
    nodes = (await db.execute(select(Node))).scalars().all()
    assert len(nodes) == 1
    assert str(nodes[0].ip_address) == "10.0.0.9"
    assert nodes[0].status == NodeStatus.ONLINE


async def test_sync_keeps_device_row_stable_across_reconnect(db, make_node, kick_spy):
    node = await make_node()
    svc = NodeService(db)
    r1 = await svc.sync_devices(str(node.id), _payload("fp-x", bus="1-1"))
    assert r1.new == 1
    dev1 = (await db.execute(select(Device).where(Device.fingerprint == "fp-x"))).scalar_one()

    # Same fingerprint, different bus id -> update in place, not a new row.
    r2 = await svc.sync_devices(str(node.id), _payload("fp-x", bus="2-1"))
    assert r2.new == 0 and r2.updated == 1
    devs = (await db.execute(select(Device).where(Device.fingerprint == "fp-x"))).scalars().all()
    assert len(devs) == 1 and devs[0].id == dev1.id
    assert devs[0].usbip_bus_id == "2-1"


async def test_sync_prunes_vanished_free_device(db, make_node):
    node = await make_node()
    svc = NodeService(db)
    await svc.sync_devices(str(node.id), _payload("fp-gone"))
    # Next sync no longer reports fp-gone -> it is pruned (FREE + unreferenced).
    result = await svc.sync_devices(str(node.id), _payload("fp-stay"))
    assert result.removed == 1
    remaining = (await db.execute(select(Device.fingerprint))).scalars().all()
    assert set(remaining) == {"fp-stay"}


async def test_sync_kicks_queue_on_change_only(db, make_node, kick_spy):
    node = await make_node()
    svc = NodeService(db)
    await svc.sync_devices(str(node.id), _payload("fp-a"))      # new=1 -> kick
    assert kick_spy["n"] == 1
    await svc.sync_devices(str(node.id), _payload("fp-a"))      # only updated -> no kick
    assert kick_spy["n"] == 1


async def test_heartbeat_marks_online(db, make_node):
    node = await make_node(status=NodeStatus.OFFLINE)
    resp = await NodeService(db).heartbeat(
        str(node.id), NodeHeartbeatPayload(timestamp=datetime.now(UTC)))
    assert resp.acknowledged
    await db.refresh(node)
    assert node.status == NodeStatus.ONLINE
