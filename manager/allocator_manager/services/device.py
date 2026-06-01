import uuid
from datetime import UTC, datetime

import structlog
from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.models.device import Device, DeviceClass
from allocator_manager.models.node import Node
from allocator_manager.schemas.device import DeviceCreate, DeviceListResponse, DevicePatch, DeviceResponse

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
        created_at=d.created_at,
    )


class DeviceService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, body: DeviceCreate) -> DeviceResponse:
        node = (await self._db.execute(
            select(Node).where(Node.id == uuid.UUID(body.node_id))
        )).scalar_one_or_none()
        if not node:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Node not found")

        existing = (await self._db.execute(
            select(Device).where(Device.logical_name == body.logical_name)
        )).scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"Device {body.logical_name!r} already exists",
            )

        device = Device(
            node_id=uuid.UUID(body.node_id),
            logical_name=body.logical_name,
            vendor_id=body.vendor_id,
            product_id=body.product_id,
            serial=body.serial,
            manufacturer=body.manufacturer,
            product_name=body.product_name,
            mac_address=body.mac_address,
            device_class=DeviceClass(body.device_class) if body.device_class in DeviceClass.__members__ else DeviceClass.GENERIC,
            usbip_bus_id=body.usbip_bus_id,
        )
        self._db.add(device)
        await self._db.commit()
        await self._db.refresh(device)
        log.info("device_registered", logical_name=body.logical_name, node_id=body.node_id)
        return _device_to_response(device)

    async def list(
        self,
        node_id: str | None = None,
        status: str | None = None,
        device_class: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> DeviceListResponse:
        q = select(Device)
        if node_id:
            q = q.where(Device.node_id == uuid.UUID(node_id))
        if status:
            q = q.where(Device.status == status)
        if device_class:
            q = q.where(Device.device_class == device_class)
        q = q.order_by(Device.logical_name).limit(limit).offset(offset)
        devices = (await self._db.execute(q)).scalars().all()
        return DeviceListResponse(items=[_device_to_response(d) for d in devices], total=len(devices))

    async def get_by_logical_name(self, logical_name: str) -> DeviceResponse | None:
        d = (await self._db.execute(
            select(Device).where(Device.logical_name == logical_name)
        )).scalar_one_or_none()
        return _device_to_response(d) if d else None

    async def patch(self, logical_name: str, body: DevicePatch) -> DeviceResponse:
        device = (await self._db.execute(
            select(Device).where(Device.logical_name == logical_name)
        )).scalar_one_or_none()
        if not device:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")
        if body.device_class:
            device.device_class = DeviceClass(body.device_class)
        if body.usbip_bus_id is not None:
            device.usbip_bus_id = body.usbip_bus_id
        device.updated_at = datetime.now(UTC)
        await self._db.commit()
        await self._db.refresh(device)
        return _device_to_response(device)

    async def delete(self, logical_name: str) -> None:
        device = (await self._db.execute(
            select(Device).where(Device.logical_name == logical_name)
        )).scalar_one_or_none()
        if not device:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")
        await self._db.delete(device)
        await self._db.commit()
