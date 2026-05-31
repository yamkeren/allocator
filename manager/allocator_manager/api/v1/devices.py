import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_manager.middleware.auth import require_api_key
from allocator_manager.schemas.device import DeviceListResponse, DevicePatch, DeviceResponse
from allocator_manager.services.device import DeviceService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    node_id: str | None = None,
    device_status: str | None = None,
    device_class: str | None = None,
    logical_name: str | None = None,
    limit: int = 100,
    offset: int = 0,
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> DeviceListResponse:
    svc = DeviceService(db)
    return await svc.list(
        node_id=node_id,
        status=device_status,
        device_class=device_class,
        logical_name=logical_name,
        limit=limit,
        offset=offset,
    )


@router.get("/{logical_name}", response_model=DeviceResponse)
async def get_device(
    logical_name: str = Path(...),
    _: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> DeviceResponse:
    svc = DeviceService(db)
    device = await svc.get_by_logical_name(logical_name)
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
    svc = DeviceService(db)
    return await svc.patch(logical_name, body)
