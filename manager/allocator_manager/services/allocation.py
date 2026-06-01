"""Core allocation algorithm.

Phase 1 — RESERVATION (single DB transaction):
  SELECT FOR UPDATE devices in canonical UUID order → validate FREE + node ONLINE
  → bulk UPDATE to ALLOCATED → INSERT session_devices → session ACTIVE

Phase 2 — BINDING (saga, outside DB transaction):
  For each device: call node agent bind
  On failure: rollback all already-bound devices → session FAILED
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.models.device import Device, DeviceStatus
from allocator_manager.models.group import Group, GroupDevice
from allocator_manager.models.node import Node, NodeStatus
from allocator_manager.models.session import Session, SessionStatus
from allocator_manager.models.session_device import SessionDevice, SessionDeviceStatus

log = structlog.get_logger(__name__)


class AllocationError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


class AllocationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def allocate(self, session: Session) -> None:
        """Drive a session to ACTIVE or FAILED."""
        try:
            devices = await self._reserve(session)
            await self._bind(session, devices)
        except AllocationError as exc:
            log.error(
                "allocation_failed",
                session_id=str(session.id),
                reason=exc.reason,
                detail=exc.detail,
            )

    async def _reserve(self, session: Session) -> list[SessionDevice]:
        async with self._db.begin_nested():
            result = await self._db.execute(
                select(Device)
                .join(GroupDevice, GroupDevice.device_id == Device.id)
                .join(Group, Group.id == GroupDevice.group_id)
                .where(Group.name == session.group_name)
                .order_by(Device.id)
                .with_for_update(nowait=True)
            )
            devices = result.scalars().all()

            if not devices:
                await self._fail(session, "group_empty", f"Group {session.group_name!r} has no devices")

            unavailable = [d for d in devices if d.status != DeviceStatus.FREE]
            if unavailable:
                names = [d.logical_name for d in unavailable]
                await self._fail(session, f"devices_unavailable:{','.join(names)}", f"Devices not FREE: {names}")

            node_ids = {d.node_id for d in devices}
            node_result = await self._db.execute(select(Node).where(Node.id.in_(node_ids)))
            nodes = {n.id: n for n in node_result.scalars().all()}
            offline = [n for n in nodes.values() if n.status != NodeStatus.ONLINE]
            if offline:
                names = [n.name for n in offline]
                await self._fail(session, f"nodes_offline:{','.join(names)}", f"Nodes not ONLINE: {names}")

            now = datetime.now(UTC)
            session_devices = []
            for device in devices:
                device.status = DeviceStatus.ALLOCATED
                device.updated_at = now
                node = nodes[device.node_id]
                sd = SessionDevice(
                    session_id=session.id,
                    device_id=device.id,
                    logical_name=device.logical_name,
                    node_id=device.node_id,
                    node_agent_url=node.agent_url,
                    usbip_bus_id=device.usbip_bus_id,
                    status=SessionDeviceStatus.ALLOCATED,
                )
                self._db.add(sd)
                session_devices.append(sd)
            await self._db.flush()

        log.info("reservation_committed", session_id=str(session.id), device_count=len(devices))
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

    async def _fail(self, session: Session, reason: str, detail: str) -> None:
        session.status = SessionStatus.FAILED
        session.failure_reason = reason
        session.updated_at = datetime.now(UTC)
        await self._db.flush()
        raise AllocationError(reason, detail)
