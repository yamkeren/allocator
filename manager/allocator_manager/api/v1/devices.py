import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_manager.middleware.auth import require_api_key
from allocator_manager.schemas.device import DeviceCreate, DeviceListResponse, DevicePatch, DeviceResponse
from allocator_manager.services.device import DeviceService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.post("", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def register_device(
    body: DeviceCreate,
    _: str = Depends(require_api_key),
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
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> DeviceListResponse:
    return await DeviceService(db).list(
        node_id=node_id, status=device_status, device_class=device_class, limit=limit, offset=offset
    )


@router.get("/{logical_name}", response_model=DeviceResponse)
async def get_device(
    logical_name: str = Path(...),
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> DeviceResponse:
    device = await DeviceService(db).get_by_logical_name(logical_name)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


@router.patch("/{logical_name}", response_model=DeviceResponse)
async def patch_device(
    body: DevicePatch,
    logical_name: str = Path(...),
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> DeviceResponse:
    return await DeviceService(db).patch(logical_name, body)


@router.delete("/{logical_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    logical_name: str = Path(...),
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> None:
    await DeviceService(db).delete(logical_name)
