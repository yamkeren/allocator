import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from allocator_manager.config import settings
from allocator_manager.models.device import Device, DeviceStatus
from allocator_manager.models.group import Group
from allocator_manager.models.node import Node
from allocator_manager.models.session import Session, SessionStatus
from allocator_manager.models.session_device import SessionDevice, SessionDeviceStatus
from allocator_manager.schemas.session import (
    SessionCreate,
    SessionDeviceAttachInfo,
    SessionHeartbeatResponse,
    SessionListResponse,
    SessionResponse,
)

log = structlog.get_logger(__name__)


def _build_attach_command(node_ip: str, bus_id: str | None) -> str | None:
    if not bus_id:
        return None
    return f"usbip attach -r {node_ip} -b {bus_id}"


def _session_to_response(session: Session, nodes: dict) -> SessionResponse:
    device_infos = []
    for sd in session.session_devices:
        node = nodes.get(str(sd.node_id))
        device_infos.append(SessionDeviceAttachInfo(
            logical_name=sd.logical_name,
            node_id=str(sd.node_id),
            node_hostname=node.hostname if node else "",
            node_ip=str(node.ip_address) if node else "",
            usbip_bus_id=sd.usbip_bus_id,
            usbip_attach_command=_build_attach_command(
                str(node.ip_address) if node else "", sd.usbip_bus_id
            ),
            device_class=sd.device.device_class.value if sd.device else "GENERIC",
            status=sd.status.value,
        ))
    return SessionResponse(
        session_id=str(session.id),
        client_id=session.client_id,
        group_name=session.group_name,
        status=session.status.value,
        lease_duration=session.lease_duration,
        lease_expires_at=session.lease_expires_at,
        last_heartbeat=session.last_heartbeat,
        failure_reason=session.failure_reason,
        devices=device_infos,
        created_at=session.created_at,
    )


class SessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, client_id: str, request: SessionCreate) -> SessionResponse:
        # Resolve group
        group_result = await self._db.execute(
            select(Group).where(Group.name == request.group_name, Group.deleted_at.is_(None))
        )
        group = group_result.scalar_one_or_none()
        if not group:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Group {request.group_name!r} not found",
            )

        session = Session(
            client_id=client_id,
            group_id=group.id,
            group_name=group.name,
            lease_duration=request.lease_duration,
            client_metadata=request.metadata,
            status=SessionStatus.PENDING,
        )
        self._db.add(session)
        await self._db.commit()
        await self._db.refresh(session)

        # Run allocation synchronously (with timeout)
        from allocator_manager.services.allocation import AllocationService
        alloc = AllocationService(self._db)
        try:
            await asyncio.wait_for(
                alloc.allocate(session),
                timeout=settings.default_lease_duration * 0.01 + 30,
            )
        except asyncio.TimeoutError:
            log.warning("allocation_timeout", session_id=str(session.id))
            # Session remains in RESERVING/BINDING — background task will clean it up

        await self._db.refresh(session, ["session_devices"])
        return await self._load_session_response(session)

    async def get(self, session_id: str, client_id: str) -> SessionResponse | None:
        session = await self._load_session(session_id)
        if not session:
            return None
        return await self._load_session_response(session)

    async def list(
        self,
        client_id: str,
        status: str | None,
        group_name: str | None,
        limit: int,
        offset: int,
    ) -> SessionListResponse:
        q = (
            select(Session)
            .options(
                selectinload(Session.session_devices).selectinload(SessionDevice.device)
            )
            .where(Session.client_id == client_id)
        )
        if status:
            q = q.where(Session.status == status)
        if group_name:
            q = q.where(Session.group_name == group_name)
        q = q.order_by(Session.created_at.desc()).limit(limit).offset(offset)
        result = await self._db.execute(q)
        sessions = result.scalars().all()

        items = []
        for s in sessions:
            items.append(await self._load_session_response(s))
        return SessionListResponse(items=items, total=len(items))

    async def release(self, session_id: str, client_id: str) -> None:
        session = await self._load_session(session_id)
        if not session:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Session not found")
        if session.client_id != client_id:
            raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="Not your session")
        if session.status in (SessionStatus.RELEASED, SessionStatus.FAILED, SessionStatus.RELEASING):
            return  # idempotent

        session.status = SessionStatus.RELEASING
        session.updated_at = datetime.now(UTC)
        await self._db.commit()

        # TODO Phase 6: trigger full unbind sequence via AllocationService
        # For now: fast-path release (devices → FREE, session → RELEASED)
        await self._fast_release(session)

    async def heartbeat(self, session_id: str, client_id: str) -> SessionHeartbeatResponse:
        session = await self._load_session(session_id)
        if not session:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Session not found")
        if session.status != SessionStatus.ACTIVE:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"Session is {session.status.value}, not ACTIVE",
            )

        now = datetime.now(UTC)
        session.last_heartbeat = now
        session.lease_expires_at = now + timedelta(seconds=session.lease_duration)
        session.updated_at = now
        await self._db.commit()

        return SessionHeartbeatResponse(
            session_id=session_id,
            lease_expires_at=session.lease_expires_at,
            status=session.status.value,
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    async def _load_session(self, session_id: str) -> Session | None:
        result = await self._db.execute(
            select(Session)
            .options(
                selectinload(Session.session_devices).selectinload(SessionDevice.device)
            )
            .where(Session.id == uuid.UUID(session_id))
        )
        return result.scalar_one_or_none()

    async def _load_session_response(self, session: Session) -> SessionResponse:
        node_ids = {sd.node_id for sd in session.session_devices}
        nodes: dict[str, Node] = {}
        if node_ids:
            result = await self._db.execute(select(Node).where(Node.id.in_(node_ids)))
            nodes = {str(n.id): n for n in result.scalars().all()}
        return _session_to_response(session, nodes)

    async def _fast_release(self, session: Session) -> None:
        """Release all reserved/bound devices back to FREE and close the session.

        Phase 4 will replace this with real usbip unbind calls.
        """
        now = datetime.now(UTC)
        for sd in session.session_devices:
            if sd.status not in (SessionDeviceStatus.RELEASED, SessionDeviceStatus.ERROR):
                sd.status = SessionDeviceStatus.RELEASED
                sd.released_at = now
                sd.updated_at = now
                # Free the device
                dev_result = await self._db.execute(
                    select(Device).where(Device.id == sd.device_id)
                )
                device = dev_result.scalar_one_or_none()
                if device and device.status != DeviceStatus.ERROR:
                    device.status = DeviceStatus.FREE
                    device.updated_at = now

        session.status = SessionStatus.RELEASED
        session.released_at = now
        session.updated_at = now
        await self._db.commit()
        log.info("session_released", session_id=str(session.id))
