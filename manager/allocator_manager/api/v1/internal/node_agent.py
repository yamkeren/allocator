"""Endpoints called by node agents (registration, device sync, hotplug events)."""

import structlog
from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_manager.middleware.auth import require_agent_secret
from allocator_manager.schemas.node import (
    DeviceEventPayload,
    DeviceSyncPayload,
    DeviceSyncResponse,
    NodeRegisterPayload,
    NodeRegisterResponse,
)
from allocator_manager.services.node import NodeService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.post(
    "/register",
    response_model=NodeRegisterResponse,
    dependencies=[Depends(require_agent_secret)],
)
async def register_node(
    body: NodeRegisterPayload,
    db: AsyncSession = Depends(get_db),
) -> NodeRegisterResponse:
    svc = NodeService(db)
    return await svc.register(body)


@router.post(
    "/{node_id}/devices/sync",
    response_model=DeviceSyncResponse,
    dependencies=[Depends(require_agent_secret)],
)
async def sync_devices(
    body: DeviceSyncPayload,
    node_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> DeviceSyncResponse:
    svc = NodeService(db)
    return await svc.sync_devices(node_id=node_id, payload=body)


@router.post(
    "/{node_id}/device-event",
    dependencies=[Depends(require_agent_secret)],
)
async def device_event(
    body: DeviceEventPayload,
    node_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    svc = NodeService(db)
    await svc.handle_device_event(node_id=node_id, payload=body)
    return {"acknowledged": True}
