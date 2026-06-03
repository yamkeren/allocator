import uuid
from datetime import UTC, datetime

import structlog
from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.models.device import Device, DeviceClass
from allocator_manager.models.device_name import DeviceName
from allocator_manager.models.node import Node
from allocator_contract.device import DeviceCreate, DeviceListResponse, DevicePatch, DeviceResponse
from allocator_manager.services.naming import unique_logical_name

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

    async def _resolve_node(self, ident: str) -> Node:
        try:
            node_uuid = uuid.UUID(ident)
            node = (await self._db.execute(
                select(Node).where(Node.id == node_uuid)
            )).scalar_one_or_none()
        except ValueError:
            node = (await self._db.execute(
                select(Node).where(Node.name == ident)
            )).scalar_one_or_none()
        if not node:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Node not found")
        return node

    async def _get_on_node(self, node_id, logical_name: str) -> Device | None:
        return (await self._db.execute(
            select(Device).where(
                Device.node_id == node_id, Device.logical_name == logical_name
            )
        )).scalar_one_or_none()

    async def create(self, body: DeviceCreate) -> DeviceResponse:
        node = (await self._db.execute(
            select(Node).where(Node.id == uuid.UUID(body.node_id))
        )).scalar_one_or_none()
        if not node:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Node not found")

        if await self._get_on_node(node.id, body.logical_name):
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"Device {body.logical_name!r} already exists on node",
            )

        device = Device(
            node_id=node.id,
            logical_name=body.logical_name,
            vendor_id=body.vendor_id,
            product_id=body.product_id,
            serial=body.serial,
            manufacturer=body.manufacturer,
            product_name=body.product_name,
            mac_address=body.mac_address,
            device_class=DeviceClass(body.device_class) if body.device_class in DeviceClass.__members__ else DeviceClass.GENERIC,
            usbip_bus_id=body.usbip_bus_id,
            fingerprint=body.fingerprint,
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
        q = q.order_by(Device.node_id, Device.logical_name).limit(limit).offset(offset)
        devices = (await self._db.execute(q)).scalars().all()
        return DeviceListResponse(items=[_device_to_response(d) for d in devices], total=len(devices))

    async def get(self, node_ident: str, logical_name: str) -> DeviceResponse | None:
        node = await self._resolve_node(node_ident)
        d = await self._get_on_node(node.id, logical_name)
        return _device_to_response(d) if d else None

    async def patch(self, node_ident: str, logical_name: str, body: DevicePatch) -> DeviceResponse:
        node = await self._resolve_node(node_ident)
        device = await self._get_on_node(node.id, logical_name)
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

    async def rename(
        self, node_ident: str, logical_name: str, new_name: str, force: bool = False
    ) -> DeviceResponse:
        """Set a custom name for a device and remember it (portable by fingerprint).

        If `new_name` is already taken on the node by another device, 409s with a
        structured conflict unless `force` is set, in which case the holder is
        renamed to a fresh generic name and its remembered name cleared.
        """
        node = await self._resolve_node(node_ident)
        device = await self._get_on_node(node.id, logical_name)
        if not device:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")

        if new_name == device.logical_name:
            return _device_to_response(device)

        now = datetime.now(UTC)
        holder = await self._get_on_node(node.id, new_name)
        if holder is not None and holder.id != device.id:
            if not force:
                raise HTTPException(
                    status_code=http_status.HTTP_409_CONFLICT,
                    detail={
                        "error": "name_conflict",
                        "name": new_name,
                        "node": node.name,
                        "holder_logical_name": holder.logical_name,
                    },
                )
            # Force: give the holder a fresh generic name and forget its custom name.
            holder.logical_name = await unique_logical_name(self._db, node.id, holder.device_class)
            holder.updated_at = now
            await self._forget_name(holder.fingerprint)
            await self._db.flush()

        device.logical_name = new_name
        device.updated_at = now
        await self._remember_name(device.fingerprint, new_name)
        await self._db.commit()
        await self._db.refresh(device)
        log.info("device_renamed", node=node.name, logical_name=new_name, fingerprint=device.fingerprint)
        return _device_to_response(device)

    async def delete(self, node_ident: str, logical_name: str) -> None:
        node = await self._resolve_node(node_ident)
        device = await self._get_on_node(node.id, logical_name)
        if not device:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Device not found")
        await self._db.delete(device)
        await self._db.commit()

    async def _remember_name(self, fingerprint: str, name: str) -> None:
        mem = (await self._db.execute(
            select(DeviceName).where(DeviceName.fingerprint == fingerprint)
        )).scalar_one_or_none()
        now = datetime.now(UTC)
        if mem:
            mem.name = name
            mem.updated_at = now
        else:
            self._db.add(DeviceName(fingerprint=fingerprint, name=name))

    async def _forget_name(self, fingerprint: str) -> None:
        mem = (await self._db.execute(
            select(DeviceName).where(DeviceName.fingerprint == fingerprint)
        )).scalar_one_or_none()
        if mem:
            await self._db.delete(mem)
