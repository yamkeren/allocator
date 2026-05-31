import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.models.device import Device, DeviceClass
from allocator_manager.schemas.device import DeviceListResponse, DevicePatch, DeviceResponse

log = structlog.get_logger(__name__)


def _device_to_response(d: Device) -> DeviceResponse:
    return DeviceResponse(
        device_id=str(d.id),
        node_id=str(d.node_id),
        logical_name=d.logical_name,
        vendor_id=d.vendor_id,
        product_id=d.product_id,
        serial=d.serial,
        manufacturer=d.manufacturer,
        product_name=d.product_name,
        mac_address=d.mac_address,
        device_class=d.device_class.value,
        status=d.status.value,
        usbip_bus_id=d.usbip_bus_id,
        fingerprint=d.fingerprint,
        extra_metadata=d.extra_metadata,
        created_at=d.created_at,
    )


class DeviceService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list(
        self,
        node_id: str | None = None,
        status: str | None = None,
        device_class: str | None = None,
        logical_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> DeviceListResponse:
        q = select(Device).where(Device.deleted_at.is_(None))
        if node_id:
            q = q.where(Device.node_id == uuid.UUID(node_id))
        if status:
            q = q.where(Device.status == status)
        if device_class:
            q = q.where(Device.device_class == device_class)
        if logical_name:
            q = q.where(Device.logical_name.ilike(f"%{logical_name}%"))
        q = q.order_by(Device.logical_name).limit(limit).offset(offset)
        result = await self._db.execute(q)
        devices = result.scalars().all()
        return DeviceListResponse(items=[_device_to_response(d) for d in devices], total=len(devices))

    async def get_by_logical_name(self, logical_name: str) -> DeviceResponse | None:
        result = await self._db.execute(
            select(Device).where(
                Device.logical_name == logical_name, Device.deleted_at.is_(None)
            )
        )
        d = result.scalar_one_or_none()
        return _device_to_response(d) if d else None

    async def patch(self, logical_name: str, body: DevicePatch) -> DeviceResponse:
        result = await self._db.execute(
            select(Device).where(
                Device.logical_name == logical_name, Device.deleted_at.is_(None)
            )
        )
        device = result.scalar_one_or_none()
        if not device:
            from fastapi import HTTPException, status as http_status
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")

        if body.device_class:
            device.device_class = DeviceClass(body.device_class)
        if body.extra_metadata is not None:
            device.extra_metadata = {**device.extra_metadata, **body.extra_metadata}
        device.updated_at = datetime.now(UTC)
        await self._db.commit()
        await self._db.refresh(device)
        return _device_to_response(device)
