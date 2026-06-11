"""Per-request-set FIFO queue for PENDING sessions.

When a node frees up (session release, device sync, or unfreeze) a queued
session may become allocatable. `process_queue` groups PENDING sessions by their
sorted requested-device list and, within each set, walks them oldest-first,
starting the ones that can now be satisfied and stopping at the first that still
can't (head-of-line ordering per identical device set). Sessions requesting
different device sets are independent and never block each other.
"""

import asyncio
from itertools import groupby

import structlog
from sqlalchemy import select

from allocator_manager.database import AsyncSessionLocal
from allocator_manager.models.session import Session, SessionStatus

log = structlog.get_logger(__name__)

# Single-instance manager: serialize queue runs so concurrent kicks don't
# double-allocate the same devices.
_lock = asyncio.Lock()

# Hold strong references to in-flight kick tasks. asyncio only keeps a weak
# reference to tasks, so without this a kicked run can be garbage-collected
# (and cancelled) before it finishes.
_inflight: set[asyncio.Task] = set()


def _set_key(session: Session) -> tuple[str, ...]:
    return tuple(sorted(session.requested_devices))


async def process_queue() -> None:
    async with _lock:
        async with AsyncSessionLocal() as db:
            pending = (await db.execute(
                select(Session)
                .where(Session.status == SessionStatus.PENDING)
                .order_by(Session.created_at)
            )).scalars().all()
            if not pending:
                return

            # Group by identical requested-device set; within a set, oldest first.
            ordered = sorted(pending, key=lambda s: (_set_key(s), s.created_at))

            from allocator_manager.services.allocation import AllocationService
            svc = AllocationService(db)
            started = 0
            for _key, sessions in groupby(ordered, key=_set_key):
                for session in sessions:  # oldest first
                    await svc.allocate(session)
                    if session.status == SessionStatus.PENDING:
                        break  # head-of-line: don't skip ahead within a set
                    if session.status == SessionStatus.ACTIVE:
                        started += 1
            if started:
                log.info("queue_processed", started=started)


def kick_queue() -> None:
    """Fire-and-forget a queue run on the current event loop, if any."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    task = asyncio.create_task(process_queue())
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)
