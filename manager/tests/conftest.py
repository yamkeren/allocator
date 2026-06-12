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
