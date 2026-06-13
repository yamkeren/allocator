"""Global, unscoped read of the whole system for the operator dashboard.

Unlike the client-facing session API (which only ever shows a caller their own
sessions), this assembles every node, its devices, and which client/session holds
each allocated device — the cross-client picture an operator needs. Read-only.
"""

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from allocator_manager.models.node import Node
from allocator_manager.models.session import Session, SessionStatus
from allocator_manager.models.session_device import SessionDeviceStatus

_VERSION = "0.1.0"


class DeviceOwner(BaseModel):
    client_id: str
    session_id: str


class OverviewDevice(BaseModel):
    device_id: str
    logical_name: str
    device_class: str
    status: str
    owner: DeviceOwner | None  # set only for ALLOCATED devices


class OverviewNode(BaseModel):
    node_id: str
    name: str
    hostname: str
    ip_address: str
    status: str
    frozen: bool
    last_heartbeat: datetime | None
    agent_version: str | None
    devices: list[OverviewDevice]


class OverviewSessionDevice(BaseModel):
    logical_name: str
    node_id: str
    device_id: str


class OverviewSession(BaseModel):
    session_id: str
    client_id: str
    node_name: str | None
    status: str
    created_at: datetime
    devices: list[OverviewSessionDevice]


class OverviewPending(BaseModel):
    session_id: str
    client_id: str
    requested_devices: list[str]
    created_at: datetime


class OverviewManager(BaseModel):
    status: str
    version: str


class OverviewResponse(BaseModel):
    manager: OverviewManager
    nodes: list[OverviewNode]
    active_sessions: list[OverviewSession]
    pending_sessions: list[OverviewPending]


class OverviewService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_overview(self) -> OverviewResponse:
        # 1. Nodes + their devices (one secondary IN query, no per-node lazy loads).
        nodes = (await self._db.execute(
            select(Node).options(selectinload(Node.devices)).order_by(Node.name)
        )).scalars().all()

        # 2. Active sessions + their session_devices. SessionDevice already carries
        #    logical_name/node_id/device_id, so we don't load the Device rows.
        active = (await self._db.execute(
            select(Session)
            .options(selectinload(Session.session_devices))
            .where(Session.status == SessionStatus.ACTIVE)
            .order_by(Session.created_at.desc())
        )).scalars().all()

        # 3. Pending sessions (no session_devices yet) — the queue, oldest first.
        pending = (await self._db.execute(
            select(Session)
            .where(Session.status == SessionStatus.PENDING)
            .order_by(Session.created_at)
        )).scalars().all()

        node_name_by_id = {n.id: n.name for n in nodes}

        # Authoritative owner map, built from the same rows that feed the right pane.
        owner_by_device_id: dict = {}
        for s in active:
            for sd in s.session_devices:
                if sd.status == SessionDeviceStatus.ALLOCATED:
                    owner_by_device_id[sd.device_id] = DeviceOwner(
                        client_id=s.client_id, session_id=str(s.id)
                    )

        overview_nodes = [
            OverviewNode(
                node_id=str(n.id),
                name=n.name,
                hostname=n.hostname,
                ip_address=str(n.ip_address),
                status=n.status.value,
                frozen=n.frozen,
                last_heartbeat=n.last_heartbeat,
                agent_version=n.agent_version,
                devices=[
                    OverviewDevice(
                        device_id=str(d.id),
                        logical_name=d.logical_name,
                        device_class=d.device_class.value,
                        status=d.status.value,
                        owner=owner_by_device_id.get(d.id),
                    )
                    for d in sorted(n.devices, key=lambda d: d.logical_name)
                ],
            )
            for n in nodes
        ]

        active_sessions = [
            OverviewSession(
                session_id=str(s.id),
                client_id=s.client_id,
                node_name=node_name_by_id.get(s.node_id),
                status=s.status.value,
                created_at=s.created_at,
                devices=[
                    OverviewSessionDevice(
                        logical_name=sd.logical_name,
                        node_id=str(sd.node_id),
                        device_id=str(sd.device_id),
                    )
                    for sd in s.session_devices
                    if sd.status == SessionDeviceStatus.ALLOCATED
                ],
            )
            for s in active
        ]

        pending_sessions = [
            OverviewPending(
                session_id=str(s.id),
                client_id=s.client_id,
                requested_devices=list(s.requested_devices),
                created_at=s.created_at,
            )
            for s in pending
        ]

        return OverviewResponse(
            manager=OverviewManager(status="healthy", version=_VERSION),
            nodes=overview_nodes,
            active_sessions=active_sessions,
            pending_sessions=pending_sessions,
        )
