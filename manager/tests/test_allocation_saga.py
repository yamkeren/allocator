"""The reserve->bind saga: rollback on bind failure, and FOR UPDATE NOWAIT
serialization between two transactions.
"""

import uuid

from sqlalchemy import select

from allocator_manager.models.device import Device, DeviceClass, DeviceStatus
from allocator_manager.models.session import SessionStatus
from allocator_manager.services.allocation import AllocationService

# Deterministic device ids so _bind's `sorted(by str(device_id))` order is fixed:
# wifi_0 sorts before hid_1, so wifi_0 binds first, hid_1 second.
WIFI_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
HID_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


async def test_bind_failure_rolls_back_everything(db, make_node, make_device, make_session, agent):
    node = await make_node()
    await make_device(node, "wifi_0", device_id=WIFI_ID, fingerprint="fp-wifi")
    await make_device(node, "hid_1", device_class=DeviceClass.HID,
                      device_id=HID_ID, fingerprint="fp-hid")
    session = await make_session(devices=["wifi_0", "hid_1"])

    agent.fail_bind_names = {"hid_1"}  # second device fails to bind
    ok = await AllocationService(db).allocate(session)

    assert ok is True  # terminal-for-queue (FAILED), not re-queued
    assert session.status == SessionStatus.FAILED
    assert "bind_failed" in (session.failure_reason or "")
    # wifi_0 bound first, then got unbound during rollback; hid_1 never bound
    assert agent.binds == ["wifi_0"]
    assert agent.unbinds == ["wifi_0"]
    # both backing devices end FREE
    devices = (await db.execute(select(Device))).scalars().all()
    assert {d.logical_name: d.status for d in devices} == {
        "wifi_0": DeviceStatus.FREE, "hid_1": DeviceStatus.FREE,
    }


async def test_locked_device_queues_the_other_reservation(db, Session, make_node, make_device, make_session, agent):
    node = await make_node()
    dev = await make_device(node, "wifi_0")
    session = await make_session(devices=["wifi_0"])

    # A second transaction holds a FOR UPDATE lock on the only matching device.
    locker = Session()
    await locker.begin()
    await locker.execute(
        select(Device).where(Device.id == dev.id).with_for_update()
    )
    try:
        # The reservation must hit NOWAIT, roll back its savepoint, and queue.
        ok = await AllocationService(db).allocate(session)
    finally:
        await locker.rollback()
        await locker.close()

    assert ok is False
    assert session.status == SessionStatus.PENDING
    assert agent.binds == []
    await db.refresh(dev)
    assert dev.status == DeviceStatus.FREE
