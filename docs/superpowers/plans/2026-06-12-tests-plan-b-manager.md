# Test Suite Plan B — Manager Integration (real Postgres)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integration tests for the manager's service and API layers against a real, throwaway Postgres (testcontainers), exercising the transactional core: `eligible_nodes` ranking, the reserve→bind saga and its rollback, `FOR UPDATE NOWAIT` locking, device naming/portability, node sync identity, session lifecycle, background tasks, and the FastAPI routes with auth.

**Architecture:** A session-scoped `testcontainers` Postgres container is migrated with `alembic upgrade head` (so the migration is tested too). Tests get a fresh `AsyncSession` per test with all tables truncated between tests. The node agent (`NodeAgentClient`) and the fire-and-forget `kick_queue` are replaced with in-process recorders so tests stay hermetic and assertable. API tests run the real FastAPI app via httpx `ASGITransport` with `get_db` overridden to the test database.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (`asyncio_mode="auto"`), SQLAlchemy async + asyncpg, testcontainers, alembic, httpx ASGITransport. Docker required (available locally).

**Spec:** `docs/superpowers/specs/2026-06-12-system-test-suite-design.md` (Plan B of three).

---

## Background for the implementer

- Working dir `/home/yam/code/allocator`. `git add` exact paths only — never `-A`/`.` (untracked `graphify-out/` must never be committed).
- `manager/.venv` already exists with pytest + pytest-asyncio (created by an earlier plan; the queue-processor test runs there). Task 1 adds testcontainers + psycopg2-binary to the dev extra and re-installs.
- `manager/pyproject.toml` already has `[tool.pytest.ini_options]` with `asyncio_mode = "auto"`, `testpaths = ["tests"]`.
- `manager/tests/__init__.py` and `manager/tests/test_queue_processor.py` already exist. Do **not** modify the queue test. The new `conftest.py` must not break it (it uses its own fakes and never touches a DB, so the testcontainers fixtures — which are not autouse except truncation — must not run for it; see the truncation-fixture guard in Task 1).
- **No production code changes anywhere in Plan B.** Every task adds only test files (plus the pyproject dev-extra line in Task 1). If a test seems to require a production change, STOP and report — do not edit `allocator_manager/`.

### Key production facts the tests depend on (verified against source)

- **Models** (`allocator_manager/models/`): `Node(name unique, hostname, ip_address INET, agent_port, agent_url, status NodeStatus{ONLINE,OFFLINE}, frozen bool, last_heartbeat, agent_version)`. `Device(node_id FK, logical_name, vendor_id, product_id, device_class DeviceClass, status DeviceStatus{FREE,ALLOCATED,ERROR}, usbip_bus_id, fingerprint)` with unique constraints `uq_device_per_node_logical (node_id, logical_name)` and `uq_device_per_node_fingerprint (node_id, fingerprint)`. `Session(client_id, requested_devices ARRAY[str], status SessionStatus{PENDING,ACTIVE,RELEASED,FAILED}, requested_node_id, node_id)`. `SessionDevice(session_id FK CASCADE, device_id FK, logical_name, node_id, node_agent_url, usbip_bus_id, status SessionDeviceStatus{ALLOCATED,RELEASED,ERROR})` with `uq_session_device (session_id, device_id)`. `DeviceName(fingerprint unique, name)`.
- **`eligible_nodes(db, names, requested_node_id=None)`** (`services/allocation.py`): returns `list[(Node, {name: Device})]`; ONLINE + not frozen + a FREE device for every name; ranked by fewest total devices then `str(node.id)`.
- **`AllocationService(db).allocate(session)`**: reserves under a savepoint with `with_for_update(nowait=True)`; on NOWAIT failure or lost race returns the session to PENDING and returns `False`. On success runs the bind saga (`_call_bind` → `NodeAgentClient(url).bind(...)`), committing per device; a bind exception triggers reverse-order unbind of already-bound devices, frees all devices, sets session `FAILED`. `_bind` processes devices `sorted(by str(device_id))`.
- **`NodeAgentClient`** (`services/node_client.py`): `await NodeAgentClient(url).bind(bus_id, logical_name, session_id) -> BindResult(bus_id)`; `await ....unbind(bus_id, logical_name) -> UnbindResult(unbound)`. Imported lazily at call sites, so patching `allocator_manager.services.node_client.NodeAgentClient` covers allocation and release.
- **`kick_queue`** is imported lazily (`from allocator_manager.tasks.queue_processor import kick_queue`) inside `SessionService.release`, `NodeService.sync_devices`, `NodeService.unfreeze`. Patch `allocator_manager.tasks.queue_processor.kick_queue`.
- **Background tasks** (`tasks/*.py`) each open their own `AsyncSessionLocal()` imported as `from allocator_manager.database import AsyncSessionLocal`. To point them at the test DB, patch the name in each task module: `allocator_manager.tasks.heartbeat_reaper.AsyncSessionLocal`, `...session_expiry.AsyncSessionLocal`, `...zombie_cleanup.AsyncSessionLocal`. Thresholds come from `settings.node_offline_timeout` (180s) and `settings.session_max_age` (3600s).
- **Auth** (`middleware/auth.py`): `require_client` → **400** if `X-Client-Id` missing. `require_agent_secret` → **401** if the `X-Agent-Secret` header ≠ `settings.agent_secret` (default `"dev-agent-secret"`).
- **Routes**: sessions `POST/GET /api/v1/sessions`, `GET/DELETE /api/v1/sessions/{id}`, `POST /api/v1/sessions/{id}/freeze`. devices `POST/GET /api/v1/devices`, `GET/PATCH/DELETE /api/v1/devices/{node}/{logical_name}`, `POST /api/v1/devices/{node}/{logical_name}/rename`. nodes `GET /api/v1/nodes`, `GET/DELETE /api/v1/nodes/{id}`, `POST /api/v1/nodes/{node}/unfreeze`. internal `POST /internal/v1/nodes/register`, `POST /internal/v1/nodes/{id}/devices/sync`, `POST /internal/v1/nodes/{id}/heartbeat`.

## File structure

```
manager/pyproject.toml                 # modify: add testcontainers + psycopg2-binary to dev extra
manager/tests/conftest.py              # create: container, migration, engine, db, factories, agent/kick recorders, app/client
manager/tests/test_infra_smoke.py      # Task 1 smoke
manager/tests/test_eligible_nodes.py   # Task 2
manager/tests/test_allocation.py       # Task 3
manager/tests/test_allocation_saga.py  # Task 4 (rollback + locking)
manager/tests/test_naming.py           # Task 5
manager/tests/test_node_sync.py        # Task 6
manager/tests/test_session_service.py  # Task 7
manager/tests/test_tasks.py            # Task 8
manager/tests/test_api.py              # Task 9
```

---

### Task 1: Test infrastructure (container + conftest + smoke)

**Files:**
- Modify: `manager/pyproject.toml`
- Create: `manager/tests/conftest.py`
- Test: `manager/tests/test_infra_smoke.py`

- [ ] **Step 1: Add test deps to `manager/pyproject.toml`**

In the existing `[project.optional-dependencies] dev = [ ... ]` list, add two entries (keep the existing ones):

```toml
    "testcontainers>=4.0",
    "psycopg2-binary>=2.9",
```

- [ ] **Step 2: Install into the existing venv**

```bash
cd /home/yam/code/allocator/manager
.venv/bin/pip install -e ../contract -e ".[dev]"
```

Expected: `Successfully installed ... testcontainers ... psycopg2-binary ...` (or "already satisfied" for the rest).

- [ ] **Step 3: Write `manager/tests/conftest.py`** (complete file)

```python
"""Integration-test fixtures for the manager.

A session-scoped throwaway Postgres (testcontainers) is migrated with
`alembic upgrade head`. Each test gets a fresh AsyncSession with all tables
truncated. The node agent and the fire-and-forget queue kick are replaced with
in-process recorders so tests are hermetic and assertable.
"""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from allocator_manager.models.device import Device, DeviceClass, DeviceStatus
from allocator_manager.models.node import Node, NodeStatus
from allocator_manager.models.session import Session as SessionModel, SessionStatus

MANAGER_DIR = Path(__file__).resolve().parent.parent
_TABLES = "nodes, devices, sessions, session_devices, device_names"


@pytest.fixture(scope="session")
def _pg_url() -> str:
    """Start Postgres, run migrations, yield an asyncpg URL."""
    with PostgresContainer("postgres:16-alpine") as pg:
        sync_url = pg.get_connection_url()           # postgresql+psycopg2://...
        async_url = sync_url.replace("+psycopg2", "+asyncpg")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=MANAGER_DIR,
            env={**os.environ, "DATABASE_URL": async_url},
            check=True,
            capture_output=True,
        )
        yield async_url


@pytest.fixture(scope="session")
def _engine(_pg_url):
    # NullPool: never reuse a connection across pytest-asyncio's per-test event
    # loops (avoids "Future attached to a different loop").
    return create_async_engine(_pg_url, poolclass=NullPool)


@pytest.fixture(scope="session")
def Session(_engine):
    return async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def _clean(Session):
    """Truncate every table before a test body runs. Any DB-touching test
    depends on this (directly or via `db`/`client`), so tests are isolated
    without making truncation autouse — the DB-free queue test never triggers
    the container."""
    async with Session() as s:
        await s.execute(text(f"TRUNCATE TABLE {_TABLES} RESTART IDENTITY CASCADE"))
        await s.commit()


@pytest.fixture
async def db(Session, _clean):
    async with Session() as session:
        yield session


# ----- ORM factories -------------------------------------------------------

@pytest.fixture
def make_node(db):
    async def _make(name="node-1", status=NodeStatus.ONLINE, frozen=False,
                    ip="10.0.0.1", **kw):
        node = Node(
            name=name, hostname=f"{name}.local", ip_address=ip,
            agent_port=5000, agent_url=f"http://{ip}:5000",
            status=status, frozen=frozen,
            last_heartbeat=datetime.now(UTC), **kw,
        )
        db.add(node)
        await db.commit()
        await db.refresh(node)
        return node
    return _make


@pytest.fixture
def make_device(db):
    async def _make(node, logical_name="wifi_0", device_class=DeviceClass.WIFI,
                    status=DeviceStatus.FREE, fingerprint=None, device_id=None,
                    usbip_bus_id="1-1", **kw):
        device = Device(
            id=device_id or uuid.uuid4(),
            node_id=node.id, logical_name=logical_name,
            vendor_id="0bda", product_id="8812",
            device_class=device_class, status=status,
            usbip_bus_id=usbip_bus_id,
            fingerprint=fingerprint or f"fp-{node.name}-{logical_name}",
            **kw,
        )
        db.add(device)
        await db.commit()
        await db.refresh(device)
        return device
    return _make


@pytest.fixture
def make_session(db):
    async def _make(client_id="host-a", devices=("wifi_0",),
                    status=SessionStatus.PENDING, **kw):
        session = SessionModel(
            client_id=client_id, requested_devices=list(devices),
            status=status, **kw,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session
    return _make


# ----- agent + queue recorders --------------------------------------------

class AgentRecorder:
    """Records bind/unbind calls; can be told to fail specific binds."""

    def __init__(self):
        self.binds: list[str] = []
        self.unbinds: list[str] = []
        self.fail_bind_names: set[str] = set()

    def factory(self, node_url):
        return _FakeAgentClient(self, node_url)


class _FakeAgentClient:
    def __init__(self, rec, url):
        self._rec = rec
        self._url = url

    async def bind(self, bus_id, logical_name, session_id):
        from allocator_manager.services.node_client import BindResult
        if logical_name in self._rec.fail_bind_names:
            raise RuntimeError(f"agent bind failed for {logical_name}")
        self._rec.binds.append(logical_name)
        return BindResult(bus_id=bus_id or f"1-{len(self._rec.binds)}")

    async def unbind(self, bus_id, logical_name):
        from allocator_manager.services.node_client import UnbindResult
        self._rec.unbinds.append(logical_name)
        return UnbindResult(unbound=True)


@pytest.fixture
def agent(monkeypatch):
    rec = AgentRecorder()
    monkeypatch.setattr(
        "allocator_manager.services.node_client.NodeAgentClient", rec.factory
    )
    return rec


@pytest.fixture
def kick_spy(monkeypatch):
    """Replace the fire-and-forget queue kick with a recording no-op."""
    calls = {"n": 0}

    def fake_kick():
        calls["n"] += 1

    monkeypatch.setattr("allocator_manager.tasks.queue_processor.kick_queue", fake_kick)
    return calls


@pytest.fixture
def task_db(monkeypatch, Session):
    """Point background-task AsyncSessionLocal at the test database."""
    for mod in ("heartbeat_reaper", "session_expiry", "zombie_cleanup"):
        monkeypatch.setattr(
            f"allocator_manager.tasks.{mod}.AsyncSessionLocal", Session
        )


# ----- API app/client ------------------------------------------------------

@pytest.fixture
async def client(Session, _clean, agent, kick_spy):
    import httpx
    from allocator_manager.database import get_db
    from allocator_manager.main import create_app

    app = create_app()

    async def _override_get_db():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mgr") as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 4: Write `manager/tests/test_infra_smoke.py`**

```python
"""Proves the container + migration + db fixture + factories all work."""

from sqlalchemy import select

from allocator_manager.models.node import Node, NodeStatus


async def test_node_round_trips(db, make_node):
    await make_node(name="lab-1")
    rows = (await db.execute(select(Node))).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "lab-1"
    assert rows[0].status == NodeStatus.ONLINE


async def test_truncation_isolates_tests(db):
    # The previous test inserted a node; truncation must have cleared it.
    rows = (await db.execute(select(Node))).scalars().all()
    assert rows == []
```

- [ ] **Step 5: Run (this builds the image on first run — may take ~30s)**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/test_infra_smoke.py tests/test_queue_processor.py -v
```

Expected: 2 smoke + 3 queue = 5 passed. The queue test must still pass (proves conftest didn't disturb it). If the container fails to start, confirm `docker ps` works and report BLOCKED.

- [ ] **Step 6: Commit**

```bash
cd /home/yam/code/allocator
git add manager/pyproject.toml manager/tests/conftest.py manager/tests/test_infra_smoke.py
git commit -m "test(manager): testcontainers Postgres fixtures + infra smoke"
```

### Task 2: eligible_nodes ranking

**Files:**
- Test: `manager/tests/test_eligible_nodes.py`

- [ ] **Step 1: Write the test**

```python
"""eligible_nodes: ONLINE + not frozen + all names FREE; ranked fewest-devices."""

from allocator_manager.models.device import DeviceClass, DeviceStatus
from allocator_manager.models.node import NodeStatus
from allocator_manager.services.allocation import eligible_nodes


async def test_excludes_offline(db, make_node, make_device):
    node = await make_node(name="n1", status=NodeStatus.OFFLINE)
    await make_device(node, "wifi_0")
    assert await eligible_nodes(db, ["wifi_0"]) == []


async def test_excludes_frozen(db, make_node, make_device):
    node = await make_node(name="n1", frozen=True)
    await make_device(node, "wifi_0")
    assert await eligible_nodes(db, ["wifi_0"]) == []


async def test_excludes_node_missing_a_name(db, make_node, make_device):
    node = await make_node(name="n1")
    await make_device(node, "wifi_0")
    # No hid_0 on the node -> cannot satisfy {wifi_0, hid_0}
    assert await eligible_nodes(db, ["wifi_0", "hid_0"]) == []


async def test_excludes_node_with_allocated_device(db, make_node, make_device):
    node = await make_node(name="n1")
    await make_device(node, "wifi_0", status=DeviceStatus.ALLOCATED)
    assert await eligible_nodes(db, ["wifi_0"]) == []


async def test_ranks_fewest_total_devices_first(db, make_node, make_device):
    big = await make_node(name="big", ip="10.0.0.1")
    small = await make_node(name="small", ip="10.0.0.2")
    # both can satisfy wifi_0, but `big` has more total hardware
    await make_device(big, "wifi_0", fingerprint="fp-big-wifi")
    await make_device(big, "hid_0", device_class=DeviceClass.HID, fingerprint="fp-big-hid")
    await make_device(big, "hid_1", device_class=DeviceClass.HID, fingerprint="fp-big-hid1")
    await make_device(small, "wifi_0", fingerprint="fp-small-wifi")

    ranked = await eligible_nodes(db, ["wifi_0"])
    assert [n.name for n, _ in ranked] == ["small", "big"]
    # the returned mapping points at the right device
    first_node, matched = ranked[0]
    assert matched["wifi_0"].node_id == small.id


async def test_pins_to_requested_node(db, make_node, make_device):
    n1 = await make_node(name="n1", ip="10.0.0.1")
    n2 = await make_node(name="n2", ip="10.0.0.2")
    await make_device(n1, "wifi_0", fingerprint="fp-n1")
    await make_device(n2, "wifi_0", fingerprint="fp-n2")
    ranked = await eligible_nodes(db, ["wifi_0"], requested_node_id=n2.id)
    assert [n.name for n, _ in ranked] == ["n2"]
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/test_eligible_nodes.py -v
```

Expected: 6 passed. If a ranking test fails, re-read `eligible_nodes` in `services/allocation.py` and fix the TEST expectation (report it) — do not touch production code.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add manager/tests/test_eligible_nodes.py
git commit -m "test(manager): eligible_nodes ranking and exclusion rules"
```

### Task 3: Allocation success + atomicity

**Files:**
- Test: `manager/tests/test_allocation.py`

- [ ] **Step 1: Write the test**

```python
"""AllocationService.allocate happy paths and atomic multi-device behavior."""

from sqlalchemy import select

from allocator_manager.models.device import Device, DeviceClass, DeviceStatus
from allocator_manager.models.session import SessionStatus
from allocator_manager.models.session_device import SessionDevice, SessionDeviceStatus
from allocator_manager.services.allocation import AllocationService


async def test_single_device_allocation_activates(db, make_node, make_device, make_session, agent):
    node = await make_node()
    dev = await make_device(node, "wifi_0")
    session = await make_session(devices=["wifi_0"])

    ok = await AllocationService(db).allocate(session)

    assert ok is True
    assert session.status == SessionStatus.ACTIVE
    assert session.node_id == node.id
    assert agent.binds == ["wifi_0"]
    await db.refresh(dev)
    assert dev.status == DeviceStatus.ALLOCATED
    sds = (await db.execute(select(SessionDevice).where(SessionDevice.session_id == session.id))).scalars().all()
    assert len(sds) == 1 and sds[0].status == SessionDeviceStatus.ALLOCATED


async def test_multi_device_allocation_atomic_on_one_node(db, make_node, make_device, make_session, agent):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fp-wifi")
    await make_device(node, "hid_0", device_class=DeviceClass.HID, fingerprint="fp-hid")
    session = await make_session(devices=["wifi_0", "hid_0"])

    await AllocationService(db).allocate(session)

    assert session.status == SessionStatus.ACTIVE
    assert sorted(agent.binds) == ["hid_0", "wifi_0"]
    allocated = (await db.execute(
        select(Device).where(Device.status == DeviceStatus.ALLOCATED)
    )).scalars().all()
    assert len(allocated) == 2


async def test_no_eligible_node_leaves_session_pending(db, make_session, agent):
    session = await make_session(devices=["wifi_0"])
    ok = await AllocationService(db).allocate(session)
    assert ok is False
    assert session.status == SessionStatus.PENDING
    assert agent.binds == []


async def test_partial_availability_does_not_allocate(db, make_node, make_device, make_session, agent):
    # node has wifi_0 FREE but hid_0 ALLOCATED -> cannot satisfy {wifi_0, hid_0}
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fp-wifi")
    await make_device(node, "hid_0", device_class=DeviceClass.HID,
                      status=DeviceStatus.ALLOCATED, fingerprint="fp-hid")
    session = await make_session(devices=["wifi_0", "hid_0"])

    await AllocationService(db).allocate(session)

    assert session.status == SessionStatus.PENDING
    wifi = (await db.execute(
        select(Device).where(Device.logical_name == "wifi_0")
    )).scalar_one()
    assert wifi.status == DeviceStatus.FREE  # untouched
    assert agent.binds == []
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/test_allocation.py -v
```

Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add manager/tests/test_allocation.py
git commit -m "test(manager): allocation success and multi-device atomicity"
```

### Task 4: Bind-saga rollback + NOWAIT locking

**Files:**
- Test: `manager/tests/test_allocation_saga.py`

- [ ] **Step 1: Write the test**

```python
"""The reserve->bind saga: rollback on bind failure, and FOR UPDATE NOWAIT
serialization between two transactions.
"""

import uuid

from sqlalchemy import select

from allocator_manager.models.device import Device, DeviceClass, DeviceStatus
from allocator_manager.models.session import SessionStatus
from allocator_manager.services.allocation import AllocationService

# Deterministic device ids so _bind's `sorted(by str(device_id))` order is fixed:
# wifi_0 sorts before hid_1, so wifi_0 binds first, hid_1 second.
WIFI_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
HID_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


async def test_bind_failure_rolls_back_everything(db, make_node, make_device, make_session, agent):
    node = await make_node()
    await make_device(node, "wifi_0", device_id=WIFI_ID, fingerprint="fp-wifi")
    await make_device(node, "hid_1", device_class=DeviceClass.HID,
                      device_id=HID_ID, fingerprint="fp-hid")
    session = await make_session(devices=["wifi_0", "hid_1"])

    agent.fail_bind_names = {"hid_1"}  # second device fails to bind
    ok = await AllocationService(db).allocate(session)

    assert ok is True  # terminal-for-queue (FAILED), not re-queued
    assert session.status == SessionStatus.FAILED
    assert "bind_failed" in (session.failure_reason or "")
    # wifi_0 bound first, then got unbound during rollback; hid_1 never bound
    assert agent.binds == ["wifi_0"]
    assert agent.unbinds == ["wifi_0"]
    # both backing devices end FREE
    devices = (await db.execute(select(Device))).scalars().all()
    assert {d.logical_name: d.status for d in devices} == {
        "wifi_0": DeviceStatus.FREE, "hid_1": DeviceStatus.FREE,
    }


async def test_locked_device_queues_the_other_reservation(db, Session, make_node, make_device, make_session, agent):
    node = await make_node()
    dev = await make_device(node, "wifi_0")
    session = await make_session(devices=["wifi_0"])

    # A second transaction holds a FOR UPDATE lock on the only matching device.
    locker = Session()
    await locker.begin()
    await locker.execute(
        select(Device).where(Device.id == dev.id).with_for_update()
    )
    try:
        # The reservation must hit NOWAIT, roll back its savepoint, and queue.
        ok = await AllocationService(db).allocate(session)
    finally:
        await locker.rollback()
        await locker.close()

    assert ok is False
    assert session.status == SessionStatus.PENDING
    assert agent.binds == []
    await db.refresh(dev)
    assert dev.status == DeviceStatus.FREE
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/test_allocation_saga.py -v
```

Expected: 2 passed. The locking test is the highest-value test in the suite — if it errors with a connection/loop issue, confirm `conftest`'s engine uses `NullPool` and report.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add manager/tests/test_allocation_saga.py
git commit -m "test(manager): bind-saga rollback and NOWAIT lock serialization"
```

### Task 5: Device naming + rename + portability

**Files:**
- Test: `manager/tests/test_naming.py`

- [ ] **Step 1: Write the test**

```python
"""unique_logical_name lowest-index, rename conflict/force, and custom-name
portability across nodes via the device_names memory table.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from allocator_manager.models.device import DeviceClass
from allocator_manager.models.device_name import DeviceName
from allocator_manager.services.device import DeviceService
from allocator_manager.services.naming import unique_logical_name
from allocator_contract.node import DeviceInfoPayload, DeviceSyncPayload


async def test_unique_name_fills_lowest_free_index(db, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fp0")
    await make_device(node, "wifi_2", fingerprint="fp2")  # gap at wifi_1
    name = await unique_logical_name(db, node.id, DeviceClass.WIFI)
    assert name == "wifi_1"


async def test_rename_conflict_raises_409(db, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fpw")
    await make_device(node, "video_0", device_class=DeviceClass.VIDEO, fingerprint="fpv")
    with pytest.raises(HTTPException) as exc:
        await DeviceService(db).rename(node.name, "video_0", "wifi_0", force=False)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "name_conflict"
    assert exc.value.detail["holder_logical_name"] == "wifi_0"


async def test_rename_force_displaces_holder(db, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fpw")
    await make_device(node, "video_0", device_class=DeviceClass.VIDEO, fingerprint="fpv")
    # force-rename video_0 -> wifi_0: the old wifi_0 holder gets a generic name
    result = await DeviceService(db).rename(node.name, "video_0", "wifi_0", force=True)
    assert result.logical_name == "wifi_0"
    # the displaced holder no longer owns wifi_0
    holder = (await DeviceService(db).get(node.name, "wifi_1"))
    assert holder is not None  # displaced to wifi_1 (lowest free wifi index)


async def test_custom_name_is_remembered(db, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fp-cam")
    await DeviceService(db).rename(node.name, "wifi_0", "cam")
    mem = (await db.execute(select(DeviceName).where(DeviceName.fingerprint == "fp-cam"))).scalar_one()
    assert mem.name == "cam"


async def test_custom_name_follows_device_to_new_node(db, make_node):
    from allocator_manager.services.node import NodeService
    n1 = await make_node(name="n1", ip="10.0.0.1")
    n2 = await make_node(name="n2", ip="10.0.0.2")

    # Device with fingerprint fp-roam first appears on n1 and is named "cam".
    sync = DeviceSyncPayload(devices=[DeviceInfoPayload(
        vendor_id="0bda", product_id="8812", device_class="WIFI",
        usbip_bus_id="1-1", fingerprint="fp-roam",
    )])
    await NodeService(db).sync_devices(str(n1.id), sync)
    await DeviceService(db).rename("n1", "wifi_0", "cam")

    # Same physical device (same fingerprint) now appears on n2.
    await NodeService(db).sync_devices(str(n2.id), sync)
    moved = await DeviceService(db).get("n2", "cam")
    assert moved is not None
    assert moved.node_id == str(n2.id)
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/test_naming.py -v
```

Expected: 5 passed. If `test_rename_force_displaces_holder` expects the wrong generic name, re-read `unique_logical_name` and adjust the TEST (the displaced holder is a WIFI device, so it takes the lowest free `wifi_*` index, which is `wifi_1` after the rename frees `wifi_0`).

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add manager/tests/test_naming.py
git commit -m "test(manager): naming index, rename conflict/force, name portability"
```

### Task 6: Node sync identity + prune + register/heartbeat

**Files:**
- Test: `manager/tests/test_node_sync.py`

- [ ] **Step 1: Write the test**

```python
"""NodeService: fingerprint identity across reconnects, prune of vanished
devices, kick on inventory change, and register/heartbeat."""

from datetime import UTC, datetime

from sqlalchemy import select

from allocator_manager.models.device import Device
from allocator_manager.models.node import Node, NodeStatus
from allocator_manager.services.node import NodeService
from allocator_contract.node import (
    DeviceInfoPayload,
    DeviceSyncPayload,
    NodeHeartbeatPayload,
    NodeRegisterPayload,
)


def _payload(*fingerprints, bus="1-1"):
    return DeviceSyncPayload(devices=[
        DeviceInfoPayload(vendor_id="0bda", product_id="8812", device_class="WIFI",
                          usbip_bus_id=bus, fingerprint=fp)
        for fp in fingerprints
    ])


async def test_register_creates_then_updates_node(db):
    svc = NodeService(db)
    r1 = await svc.register(NodeRegisterPayload(
        name="lab-1", hostname="lab-1.local", ip_address="10.0.0.5"))
    assert r1.registered
    # Re-register same name -> same node row, updated fields, ONLINE.
    r2 = await svc.register(NodeRegisterPayload(
        name="lab-1", hostname="lab-1.local", ip_address="10.0.0.9", agent_port=5001))
    assert r2.node_id == r1.node_id
    nodes = (await db.execute(select(Node))).scalars().all()
    assert len(nodes) == 1
    assert str(nodes[0].ip_address) == "10.0.0.9"
    assert nodes[0].status == NodeStatus.ONLINE


async def test_sync_keeps_device_row_stable_across_reconnect(db, make_node, kick_spy):
    node = await make_node()
    svc = NodeService(db)
    r1 = await svc.sync_devices(str(node.id), _payload("fp-x", bus="1-1"))
    assert r1.new == 1
    dev1 = (await db.execute(select(Device).where(Device.fingerprint == "fp-x"))).scalar_one()

    # Same fingerprint, different bus id -> update in place, not a new row.
    r2 = await svc.sync_devices(str(node.id), _payload("fp-x", bus="2-1"))
    assert r2.new == 0 and r2.updated == 1
    devs = (await db.execute(select(Device).where(Device.fingerprint == "fp-x"))).scalars().all()
    assert len(devs) == 1 and devs[0].id == dev1.id
    assert devs[0].usbip_bus_id == "2-1"


async def test_sync_prunes_vanished_free_device(db, make_node):
    node = await make_node()
    svc = NodeService(db)
    await svc.sync_devices(str(node.id), _payload("fp-gone"))
    # Next sync no longer reports fp-gone -> it is pruned (FREE + unreferenced).
    result = await svc.sync_devices(str(node.id), _payload("fp-stay"))
    assert result.removed == 1
    remaining = (await db.execute(select(Device.fingerprint))).scalars().all()
    assert set(remaining) == {"fp-stay"}


async def test_sync_kicks_queue_on_change_only(db, make_node, kick_spy):
    node = await make_node()
    svc = NodeService(db)
    await svc.sync_devices(str(node.id), _payload("fp-a"))      # new=1 -> kick
    assert kick_spy["n"] == 1
    await svc.sync_devices(str(node.id), _payload("fp-a"))      # only updated -> no kick
    assert kick_spy["n"] == 1


async def test_heartbeat_marks_online(db, make_node):
    node = await make_node(status=NodeStatus.OFFLINE)
    resp = await NodeService(db).heartbeat(
        str(node.id), NodeHeartbeatPayload(timestamp=datetime.now(UTC)))
    assert resp.acknowledged
    await db.refresh(node)
    assert node.status == NodeStatus.ONLINE
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/test_node_sync.py -v
```

Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add manager/tests/test_node_sync.py
git commit -m "test(manager): node sync identity, prune, kick-on-change, heartbeat"
```

### Task 7: Session service lifecycle

**Files:**
- Test: `manager/tests/test_session_service.py`

- [ ] **Step 1: Write the test**

```python
"""SessionService: create (ACTIVE vs PENDING), release, freeze/unfreeze, and
freeze's effect on eligibility."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from allocator_manager.models.device import Device, DeviceStatus
from allocator_manager.models.node import Node
from allocator_manager.models.session import SessionStatus
from allocator_manager.services.allocation import eligible_nodes
from allocator_manager.services.node import NodeService
from allocator_manager.services.session import SessionService
from allocator_contract.session import SessionCreate


async def test_create_active_when_satisfiable(db, make_node, make_device, agent):
    node = await make_node()
    await make_device(node, "wifi_0")
    resp = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))
    assert resp.status == "ACTIVE"
    assert resp.node_name == node.name
    assert agent.binds == ["wifi_0"]


async def test_create_pending_when_no_node(db, agent):
    resp = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))
    assert resp.status == "PENDING"
    assert resp.devices == []


async def test_release_frees_devices_and_kicks(db, make_node, make_device, agent, kick_spy):
    node = await make_node()
    dev = await make_device(node, "wifi_0")
    created = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))
    kick_before = kick_spy["n"]

    await SessionService(db).release(created.session_id, "host-a")

    await db.refresh(dev)
    assert dev.status == DeviceStatus.FREE
    assert agent.unbinds == ["wifi_0"]
    assert kick_spy["n"] == kick_before + 1
    released = await SessionService(db).get(created.session_id, "host-a")
    assert released.status == "RELEASED"


async def test_release_rejects_other_client(db, make_node, make_device, agent):
    node = await make_node()
    await make_device(node, "wifi_0")
    created = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))
    with pytest.raises(HTTPException) as exc:
        await SessionService(db).release(created.session_id, "host-b")
    assert exc.value.status_code == 403


async def test_freeze_only_own_active_session(db, make_node, make_device, agent):
    node = await make_node()
    await make_device(node, "wifi_0")
    created = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))

    # not the owner -> 403
    with pytest.raises(HTTPException) as exc:
        await SessionService(db).freeze(created.session_id, "host-b")
    assert exc.value.status_code == 403

    # owner -> node frozen
    await SessionService(db).freeze(created.session_id, "host-a")
    node_row = (await db.execute(select(Node).where(Node.id == node.id))).scalar_one()
    assert node_row.frozen is True


async def test_frozen_node_excluded_then_unfreeze_kicks(db, make_node, make_device, agent, kick_spy):
    node = await make_node()
    await make_device(node, "wifi_0")
    created = await SessionService(db).create("host-a", SessionCreate(devices=["wifi_0"]))
    await SessionService(db).freeze(created.session_id, "host-a")

    # a different device set on the frozen node is now ineligible
    await make_device(node, "hid_0", fingerprint="fp-hid")
    assert await eligible_nodes(db, ["hid_0"]) == []

    before = kick_spy["n"]
    await NodeService(db).unfreeze(node.name)
    assert kick_spy["n"] == before + 1
    node_row = (await db.execute(select(Node).where(Node.id == node.id))).scalar_one()
    assert node_row.frozen is False
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/test_session_service.py -v
```

Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add manager/tests/test_session_service.py
git commit -m "test(manager): session create/release/freeze/unfreeze lifecycle"
```

### Task 8: Background tasks

**Files:**
- Test: `manager/tests/test_tasks.py`

- [ ] **Step 1: Write the test**

```python
"""Background tasks against the test DB. Each task opens its own
AsyncSessionLocal, which `task_db` repoints at the test database."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from allocator_manager.models.device import DeviceStatus
from allocator_manager.models.node import Node, NodeStatus
from allocator_manager.models.session import SessionStatus
from allocator_manager.tasks.heartbeat_reaper import reap_stale_nodes
from allocator_manager.tasks.session_expiry import expire_stale_sessions
from allocator_manager.tasks.zombie_cleanup import cleanup_zombies


async def test_reaper_marks_stale_node_offline(db, make_node, task_db):
    node = await make_node(status=NodeStatus.ONLINE)
    node.last_heartbeat = datetime.now(UTC) - timedelta(days=1)
    await db.commit()

    await reap_stale_nodes()

    refreshed = (await db.execute(select(Node).where(Node.id == node.id))).scalar_one()
    assert refreshed.status == NodeStatus.OFFLINE


async def test_reaper_leaves_fresh_node_online(db, make_node, task_db):
    node = await make_node(status=NodeStatus.ONLINE)  # last_heartbeat = now
    await reap_stale_nodes()
    refreshed = (await db.execute(select(Node).where(Node.id == node.id))).scalar_one()
    assert refreshed.status == NodeStatus.ONLINE


async def test_expiry_releases_old_active_session(db, make_node, make_device, make_session, task_db):
    node = await make_node()
    dev = await make_device(node, "wifi_0", status=DeviceStatus.ALLOCATED)
    session = await make_session(devices=["wifi_0"], status=SessionStatus.ACTIVE, node_id=node.id)
    session.updated_at = datetime.now(UTC) - timedelta(days=1)
    await db.commit()

    await expire_stale_sessions()

    await db.refresh(session)
    await db.refresh(dev)
    assert session.status == SessionStatus.RELEASED
    assert dev.status == DeviceStatus.FREE


async def test_zombie_cleanup_frees_orphan_allocated_device(db, make_node, make_device, task_db):
    node = await make_node()
    # ALLOCATED device with no ACTIVE session referencing it -> zombie.
    dev = await make_device(node, "wifi_0", status=DeviceStatus.ALLOCATED)
    await cleanup_zombies()
    await db.refresh(dev)
    assert dev.status == DeviceStatus.FREE
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/test_tasks.py -v
```

Expected: 4 passed.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add manager/tests/test_tasks.py
git commit -m "test(manager): heartbeat reaper, session expiry, zombie cleanup"
```

### Task 9: API integration (auth + status codes)

**Files:**
- Test: `manager/tests/test_api.py`

- [ ] **Step 1: Write the test**

```python
"""FastAPI routes via ASGITransport against the real test DB. The `client`
fixture overrides get_db, and patches the agent + queue kick."""

import uuid

AGENT = {"X-Agent-Secret": "dev-agent-secret"}
CID = {"X-Client-Id": "host-a"}


async def test_create_session_requires_client_id(client):
    resp = await client.post("/api/v1/sessions", json={"devices": ["wifi_0"]})
    assert resp.status_code == 400  # missing X-Client-Id


async def test_register_requires_agent_secret(client):
    body = {"name": "lab-1", "hostname": "lab-1.local", "ip_address": "10.0.0.5"}
    bad = await client.post("/internal/v1/nodes/register", json=body)
    assert bad.status_code == 401
    ok = await client.post("/internal/v1/nodes/register", json=body, headers=AGENT)
    assert ok.status_code == 200
    assert ok.json()["registered"] is True


async def test_create_session_pending_returns_201(client):
    resp = await client.post("/api/v1/sessions", json={"devices": ["wifi_0"]}, headers=CID)
    assert resp.status_code == 201
    assert resp.json()["status"] == "PENDING"


async def test_duplicate_devices_is_422(client):
    resp = await client.post("/api/v1/sessions", json={"devices": ["wifi_0", "wifi_0"]}, headers=CID)
    assert resp.status_code == 422


async def test_get_unknown_session_is_404(client):
    resp = await client.get(f"/api/v1/sessions/{uuid.uuid4()}", headers=CID)
    assert resp.status_code == 404


async def test_release_other_clients_session_is_403(client, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0")
    created = await client.post("/api/v1/sessions", json={"devices": ["wifi_0"]}, headers=CID)
    assert created.status_code == 201 and created.json()["status"] == "ACTIVE"
    sid = created.json()["session_id"]

    resp = await client.delete(f"/api/v1/sessions/{sid}", headers={"X-Client-Id": "host-b"})
    assert resp.status_code == 403


async def test_rename_conflict_is_409(client, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fpw")
    await make_device(node, "video_0", fingerprint="fpv")
    resp = await client.post(
        f"/api/v1/devices/{node.name}/video_0/rename",
        json={"name": "wifi_0", "force": False},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "name_conflict"
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/test_api.py -v
```

Expected: 7 passed. Note `make_node`/`make_device` here commit through the `db` fixture's session while the app reads through the `get_db` override — both hit the same Postgres, so seeded rows are visible.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add manager/tests/test_api.py
git commit -m "test(manager): API auth, status codes, ownership via ASGI transport"
```

### Task 10: Full manager suite + verification

**Files:** none (verification only).

- [ ] **Step 1: Run the entire manager suite**

```bash
cd /home/yam/code/allocator/manager && .venv/bin/pytest tests/ -v
```

Expected: all pass — 3 (queue) + 2 (smoke) + 6 (eligible) + 4 (allocation) + 2 (saga) + 5 (naming) + 5 (node sync) + 6 (session) + 4 (tasks) + 7 (api) = **44 passed**. Single container starts once for the session; total run under ~60s after the image is cached.

- [ ] **Step 2: Confirm no production code changed**

```bash
git -C /home/yam/code/allocator diff 8d98493..HEAD --stat -- 'manager/allocator_manager'
```

Expected: empty (no output). If anything appears, a task edited production code — revert it and report.

- [ ] **Step 3: Commit (only if Step 1/2 surfaced a fixture tweak; otherwise skip)**

```bash
cd /home/yam/code/allocator
git add manager/tests/conftest.py
git commit -m "test(manager): suite-run fixture fixes"
```

---

## Verification (whole plan)

`cd manager && .venv/bin/pytest tests/ -q` → 44 passed. `git diff 8d98493..HEAD --stat -- manager/allocator_manager` empty (test-only change; the sole non-test diff in the whole plan is the two dev-extra lines in `manager/pyproject.toml`).
