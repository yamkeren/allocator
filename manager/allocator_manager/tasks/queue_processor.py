"""Global skip-FIFO queue for PENDING sessions.

When a node frees up (session release, device sync, or unfreeze) a queued
session may become allocatable. `process_queue` walks all PENDING sessions
oldest-first (global FIFO by `created_at`) and tries to start each one; a
session that still cannot be satisfied is skipped and never blocks the
sessions behind it. Age is the global priority: older sessions are always
attempted before newer ones. Starvation of multi-device requests by newer
subset requests is an accepted risk
(docs/superpowers/specs/2026-06-12-global-fifo-design.md).
"""

import asyncio

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
                .order_by(Session.created_at)
            )).scalars().all()
            if not pending:
                return

            from allocator_manager.services.allocation import AllocationService
            svc = AllocationService(db)
            started = 0
            for session in pending:  # oldest first; unsatisfiable are skipped
                await svc.allocate(session)
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
