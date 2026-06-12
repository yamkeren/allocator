# Allocator — Project Handoff

_Last updated: 2026-06-04_

> **2026-06-04 — Groups removed.** The `group` abstraction is gone end-to-end. A client now
> starts a session with an **ad-hoc list of device names** (`session create --devices a,b,c`)
> instead of a saved group name. Sessions store the request in a `sessions.requested_devices`
> `varchar(255)[]` column; the wait queue is a **global skip-FIFO by creation time** (was
> per-group). The `groups`/`group_devices` tables, `/api/v1/groups` API, `GroupService`,
> `allocator group` CLI, and the `allocator_contract.group` models were all deleted. The
> sections below have been updated to match.

A distributed, transactional scheduler that allocates **USB devices across Linux nodes** to
client sessions as atomic sets, using **USB/IP** (`usbip bind`/`attach`) as the transport.
It is a correctness-first resource scheduler (leasing, locking, rollback, a queue), not a
simple USB manager.

---

## 1. Repository layout

```
allocator/
├── contract/           # allocator-contract: shared Pydantic API models (pydantic-only)
│   └── allocator_contract/{node,device,session,usbip}.py
├── manager/            # central FastAPI service + Postgres (the brain)
│   ├── allocator_manager/{models,services,api,tasks,middleware}/
│   ├── alembic/        # single migration 0001 (greenfield — edited in place, not appended)
│   ├── deploy/         # docker-compose.yml + postgres/init.sql + pgadmin/servers.json
│   ├── setup.sh        # compose up + publish <name>.local over mDNS (--domain, --port; port 80 default)
│   ├── stop.sh         # stop stack + mDNS publisher (--volumes wipes data)
│   └── Dockerfile      # build context = repo root (to include contract/)
├── agent/              # node agent: scans USB, runs usbip bind/unbind (FastAPI + CLI)
│   ├── allocator_agent/...
│   ├── setup.sh        # rsync to /opt + venv + systemd units (usbipd + allocator-agent)
│   └── stop.sh         # stop allocator-agent + usbipd
├── client/             # typer CLI + synchronous client library
│   ├── allocator_client/...   # AllocatorClient + AllocatorSession (sync, httpx.Client)
│   │                          # config = JSON file ~/.config/allocator/config.json
│   └── setup.sh        # venv installer + dependency checker
├── README.md
└── HANDOFF.md          # this file
```

All three components depend on `contract/` as an **editable path dependency** (install
`contract` first). They share no other code; coupling is the HTTP/JSON contract, now typed
and single-sourced in `allocator_contract`.

---

## 2. Architecture & flow

- **Client → Manager** (HTTP, identity = hostname header `X-Client-Id`, no auth): all
  orchestration — create/list/release sessions, freeze nodes, device rename, reads.
- **Agent → Manager** (HTTP, `X-Agent-Secret`): self-register, heartbeat, push device inventory.
- **Manager → Agent** (HTTP, `X-Agent-Secret`): `usbip bind`/`unbind` during allocation.
- **Client ↔ Agent** (TCP 3240, USB/IP): the client runs `usbip attach` using coordinates
  the manager returns in the session response (`usbip attach -r <node_ip> -b <bus_id>`).

Background tasks (manager `tasks/`, started in `main.py` lifespan): `heartbeat_reaper`
(nodes → OFFLINE after 90s), `session_expiry`, `zombie_cleanup`, `queue_processor`
(PENDING sessions).

---

## 3. Data model (Postgres, `manager/allocator_manager/models/`)

- **nodes**: identity, `agent_url`, `status` (ONLINE/OFFLINE), **`frozen` bool**.
- **devices**: `node_id`, `logical_name`, `fingerprint`, `vendor/product_id`, `serial`,
  `mac_address`, `device_class` (enum), `status` (FREE/ALLOCATED/ERROR), `usbip_bus_id`.
  - **`UNIQUE(node_id, logical_name)`** and **`UNIQUE(node_id, fingerprint)`** — names and
    fingerprints are unique **per node**, not globally.
- **device_names**: `(fingerprint → name)` — durable, portable custom-name memory; survives
  unplug/prune and follows a device across nodes.
- **sessions**: `client_id`, `requested_devices` (`varchar(255)[]` of logical names the client asked
  for), `status` (**PENDING**/ACTIVE/RELEASED/FAILED), `requested_node_id` (pin),
  `node_id` (resolved).
- **session_devices**: per-device allocation rows (device_id, snapshot logical_name, node,
  agent_url, bus_id, status).

`DeviceClass` enum: WIFI, ETHERNET, BLUETOOTH, AUDIO, VIDEO, HID, MASS_STORAGE, PRINTER,
IMAGE, SMARTCARD, SERIAL, GENERIC.

---

## 4. Key behaviors / design decisions

**Device identity = fingerprint** (`sha256(vid:pid:serial:mac)`). Sync matches devices by
`(node_id, fingerprint)`, so a physical device keeps its row/UUID across reconnects.

**Naming (manager-owned, per node):**
- Agent reports identity only (no names). Manager assigns a per-node generic name
  `{class}_{n}` (lowest free index) on first sight — see `services/naming.py`.
- A client can **rename** a device to a custom name (`POST /devices/{node}/{logical}/rename`).
  Custom names are remembered in `device_names` and **follow the physical device** to new
  nodes (applied if free there, else generic).
- Rename conflict on a node → `409 {error:"name_conflict",...}`; the CLI prompts
  **make-other-generic / pick-another / abort**. Automatic moves fall back to generic silently.
- Classification (`device_discoverer._classify`) inspects device + all interface USB classes,
  resolves composite devices by priority, Bluetooth gated on subclass/protocol.

**Allocation (single-node model, `services/allocation.py`):**
- A session's requested device list is satisfied by **one node** that has a FREE device for
  **every** name.
- Eligible node = ONLINE, **not frozen**, all names FREE. Among eligible, pick **fewest total
  devices**. Reservation locks rows `FOR UPDATE NOWAIT` in a savepoint; bind is a saga outside
  the txn with reverse-order rollback on failure.
- No eligible node → session **PENDING** (queued). `queue_processor` is a **global skip-FIFO
  by `created_at`**, triggered on release/sync/unfreeze (+ 15s safety sweep): every PENDING
  session is tried oldest-first; unsatisfiable ones are skipped and never block younger ones
  (starvation of multi-device requests by newer subset requests is an accepted risk).
- Client may **pin** a node (`--node`); frozen/missing → reject, busy → queue for that node.

**Freezing:** a client with an ACTIVE session freezes that session's node
(`POST /sessions/{id}/freeze`). Frozen nodes are excluded from all new sessions; the running
session is unaffected and the node stays frozen after release. **Anyone** can unfreeze
(`POST /nodes/{node}/unfreeze`, no auth).

**Auth model:** clients are unauthenticated; identity = the client host's name sent as
`X-Client-Id` (defaults to the hostname; override via `allocator config set client_id <name>`).
Sessions are owned by this value. Agents authenticate with the shared `X-Agent-Secret`.

**Client config:** a JSON file at `~/.config/allocator/config.json` (`allocator_client.config.Config`).
Keys `url` (default `http://localhost:8000`) and `client_id` (default hostname). Manage via
`allocator config set/get/show/unset`, the library `client.config.get/set`, or constructor args.
No env vars (deliberately — the client is a user CLI, not a server).

**Agent IP:** registers its primary outbound IP (UDP-connect trick), not Debian's
`127.0.1.1`. Override via `ADVERTISE_IP`. This is what lets a dockerized manager reach a host
agent and what the client's attach command uses.

---

## 5. API surface (manager)

```
# client-facing (X-Client-Id; devices/nodes need no header)
POST   /api/v1/sessions                 {devices: [name, ...], node?}
GET    /api/v1/sessions[/{id}]
POST   /api/v1/sessions/{id}/freeze
DELETE /api/v1/sessions/{id}            (release)
GET    /api/v1/devices                  (list; filters node_id/status/class)
GET/PATCH/DELETE /api/v1/devices/{node}/{logical_name}
POST   /api/v1/devices/{node}/{logical_name}/rename  {name, force}
GET    /api/v1/nodes[/{id}]
POST   /api/v1/nodes/{node}/unfreeze

# internal (X-Agent-Secret)
POST   /internal/v1/nodes/register
POST   /internal/v1/nodes/{id}/heartbeat
POST   /internal/v1/nodes/{id}/devices/sync

# agent (manager → agent, X-Agent-Secret): POST /api/v1/usbip/{bind,unbind}; GET /api/v1/health
```

---

## 6. What this session did

1. **Big refactor** of naming/allocation/auth (all the §4 behaviors): fingerprint identity,
   per-node names, portable custom names + rename/conflict, single-node allocation with the
   per-group FIFO queue, node freezing, hostname-based client identity (API key removed),
   richer device classification.
2. **Agent IP fix** — register the real LAN IP instead of `127.0.1.1` (+ `ADVERTISE_IP`).
3. **Shared typed contract** — extracted `contract/` (`allocator-contract`); manager imports
   it (deleted `schemas/`), agent sends typed payloads, **client + CLI fully typed** (no more
   raw dicts).
4. **Co-located deploy/scripts** — `deploy/`→`manager/deploy/`, `scripts/setup_*`→
   `<component>/setup.sh`; Dockerfiles/compose use repo-root build context to include `contract/`.
5. **Client is synchronous** — the client library and CLI are now sync-only: `AllocatorClient`
   (blocking `httpx.Client`) + `AllocatorSession` (context manager: allocate → usbip attach →
   detach + release, with rollback). The async variant was removed (the CLI was the only
   consumer and is sequential). CLI `session create --attach` records attached ports and
   `session release` auto-detaches them.
6. **Docs** — rewrote the top-level `README.md` to the current system; wrote `client/README.md`
   (user guide) and this `HANDOFF.md`.
7. **Deployment & client config** — `manager/setup.sh --domain <name>` publishes `<name>.local`
   over mDNS (Avahi systemd unit); the manager is now published on **port 80** by default
   (`--port` / `MANAGER_PORT`, container still listens on 8000), so the LAN URL needs no port.
   Added `manager/stop.sh` + `agent/stop.sh`. Client config moved from env vars to a JSON file
   (`~/.config/allocator/config.json`) with an `allocator config` command and `client.config`.
   `session create --attach` records local usbip ports; `session release` auto-detaches them.

**Bugs found & fixed (mostly on the real-allocation path, previously unexercised):**
- `allocate()` let `AllocationError` from `_bind` escape → 500 on any bind failure. Now caught.
- `create` serialized the in-memory session and lazy-loaded `sd.device` → `MissingGreenlet`
  500. Now reloads with eager `selectinload`.
- `delete_group` 500'd for groups with released sessions (FK with no ON DELETE). Now deletes
  non-active session history first.
- `_reserve` caught the NOWAIT lock error **inside** the savepoint (aborted-savepoint commit).
  Now the error propagates out and is handled outside → clean queue.
- `kick_queue` didn't keep a task reference (GC risk). Now retained in a set.
- Device sync collided on `uq_device_per_node_logical` for multiple same-class new devices
  (autoflush off). Now flushes each insert before naming the next.
- `AllocatorSession` (async context manager) did dict access on the now-typed `SessionResponse`
  → `TypeError`. Fixed to typed attribute access (found while adding the sync client).

**Verified this session:** full reserve→`usbip bind`→release cycle on a real `mass_storage_0`
device through the live agent; 22-check live client↔manager integration; 12-check typed
in-process integration (real `AllocatorClient` → real manager app → live Postgres); sync-client
checks via `httpx.MockTransport` (typed responses, 204, `NameConflict`).

---

## 7. Current state & required actions

- **Nothing is committed.** All session work is in the working tree on branch
  `starting_design`. No PR/push has been made.
- **The dev stack was down at last check.** **(Re)create it** — easiest via the setup script,
  which also publishes the LAN domain over mDNS:
  ```bash
  manager/setup.sh --domain allocator        # compose up --build + publish allocator.local (port 80)
  # or plain: cd manager/deploy && docker compose down && docker compose up -d --build
  ```
  Note the manager now publishes on **port 80** by default (`--port 8000` to keep 8000). The old
  container predated `contract/`, so a rebuild is required regardless (`up -d --build`; a plain
  `restart` would fail to import `allocator_contract`). Then restart the agent so it re-registers
  with the typed payload + correct IP (`sudo systemctl restart allocator-agent`, or `agent/setup.sh`).
- **No DB migration needed** for the contract move (models unchanged), BUT earlier session
  changes edited the initial migration `0001` in place (enum values, `frozen`, `device_names`,
  per-node uniqueness, sessions columns). If a database predates those, recreate the volume:
  `docker compose down -v && docker compose up`.
- A root-owned `manager/allocator_manager/schemas/__pycache__/` may linger (created by the
  container as root; the `.py` files are deleted). Harmless, gitignored; remove with sudo if
  desired.

---

## 8. Open items / not yet done

- **No automated tests.** `tests/` packages are empty. Good targets: `eligible_nodes` ranking,
  per-device-set FIFO queue, rename/conflict + portability, allocation rollback. The shared
  contract makes request/response fixtures trivial.
- **Contract drift guard:** the API contract is single-sourced, but nothing asserts the agent
  scan output round-trips `DeviceInfoPayload` in CI — add that.
- **Manager↔agent networking** in the dockerized topology depends on the agent advertising a
  reachable IP; document/automate the `ADVERTISE_IP` story for non-LAN setups
  (`host.docker.internal` / bridge IP).
- Horizontal scale is out of scope: the queue lock and `kick_queue` assume a **single manager
  instance**.

---

## 9. Quick start (local)

```bash
# stack — published on port 80 as allocator.local over mDNS
manager/setup.sh --domain allocator                    # postgres + manager + pgadmin + mDNS
# client
client/setup.sh && source client/.venv/bin/activate
allocator config set url http://allocator.local        # persists to ~/.config/allocator/config.json
allocator node list ; allocator device list
# agent (on a USB host)
sudo agent/setup.sh --manager-url http://allocator.local
# stop
manager/stop.sh ; sudo agent/stop.sh
```
Manager API docs: `http://allocator.local/docs` (or `:8000` if started with `--port 8000`).
pgAdmin: `http://<host>:5050`.
