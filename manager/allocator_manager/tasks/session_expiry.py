from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from allocator_manager.config import settings
from allocator_manager.database import AsyncSessionLocal
from allocator_manager.models.device import Device, DeviceStatus
from allocator_manager.models.session import Session, SessionStatus
from allocator_manager.models.session_device import SessionDevice, SessionDeviceStatus

log = structlog.get_logger(__name__)


async def expire_stale_sessions() -> None:
    """Release sessions that have been ACTIVE longer than session_max_age without update."""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.session_max_age)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Session).where(
                Session.status == SessionStatus.ACTIVE,
                Session.updated_at < cutoff,
            )
        )
        stale = result.scalars().all()
        if not stale:
            return

        now = datetime.now(UTC)
        for session in stale:
            # Free all session devices and their backing devices
            sd_result = await db.execute(
                select(SessionDevice).where(SessionDevice.session_id == session.id)
            )
            for sd in sd_result.scalars().all():
                if sd.status not in (SessionDeviceStatus.RELEASED, SessionDeviceStatus.ERROR):
                    sd.status = SessionDeviceStatus.RELEASED
                    sd.released_at = now
                    sd.updated_at = now
                dev_result = await db.execute(
                    select(Device).where(Device.id == sd.device_id)
                )
                device = dev_result.scalar_one_or_none()
                if device and device.status != DeviceStatus.ERROR:
                    device.status = DeviceStatus.FREE
                    device.updated_at = now

            session.status = SessionStatus.RELEASED
            session.released_at = now
            session.updated_at = now
            log.info("session_expired", session_id=str(session.id), age_s=settings.session_max_age)

        await db.commit()
