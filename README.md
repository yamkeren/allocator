# Allocator

Distributed USB resource orchestration system. Lets multiple clients share a pool of physical USB devices across a cluster of Linux nodes by exposing them over USB/IP and managing leased sessions with atomic allocation.

## Architecture

```
┌─────────────┐      REST API        ┌────────────────┐
│   Client    │ ──────────────────── │    Manager     │
│  (CLI/lib)  │   X-API-Key auth     │  (FastAPI +    │
└─────────────┘                      │   Postgres +   │
                                     │   Redis)       │
                                     └───────┬────────┘
                                             │ Internal API
                                    X-Agent-Secret auth
                                             │
                              ┌──────────────┼──────────────┐
                              │              │              │
                         ┌────┴────┐   ┌────┴────┐   ┌────┴────┐
                         │  Agent  │   │  Agent  │   │  Agent  │
                         │ Node A  │   │ Node B  │   │ Node C  │
                         └────┬────┘   └────┬────┘   └────┬────┘
                              │              │              │
                           USB devs       USB devs       USB devs
```

Three packages live in this repo:

| Package | Path | Description |
|---|---|---|
| `allocator-manager` | `manager/` | Central orchestrator — REST API, allocation engine, background tasks |
| `allocator-agent` | `agent/` | Per-node daemon — USB discovery, USB/IP bind/unbind, heartbeat |
| `allocator-client` | `client/` | CLI (`allocator`) and Python client library |

## Concepts

**Node** — A Linux machine running `allocator-agent`. Nodes register themselves with the manager and send periodic heartbeats. A node is `ONLINE`, `OFFLINE`, or `DEGRADED`.

**Device** — A physical USB device attached to a node. Devices are discovered automatically via udev and tracked in the manager's database with a lifecycle status: `FREE → RESERVED → BINDING → BOUND → … → FREE`.

**Group** — A named, ordered set of devices that must be allocated together atomically. Clients request sessions against a group, not individual devices.

**Session** — A time-limited lease granting exclusive access to all devices in a group. Sessions follow the lifecycle:

```
PENDING → RESERVING → BINDING → ACTIVE → RELEASING → RELEASED
                 ↘ FAILED          ↗ EXPIRED (heartbeat timeout)
```

## Allocation algorithm

Allocation runs in two phases to ensure atomicity without long-held locks:

**Phase 1 — Reservation (single DB transaction)**
1. Acquire a PostgreSQL advisory lock on the group name.
2. `SELECT … FOR UPDATE` all group devices in canonical UUID order (prevents deadlocks).
3. Validate every device is `FREE` and its node is `ONLINE`.
4. Bulk-update devices to `RESERVED`, insert `session_devices` rows.
5. Commit.

**Phase 2 — Binding (saga)**
1. For each device (sorted for determinism): call the node agent's bind endpoint.
2. On success: mark device `BOUND`.
3. On any failure: reverse-order unbind all already-bound devices and mark the session `FAILED`.

## Getting started

### Prerequisites

- Docker + Docker Compose
- Python 3.12+ (for local development)
- `usbip` on each agent node (`linux-tools-common` or `usbip` package)

### Spin up the manager stack

```bash
cd deploy
docker compose up -d
```

This starts PostgreSQL 16, Redis 7, the manager (port 8000), and Prometheus (port 9090).

Run database migrations:

```bash
cd manager
pip install -e .
alembic upgrade head
```

### Install and run the agent

On each USB host node:

```bash
cd agent
pip install -e .

# Configure via environment or .env file
export MANAGER_URL=http://<manager-host>:8000
export AGENT_SECRET=dev-agent-secret   # must match manager's AGENT_SECRET
export NODE_NAME=node-a                # defaults to hostname

allocator-agent
```

The agent will:
1. Scan USB devices via pyudev/pyusb.
2. Register/update the node with the manager.
3. Send heartbeats every 15 seconds.
4. Watch for hotplug events via udev and report them.

### Install the client

```bash
cd client
pip install -e .
```

Configure:

```bash
export ALLOCATOR_MANAGER_URL=http://<manager-host>:8000
export ALLOCATOR_API_KEY=your-key
```

## CLI usage

```bash
# Group management
allocator group create --name my-group --devices dev-a dev-b
allocator group list
allocator group show my-group
allocator group delete my-group

# Session management
allocator session create --group my-group --lease 7200
allocator session list
allocator session show <session-id>
allocator session release <session-id>

# Inventory inspection
allocator device list
allocator device show <logical-name>
allocator node list
allocator node show <node-id>
```

When a session is created, the output includes the USB/IP attach command for each device so the client machine can attach them locally.

## Python client library

```python
from allocator_client.client import AllocatorClient

async with AllocatorClient("http://manager:8000", api_key="my-key") as client:
    session = await client.create_session("my-group", lease_duration=3600)
    session_id = session["session_id"]

    # ... use USB devices ...

    await client.release_session(session_id)
```

For long-running workloads, send periodic heartbeats to keep the session alive:

```python
await client.heartbeat(session_id)
```

## REST API

The manager exposes:

| Prefix | Auth | Description |
|---|---|---|
| `GET /health` | None | Health check |
| `GET /metrics` | None | Prometheus metrics |
| `/api/v1/sessions` | `X-API-Key` | Session CRUD + heartbeat |
| `/api/v1/groups` | `X-API-Key` | Group management |
| `/api/v1/devices` | `X-API-Key` | Device inventory (read-only) |
| `/api/v1/nodes` | `X-API-Key` | Node listing |
| `/internal/v1/nodes` | `X-Agent-Secret` | Agent registration + heartbeat |

Interactive docs: `http://manager:8000/docs`

## Configuration

### Manager environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://allocator:allocator@localhost:5432/allocator` | Postgres DSN |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL |
| `SECRET_KEY` | `dev-secret-change-in-production` | Application secret |
| `AGENT_SECRET` | `dev-agent-secret` | Shared secret for agent auth |
| `DEFAULT_LEASE_DURATION` | `3600` | Session lease in seconds |
| `HEARTBEAT_TIMEOUT` | `90` | Seconds before session expires |
| `NODE_OFFLINE_TIMEOUT` | `90` | Seconds before node marked OFFLINE |
| `LOG_LEVEL` | `INFO` | Log level |
| `LOG_JSON` | `true` | Emit JSON logs |

### Agent environment variables

| Variable | Default | Description |
|---|---|---|
| `MANAGER_URL` | `http://localhost:8000` | Manager base URL |
| `AGENT_SECRET` | `dev-agent-secret` | Shared secret |
| `NODE_NAME` | *(hostname)* | Node identifier |
| `AGENT_PORT` | `5000` | Port the agent listens on |
| `HEARTBEAT_INTERVAL` | `15` | Seconds between heartbeats |
| `USBIP_BIND_TIMEOUT` | `10` | Seconds for usbip bind/unbind |
| `LOG_LEVEL` | `INFO` | Log level |

## Background tasks

The manager runs three background tasks on a scheduler:

| Task | Default interval | Description |
|---|---|---|
| Heartbeat reaper | 30 s | Marks nodes `OFFLINE` if no heartbeat within `NODE_OFFLINE_TIMEOUT` |
| Session expiry | 60 s | Expires `ACTIVE` sessions past `lease_expires_at` |
| Zombie cleanup | 300 s | Releases sessions stuck in `BINDING`/`RELEASING` past `BIND_TIMEOUT` |

## Observability

Prometheus metrics exposed at `/metrics`:

| Metric | Type | Description |
|---|---|---|
| `allocator_sessions_total` | Counter | Sessions by terminal status |
| `allocator_sessions_active` | Gauge | Currently active sessions |
| `allocator_allocation_duration_seconds` | Histogram | Full reserve + bind latency |
| `allocator_allocation_failures_total` | Counter | Failures by reason |
| `allocator_bind_duration_seconds` | Histogram | Per-device bind call latency |
| `allocator_rollback_total` | Counter | Rollbacks by reason |
| `allocator_devices_total` | Gauge | Device count by status and class |
| `allocator_nodes_total` | Gauge | Node count by status |

Structured JSON logs (structlog) are emitted to stdout by all three components.

## Database schema

```
nodes ──< devices ──< group_devices >── groups
                             │
                         sessions ──< session_devices
                             │
                         audit_log
```

All entity tables carry `created_at`, `updated_at`, and `deleted_at` (soft-delete). The `audit_log` table records every device status transition.

## Development

```bash
# Manager
cd manager
pip install -e ".[dev]"
pytest

# Agent
cd agent
pip install -e ".[dev]"
pytest

# Client
cd client
pip install -e ".[dev]"
pytest
```

All packages use `ruff` (line length 100, target Python 3.12) and `pytest-asyncio` in auto mode.

## Project status

This is an initial design (`v0.1.0`). The auth layer accepts any non-empty API key as the client identity — a proper clients table with hashed keys is planned for a later phase.
