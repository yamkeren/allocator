# Allocator

Distributed USB resource orchestration system. Lets multiple clients share a pool of physical USB devices across a cluster of Linux nodes by exposing them over USB/IP and managing exclusive sessions with atomic allocation.

## Architecture

```
┌─────────────┐      REST API        ┌────────────────┐
│   Client    │ ──────────────────── │    Manager     │
│  (CLI/lib)  │   X-API-Key auth     │  (FastAPI +    │
└─────────────┘                      │   Postgres)    │
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
| `allocator-manager` | `manager/` | Central orchestrator — REST API, allocation engine |
| `allocator-agent` | `agent/` | Per-node daemon — USB/IP bind/unbind, heartbeat |
| `allocator-client` | `client/` | CLI (`allocator`) and Python client library |

## Concepts

**Node** — A Linux machine running `allocator-agent`. Nodes register themselves with the manager on startup. Status is `ONLINE` or `OFFLINE`.

**Device** — A USB device registered by an admin via the manager API. Devices have a human-assigned `logical_name` and a `device_class`. Status is `FREE`, `ALLOCATED`, or `ERROR`.

**Group** — A named, ordered set of devices that must be allocated together atomically. Clients request sessions against a group.

**Session** — A lease granting exclusive access to all devices in a group. Sessions are created, used, and released manually — there is no auto-expiry. Status is `ACTIVE`, `RELEASED`, or `FAILED`.

## Allocation algorithm

Allocation runs in two phases:

**Phase 1 — Reservation (single DB transaction)**
1. `SELECT … FOR UPDATE` all group devices in canonical UUID order (prevents deadlocks).
2. Validate every device is `FREE` and its node is `ONLINE`.
3. Bulk-update devices to `ALLOCATED`, insert `session_devices` rows.
4. Commit.

**Phase 2 — Binding (saga)**
1. For each device: call the node agent's bind endpoint (`usbip bind`).
2. On success: update `usbip_bus_id`, session → `ACTIVE`.
3. On any failure: reverse-order unbind all already-bound devices, free all devices, session → `FAILED`.

## Getting started

### Prerequisites

- Docker + Docker Compose
- Python 3.12+
- `usbip` on each agent node (`linux-tools-common` or distro equivalent)

### Start the manager stack

```bash
cd deploy
docker compose up -d
```

This starts PostgreSQL 16 and the manager on port 8000.

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

export MANAGER_URL=http://<manager-host>:8000
export AGENT_SECRET=dev-agent-secret
export NODE_NAME=node-a        # defaults to hostname

allocator-agent start
```

The agent registers itself with the manager on startup and sends periodic heartbeats.

### Install the client

```bash
cd client
pip install -e .

export ALLOCATOR_URL=http://<manager-host>:8000
export ALLOCATOR_API_KEY=your-key
```

## Admin setup

Devices are registered manually — there is no auto-discovery.

**1. Check that the node registered itself:**
```bash
allocator node list
```

**2. Register a device on a node:**
```bash
allocator device register \
  --node-id <node-uuid> \
  --name wifi_usb_1 \
  --vendor-id 0bda \
  --product-id 8812 \
  --class WIFI \
  --bus-id 1-1.2
```

Or via the API directly:
```bash
curl -X POST http://manager:8000/api/v1/devices \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "node_id": "<node-uuid>",
    "logical_name": "wifi_usb_1",
    "vendor_id": "0bda",
    "product_id": "8812",
    "device_class": "WIFI",
    "usbip_bus_id": "1-1.2"
  }'
```

**3. Create a group:**
```bash
allocator group create --name wifi-lab --devices wifi_usb_1 wifi_usb_2
```

## CLI usage

```bash
# Session management
allocator session create --group wifi-lab
allocator session list
allocator session show <session-id>
allocator session release <session-id>

# Group management
allocator group create --name my-group --devices dev-a dev-b
allocator group list
allocator group show my-group
allocator group delete my-group

# Device management
allocator device list
allocator device show <logical-name>

# Node inspection
allocator node list
allocator node show <node-id>
```

When a session is created, the response includes the `usbip_attach_command` for each device so the client machine can attach them locally.

## Python client library

```python
from allocator_client.client import AllocatorClient

async with AllocatorClient("http://manager:8000", api_key="my-key") as client:
    session = await client.create_session("wifi-lab")
    session_id = session["session_id"]

    if session["status"] == "FAILED":
        print(session["failure_reason"])
    else:
        # use USB devices ...
        await client.release_session(session_id)
```

For automatic USB attach/detach use the context manager helper:

```python
from allocator_client.session_context import AllocatorSession

async with AllocatorSession(client, "wifi-lab") as session:
    # usbip attach has been called for each device
    # session.devices contains logical_name, node_ip, usbip_bus_id, local_port
    pass
# on exit: usbip detach + session released automatically
```

## REST API

| Prefix | Auth | Description |
|---|---|---|
| `GET /health` | None | Health check |
| `POST /api/v1/sessions` | `X-API-Key` | Create session (allocate group) |
| `GET /api/v1/sessions` | `X-API-Key` | List sessions |
| `GET /api/v1/sessions/{id}` | `X-API-Key` | Get session |
| `DELETE /api/v1/sessions/{id}` | `X-API-Key` | Release session |
| `POST /api/v1/devices` | `X-API-Key` | Register a device |
| `GET /api/v1/devices` | `X-API-Key` | List devices |
| `GET /api/v1/devices/{name}` | `X-API-Key` | Get device |
| `PATCH /api/v1/devices/{name}` | `X-API-Key` | Update device |
| `DELETE /api/v1/devices/{name}` | `X-API-Key` | Remove device |
| `/api/v1/groups` | `X-API-Key` | Group CRUD |
| `/api/v1/nodes` | `X-API-Key` | Node listing |
| `POST /internal/v1/nodes/register` | `X-Agent-Secret` | Agent registration |
| `POST /internal/v1/nodes/{id}/heartbeat` | `X-Agent-Secret` | Agent heartbeat |

Interactive docs: `http://manager:8000/docs`

## Configuration

### Manager environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://allocator:allocator@localhost:5432/allocator` | Postgres DSN |
| `AGENT_SECRET` | `dev-agent-secret` | Shared secret for agent auth |
| `AGENT_REQUEST_TIMEOUT` | `10.0` | Seconds for manager → agent calls |
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

## Database schema

```
nodes ──< devices ──< group_devices >── groups
                                            │
                                        sessions ──< session_devices
```

All tables use hard deletes. No soft-delete, no audit log.

## Development

```bash
# Manager
cd manager && pip install -e ".[dev]" && pytest

# Agent
cd agent && pip install -e ".[dev]" && pytest

# Client
cd client && pip install -e ".[dev]" && pytest
```

All packages use `ruff` (line length 100, target Python 3.12) and `pytest-asyncio` in auto mode.

## Project status

`v0.1.0` — initial design. Auth accepts any non-empty API key as the client identity.
