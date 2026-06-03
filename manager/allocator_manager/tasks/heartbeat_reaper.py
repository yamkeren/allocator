from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import update

from allocator_manager.config import settings
from allocator_manager.database import AsyncSessionLocal
from allocator_manager.models.node import Node, NodeStatus

log = structlog.get_logger(__name__)


async def reap_stale_nodes() -> None:
    """Mark nodes OFFLINE when their heartbeat has timed out."""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.node_offline_timeout)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(Node)
            .where(Node.status == NodeStatus.ONLINE, Node.last_heartbeat < cutoff)
            .values(status=NodeStatus.OFFLINE, updated_at=datetime.now(UTC))
            .returning(Node.id, Node.name)
        )
        stale = result.fetchall()
        if stale:
            await db.commit()
            for row in stale:
                log.warning("node_marked_offline", node_id=str(row.id), node_name=row.name)
