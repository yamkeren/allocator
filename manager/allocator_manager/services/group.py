import uuid
from datetime import UTC, datetime

import structlog
from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from allocator_manager.models.device import Device, DeviceStatus
from allocator_manager.models.group import Group, GroupDevice
from allocator_manager.models.session import Session, SessionStatus
from allocator_manager.schemas.group import (
    GroupCreate,
    GroupDeviceEntry,
    GroupListResponse,
    GroupResponse,
    GroupUpdate,
)

log = structlog.get_logger(__name__)


def _group_to_response(group: Group, devices: list[Device], active_sessions: int) -> GroupResponse:
    available = all(d.status == DeviceStatus.FREE for d in devices)
    return GroupResponse(
        group_id=str(group.id),
        name=group.name,
        description=group.description,
        device_count=len(devices),
        available=available,
        active_sessions=active_sessions,
        devices=[
            GroupDeviceEntry(
                logical_name=d.logical_name,
                device_id=str(d.id),
                node_id=str(d.node_id),
                device_class=d.device_class.value,
            )
            for d in devices
        ],
        created_at=group.created_at,
    )


class GroupService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _load_group_devices(self, group: Group) -> list[Device]:
        device_ids = [gd.device_id for gd in group.devices]
        if not device_ids:
            return []
        return (await self._db.execute(select(Device).where(Device.id.in_(device_ids)))).scalars().all()

    async def _count_active_sessions(self, group_id: uuid.UUID) -> int:
        return len((await self._db.execute(
            select(Session).where(Session.group_id == group_id, Session.status == SessionStatus.ACTIVE)
        )).scalars().all())

    async def create(self, body: GroupCreate) -> GroupResponse:
        found_devices = (await self._db.execute(
            select(Device).where(Device.logical_name.in_(body.devices))
        )).scalars().all()
        found_names = {d.logical_name for d in found_devices}
        missing = set(body.devices) - found_names
        if missing:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown device logical names: {sorted(missing)}",
            )

        group = Group(name=body.name, description=body.description)
        self._db.add(group)
        await self._db.flush()

        for i, logical_name in enumerate(body.devices):
            device = next(d for d in found_devices if d.logical_name == logical_name)
            self._db.add(GroupDevice(group_id=group.id, device_id=device.id, ordinal=i))

        await self._db.commit()
        await self._db.refresh(group, ["devices"])
        devices = await self._load_group_devices(group)
        log.info("group_created", group_id=str(group.id), name=group.name)
        return _group_to_response(group, devices, active_sessions=0)

    async def get_by_name(self, name: str) -> GroupResponse | None:
        group = (await self._db.execute(
            select(Group).options(selectinload(Group.devices)).where(Group.name == name)
        )).scalar_one_or_none()
        if not group:
            return None
        devices = await self._load_group_devices(group)
        return _group_to_response(group, devices, await self._count_active_sessions(group.id))

    async def list(self) -> GroupListResponse:
        groups = (await self._db.execute(
            select(Group).options(selectinload(Group.devices)).order_by(Group.name)
        )).scalars().all()
        items = []
        for group in groups:
            devices = await self._load_group_devices(group)
            items.append(_group_to_response(group, devices, await self._count_active_sessions(group.id)))
        return GroupListResponse(items=items, total=len(items))

    async def update(self, name: str, body: GroupUpdate) -> GroupResponse:
        group = (await self._db.execute(
            select(Group).options(selectinload(Group.devices)).where(Group.name == name)
        )).scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Group not found")

        active = await self._count_active_sessions(group.id)
        if active > 0 and body.devices is not None:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="Cannot modify devices of a group with active sessions",
            )

        if body.description is not None:
            group.description = body.description

        if body.devices is not None:
            found_devices = (await self._db.execute(
                select(Device).where(Device.logical_name.in_(body.devices))
            )).scalars().all()
            found_names = {d.logical_name for d in found_devices}
            missing = set(body.devices) - found_names
            if missing:
                raise HTTPException(
                    status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Unknown device logical names: {sorted(missing)}",
                )
            for gd in group.devices:
                await self._db.delete(gd)
            await self._db.flush()
            for i, logical_name in enumerate(body.devices):
                device = next(d for d in found_devices if d.logical_name == logical_name)
                self._db.add(GroupDevice(group_id=group.id, device_id=device.id, ordinal=i))

        group.updated_at = datetime.now(UTC)
        await self._db.commit()
        await self._db.refresh(group, ["devices"])
        devices = await self._load_group_devices(group)
        return _group_to_response(group, devices, await self._count_active_sessions(group.id))

    async def delete(self, name: str) -> None:
        group = (await self._db.execute(
            select(Group).where(Group.name == name)
        )).scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Group not found")
        if await self._count_active_sessions(group.id) > 0:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="Cannot delete group with active sessions",
            )
        await self._db.delete(group)
        await self._db.commit()
