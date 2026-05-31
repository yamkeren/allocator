import structlog
from sqlalchemy import select

from allocator_manager.database import AsyncSessionLocal
from allocator_manager.models.device import Device, DeviceStatus
from allocator_manager.models.session import Session, SessionStatus
from allocator_manager.models.session_device import SessionDevice

log = structlog.get_logger(__name__)


async def cleanup_zombies() -> None:
    """Find devices in BOUND/BINDING state with no active session and unbind them."""
    async with AsyncSessionLocal() as db:
        # Devices stuck in BOUND or BINDING with no corresponding ACTIVE/BINDING session
        result = await db.execute(
            select(Device).where(
                Device.status.in_([DeviceStatus.BOUND, DeviceStatus.BINDING]),
                Device.deleted_at.is_(None),
            )
        )
        candidates = result.scalars().all()

        for device in candidates:
            # Check for an active session_device entry
            sd_result = await db.execute(
                select(SessionDevice)
                .join(SessionDevice.session)
                .where(
                    SessionDevice.device_id == device.id,
                    SessionDevice.session.has(
                        Session.status.in_([
                            SessionStatus.BINDING,
                            SessionStatus.ACTIVE,
                            SessionStatus.RELEASING,
                        ])
                    ),
                )
            )
            active_sd = sd_result.scalar_one_or_none()
            if active_sd is None:
                log.warning(
                    "zombie_device_detected",
                    device_id=str(device.id),
                    logical_name=device.logical_name,
                    status=device.status,
                )
                # TODO Phase 6: call agent unbind; for now just log
