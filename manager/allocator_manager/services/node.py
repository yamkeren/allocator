import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.models.node import Node, NodeStatus
from allocator_manager.schemas.node import (
    NodeHeartbeatPayload,
    NodeHeartbeatResponse,
    NodeListResponse,
    NodeRegisterPayload,
    NodeRegisterResponse,
    NodeResponse,
)

log = structlog.get_logger(__name__)


def _node_to_response(node: Node) -> NodeResponse:
    return NodeResponse(
        node_id=str(node.id),
        name=node.name,
        hostname=node.hostname,
        ip_address=str(node.ip_address),
        agent_port=node.agent_port,
        agent_url=node.agent_url,
        status=node.status.value,
        last_heartbeat=node.last_heartbeat,
        agent_version=node.agent_version,
        created_at=node.created_at,
    )


class NodeService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def register(self, payload: NodeRegisterPayload) -> NodeRegisterResponse:
        agent_url = f"http://{payload.ip_address}:{payload.agent_port}"
        node = (await self._db.execute(
            select(Node).where(Node.name == payload.name)
        )).scalar_one_or_none()
        now = datetime.now(UTC)

        if node:
            node.hostname = payload.hostname
            node.ip_address = payload.ip_address
            node.agent_port = payload.agent_port
            node.agent_url = agent_url
            node.agent_version = payload.agent_version
            node.status = NodeStatus.ONLINE
            node.last_heartbeat = now
            node.updated_at = now
        else:
            node = Node(
                name=payload.name,
                hostname=payload.hostname,
                ip_address=payload.ip_address,
                agent_port=payload.agent_port,
                agent_url=agent_url,
                agent_version=payload.agent_version,
                status=NodeStatus.ONLINE,
                last_heartbeat=now,
            )
            self._db.add(node)

        await self._db.commit()
        await self._db.refresh(node)
        log.info("node_registered", node_id=str(node.id), name=node.name)
        return NodeRegisterResponse(node_id=str(node.id), registered=True)

    async def heartbeat(self, node_id: str, payload: NodeHeartbeatPayload) -> NodeHeartbeatResponse:
        node = (await self._db.execute(
            select(Node).where(Node.id == uuid.UUID(node_id))
        )).scalar_one_or_none()
        if node:
            node.last_heartbeat = datetime.now(UTC)
            node.status = NodeStatus.ONLINE
            if payload.agent_version:
                node.agent_version = payload.agent_version
            node.updated_at = datetime.now(UTC)
            await self._db.commit()
        return NodeHeartbeatResponse(acknowledged=True)

    async def list(self) -> NodeListResponse:
        nodes = (await self._db.execute(select(Node).order_by(Node.name))).scalars().all()
        return NodeListResponse(items=[_node_to_response(n) for n in nodes], total=len(nodes))

    async def get(self, node_id: str) -> NodeResponse | None:
        node = (await self._db.execute(
            select(Node).where(Node.id == uuid.UUID(node_id))
        )).scalar_one_or_none()
        return _node_to_response(node) if node else None

    async def delete(self, node_id: str) -> None:
        node = (await self._db.execute(
            select(Node).where(Node.id == uuid.UUID(node_id))
        )).scalar_one_or_none()
        if node:
            node.status = NodeStatus.OFFLINE
            node.updated_at = datetime.now(UTC)
            await self._db.commit()
