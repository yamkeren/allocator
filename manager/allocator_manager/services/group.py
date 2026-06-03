import uuid
from datetime import UTC, datetime

import structlog
from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from allocator_manager.models.group import Group, GroupDevice
from allocator_manager.models.session import Session, SessionStatus
from allocator_contract.group import (
    GroupCreate,
    GroupListResponse,
    GroupResponse,
    GroupUpdate,
)
from allocator_manager.services.allocation import eligible_nodes

log = structlog.get_logger(__name__)


def _ordered_names(group: Group) -> list[str]:
    return [gd.logical_name for gd in sorted(group.devices, key=lambda gd: gd.ordinal)]


class GroupService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _to_response(self, group: Group, active_sessions: int) -> GroupResponse:
        names = _ordered_names(group)
        available = bool(await eligible_nodes(self._db, names)) if names else False
        return GroupResponse(
            group_id=str(group.id),
            name=group.name,
            description=group.description,
            device_count=len(names),
            available=available,
            active_sessions=active_sessions,
            devices=names,
            created_at=group.created_at,
        )

    async def _count_active_sessions(self, group_id: uuid.UUID) -> int:
        return len((await self._db.execute(
            select(Session).where(Session.group_id == group_id, Session.status == SessionStatus.ACTIVE)
        )).scalars().all())

    async def create(self, body: GroupCreate) -> GroupResponse:
        # A group is a name template; names are resolved to devices at session
        # time, so we accept any names without requiring they exist yet.
        group = Group(name=body.name, description=body.description)
        self._db.add(group)
        await self._db.flush()

        for i, logical_name in enumerate(body.devices):
            self._db.add(GroupDevice(group_id=group.id, logical_name=logical_name, ordinal=i))

        await self._db.commit()
        await self._db.refresh(group, ["devices"])
        log.info("group_created", group_id=str(group.id), name=group.name)
        return await self._to_response(group, active_sessions=0)

    async def get_by_name(self, name: str) -> GroupResponse | None:
        group = (await self._db.execute(
            select(Group).options(selectinload(Group.devices)).where(Group.name == name)
        )).scalar_one_or_none()
        if not group:
            return None
        return await self._to_response(group, await self._count_active_sessions(group.id))

    async def list(self) -> GroupListResponse:
        groups = (await self._db.execute(
            select(Group).options(selectinload(Group.devices)).order_by(Group.name)
        )).scalars().all()
        items = [
            await self._to_response(group, await self._count_active_sessions(group.id))
            for group in groups
        ]
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
            for gd in group.devices:
                await self._db.delete(gd)
            await self._db.flush()
            for i, logical_name in enumerate(body.devices):
                self._db.add(GroupDevice(group_id=group.id, logical_name=logical_name, ordinal=i))

        group.updated_at = datetime.now(UTC)
        await self._db.commit()
        await self._db.refresh(group, ["devices"])
        return await self._to_response(group, await self._count_active_sessions(group.id))

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
        # Remove non-active session history first — sessions.group_id is a FK
        # with no ON DELETE, so it would otherwise block the group delete.
        # (Active sessions were already excluded above.) session_devices cascade.
        old_sessions = (await self._db.execute(
            select(Session).where(Session.group_id == group.id)
        )).scalars().all()
        for session in old_sessions:
            await self._db.delete(session)
        await self._db.delete(group)
        await self._db.commit()
