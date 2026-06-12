# Test Suite Plan C — Docker-Compose End-to-End

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** End-to-end tests that run the **real manager image + Postgres + a stub agent** under docker-compose and drive the **real client library** over HTTP through the full session lifecycle, queue wake, freeze, and API validation — the one layer no unit/integration test covers (real cross-process HTTP, real container networking, the manager→agent bind callback).

**Architecture:** `e2e/docker-compose.test.yml` brings up Postgres, the manager (built from the existing `manager/Dockerfile`), and a tiny FastAPI **stub agent** that registers itself with the manager, syncs a fixed device inventory, and serves `/api/v1/usbip/bind` + `/unbind` (always succeeding, recording every call at `GET /_calls`). A session-scoped pytest fixture runs `docker compose up -d --build`, waits for health + stub registration, yields the published URLs, and tears the stack down. Tests drive `AllocatorClient` (sync) at the manager's host port. `usbip attach` (kernel-side) is never exercised here — it stays mocked at the client-unit level (Plan A).

**Tech Stack:** Docker Compose v2, FastAPI/uvicorn (stub), httpx, pytest (sync — no asyncio), the real `allocator-client` + `allocator-contract` + manager image. Docker daemon required (reachable from the sandbox).

**Spec:** `docs/superpowers/specs/2026-06-12-system-test-suite-design.md` (Plan C of three).

---

## Background for the implementer

- Working dir `/home/yam/code/allocator`. `git add` exact paths only — never `-A`/`.` (untracked `graphify-out/` must never be committed).
- Docker is reachable from the normal Bash sandbox (verified: `docker version` → server 29.5.2). Run all commands normally; no special flag. `docker compose` (v2, space form) is available.
- **No production code changes in Plan C.** Everything is new files under `e2e/` plus a root `run_tests.sh`. Nothing under `manager/`, `agent/`, `client/`, `contract/` `allocator_*` packages changes.
- First `up --build` builds the manager image (deps install, ~1–2 min) and the stub image (~30s), and pulls `postgres:16-alpine`. Budget a generous timeout. Subsequent runs are cached.

### Production facts the e2e relies on (verified against source)

- **Manager image** (`manager/Dockerfile`, build context = repo root): copies `contract/` + `manager/`, `CMD` runs `alembic upgrade head && uvicorn allocator_manager.main:app --host 0.0.0.0 --port 8000`. Health route: `GET /health` → `{"status":"healthy",...}` (200).
- **Manager config defaults** (`manager/allocator_manager/config.py`): `agent_secret="dev-agent-secret"`, `agent_secret_header="X-Agent-Secret"`, `queue_processor_interval=15` (the safety sweep — so a queued session wakes within ~15s even without an explicit kick; release also kicks immediately). `DATABASE_URL` is read from env.
- **Register**: `POST /internal/v1/nodes/register` with header `X-Agent-Secret: dev-agent-secret`, body = `NodeRegisterPayload{name, hostname, ip_address, agent_port, agent_version?}`. `ip_address` is stored in an **INET** column, so it must be a real IP, not a hostname. The manager builds `agent_url = f"http://{ip_address}:{agent_port}"` and later calls the agent at that URL → the stub must register **its own container IP** (resolve at runtime via a UDP-connect, exactly like the real agent's `_detect_advertise_ip`). Response: `{"node_id": "...", "registered": true}`.
- **Sync**: `POST /internal/v1/nodes/{node_id}/devices/sync` (header `X-Agent-Secret`), body = `DeviceSyncPayload{devices:[DeviceInfoPayload{vendor_id, product_id, device_class, usbip_bus_id, fingerprint, ...}]}`. The manager **owns naming**: an agent reports identity+fingerprint only; the manager assigns generic `{class}_{n}` names. One WIFI device → `wifi_0`; one HID device → `hid_0` (independent per-class lowest index).
- **Allocation bind callback**: during `create_session`, the manager calls the agent `POST /api/v1/usbip/bind` with `{bus_id, logical_name, session_id}` and expects `BindResponse{bound, bus_id}`. On release it calls `POST /api/v1/usbip/unbind` `{bus_id, logical_name}` → `UnbindResponse{unbound}`. (Contract: `allocator_contract.usbip`.)
- **Client library** (`allocator_client.client.AllocatorClient`, sync): `AllocatorClient(manager_url=..., client_id=...)`; `.create_session(devices, node=None) -> SessionResponse`; `.get_session(id)`, `.release_session(id)`, `.list_devices()`, `.list_nodes()`. `SessionResponse.status` ∈ {PENDING,ACTIVE,RELEASED,FAILED}; `.devices[i].usbip_bus_id`. **The client's `SessionCreate` rejects duplicate device names locally** (pydantic), so the API-422 test must POST raw via httpx, not through the client.
- **Unfreeze** `POST /api/v1/nodes/{node}/unfreeze` has no auth dependency (safe idempotent reset between tests).

## File structure

```
e2e/
  stub_agent/
    app.py                    # FastAPI stub agent (registers, syncs, serves bind/unbind, /_calls)
    Dockerfile                # python:3.12-slim + contract + fastapi/uvicorn/httpx
  docker-compose.test.yml     # postgres + manager + stub-agent
  pytest.ini                  # testpaths=tests (sync, no asyncio)
  tests/
    conftest.py               # session-scoped stack fixture (compose up/wait/down) + per-test unfreeze
    test_connectivity.py      # stack smoke: manager health, node ONLINE, wifi_0 present
    test_lifecycle.py         # create->ACTIVE->bind recorded->release->FREE->unbind recorded
    test_queue.py             # second session PENDING; release first wakes it
    test_freeze_validation.py # freeze excludes node; unfreeze re-enables; raw duplicate POST -> 422
run_tests.sh                  # orchestrator: every package venv + e2e
```

The `e2e/.venv` (gitignored) holds pytest + httpx + editable client/contract. Tests are **synchronous**.

---

### Task 1: Stub agent (app + image)

**Files:**
- Create: `e2e/stub_agent/app.py`
- Create: `e2e/stub_agent/Dockerfile`

- [ ] **Step 1: Write `e2e/stub_agent/app.py`**

```python
"""Stub node agent for e2e tests.

On startup it registers itself with the manager (using its real container IP,
which the manager stores in an INET column and dials back for bind), then syncs
a fixed two-device inventory. It serves the usbip bind/unbind endpoints the
manager calls during allocation/release, recording every call so the host test
can assert the manager actually drove the agent. It never touches real usbip.
"""

import os
import socket
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from allocator_contract.node import (
    DeviceInfoPayload,
    DeviceSyncPayload,
    NodeRegisterPayload,
)
from allocator_contract.usbip import BindRequest, BindResponse, UnbindRequest, UnbindResponse

MANAGER_URL = os.environ["MANAGER_URL"]
AGENT_SECRET = os.environ.get("AGENT_SECRET", "dev-agent-secret")
NODE_NAME = os.environ.get("NODE_NAME", "stub-1")
AGENT_PORT = int(os.environ.get("AGENT_PORT", "5000"))

CALLS: dict[str, list[str]] = {"binds": [], "unbinds": []}

INVENTORY = [
    DeviceInfoPayload(vendor_id="0bda", product_id="8812", device_class="WIFI",
                      usbip_bus_id="1-1", fingerprint="e2e-wifi"),
    DeviceInfoPayload(vendor_id="046d", product_id="c52b", device_class="HID",
                      usbip_bus_id="1-2", fingerprint="e2e-hid"),
]


def _container_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ip = _container_ip()
    headers = {"X-Agent-Secret": AGENT_SECRET}
    async with httpx.AsyncClient(base_url=MANAGER_URL, headers=headers, timeout=10) as c:
        reg = await c.post("/internal/v1/nodes/register", json=NodeRegisterPayload(
            name=NODE_NAME, hostname=NODE_NAME, ip_address=ip, agent_port=AGENT_PORT,
        ).model_dump())
        reg.raise_for_status()
        node_id = reg.json()["node_id"]
        sync = await c.post(
            f"/internal/v1/nodes/{node_id}/devices/sync",
            json=DeviceSyncPayload(devices=INVENTORY).model_dump(mode="json"),
        )
        sync.raise_for_status()
    app.state.node_id = node_id
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/api/v1/usbip/bind", response_model=BindResponse)
async def bind(body: BindRequest) -> BindResponse:
    CALLS["binds"].append(body.logical_name)
    return BindResponse(bound=True, bus_id=body.bus_id)


@app.post("/api/v1/usbip/unbind", response_model=UnbindResponse)
async def unbind(body: UnbindRequest) -> UnbindResponse:
    CALLS["unbinds"].append(body.logical_name)
    return UnbindResponse(unbound=True)


@app.get("/_calls")
async def calls() -> dict:
    return CALLS


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "node_id": getattr(app.state, "node_id", None)}
```

- [ ] **Step 2: Write `e2e/stub_agent/Dockerfile`**

```dockerfile
# Build context is the repo root so the image can install the shared contract.
FROM python:3.12-slim

WORKDIR /app

COPY contract/ ./contract/
RUN pip install --no-cache-dir ./contract fastapi "uvicorn[standard]" httpx

COPY e2e/stub_agent/app.py ./app.py

EXPOSE 5000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]
```

- [ ] **Step 3: Syntax-check the stub app locally (no container yet)**

```bash
cd /home/yam/code/allocator
python3 -m py_compile e2e/stub_agent/app.py && echo "compiles"
```

Expected: `compiles`. (Imports of `allocator_contract` are not exercised by py_compile.)

- [ ] **Step 4: Commit**

```bash
cd /home/yam/code/allocator
git add e2e/stub_agent/app.py e2e/stub_agent/Dockerfile
git commit -m "test(e2e): stub node agent (register, sync, bind/unbind recorder)"
```

### Task 2: Compose file + stack fixture + connectivity smoke

**Files:**
- Create: `e2e/docker-compose.test.yml`
- Create: `e2e/pytest.ini`
- Create: `e2e/tests/conftest.py`
- Create: `e2e/tests/__init__.py` (empty)
- Test: `e2e/tests/test_connectivity.py`

- [ ] **Step 1: Write `e2e/docker-compose.test.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: allocator
      POSTGRES_PASSWORD: allocator
      POSTGRES_DB: allocator
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U allocator"]
      interval: 3s
      timeout: 3s
      retries: 20

  manager:
    build:
      context: ../..
      dockerfile: manager/Dockerfile
    environment:
      DATABASE_URL: "postgresql+asyncpg://allocator:allocator@postgres:5432/allocator"
      LOG_LEVEL: "INFO"
    ports:
      - "18080:8000"
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)\""]
      interval: 3s
      timeout: 3s
      retries: 30

  stub-agent:
    build:
      context: ../..
      dockerfile: e2e/stub_agent/Dockerfile
    environment:
      MANAGER_URL: "http://manager:8000"
      AGENT_SECRET: "dev-agent-secret"
      NODE_NAME: "stub-1"
      AGENT_PORT: "5000"
    ports:
      - "15000:5000"
    depends_on:
      manager:
        condition: service_healthy
```

- [ ] **Step 2: Write `e2e/pytest.ini`**

```ini
[pytest]
testpaths = tests
```

- [ ] **Step 3: Write `e2e/tests/conftest.py`**

```python
"""Brings the full stack up under docker-compose for the test session.

Session scope: one `up -d --build`, health-wait, then `down -v`. A per-test
autouse fixture unfreezes the node so a freeze test cannot leak state.
"""

import subprocess
import time
from pathlib import Path

import httpx
import pytest

E2E_DIR = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", str(E2E_DIR / "docker-compose.test.yml")]
MANAGER_URL = "http://localhost:18080"
STUB_URL = "http://localhost:15000"


def _wait_http(url: str, timeout: float = 180.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=3)
            if r.status_code == 200:
                return r
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(2)
    raise RuntimeError(f"timed out waiting for {url}: {last}")


def _wait_stub_registered(timeout: float = 90.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{STUB_URL}/health", timeout=3)
            if r.status_code == 200 and r.json().get("node_id"):
                return r.json()["node_id"]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    raise RuntimeError("stub agent never registered a node_id")


@pytest.fixture(scope="session")
def stack():
    subprocess.run(COMPOSE + ["up", "-d", "--build"], cwd=E2E_DIR, check=True)
    try:
        _wait_http(f"{MANAGER_URL}/health")
        node_id = _wait_stub_registered()
        yield {"manager_url": MANAGER_URL, "stub_url": STUB_URL, "node_id": node_id}
    finally:
        subprocess.run(COMPOSE + ["down", "-v"], cwd=E2E_DIR, check=False)


@pytest.fixture
def manager_url(stack):
    return stack["manager_url"]


@pytest.fixture
def stub_url(stack):
    return stack["stub_url"]


@pytest.fixture(autouse=True)
def _unfreeze_after(stack):
    """Safety net: unfreeze stub-1 after every test (idempotent, no auth)."""
    yield
    try:
        httpx.post(f"{stack['manager_url']}/api/v1/nodes/stub-1/unfreeze", timeout=5)
    except Exception:  # noqa: BLE001
        pass
```

- [ ] **Step 4: Write `e2e/tests/__init__.py`** (empty file).

- [ ] **Step 5: Write `e2e/tests/test_connectivity.py`**

```python
"""Stack smoke: the manager is up, the stub registered an ONLINE node, and the
manager assigned the expected per-class generic names."""

from allocator_client.client import AllocatorClient


def test_node_is_online(manager_url):
    with AllocatorClient(manager_url=manager_url, client_id="e2e-smoke") as client:
        nodes = client.list_nodes()
    names = {n.name: n for n in nodes.items}
    assert "stub-1" in names
    assert names["stub-1"].status == "ONLINE"


def test_devices_named_by_manager(manager_url):
    with AllocatorClient(manager_url=manager_url, client_id="e2e-smoke") as client:
        devices = client.list_devices()
    logical = {d.logical_name for d in devices.items}
    # one WIFI + one HID synced -> manager assigns wifi_0 and hid_0
    assert "wifi_0" in logical
    assert "hid_0" in logical
```

- [ ] **Step 6: Create the e2e venv and run the smoke (this builds images — slow first time)**

```bash
cd /home/yam/code/allocator/e2e
python3 -m venv .venv
.venv/bin/pip install -e ../client -e ../contract pytest httpx
.venv/bin/pytest tests/test_connectivity.py -v
```

Expected: 2 passed. The first run does `docker compose up --build` (manager image build can take 1–2 min). If the manager image fails to build, run `docker compose -f e2e/docker-compose.test.yml build manager` from the repo root and report the build error — do NOT change production code. Confirm `e2e/.venv` is git-ignored (root `.gitignore` covers `.venv/`).

- [ ] **Step 7: Commit**

```bash
cd /home/yam/code/allocator
git add e2e/docker-compose.test.yml e2e/pytest.ini e2e/tests/conftest.py e2e/tests/__init__.py e2e/tests/test_connectivity.py
git commit -m "test(e2e): compose stack fixture + connectivity smoke"
```

### Task 3: Session lifecycle e2e

**Files:**
- Test: `e2e/tests/test_lifecycle.py`

- [ ] **Step 1: Write the test**

```python
"""Full lifecycle over real HTTP: create -> ACTIVE -> the manager called the
stub agent's bind -> release -> the manager called unbind -> session RELEASED.
"""

import httpx

from allocator_client.client import AllocatorClient


def test_session_lifecycle_drives_agent(manager_url, stub_url):
    with AllocatorClient(manager_url=manager_url, client_id="e2e-life") as client:
        session = client.create_session(["wifi_0"])
        assert session.status == "ACTIVE"
        assert session.node_name == "stub-1"
        # the session carries usbip coordinates from the agent
        dev = next(d for d in session.devices if d.logical_name == "wifi_0")
        assert dev.usbip_bus_id  # non-empty bus id returned by the stub's bind

        # the manager actually called the stub agent's bind endpoint
        binds = httpx.get(f"{stub_url}/_calls", timeout=5).json()["binds"]
        assert "wifi_0" in binds

        client.release_session(session.session_id)
        assert client.get_session(session.session_id).status == "RELEASED"

        # release drove the stub agent's unbind endpoint
        unbinds = httpx.get(f"{stub_url}/_calls", timeout=5).json()["unbinds"]
        assert "wifi_0" in unbinds


def test_device_is_free_again_after_release(manager_url):
    with AllocatorClient(manager_url=manager_url, client_id="e2e-life2") as client:
        session = client.create_session(["wifi_0"])
        assert session.status == "ACTIVE"
        client.release_session(session.session_id)
        # wifi_0 must be FREE again -> a fresh session allocates immediately
        again = client.create_session(["wifi_0"])
        assert again.status == "ACTIVE"
        client.release_session(again.session_id)
```

- [ ] **Step 2: Run (stack already cached from Task 2)**

```bash
cd /home/yam/code/allocator/e2e && .venv/bin/pytest tests/test_lifecycle.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add e2e/tests/test_lifecycle.py
git commit -m "test(e2e): full session lifecycle drives the agent bind/unbind"
```

### Task 4: Queue wake e2e

**Files:**
- Test: `e2e/tests/test_queue.py`

- [ ] **Step 1: Write the test**

```python
"""Two clients contend for the single wifi_0. The second is queued PENDING and
must wake to ACTIVE after the first releases (global skip-FIFO, end to end)."""

import time

from allocator_client.client import AllocatorClient


def _wait_status(client, session_id, target, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get_session(session_id).status
        if status == target:
            return status
        time.sleep(0.5)
    return client.get_session(session_id).status


def test_queued_session_wakes_on_release(manager_url):
    c1 = AllocatorClient(manager_url=manager_url, client_id="e2e-q1")
    c2 = AllocatorClient(manager_url=manager_url, client_id="e2e-q2")
    try:
        s1 = c1.create_session(["wifi_0"])
        assert s1.status == "ACTIVE"

        s2 = c2.create_session(["wifi_0"])
        assert s2.status == "PENDING"  # only one wifi_0 -> queued

        c1.release_session(s1.session_id)

        # the queue processor (release kick + 15s safety sweep) promotes s2
        assert _wait_status(c2, s2.session_id, "ACTIVE") == "ACTIVE"

        c2.release_session(s2.session_id)
    finally:
        c1.close()
        c2.close()
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/e2e && .venv/bin/pytest tests/test_queue.py -v
```

Expected: 1 passed (allow up to ~20s — the wake happens on the release kick, well under the 30s poll).

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add e2e/tests/test_queue.py
git commit -m "test(e2e): queued session wakes on release"
```

### Task 5: Freeze + API validation e2e

**Files:**
- Test: `e2e/tests/test_freeze_validation.py`

- [ ] **Step 1: Write the test**

```python
"""Freeze excludes the node from new sessions; unfreeze re-enables it. And a
raw duplicate-devices POST is rejected by the API with 422 (the client library
would reject duplicates locally, so we bypass it with httpx)."""

import httpx

from allocator_client.client import AllocatorClient


def test_freeze_excludes_then_unfreeze_restores(manager_url):
    client = AllocatorClient(manager_url=manager_url, client_id="e2e-freeze")
    try:
        active = client.create_session(["wifi_0"])
        assert active.status == "ACTIVE"

        # freeze this session's node
        client.freeze_session_node(active.session_id)

        # a new session for hid_0 on the same (now frozen) node must queue
        pending = client.create_session(["hid_0"])
        assert pending.status == "PENDING"

        # unfreeze -> the queued hid_0 session can now allocate
        client.unfreeze_node("stub-1")
        import time
        deadline = time.time() + 30
        while time.time() < deadline:
            if client.get_session(pending.session_id).status == "ACTIVE":
                break
            time.sleep(0.5)
        assert client.get_session(pending.session_id).status == "ACTIVE"

        client.release_session(active.session_id)
        client.release_session(pending.session_id)
    finally:
        client.unfreeze_node("stub-1")
        client.close()


def test_duplicate_devices_rejected_by_api(manager_url):
    # Bypass the client library (which rejects duplicates locally) and POST raw.
    resp = httpx.post(
        f"{manager_url}/api/v1/sessions",
        json={"devices": ["wifi_0", "wifi_0"]},
        headers={"X-Client-Id": "e2e-dup"},
        timeout=10,
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/e2e && .venv/bin/pytest tests/test_freeze_validation.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add e2e/tests/test_freeze_validation.py
git commit -m "test(e2e): node freeze/unfreeze and API duplicate rejection"
```

### Task 6: Orchestrator + full run + verification

**Files:**
- Create: `run_tests.sh`

- [ ] **Step 1: Write `run_tests.sh`**

```bash
#!/usr/bin/env bash
# Run every package test suite plus the docker-compose e2e suite.
# Each suite uses its own .venv. Reports all failures; exits non-zero if any fail.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
fail=0

run() {
    local name="$1" dir="$2"
    echo "=== $name ==="
    if (cd "$ROOT/$dir" && .venv/bin/pytest -q); then
        echo "--- $name OK"
    else
        echo "--- $name FAILED"
        fail=1
    fi
}

run contract contract
run agent    agent
run client   client
run manager  manager
run e2e      e2e

echo
if [ "$fail" -eq 0 ]; then echo "ALL SUITES PASSED"; else echo "SOME SUITES FAILED"; fi
exit "$fail"
```

- [ ] **Step 2: Make it executable and run the e2e suite end to end**

```bash
chmod +x /home/yam/code/allocator/run_tests.sh
cd /home/yam/code/allocator/e2e && .venv/bin/pytest tests/ -v
```

Expected: 7 e2e tests pass (2 connectivity + 2 lifecycle + 1 queue + 2 freeze/validation). One compose stack starts once for the whole e2e session and tears down at the end.

- [ ] **Step 3: Run the full orchestrator (all five suites)**

```bash
/home/yam/code/allocator/run_tests.sh
```

Expected: ends with `ALL SUITES PASSED`. Totals: contract 26 + agent 30 + client 31 + manager 44 + e2e 7 = **138** across all suites.

- [ ] **Step 4: Confirm no production code changed across Plan C**

```bash
git -C /home/yam/code/allocator diff 43efcb4..HEAD --stat -- 'manager/allocator_manager' 'agent/allocator_agent' 'client/allocator_client' 'contract/allocator_contract'
```

Expected: empty.

- [ ] **Step 5: Commit**

```bash
cd /home/yam/code/allocator
git add run_tests.sh
git commit -m "test: run_tests.sh orchestrates all package suites + e2e"
```

---

## Verification (whole plan)

`cd e2e && .venv/bin/pytest tests/ -q` → 7 passed (single compose stack). `run_tests.sh` → `ALL SUITES PASSED`. `git diff 43efcb4..HEAD --stat` over the four `allocator_*` packages is empty — Plan C is entirely new `e2e/` files plus `run_tests.sh`, zero production change.
