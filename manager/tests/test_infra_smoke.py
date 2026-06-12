"""Proves the container + migration + db fixture + factories all work."""

from sqlalchemy import select

from allocator_manager.models.node import Node, NodeStatus


async def test_node_round_trips(db, make_node):
    await make_node(name="lab-1")
    rows = (await db.execute(select(Node))).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "lab-1"
    assert rows[0].status == NodeStatus.ONLINE


async def test_truncation_isolates_tests(db):
    # The previous test inserted a node; truncation must have cleared it.
    rows = (await db.execute(select(Node))).scalars().all()
    assert rows == []
