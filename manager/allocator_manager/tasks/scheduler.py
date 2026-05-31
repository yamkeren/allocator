import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from allocator_manager.config import settings

log = structlog.get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def start_scheduler() -> None:
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")

    # Import here to avoid circular imports at module load time
    from allocator_manager.tasks.heartbeat_reaper import reap_stale_nodes
    from allocator_manager.tasks.session_expiry import expire_stale_sessions
    from allocator_manager.tasks.zombie_cleanup import cleanup_zombies

    _scheduler.add_job(
        reap_stale_nodes,
        "interval",
        seconds=settings.heartbeat_reaper_interval,
        id="heartbeat_reaper",
        replace_existing=True,
    )
    _scheduler.add_job(
        expire_stale_sessions,
        "interval",
        seconds=settings.session_expiry_interval,
        id="session_expiry",
        replace_existing=True,
    )
    _scheduler.add_job(
        cleanup_zombies,
        "interval",
        seconds=settings.zombie_cleanup_interval,
        id="zombie_cleanup",
        replace_existing=True,
    )

    _scheduler.start()
    log.info("scheduler_started")


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=True)
        log.info("scheduler_stopped")
