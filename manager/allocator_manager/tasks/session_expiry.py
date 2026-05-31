from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from allocator_manager.config import settings
from allocator_manager.database import AsyncSessionLocal
from allocator_manager.models.session import Session, SessionStatus

log = structlog.get_logger(__name__)


async def expire_stale_sessions() -> None:
    """Expire ACTIVE sessions whose lease has passed or whose heartbeat has timed out."""
    now = datetime.now(UTC)
    heartbeat_cutoff = now - timedelta(seconds=settings.heartbeat_timeout)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Session).where(
                Session.status == SessionStatus.ACTIVE,
                (Session.lease_expires_at < now) | (Session.last_heartbeat < heartbeat_cutoff),
            )
        )
        expired = result.scalars().all()

        for session in expired:
            log.warning(
                "session_expiring",
                session_id=str(session.id),
                lease_expires_at=session.lease_expires_at,
                last_heartbeat=session.last_heartbeat,
            )
            session.status = SessionStatus.EXPIRED
            session.updated_at = now
            db.add(session)
            # TODO Phase 6: trigger full releasing sequence (unbind devices)

        if expired:
            await db.commit()
