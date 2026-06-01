import uuid
from datetime import UTC, datetime

import structlog
from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from allocator_manager.models.device import Device, DeviceStatus
from allocator_manager.models.group import Group
from allocator_manager.models.node import Node
from allocator_manager.models.session import Session, SessionStatus
from allocator_manager.models.session_device import SessionDevice, SessionDeviceStatus
from allocator_manager.schemas.session import (
    SessionCreate,
    SessionDeviceAttachInfo,
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
        ))
    return SessionResponse(
        session_id=str(session.id),
        client_id=session.client_id,
        group_name=session.group_name,
        status=session.status.value,
        failure_reason=session.failure_reason,
        devices=device_infos,
        created_at=session.created_at,
    )


class SessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, client_id: str, request: SessionCreate) -> SessionResponse:
        group_result = await self._db.execute(
            select(Group).where(Group.name == request.group_name)
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
            status=SessionStatus.ACTIVE,
        )
        self._db.add(session)
        await self._db.commit()
        await self._db.refresh(session)

        from allocator_manager.services.allocation import AllocationService
        await AllocationService(self._db).allocate(session)

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
            .options(selectinload(Session.session_devices).selectinload(SessionDevice.device))
            .where(Session.client_id == client_id)
        )
        if status:
            q = q.where(Session.status == status)
        if group_name:
            q = q.where(Session.group_name == group_name)
        q = q.order_by(Session.created_at.desc()).limit(limit).offset(offset)
        sessions = (await self._db.execute(q)).scalars().all()
        items = [await self._load_session_response(s) for s in sessions]
        return SessionListResponse(items=items, total=len(items))

    async def release(self, session_id: str, client_id: str) -> None:
        session = await self._load_session(session_id)
        if not session:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Session not found")
        if session.client_id != client_id:
            raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="Not your session")
        if session.status in (SessionStatus.RELEASED, SessionStatus.FAILED):
            return  # idempotent

        now = datetime.now(UTC)
        for sd in session.session_devices:
            if sd.status not in (SessionDeviceStatus.RELEASED, SessionDeviceStatus.ERROR):
                try:
                    from allocator_manager.services.node_client import NodeAgentClient
                    await NodeAgentClient(sd.node_agent_url).unbind(
                        bus_id=sd.usbip_bus_id or "", logical_name=sd.logical_name
                    )
                except Exception as exc:
                    log.warning("unbind_failed_on_release", logical_name=sd.logical_name, error=str(exc))
                sd.status = SessionDeviceStatus.RELEASED
                sd.released_at = now
                sd.updated_at = now
                dev = (await self._db.execute(
                    select(Device).where(Device.id == sd.device_id)
                )).scalar_one_or_none()
                if dev and dev.status != DeviceStatus.ERROR:
                    dev.status = DeviceStatus.FREE
                    dev.updated_at = now

        session.status = SessionStatus.RELEASED
        session.released_at = now
        session.updated_at = now
        await self._db.commit()
        log.info("session_released", session_id=session_id)

    async def _load_session(self, session_id: str) -> Session | None:
        return (await self._db.execute(
            select(Session)
            .options(selectinload(Session.session_devices).selectinload(SessionDevice.device))
            .where(Session.id == uuid.UUID(session_id))
        )).scalar_one_or_none()

    async def _load_session_response(self, session: Session) -> SessionResponse:
        node_ids = {sd.node_id for sd in session.session_devices}
        nodes: dict[str, Node] = {}
        if node_ids:
            nodes = {
                str(n.id): n
                for n in (await self._db.execute(select(Node).where(Node.id.in_(node_ids)))).scalars().all()
            }
        return _session_to_response(session, nodes)
