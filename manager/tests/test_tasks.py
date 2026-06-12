"""Background tasks against the test DB. Each task opens its own
AsyncSessionLocal, which `task_db` repoints at the test database."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from allocator_manager.models.device import DeviceStatus
from allocator_manager.models.node import Node, NodeStatus
from allocator_manager.models.session import SessionStatus
from allocator_manager.models.session_device import SessionDevice, SessionDeviceStatus
from allocator_manager.tasks.heartbeat_reaper import reap_stale_nodes
from allocator_manager.tasks.session_expiry import expire_stale_sessions
from allocator_manager.tasks.zombie_cleanup import cleanup_zombies


async def test_reaper_marks_stale_node_offline(db, make_node, task_db):
    node = await make_node(status=NodeStatus.ONLINE)
    node.last_heartbeat = datetime.now(UTC) - timedelta(days=1)
    await db.commit()

    await reap_stale_nodes()

    # Expire the identity-map cache so re-query reflects the task's UPDATE.
    # Capture id before expiring to avoid lazy-load in the WHERE clause.
    node_id = node.id
    db.expire(node)
    refreshed = (await db.execute(select(Node).where(Node.id == node_id))).scalar_one()
    assert refreshed.status == NodeStatus.OFFLINE


async def test_reaper_leaves_fresh_node_online(db, make_node, task_db):
    node = await make_node(status=NodeStatus.ONLINE)  # last_heartbeat = now
    node_id = node.id
    await reap_stale_nodes()
    db.expire(node)
    refreshed = (await db.execute(select(Node).where(Node.id == node_id))).scalar_one()
    assert refreshed.status == NodeStatus.ONLINE


async def test_expiry_releases_old_active_session(db, make_node, make_device, make_session, task_db):
    node = await make_node()
    dev = await make_device(node, "wifi_0", status=DeviceStatus.ALLOCATED)
    session = await make_session(devices=["wifi_0"], status=SessionStatus.ACTIVE, node_id=node.id)
    # Wire the SessionDevice row that expire_stale_sessions uses to locate and
    # free the backing device (the task iterates session_devices, not Device directly).
    sd = SessionDevice(
        session_id=session.id,
        device_id=dev.id,
        logical_name="wifi_0",
        node_id=node.id,
        node_agent_url=node.agent_url,
        usbip_bus_id=dev.usbip_bus_id,
        status=SessionDeviceStatus.ALLOCATED,
    )
    db.add(sd)
    session.updated_at = datetime.now(UTC) - timedelta(days=1)
    await db.commit()

    await expire_stale_sessions()

    await db.refresh(session)
    await db.refresh(dev)
    assert session.status == SessionStatus.RELEASED
    assert dev.status == DeviceStatus.FREE


async def test_zombie_cleanup_frees_orphan_allocated_device(db, make_node, make_device, task_db):
    node = await make_node()
    # ALLOCATED device with no ACTIVE session referencing it -> zombie.
    dev = await make_device(node, "wifi_0", status=DeviceStatus.ALLOCATED)
    await cleanup_zombies()
    await db.refresh(dev)
    assert dev.status == DeviceStatus.FREE
