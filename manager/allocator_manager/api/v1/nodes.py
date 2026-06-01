import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_manager.middleware.auth import require_api_key
from allocator_manager.schemas.node import NodeListResponse, NodeResponse
from allocator_manager.services.node import NodeService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.get("", response_model=NodeListResponse)
async def list_nodes(
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> NodeListResponse:
    return await NodeService(db).list()


@router.get("/{node_id}", response_model=NodeResponse)
async def get_node(
    node_id: str = Path(...),
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> NodeResponse:
    node = await NodeService(db).get(node_id)
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return node


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: str = Path(...),
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> None:
    await NodeService(db).delete(node_id)
