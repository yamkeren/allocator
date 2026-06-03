import structlog
from sqlalchemy import select

from allocator_manager.database import AsyncSessionLocal
from allocator_manager.models.device import Device, DeviceStatus
from allocator_manager.models.session import Session, SessionStatus
from allocator_manager.models.session_device import SessionDevice, SessionDeviceStatus

log = structlog.get_logger(__name__)


async def cleanup_zombies() -> None:
    """Free ALLOCATED devices that have no corresponding ACTIVE session."""
    from datetime import UTC, datetime
    async with AsyncSessionLocal() as db:
        # Find devices stuck in ALLOCATED with no active session referencing them
        result = await db.execute(
            select(Device).where(
                Device.status == DeviceStatus.ALLOCATED,
            )
        )
        candidates = result.scalars().all()
        if not candidates:
            return

        now = datetime.now(UTC)
        freed = 0
        for device in candidates:
            # Check for an active session_device entry
            sd_result = await db.execute(
                select(SessionDevice)
                .join(Session, Session.id == SessionDevice.session_id)
                .where(
                    SessionDevice.device_id == device.id,
                    Session.status == SessionStatus.ACTIVE,
                )
            )
            active_sd = sd_result.scalar_one_or_none()
            if active_sd is None:
                device.status = DeviceStatus.FREE
                device.updated_at = now
                freed += 1
                log.warning(
                    "zombie_device_freed",
                    device_id=str(device.id),
                    logical_name=device.logical_name,
                )

        if freed:
            await db.commit()
