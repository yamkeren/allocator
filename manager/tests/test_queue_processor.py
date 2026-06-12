"""Tests for the global skip-FIFO queue processor.

No real database: `AsyncSessionLocal` is replaced with a fake that returns
in-memory Session objects, and `AllocationService` with a fake that succeeds
iff every requested device is currently free (consuming them on success),
mirroring the real contract: failure sets status back to PENDING and consumes
nothing.
"""

from datetime import UTC, datetime, timedelta

import pytest

import allocator_manager.services.allocation as allocation
from allocator_manager.models.session import Session, SessionStatus
from allocator_manager.tasks import queue_processor


class FakeResult:
    def __init__(self, sessions):
        self._sessions = sessions

    def scalars(self):
        return self

    def all(self):
        return self._sessions


class FakeDb:
    """Stands in for AsyncSessionLocal(): async context manager + execute()."""

    def __init__(self, sessions):
        self._sessions = sessions

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        # Mirror the real query: PENDING only, ordered by created_at.
        pending = [s for s in self._sessions if s.status == SessionStatus.PENDING]
        return FakeResult(sorted(pending, key=lambda s: s.created_at))


class FakeAllocationService:
    """allocate() succeeds iff all requested devices are free, consuming them."""

    def __init__(self, free_devices):
        self.free = set(free_devices)
        self.attempts: list[Session] = []

    async def allocate(self, session):
        self.attempts.append(session)
        names = set(session.requested_devices)
        if names <= self.free:
            self.free -= names
            session.status = SessionStatus.ACTIVE
            return True
        session.status = SessionStatus.PENDING
        return False


def make_session(devices, age_seconds):
    return Session(
        client_id="test",
        requested_devices=list(devices),
        status=SessionStatus.PENDING,
        created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )


@pytest.fixture
def patch_queue(monkeypatch):
    def _patch(sessions, free_devices):
        svc = FakeAllocationService(free_devices)
        monkeypatch.setattr(queue_processor, "AsyncSessionLocal", lambda: FakeDb(sessions))
        monkeypatch.setattr(allocation, "AllocationService", lambda db: svc)
        return svc

    return _patch


async def test_older_session_tried_first_across_sets(patch_queue):
    # S1 (older) wants {a, b}; S2 (newer) wants {a}. Only one outcome respects
    # global age order: S1 takes both devices, S2 waits. The old per-set code
    # processed set ("a",) before ("a","b") and gave the device to S2.
    s1 = make_session(["a", "b"], age_seconds=60)
    s2 = make_session(["a"], age_seconds=10)
    svc = patch_queue([s2, s1], free_devices={"a", "b"})

    await queue_processor.process_queue()

    assert svc.attempts == [s1, s2]
    assert s1.status == SessionStatus.ACTIVE
    assert s2.status == SessionStatus.PENDING


async def test_unsatisfiable_head_is_skipped(patch_queue):
    # The oldest session wants a device that doesn't exist; it must not block
    # the satisfiable newer session.
    s1 = make_session(["missing"], age_seconds=60)
    s2 = make_session(["a"], age_seconds=10)
    patch_queue([s1, s2], free_devices={"a"})

    await queue_processor.process_queue()

    assert s1.status == SessionStatus.PENDING
    assert s2.status == SessionStatus.ACTIVE


async def test_same_set_oldest_wins(patch_queue):
    # Two sessions requesting the identical set: age decides.
    s1 = make_session(["a"], age_seconds=60)
    s2 = make_session(["a"], age_seconds=10)
    patch_queue([s2, s1], free_devices={"a"})

    await queue_processor.process_queue()

    assert s1.status == SessionStatus.ACTIVE
    assert s2.status == SessionStatus.PENDING
