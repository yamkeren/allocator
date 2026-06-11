"""Core allocation algorithm (single-node model).

A session requests a list of logical names. It is satisfied by ONE node that has
a FREE device for every name. Among eligible nodes we pick the one with the
fewest TOTAL devices (least "additional" hardware tied up). If no node is
eligible the session is left PENDING and the queue processor retries it later.

Phase 1 — RESERVATION (single DB transaction): lock the chosen node's matched
devices, re-validate FREE, mark ALLOCATED, insert session_devices.
Phase 2 — BINDING (saga, outside the txn): usbip bind each device on the node;
roll back on failure.
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.models.device import Device, DeviceStatus
from allocator_manager.models.node import Node, NodeStatus
from allocator_manager.models.session import Session, SessionStatus
from allocator_manager.models.session_device import SessionDevice, SessionDeviceStatus

log = structlog.get_logger(__name__)


class AllocationError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


class _DeviceRaced(Exception):
    """A chosen device stopped being FREE between selection and locking.

    Raised inside the reservation savepoint so it rolls back cleanly; the
    caller treats it as "not available now" and queues the session.
    """


async def eligible_nodes(
    db: AsyncSession,
    names: list[str],
    requested_node_id=None,
) -> list[tuple[Node, dict[str, Device]]]:
    """Nodes that can satisfy all `names` right now, ranked best-first.

    Eligible = ONLINE, not frozen, and a FREE device exists for every name.
    Ranked by fewest total devices on the node (then node id for determinism).
    Returns (node, {name: device}) tuples. Read-only — no locking.
    """
    if not names:
        return []
    node_q = select(Node).where(Node.status == NodeStatus.ONLINE, Node.frozen.is_(False))
    if requested_node_id is not None:
        node_q = node_q.where(Node.id == requested_node_id)
    nodes = (await db.execute(node_q)).scalars().all()

    ranked: list[tuple[int, Node, dict[str, Device]]] = []
    for node in nodes:
        free_devs = (await db.execute(
            select(Device).where(
                Device.node_id == node.id,
                Device.status == DeviceStatus.FREE,
                Device.logical_name.in_(names),
            )
        )).scalars().all()
        by_name: dict[str, Device] = {}
        for d in free_devs:
            by_name.setdefault(d.logical_name, d)
        if not all(n in by_name for n in names):
            continue
        total = (await db.execute(
            select(func.count()).select_from(Device).where(Device.node_id == node.id)
        )).scalar_one()
        ranked.append((total, node, {n: by_name[n] for n in names}))

    ranked.sort(key=lambda r: (r[0], str(r[1].id)))
    return [(node, matched) for _, node, matched in ranked]


class AllocationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def allocate(self, session: Session) -> bool:
        """Try to drive a PENDING session to ACTIVE.

        Returns True if the session reached a terminal-for-the-queue state
        (ACTIVE or FAILED), False if it remains PENDING (no eligible node yet).
        """
        names = list(session.requested_devices)
        if not names:
            await self._fail_session(session, "no_devices", "Session requested no devices")
            return True

        session_devices = await self._reserve(session, names)
        if session_devices is None:
            session.status = SessionStatus.PENDING
            session.updated_at = datetime.now(UTC)
            await self._db.commit()
            log.info("session_queued", session_id=str(session.id), devices=names)
            return False

        try:
            await self._bind(session, session_devices)
        except AllocationError:
            # _bind already rolled back and marked the session FAILED.
            pass
        return True

    async def _reserve(self, session: Session, names: list[str]) -> list[SessionDevice] | None:
        # Read-only candidate selection (no savepoint needed).
        ranked = await eligible_nodes(self._db, names, session.requested_node_id)
        if not ranked:
            return None
        node, matched = ranked[0]

        # Reserve under a savepoint. A NOWAIT lock failure (another reservation
        # holds a device) or a lost race (device no longer FREE) must propagate
        # OUT of begin_nested so the savepoint is rolled back cleanly; we then
        # treat it as "not available right now" and let the caller queue.
        session_devices: list[SessionDevice] = []
        try:
            async with self._db.begin_nested():
                locked = (await self._db.execute(
                    select(Device)
                    .where(Device.id.in_([d.id for d in matched.values()]))
                    .with_for_update(nowait=True)
                )).scalars().all()
                if any(d.status != DeviceStatus.FREE for d in locked):
                    raise _DeviceRaced()

                now = datetime.now(UTC)
                session.node_id = node.id
                session.updated_at = now
                for name in names:
                    device = matched[name]
                    device.status = DeviceStatus.ALLOCATED
                    device.updated_at = now
                    sd = SessionDevice(
                        session_id=session.id,
                        device_id=device.id,
                        logical_name=name,
                        node_id=node.id,
                        node_agent_url=node.agent_url,
                        usbip_bus_id=device.usbip_bus_id,
                        status=SessionDeviceStatus.ALLOCATED,
                    )
                    self._db.add(sd)
                    session_devices.append(sd)
                await self._db.flush()
        except (DBAPIError, _DeviceRaced):
            return None

        log.info(
            "reservation_committed",
            session_id=str(session.id),
            node_id=str(node.id),
            device_count=len(session_devices),
        )
        return session_devices

    async def _bind(self, session: Session, session_devices: list[SessionDevice]) -> None:
        await self._db.commit()

        bound: list[SessionDevice] = []
        failed_sd: SessionDevice | None = None
        fail_detail = ""

        for sd in sorted(session_devices, key=lambda x: str(x.device_id)):
            try:
                bus_id = await self._call_bind(sd)
                now = datetime.now(UTC)
                sd.usbip_bus_id = bus_id
                sd.updated_at = now
                dev_result = await self._db.execute(select(Device).where(Device.id == sd.device_id))
                dev_result.scalar_one().usbip_bus_id = bus_id
                await self._db.commit()
                bound.append(sd)
                log.info("device_bound", logical_name=sd.logical_name, bus_id=bus_id)
            except Exception as exc:
                fail_detail = str(exc)
                failed_sd = sd
                log.error("bind_failed", logical_name=sd.logical_name, error=fail_detail)
                break

        if failed_sd is not None:
            await self._rollback(session, bound, failed_sd, fail_detail)
            raise AllocationError("bind_failed", fail_detail)

        session.status = SessionStatus.ACTIVE
        session.updated_at = datetime.now(UTC)
        await self._db.commit()
        log.info("session_activated", session_id=str(session.id))

    async def _call_bind(self, sd: SessionDevice) -> str:
        from allocator_manager.services.node_client import NodeAgentClient
        result = await NodeAgentClient(sd.node_agent_url).bind(
            bus_id=sd.usbip_bus_id or "",
            logical_name=sd.logical_name,
            session_id=str(sd.session_id),
        )
        return result.bus_id

    async def _rollback(
        self,
        session: Session,
        bound: list[SessionDevice],
        failed_sd: SessionDevice,
        fail_detail: str,
    ) -> None:
        now = datetime.now(UTC)
        for sd in reversed(bound):
            try:
                from allocator_manager.services.node_client import NodeAgentClient
                await NodeAgentClient(sd.node_agent_url).unbind(
                    bus_id=sd.usbip_bus_id or "", logical_name=sd.logical_name
                )
                sd.status = SessionDeviceStatus.RELEASED
                sd.released_at = now
                sd.updated_at = now
                dev = (await self._db.execute(select(Device).where(Device.id == sd.device_id))).scalar_one()
                dev.status = DeviceStatus.FREE
                dev.updated_at = now
            except Exception as exc:
                sd.status = SessionDeviceStatus.ERROR
                sd.error_detail = str(exc)
                sd.updated_at = now
                dev = (await self._db.execute(select(Device).where(Device.id == sd.device_id))).scalar_one()
                dev.status = DeviceStatus.ERROR
                dev.updated_at = now
                log.error("rollback_unbind_failed", logical_name=sd.logical_name, error=str(exc))

        # The device that failed to bind was never bound — free it
        failed_dev = (await self._db.execute(select(Device).where(Device.id == failed_sd.device_id))).scalar_one()
        failed_dev.status = DeviceStatus.FREE
        failed_dev.updated_at = now
        failed_sd.status = SessionDeviceStatus.ERROR
        failed_sd.error_detail = fail_detail
        failed_sd.updated_at = now

        # Free any devices that were ALLOCATED but never attempted
        remaining = (await self._db.execute(
            select(SessionDevice).where(
                SessionDevice.session_id == session.id,
                SessionDevice.id.notin_({sd.id for sd in bound} | {failed_sd.id}),
                SessionDevice.status == SessionDeviceStatus.ALLOCATED,
            )
        )).scalars().all()
        for sd in remaining:
            sd.status = SessionDeviceStatus.RELEASED
            sd.updated_at = now
            dev = (await self._db.execute(select(Device).where(Device.id == sd.device_id))).scalar_one()
            dev.status = DeviceStatus.FREE
            dev.updated_at = now

        session.status = SessionStatus.FAILED
        session.failure_reason = f"bind_failed: {fail_detail}"
        session.updated_at = now
        await self._db.commit()
        log.info("session_failed_after_rollback", session_id=str(session.id))

    async def _fail_session(self, session: Session, reason: str, detail: str) -> None:
        session.status = SessionStatus.FAILED
        session.failure_reason = reason
        session.updated_at = datetime.now(UTC)
        await self._db.commit()
        log.error("allocation_failed", session_id=str(session.id), reason=reason, detail=detail)
