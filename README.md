# Allocator

Distributed USB resource orchestration. Multiple clients share a pool of physical USB devices
across a cluster of Linux nodes: the manager allocates named **groups** of devices to client
**sessions**, and the devices are carried to the client over **USB/IP** (`usbip bind`/`attach`).
It is a correctness-first scheduler — atomic reservation, row locking, a saga with rollback,
leasing, a wait queue, and node freezing.

## Architecture

```
┌─────────────┐   REST (X-Client-Id = hostname)    ┌────────────────┐
│   Client    │ ─────────────────────────────────  │    Manager     │
│  (CLI/lib)  │                                    │  (FastAPI +    │
└──────┬──────┘                                    │   Postgres)    │
       │                                           └───────┬────────┘
       │ USB/IP attach (TCP 3240)                          │ internal API
       │                                          (X-Agent-Secret)
       │                              ┌──────────────┼──────────────┐
       │                         ┌────┴────┐    ┌────┴────┐    ┌────┴────┐
       └──── usbip attach ─────▶ │  Agent  │    │  Agent  │    │  Agent  │
                                 │ Node A  │    │ Node B  │    │ Node C  │
                                 └────┬────┘    └────┬────┘    └────┬────┘
                                   USB devs       USB devs       USB devs
```

The client talks **only to the manager** for orchestration; the manager returns ready-to-run
`usbip attach` commands, and the client connects directly to the node's `usbipd` for transport.

Four packages live in this repo:

| Package | Path | Description |
|---|---|---|
| `allocator-contract` | `contract/` | Shared Pydantic API models (single source of truth) |
| `allocator-manager` | `manager/` | Central orchestrator — REST API, allocation engine, Postgres |
| `allocator-agent` | `agent/` | Per-node daemon — USB discovery, `usbip` bind/unbind, heartbeat |
| `allocator-client` | `client/` | CLI (`allocator`) + Python client library (async & sync) |

`manager`, `agent`, and `client` all depend on `contract` (install it first).

## Concepts

**Node** — a Linux machine running `allocator-agent`. Agents self-register and heartbeat.
Status is `ONLINE`/`OFFLINE`; a node can also be **frozen** (excluded from new sessions).

**Device** — **auto-discovered** by the agent from sysfs + `usbip` (no manual registration).
A device's stable identity is its **fingerprint** (`sha256(vid:pid:serial:mac)`). The manager
assigns a **per-node** name like `wifi_0`/`hid_1`; a client can give it a **custom name** that
follows the physical device across nodes. Status is `FREE`/`ALLOCATED`/`ERROR`. Names are
unique **per node**, not globally.

**Group** — an ordered **list of logical names** (a template). It is resolved to actual devices
on a single node when a session is created — groups are not bound to specific devices.

**Session** — a lease over a group's devices on **one node**. Status is
`PENDING` (queued, waiting for a node) → `ACTIVE` → `RELEASED`/`FAILED`. Owned by the client's
identity (its hostname).

## Allocation algorithm

A session is satisfied by a **single node** that has every group device free.

1. **Select node** — candidate nodes are `ONLINE`, **not frozen**, and have a `FREE` device for
   **every** name in the group. Among candidates, pick the one with the **fewest total
   devices** (least extra hardware tied up). A client may **pin** a node instead.
2. **Reserve** (one DB transaction) — lock the chosen devices `FOR UPDATE NOWAIT`, re-validate
   `FREE`, mark `ALLOCATED`, insert `session_devices`.
3. **Bind** (saga, outside the txn) — call the node agent's `usbip bind` per device; on any
   failure, reverse-order unbind + free everything, session → `FAILED`.
4. **Queue** — if no node can satisfy the group right now, the session is `PENDING`. A
   **per-group FIFO** queue retries it when a node frees up (release / device sync / unfreeze),
   plus a periodic safety sweep.

**Freezing** — a client holding an `ACTIVE` session can freeze that session's node; it's then
excluded from all new sessions and **stays frozen after release**, until **anyone** unfreezes it.

## Getting started

### Prerequisites

- Docker + Docker Compose (manager stack)
- Python 3.12+
- `usbip` on each agent node and on client machines that attach devices
  (`linux-tools-generic` / distro equivalent)

> **Layout:** the deploy stack and each component's setup script live inside their component
> (`manager/deploy/`, `manager/setup.sh`, `agent/setup.sh`, `client/setup.sh`). All components
> depend on `contract/`, so it is installed first (the setup scripts and Docker images do this).

### Start the manager stack

Recommended — `manager/setup.sh` brings up the stack and publishes a LAN domain over mDNS so
clients/agents can reach it by name (no port needed at the default port 80):

```bash
manager/setup.sh --domain allocator            # -> http://allocator.local
manager/setup.sh --domain allocator --port 8000  # -> http://allocator.local:8000
```

Or bring up the stack directly (host port via `MANAGER_PORT`, default 80; pgAdmin on `:5050`):

```bash
cd manager/deploy
docker compose up -d                 # manager on http://localhost
MANAGER_PORT=8000 docker compose up -d   # or pin a host port
```

The manager image installs `allocator-contract` then the manager, and runs migrations on start.
The container always listens on 8000 internally; `MANAGER_PORT` only changes the published host port.
To run migrations against a local install: `cd manager && pip install -e ../contract -e . && alembic upgrade head`.

### Install and run the agent

On each USB host node — easiest via the setup script (installs contract + agent, sets up
`usbipd`, writes the systemd unit, loads kernel modules):

```bash
sudo agent/setup.sh --manager-url http://<manager-host>:8000
```

Or manually:

```bash
cd agent && pip install -e ../contract -e .
export MANAGER_URL=http://<manager-host>:8000
export AGENT_SECRET=dev-agent-secret
allocator-agent start
```

The agent self-registers (advertising its real outbound IP), heartbeats, and **continuously
discovers** USB devices, pushing the inventory to the manager.

### Install the client

```bash
client/setup.sh                                   # venv + contract + client + dep check
allocator config set url http://<manager-host>:8000
# No API key — identity is your hostname (override: allocator config set client_id <name>).
```

See [client/README.md](client/README.md) for the full client guide.

## Usage

Devices are discovered automatically — no manual registration.

```bash
# 1. see what the agents reported (names are per node)
allocator node list
allocator device list

# 2. (optional) give a device a custom, portable name
allocator device name <node> wifi_0 lab_wifi      # prompts on a name clash

# 3. define a group as a list of names, then open a session
allocator group create wifi-lab --devices lab_wifi,hid_0
allocator session create --group wifi-lab [--node <node>]

# 4. attach the devices the session hands out (printed per device)
sudo usbip attach -r <node_ip> -b <bus_id>
# ... work ...
allocator session release <session-id>
```

If no node can satisfy the group, the session is `PENDING` and starts automatically when one
frees up.

### CLI reference

```bash
# sessions
allocator session create --group G [--node N]    # allocate (pin optional)
allocator session list [--status ACTIVE|PENDING|RELEASED|FAILED]
allocator session show <id>
allocator session freeze <id>                     # freeze this session's node
allocator session release <id>

# groups
allocator group create NAME --devices a,b,c
allocator group list / show NAME / delete NAME

# devices (names are per-node)
allocator device list [--node N] [--status S] [--class C]
allocator device show <node> <name>
allocator device name <node> <name> <new_name>    # custom name, follows the device

# nodes
allocator node list / show <id>
allocator node unfreeze <node>                    # anyone can unfreeze
```

## Python client library

`AllocatorClient` is a synchronous client (no event loop) returning typed `allocator_contract`
models:

```python
from allocator_client import AllocatorClient

with AllocatorClient("http://manager:8000") as c:   # identity = hostname
    s = c.create_session("wifi-lab")                # -> SessionResponse
    for d in s.devices:
        print(d.logical_name, d.usbip_attach_command)
    c.release_session(s.session_id)
```

`AllocatorSession` is a context manager that allocates a group, `usbip attach`es each device,
and detaches + releases on exit (with rollback if an attach fails). `rename_device` raises
`allocator_client.NameConflict` on a name clash. See [client/README.md](client/README.md).

## REST API

| Endpoint | Auth | Description |
|---|---|---|
| `GET /health` | none | Health check |
| `POST /api/v1/sessions` | `X-Client-Id` | Create session `{group_name, node?}` |
| `GET /api/v1/sessions[/{id}]` | `X-Client-Id` | List / get your sessions |
| `POST /api/v1/sessions/{id}/freeze` | `X-Client-Id` | Freeze this session's node |
| `DELETE /api/v1/sessions/{id}` | `X-Client-Id` | Release session |
| `GET/POST/PUT/DELETE /api/v1/groups[/{name}]` | none | Group CRUD |
| `GET /api/v1/devices` | none | List devices (filters) |
| `GET/PATCH/DELETE /api/v1/devices/{node}/{name}` | none | Per-node device ops |
| `POST /api/v1/devices/{node}/{name}/rename` | none | Set a custom name `{name, force}` |
| `GET /api/v1/nodes[/{id}]` | none | Node listing |
| `POST /api/v1/nodes/{node}/unfreeze` | none | Unfreeze a node |
| `POST /internal/v1/nodes/register` | `X-Agent-Secret` | Agent registration |
| `POST /internal/v1/nodes/{id}/heartbeat` | `X-Agent-Secret` | Agent heartbeat |
| `POST /internal/v1/nodes/{id}/devices/sync` | `X-Agent-Secret` | Push device inventory |

Clients are unauthenticated — identity is the `X-Client-Id` header (the client's hostname),
which scopes session ownership. Agents authenticate with the shared `X-Agent-Secret`.
Interactive docs: `http://manager:8000/docs`.

## Configuration

### Manager environment

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://allocator:allocator@localhost:5432/allocator` | Postgres DSN |
| `AGENT_SECRET` | `dev-agent-secret` | Shared secret for agent auth |
| `AGENT_REQUEST_TIMEOUT` | `10.0` | Seconds for manager → agent calls |
| `NODE_OFFLINE_TIMEOUT` / `SESSION_MAX_AGE` | `90` / `3600` | Reaper + lease-expiry seconds |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true` | Logging |

### Agent environment

| Variable | Default | Description |
|---|---|---|
| `MANAGER_URL` | `http://localhost:8000` | Manager base URL |
| `AGENT_SECRET` | `dev-agent-secret` | Shared secret |
| `NODE_NAME` | *(hostname)* | Node identifier |
| `ADVERTISE_IP` | *(auto-detected)* | IP the manager should reach the agent on |
| `AGENT_PORT` / `HEARTBEAT_INTERVAL` | `5000` / `15` | Listen port / heartbeat seconds |

### Client config

JSON file at `~/.config/allocator/config.json` (manage with `allocator config set/get/show`).

| Key | Default | Description |
|---|---|---|
| `url` | `http://localhost:8000` | Manager base URL |
| `client_id` | *(hostname)* | Identity that owns your sessions |

## Database schema

```
nodes ─< devices            device_names (fingerprint → custom name)
groups ─< group_devices (logical_name list)
sessions ─< session_devices >─ devices
```

- `devices`: `UNIQUE(node_id, logical_name)` and `UNIQUE(node_id, fingerprint)` — per-node.
- `group_devices` stores logical **names**, not device FKs (a group is a template).
- `nodes.frozen`; `sessions.status ∈ {PENDING,ACTIVE,RELEASED,FAILED}` + `node_id`/`requested_node_id`.
- `device_names` is durable, portable name memory keyed by fingerprint.

Hard deletes; no soft-delete. Background tasks: heartbeat reaper, session expiry, zombie
cleanup, queue processor.

## Development

```bash
# install contract first, then a component (editable, with dev extras)
pip install -e contract
cd client && pip install -e ".[dev]" && pytest      # parity test guards async/sync drift
```

All packages use `ruff` (line length 100, target 3.12). Test coverage is currently minimal
(the client has a drift-guard parity test); the typed `contract` makes fixtures easy to add.

## Project status

`v0.1.0` — actively evolving. Clients are unauthenticated (identity = hostname); the design
assumes a **single manager instance** (the wait queue is in-process).
