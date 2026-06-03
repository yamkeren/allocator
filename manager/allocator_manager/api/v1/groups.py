import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_contract.group import (
    GroupCreate,
    GroupListResponse,
    GroupResponse,
    GroupUpdate,
)
from allocator_manager.services.group import GroupService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupCreate,
    db: AsyncSession = Depends(get_db),
) -> GroupResponse:
    svc = GroupService(db)
    return await svc.create(body)


@router.get("", response_model=GroupListResponse)
async def list_groups(
    db: AsyncSession = Depends(get_db),
) -> GroupListResponse:
    svc = GroupService(db)
    return await svc.list()


@router.get("/{group_name}", response_model=GroupResponse)
async def get_group(
    group_name: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> GroupResponse:
    svc = GroupService(db)
    group = await svc.get_by_name(group_name)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@router.put("/{group_name}", response_model=GroupResponse)
async def update_group(
    body: GroupUpdate,
    group_name: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> GroupResponse:
    svc = GroupService(db)
    return await svc.update(group_name, body)


@router.delete("/{group_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_name: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> None:
    svc = GroupService(db)
    await svc.delete(group_name)
