"""Core allocation algorithm.

Phase 1 — RESERVATION (single DB transaction):
  Advisory lock on group name → SELECT FOR UPDATE devices in canonical UUID order
  → validate FREE + node ONLINE → bulk UPDATE to RESERVED → INSERT session_devices

Phase 2 — BINDING (saga, outside DB transaction):
  For each device in canonical order: call node agent bind → track progress
  On failure: rollback all already-bound devices (best-effort) → session FAILED
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.config import settings
from allocator_manager.locking.advisory import acquire_advisory_xact_lock_with_retry
from allocator_manager.models.audit import AuditLog
from allocator_manager.models.device import Device, DeviceStatus
from allocator_manager.models.group import Group, GroupDevice
from allocator_manager.models.node import Node, NodeStatus
from allocator_manager.models.session import Session, SessionStatus
from allocator_manager.models.session_device import SessionDevice, SessionDeviceStatus
from allocator_manager.observability.metrics import (
    allocation_duration,
    allocation_failures,
    rollback_total,
    sessions_total,
)

log = structlog.get_logger(__name__)


class AllocationError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


class AllocationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -------------------------------------------------------------------------
    # Public entry point
    # -------------------------------------------------------------------------

    async def allocate(self, session: Session) -> None:
        """Drive a session from PENDING all the way to ACTIVE (or FAILED)."""
        start = time.monotonic()
        try:
            await self._reserve(session)
            await self._bind(session)
        except AllocationError as exc:
            allocation_failures.labels(reason=exc.reason).inc()
            log.error(
                "allocation_failed",
                session_id=str(session.id),
                reason=exc.reason,
                detail=exc.detail,
            )
        finally:
            allocation_duration.observe(time.monotonic() - start)

    # -------------------------------------------------------------------------
    # Phase 1: Reservation (single DB transaction)
    # -------------------------------------------------------------------------

    async def _reserve(self, session: Session) -> None:
        async with self._db.begin_nested():
            # Advisory lock serialises concurrent requests for the same group
            acquired = await acquire_advisory_xact_lock_with_retry(
                self._db, f"group:{session.group_name}"
            )
            if not acquired:
                session.status = SessionStatus.FAILED
                session.failure_reason = "lock_timeout"
                session.updated_at = datetime.now(UTC)
                await self._db.flush()
                raise AllocationError("lock_timeout", "Could not acquire group lock after retries")

            # Load and lock all group devices in canonical UUID order (prevents deadlocks)
            result = await self._db.execute(
                select(Device)
                .join(GroupDevice, GroupDevice.device_id == Device.id)
                .join(Group, Group.id == GroupDevice.group_id)
                .where(
                    Group.name == session.group_name,
                    Group.deleted_at.is_(None),
                )
                .order_by(Device.id)  # canonical order
                .with_for_update(nowait=True)
            )
            devices = result.scalars().all()

            if not devices:
                session.status = SessionStatus.FAILED
                session.failure_reason = "group_empty_or_not_found"
                session.updated_at = datetime.now(UTC)
                await self._db.flush()
                raise AllocationError("group_empty", f"Group {session.group_name!r} has no devices")

            # Validate device availability
            unavailable = [d for d in devices if d.status != DeviceStatus.FREE]
            if unavailable:
                names = [d.logical_name for d in unavailable]
                session.status = SessionStatus.FAILED
                session.failure_reason = f"devices_unavailable:{','.join(names)}"
                session.updated_at = datetime.now(UTC)
                await self._db.flush()
                raise AllocationError("devices_unavailable", f"Devices not FREE: {names}")

            # Validate all nodes are ONLINE
            node_ids = {d.node_id for d in devices}
            node_result = await self._db.execute(
                select(Node).where(Node.id.in_(node_ids))
            )
            nodes = {n.id: n for n in node_result.scalars().all()}
            offline_nodes = [n for n in nodes.values() if n.status != NodeStatus.ONLINE]
            if offline_nodes:
                names = [n.name for n in offline_nodes]
                session.status = SessionStatus.FAILED
                session.failure_reason = f"nodes_offline:{','.join(names)}"
                session.updated_at = datetime.now(UTC)
                await self._db.flush()
                raise AllocationError("nodes_offline", f"Nodes not ONLINE: {names}")

            # Transition session to RESERVING
            session.status = SessionStatus.RESERVING
            session.updated_at = datetime.now(UTC)
            await self._db.flush()

            now = datetime.now(UTC)

            # Bulk-update devices to RESERVED and create session_device rows
            for device in devices:
                device.status = DeviceStatus.RESERVED
                device.updated_at = now

                node = nodes[device.node_id]
                sd = SessionDevice(
                    session_id=session.id,
                    device_id=device.id,
                    logical_name=device.logical_name,
                    node_id=device.node_id,
                    node_agent_url=node.agent_url,
                    usbip_bus_id=device.usbip_bus_id,
                    status=SessionDeviceStatus.RESERVED,
                )
                self._db.add(sd)

                self._db.add(AuditLog(
                    entity_type="device",
                    entity_id=device.id,
                    action="reserved",
                    old_status=DeviceStatus.FREE.value,
                    new_status=DeviceStatus.RESERVED.value,
                    actor=session.client_id,
                    detail={"session_id": str(session.id)},
                ))

            await self._db.flush()

        log.info(
            "reservation_committed",
            session_id=str(session.id),
            device_count=len(devices),
        )

    # -------------------------------------------------------------------------
    # Phase 2: Binding (saga — each bind is an independent network call)
    # -------------------------------------------------------------------------

    async def _bind(self, session: Session) -> None:
        # Reload session_devices after reservation commit
        sd_result = await self._db.execute(
            select(SessionDevice).where(SessionDevice.session_id == session.id)
        )
        session_devices = sd_result.scalars().all()

        session.status = SessionStatus.BINDING
        session.updated_at = datetime.now(UTC)
        await self._db.commit()

        bound: list[SessionDevice] = []
        failed_sd: SessionDevice | None = None
        fail_detail = ""

        for sd in sorted(session_devices, key=lambda x: str(x.device_id)):
            sd.status = SessionDeviceStatus.BINDING
            sd.updated_at = datetime.now(UTC)
            await self._db.commit()

            try:
                bus_id = await self._call_bind(sd)
                now = datetime.now(UTC)
                sd.status = SessionDeviceStatus.BOUND
                sd.usbip_bus_id = bus_id
                sd.bound_at = now
                sd.updated_at = now

                # Update device table too
                device_result = await self._db.execute(
                    select(Device).where(Device.id == sd.device_id)
                )
                device = device_result.scalar_one()
                device.status = DeviceStatus.BOUND
                device.updated_at = now

                self._db.add(AuditLog(
                    entity_type="device",
                    entity_id=sd.device_id,
                    action="bound",
                    old_status=DeviceStatus.RESERVED.value,
                    new_status=DeviceStatus.BOUND.value,
                    actor="system",
                    detail={"session_id": str(session.id), "bus_id": bus_id},
                ))
                await self._db.commit()
                bound.append(sd)
                log.info("device_bound", logical_name=sd.logical_name, bus_id=bus_id)

            except Exception as exc:
                fail_detail = str(exc)
                failed_sd = sd
                log.error(
                    "bind_failed",
                    logical_name=sd.logical_name,
                    error=fail_detail,
                )
                break

        if failed_sd is not None:
            await self._rollback_bindings(session, bound, failed_sd, fail_detail)
            raise AllocationError("bind_failed", fail_detail)

        # All devices bound — activate session
        now = datetime.now(UTC)
        session.status = SessionStatus.ACTIVE
        session.lease_expires_at = now + timedelta(seconds=session.lease_duration)
        session.last_heartbeat = now
        session.updated_at = now
        await self._db.commit()
        sessions_total.labels(status="active").inc()
        log.info("session_activated", session_id=str(session.id))

    async def _call_bind(self, sd: SessionDevice) -> str:
        """Call the node agent bind endpoint. Returns the confirmed bus_id."""
        # Import here to avoid circular import
        from allocator_manager.services.node_client import NodeAgentClient
        client = NodeAgentClient(sd.node_agent_url)
        result = await client.bind(
            bus_id=sd.usbip_bus_id or "",
            logical_name=sd.logical_name,
            session_id=str(sd.session_id),
        )
        return result.bus_id

    # -------------------------------------------------------------------------
    # Rollback: unbind all successfully-bound devices
    # -------------------------------------------------------------------------

    async def _rollback_bindings(
        self,
        session: Session,
        bound: list[SessionDevice],
        failed_sd: SessionDevice,
        fail_detail: str,
    ) -> None:
        rollback_total.labels(reason="bind_failed").inc()
        now = datetime.now(UTC)

        session.status = SessionStatus.RELEASING
        session.updated_at = now
        await self._db.commit()

        # Unbind already-bound devices in reverse order (best-effort)
        errors: list[tuple[SessionDevice, str]] = []
        for sd in reversed(bound):
            try:
                from allocator_manager.services.node_client import NodeAgentClient
                client = NodeAgentClient(sd.node_agent_url)
                await client.unbind(bus_id=sd.usbip_bus_id or "", logical_name=sd.logical_name)
                sd.status = SessionDeviceStatus.RELEASED
                sd.released_at = now
                sd.updated_at = now
                # Restore device to FREE
                dev_result = await self._db.execute(select(Device).where(Device.id == sd.device_id))
                device = dev_result.scalar_one()
                device.status = DeviceStatus.FREE
                device.updated_at = now
            except Exception as exc:
                errors.append((sd, str(exc)))
                sd.status = SessionDeviceStatus.ERROR
                sd.error_detail = str(exc)
                sd.updated_at = now
                dev_result = await self._db.execute(select(Device).where(Device.id == sd.device_id))
                device = dev_result.scalar_one()
                device.status = DeviceStatus.ERROR  # quarantined — requires zombie cleanup
                device.updated_at = now
                log.error("rollback_unbind_failed", logical_name=sd.logical_name, error=str(exc))

        # The device that failed to bind: it was never bound, set it FREE
        failed_dev_result = await self._db.execute(
            select(Device).where(Device.id == failed_sd.device_id)
        )
        failed_device = failed_dev_result.scalar_one()
        failed_device.status = DeviceStatus.FREE
        failed_device.updated_at = now
        failed_sd.status = SessionDeviceStatus.ERROR
        failed_sd.error_detail = fail_detail
        failed_sd.updated_at = now

        # Devices that were RESERVED but never reached BINDING phase
        for sd in await self._unreached_devices(session.id, bound, failed_sd):
            sd.status = SessionDeviceStatus.RELEASED
            sd.updated_at = now
            dev_result = await self._db.execute(select(Device).where(Device.id == sd.device_id))
            device = dev_result.scalar_one()
            device.status = DeviceStatus.FREE
            device.updated_at = now

        session.status = SessionStatus.FAILED
        session.failure_reason = f"bind_failed: {fail_detail}"
        session.updated_at = now
        await self._db.commit()
        sessions_total.labels(status="failed").inc()
        log.info(
            "session_failed_after_rollback",
            session_id=str(session.id),
            rollback_errors=len(errors),
        )

    async def _unreached_devices(
        self,
        session_id: uuid.UUID,
        bound: list[SessionDevice],
        failed_sd: SessionDevice,
    ) -> list[SessionDevice]:
        """Return session_devices that are still in RESERVED state (never attempted)."""
        bound_ids = {sd.id for sd in bound} | {failed_sd.id}
        result = await self._db.execute(
            select(SessionDevice).where(
                SessionDevice.session_id == session_id,
                SessionDevice.id.notin_(bound_ids),
                SessionDevice.status == SessionDeviceStatus.RESERVED,
            )
        )
        return result.scalars().all()
