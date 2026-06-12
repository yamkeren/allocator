"""SessionService: create (ACTIVE vs PENDING), release, freeze/unfreeze, and
freeze's effect on eligibility."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from allocator_manager.models.device import Device, DeviceStatus
from allocator_manager.models.node import Node
from allocator_manager.models.session import SessionStatus
from allocator_manager.services.allocation import eligible_nodes
from allocator_manager.services.node import NodeService
from allocator_manager.services.session import SessionService
from allocator_contract.session import SessionCreate


async def test_create_active_when_satisfiable(db, make_node, make_device, agent):
    node = await make_node()
    await make_device(node, "wifi_0")
    resp = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))
    assert resp.status == "ACTIVE"
    assert resp.node_name == node.name
    assert agent.binds == ["wifi_0"]


async def test_create_pending_when_no_node(db, agent):
    resp = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))
    assert resp.status == "PENDING"
    assert resp.devices == []


async def test_release_frees_devices_and_kicks(db, make_node, make_device, agent, kick_spy):
    node = await make_node()
    dev = await make_device(node, "wifi_0")
    created = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))
    kick_before = kick_spy["n"]

    await SessionService(db).release(created.session_id, "host-a")

    await db.refresh(dev)
    assert dev.status == DeviceStatus.FREE
    assert agent.unbinds == ["wifi_0"]
    assert kick_spy["n"] == kick_before + 1
    released = await SessionService(db).get(created.session_id, "host-a")
    assert released.status == "RELEASED"


async def test_release_rejects_other_client(db, make_node, make_device, agent):
    node = await make_node()
    await make_device(node, "wifi_0")
    created = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))
    with pytest.raises(HTTPException) as exc:
        await SessionService(db).release(created.session_id, "host-b")
    assert exc.value.status_code == 403


async def test_freeze_only_own_active_session(db, make_node, make_device, agent):
    node = await make_node()
    await make_device(node, "wifi_0")
    created = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))

    # not the owner -> 403
    with pytest.raises(HTTPException) as exc:
        await SessionService(db).freeze(created.session_id, "host-b")
    assert exc.value.status_code == 403

    # owner -> node frozen
    await SessionService(db).freeze(created.session_id, "host-a")
    node_row = (await db.execute(select(Node).where(Node.id == node.id))).scalar_one()
    assert node_row.frozen is True


async def test_frozen_node_excluded_then_unfreeze_kicks(db, make_node, make_device, agent, kick_spy):
    node = await make_node()
    await make_device(node, "wifi_0")
    created = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))
    await SessionService(db).freeze(created.session_id, "host-a")

    # a different device set on the frozen node is now ineligible
    await make_device(node, "hid_0", fingerprint="fp-hid")
    assert await eligible_nodes(db, ["hid_0"]) == []

    before = kick_spy["n"]
    await NodeService(db).unfreeze(node.name)
    assert kick_spy["n"] == before + 1
    node_row = (await db.execute(select(Node).where(Node.id == node.id))).scalar_one()
    assert node_row.frozen is False
