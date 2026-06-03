"""Per-group FIFO queue for PENDING sessions.

When a node frees up (session release, device sync, or unfreeze) a queued
session may become allocatable. `process_queue` walks PENDING sessions oldest
-first within each group and starts the ones whose group can now be satisfied,
stopping at the first that still can't (head-of-line ordering per group).
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


async def process_queue() -> None:
    async with _lock:
        async with AsyncSessionLocal() as db:
            pending = (await db.execute(
                select(Session)
                .where(Session.status == SessionStatus.PENDING)
                .order_by(Session.group_name, Session.created_at)
            )).scalars().all()
            if not pending:
                return

            from allocator_manager.services.allocation import AllocationService
            svc = AllocationService(db)
            started = 0
            for _group, sessions in groupby(pending, key=lambda s: s.group_name):
                for session in sessions:  # oldest first
                    await svc.allocate(session)
                    if session.status == SessionStatus.PENDING:
                        break  # head-of-line: don't skip ahead within a group
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
