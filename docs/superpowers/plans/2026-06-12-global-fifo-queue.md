# Global FIFO Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-device-set FIFO queue with a single global skip-FIFO across all PENDING sessions, ordered by `created_at`.

**Architecture:** All logic lives in `manager/allocator_manager/tasks/queue_processor.py`. `process_queue` already fetches PENDING sessions ordered by `created_at`; the change deletes the group-by-device-set logic and walks the global list oldest-first, skipping sessions that `AllocationService.allocate()` leaves PENDING. Tests fake the DB session factory and the allocation service — no Postgres needed.

**Tech Stack:** Python 3.12, SQLAlchemy async ORM, pytest + pytest-asyncio (`asyncio_mode = "auto"`, already configured in `manager/pyproject.toml`).

**Spec:** `docs/superpowers/specs/2026-06-12-global-fifo-design.md`

---

## Background for the implementer

- `Session` model (`manager/allocator_manager/models/session.py`): fields used here are `requested_devices: list[str]`, `status: SessionStatus` (PENDING/ACTIVE/RELEASED/FAILED), `created_at: datetime`. Constructing `Session(...)` standalone (no DB) works; column defaults apply only at flush, so set `status` and `created_at` explicitly in tests.
- `AllocationService.allocate(session)` (`manager/allocator_manager/services/allocation.py:89`): tries to drive a PENDING session to ACTIVE. On failure it sets `session.status = SessionStatus.PENDING` and returns False; on success the session ends ACTIVE (or FAILED) and it returns True. A failed attempt does not consume devices.
- `queue_processor.py` imports `AsyncSessionLocal` at module top (patch it on the `queue_processor` module) and imports `AllocationService` *inside* `process_queue` (patch it on `allocator_manager.services.allocation`).
- Current behavior being replaced: sessions sorted by `(sorted_device_set, created_at)`, grouped per set, head-of-line break within a set. Key observable difference: the old code orders groups by device-set key, so a *newer* session whose set sorts first (e.g. `("a",)` before `("a","b")`) gets devices before an *older* overlapping one. The new code always tries oldest first.

## File structure

- Modify: `manager/allocator_manager/tasks/queue_processor.py` — the queue loop and module docstring.
- Create: `manager/tests/test_queue_processor.py` — all queue tests (fakes + 3 behavior tests).
- Modify: `HANDOFF.md:108-110` — queue description bullet.
- No other files. No DB, contract, or API changes.

---

### Task 1: Test environment

**Files:** none created (venv is gitignored infrastructure).

- [ ] **Step 1: Create venv with dev deps**

```bash
cd /home/yam/code/allocator/manager
python3 -m venv .venv
.venv/bin/pip install -e ../contract -e ".[dev]"
```

Expected: ends with `Successfully installed ... allocator-manager ... pytest ... pytest-asyncio ...`

- [ ] **Step 2: Verify pytest collects (zero tests is fine)**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/ --collect-only -q
```

Expected: `no tests ran` / `collected 0 items`, exit without import errors.

- [ ] **Step 3: Verify .venv is ignored by git**

```bash
cd /home/yam/code/allocator && git status --porcelain | grep -c .venv || echo "ignored"
```

Expected: `ignored` (or `0`). If `.venv` shows up, add `manager/.venv/` to `.gitignore` and include that file in the Task 2 commit.

### Task 2: Failing tests for global skip-FIFO

**Files:**
- Test: `manager/tests/test_queue_processor.py` (create)

- [ ] **Step 1: Write the test file (complete content)**

```python
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
```

- [ ] **Step 2: Run tests — expect exactly one failure**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/test_queue_processor.py -v
```

Expected: `test_older_session_tried_first_across_sets` **FAILS** (old code attempts S2 first: assertion `svc.attempts == [s1, s2]` fails). `test_unsatisfiable_head_is_skipped` and `test_same_set_oldest_wins` PASS (old code already satisfies them; they are regression guards). If the first test passes, stop — the patching isn't reaching the real code path; check the two `monkeypatch.setattr` targets.

- [ ] **Step 3: Commit the red test**

```bash
cd /home/yam/code/allocator
git add manager/tests/test_queue_processor.py
git commit -m "test: queue processor global skip-FIFO behavior (red)"
```

### Task 3: Implement global skip-FIFO

**Files:**
- Modify: `manager/allocator_manager/tasks/queue_processor.py`

- [ ] **Step 1: Replace the file content (complete file)**

```python
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
```

(Deletions vs. old file: module docstring rewritten; `from itertools import groupby` gone; `_set_key()` gone; the sort + `groupby` loop with the head-of-line `break` replaced by the flat loop above. `kick_queue`, `_lock`, `_inflight` byte-identical.)

- [ ] **Step 2: Run tests — all green**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/test_queue_processor.py -v
```

Expected: 3 passed.

- [ ] **Step 3: Grep for dead references**

```bash
grep -rn "_set_key\|groupby" /home/yam/code/allocator/manager/allocator_manager/
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
cd /home/yam/code/allocator
git add manager/allocator_manager/tasks/queue_processor.py
git commit -m "feat: queue processor is a global skip-FIFO (was per-device-set)"
```

### Task 4: Update HANDOFF.md

**Files:**
- Modify: `HANDOFF.md:108-110`

- [ ] **Step 1: Replace the queue bullet**

Old text (lines 108-110):

```
- No eligible node → session **PENDING** (queued). `queue_processor` is **FIFO keyed on the
  sorted requested-device set**, triggered on release/sync/unfreeze (+ 15s safety sweep),
  head-of-line per device set (sessions wanting different sets never block each other).
```

New text:

```
- No eligible node → session **PENDING** (queued). `queue_processor` is a **global skip-FIFO
  by `created_at`**, triggered on release/sync/unfreeze (+ 15s safety sweep): every PENDING
  session is tried oldest-first; unsatisfiable ones are skipped and never block younger ones
  (starvation of multi-device requests by newer subset requests is an accepted risk).
```

Also check the header note around `HANDOFF.md:8` ("the wait queue is now keyed on the **sorted requested-device set**") and update that phrase to "the wait queue is a **global skip-FIFO by creation time**".

- [ ] **Step 2: Commit**

**Caution:** `HANDOFF.md` (and `client/.../session.py`, `contract/.../session.py`) had pre-existing uncommitted changes when this plan was written. Before committing, run `git diff HANDOFF.md` and confirm you are only committing the queue-bullet edit plus whatever was already there *if the user has said those changes belong in this branch*. If the pre-existing diff looks unrelated and unexplained, stop and ask the user instead of committing it.

```bash
cd /home/yam/code/allocator
git add HANDOFF.md
git commit -m "docs: HANDOFF reflects global skip-FIFO queue"
```

---

## Verification (whole plan)

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/ -v
```

Expected: 3 passed. Manual smoke (optional, needs deployed stack): create two sessions where the older requests an unavailable device — newer one must go ACTIVE.
