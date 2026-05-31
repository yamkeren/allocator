"""Endpoint for node agent heartbeats."""

import structlog
from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_manager.middleware.auth import require_agent_secret
from allocator_manager.schemas.node import NodeHeartbeatPayload, NodeHeartbeatResponse
from allocator_manager.services.node import NodeService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.post(
    "/{node_id}/heartbeat",
    response_model=NodeHeartbeatResponse,
    dependencies=[Depends(require_agent_secret)],
)
async def node_heartbeat(
    body: NodeHeartbeatPayload,
    node_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> NodeHeartbeatResponse:
    svc = NodeService(db)
    return await svc.heartbeat(node_id=node_id, payload=body)
