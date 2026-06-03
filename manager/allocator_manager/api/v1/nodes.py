import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_contract.node import NodeListResponse, NodeResponse
from allocator_manager.services.node import NodeService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.get("", response_model=NodeListResponse)
async def list_nodes(
    db: AsyncSession = Depends(get_db),
) -> NodeListResponse:
    return await NodeService(db).list()


@router.get("/{node_id}", response_model=NodeResponse)
async def get_node(
    node_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> NodeResponse:
    node = await NodeService(db).get(node_id)
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return node


@router.post("/{node}/unfreeze", response_model=NodeResponse)
async def unfreeze_node(
    node: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> NodeResponse:
    """Unfreeze a node so it can be selected for sessions again."""
    return await NodeService(db).unfreeze(node)


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> None:
    await NodeService(db).delete(node_id)
