import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_contract.device import (
    DeviceCreate,
    DeviceListResponse,
    DevicePatch,
    DeviceRename,
    DeviceResponse,
)
from allocator_manager.services.device import DeviceService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.post("", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def register_device(
    body: DeviceCreate,
    db: AsyncSession = Depends(get_db),
) -> DeviceResponse:
    return await DeviceService(db).create(body)


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    node_id: str | None = None,
    device_status: str | None = None,
    device_class: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> DeviceListResponse:
    return await DeviceService(db).list(
        node_id=node_id, status=device_status, device_class=device_class, limit=limit, offset=offset
    )


# Device names are unique per node, so device routes are scoped by node.
@router.get("/{node}/{logical_name}", response_model=DeviceResponse)
async def get_device(
    node: str = Path(...),
    logical_name: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> DeviceResponse:
    device = await DeviceService(db).get(node, logical_name)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


@router.patch("/{node}/{logical_name}", response_model=DeviceResponse)
async def patch_device(
    body: DevicePatch,
    node: str = Path(...),
    logical_name: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> DeviceResponse:
    return await DeviceService(db).patch(node, logical_name, body)


@router.post("/{node}/{logical_name}/rename", response_model=DeviceResponse)
async def rename_device(
    body: DeviceRename,
    node: str = Path(...),
    logical_name: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> DeviceResponse:
    return await DeviceService(db).rename(node, logical_name, body.name, force=body.force)


@router.delete("/{node}/{logical_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    node: str = Path(...),
    logical_name: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> None:
    await DeviceService(db).delete(node, logical_name)
