"""AllocationService.allocate happy paths and atomic multi-device behavior."""

from sqlalchemy import select

from allocator_manager.models.device import Device, DeviceClass, DeviceStatus
from allocator_manager.models.session import SessionStatus
from allocator_manager.models.session_device import SessionDevice, SessionDeviceStatus
from allocator_manager.services.allocation import AllocationService


async def test_single_device_allocation_activates(db, make_node, make_device, make_session, agent):
    node = await make_node()
    dev = await make_device(node, "wifi_0")
    session = await make_session(devices=["wifi_0"])

    ok = await AllocationService(db).allocate(session)

    assert ok is True
    assert session.status == SessionStatus.ACTIVE
    assert session.node_id == node.id
    assert agent.binds == ["wifi_0"]
    await db.refresh(dev)
    assert dev.status == DeviceStatus.ALLOCATED
    sds = (await db.execute(select(SessionDevice).where(SessionDevice.session_id == session.id))).scalars().all()
    assert len(sds) == 1 and sds[0].status == SessionDeviceStatus.ALLOCATED


async def test_multi_device_allocation_atomic_on_one_node(db, make_node, make_device, make_session, agent):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fp-wifi")
    await make_device(node, "hid_0", device_class=DeviceClass.HID, fingerprint="fp-hid")
    session = await make_session(devices=["wifi_0", "hid_0"])

    await AllocationService(db).allocate(session)

    assert session.status == SessionStatus.ACTIVE
    assert sorted(agent.binds) == ["hid_0", "wifi_0"]
    allocated = (await db.execute(
        select(Device).where(Device.status == DeviceStatus.ALLOCATED)
    )).scalars().all()
    assert len(allocated) == 2


async def test_no_eligible_node_leaves_session_pending(db, make_session, agent):
    session = await make_session(devices=["wifi_0"])
    ok = await AllocationService(db).allocate(session)
    assert ok is False
    assert session.status == SessionStatus.PENDING
    assert agent.binds == []


async def test_partial_availability_does_not_allocate(db, make_node, make_device, make_session, agent):
    # node has wifi_0 FREE but hid_0 ALLOCATED -> cannot satisfy {wifi_0, hid_0}
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fp-wifi")
    await make_device(node, "hid_0", device_class=DeviceClass.HID,
                      status=DeviceStatus.ALLOCATED, fingerprint="fp-hid")
    session = await make_session(devices=["wifi_0", "hid_0"])

    await AllocationService(db).allocate(session)

    assert session.status == SessionStatus.PENDING
    wifi = (await db.execute(
        select(Device).where(Device.logical_name == "wifi_0")
    )).scalar_one()
    assert wifi.status == DeviceStatus.FREE  # untouched
    assert agent.binds == []
