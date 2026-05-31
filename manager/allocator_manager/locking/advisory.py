"""PostgreSQL advisory lock helpers.

Uses transaction-scoped advisory locks (pg_try_advisory_xact_lock) so locks
are released automatically on COMMIT or ROLLBACK — no manual cleanup needed.

Lock key derivation: hashtext(string) maps a string to a 32-bit integer using
PostgreSQL's built-in hash function, ensuring consistent keys across instances.
"""

import asyncio

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.config import settings

log = structlog.get_logger(__name__)


async def try_advisory_xact_lock(db: AsyncSession, key: str) -> bool:
    """Attempt to acquire a transaction-scoped advisory lock for `key`.

    Returns True if the lock was acquired, False if already held by another
    transaction. The lock is released automatically when the transaction ends.
    """
    result = await db.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": key},
    )
    acquired: bool = result.scalar_one()
    if not acquired:
        log.debug("advisory_lock_contention", key=key)
    return acquired


async def acquire_advisory_xact_lock_with_retry(db: AsyncSession, key: str) -> bool:
    """Try acquiring the lock with exponential backoff retries.

    Returns True if eventually acquired, False after all retries exhausted.
    """
    for attempt in range(settings.allocation_lock_retries):
        if await try_advisory_xact_lock(db, key):
            return True
        delay = settings.allocation_lock_retry_base_ms * (2 ** attempt) / 1000.0
        log.debug("advisory_lock_retry", key=key, attempt=attempt + 1, delay_s=delay)
        await asyncio.sleep(delay)
    return False
