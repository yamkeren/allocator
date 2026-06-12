# System Test Suite Design

**Date:** 2026-06-12
**Status:** Approved (sections approved in session)
**Scope:** all four packages (`contract/`, `manager/`, `agent/`, `client/`) + new root `e2e/`

## Goal

A test suite covering the whole allocator system: unit tests per package, API
integration tests for both FastAPI services, and a docker-compose end-to-end
suite exercising the full session lifecycle against a real manager.

## Decisions (made in session)

- **Real Postgres via Docker** for manager service tests. All five manager
  models use Postgres-only column types (`ARRAY`, `UUID` dialect), and the
  system's core risk is transactional (FOR UPDATE NOWAIT, savepoints, unique
  constraints, saga rollback) — fakes cannot cover it. SQLite is impossible.
- **Depth: unit + API integration + e2e.** Hardware usbip stays mocked
  everywhere (no kernel module in tests).
- **Local only.** A root `run_tests.sh` orchestrates everything. CI wiring is
  a separate future project.
- **Infra approach A:** `testcontainers-python` for the manager's throwaway
  Postgres (self-contained pytest), plus a small `e2e/docker-compose.test.yml`
  used only by the e2e suite.

## Layout

```
contract/tests/            # validation + round-trip units (no infra)
manager/tests/             # service + API tests (testcontainers Postgres)
agent/tests/               # component + API units (mocked subprocess)
client/tests/              # lib + CLI units (httpx MockTransport, mocked subprocess)
e2e/
  docker-compose.test.yml  # postgres + manager image + stub agent
  stub_agent/              # tiny FastAPI app (see below)
  tests/                   # lifecycle tests driving the real client library
run_tests.sh               # orchestrator, exit nonzero on any failure
```

Each package gains a `dev` extra (pytest, pytest-asyncio, plus per-package
needs) following the existing `manager/pyproject.toml` pattern, and a
`.venv` created the same way the manager's was.

## Manager test infrastructure

- `manager/tests/conftest.py`: session-scoped `testcontainers` Postgres
  container; schema created by running **alembic upgrade head** against it
  (this also tests the migration). Per-test cleanup truncates all tables.
- `NodeAgentClient` is monkeypatched in service tests — unit tests never
  perform real HTTP to an agent. The mock records bind/unbind calls and can be
  programmed to fail at the Nth call (for saga rollback tests).
- API tests use httpx `ASGITransport` against the real app wired to the real
  test database; internal routes send `X-Agent-Secret`, client routes send
  `X-Client-Id`.

## Test targets

### contract
- `SessionCreate`: duplicate devices rejected, empty list rejected, name
  pattern enforced.
- Round-trip guard: `DeviceInfoPayload` (and sibling payloads) serialize →
  deserialize unchanged (the HANDOFF §8 "contract drift" ask).

### manager — services (real Postgres)
- `eligible_nodes`: OFFLINE excluded, frozen excluded, node missing one
  requested name excluded, tie-break prefers fewest total devices.
- Allocation: success path (session ACTIVE, devices ALLOCATED, SessionDevice
  rows correct); multi-device atomicity (one name taken → nothing allocated,
  session PENDING); bind saga rollback (mock agent fails on 2nd bind →
  reverse-order unbind of the 1st, devices back to FREE); duplicate-name
  request fails validation upstream (contract) — service-level guard not
  required.
- Concurrency: two concurrent allocates racing for the same device — exactly
  one wins, the loser is queued (exercises FOR UPDATE NOWAIT + savepoint).
- Naming: generic `{class}_{n}` lowest-free-index assignment;
  `unique_logical_name`; rename conflict → 409 path; custom-name portability
  (device moves node, name follows if free there, generic otherwise; durable
  through unplug/prune via `device_names`).
- Node sync: fingerprint identity keeps device row/UUID across reconnects;
  vanished devices pruned; sync triggers `kick_queue`.
- Session service: create (immediate ACTIVE when satisfiable, PENDING
  otherwise), release (devices freed, unbind called, queue kicked), freeze
  (only own ACTIVE session's node; frozen node excluded from new sessions),
  unfreeze.
- Tasks: `heartbeat_reaper` (node OFFLINE after threshold), `session_expiry`,
  `zombie_cleanup`. Queue processor already covered
  (`manager/tests/test_queue_processor.py`) — keep, don't duplicate.

### manager — API (ASGITransport + real DB)
- Auth middleware: internal route without `X-Agent-Secret` → 401; session
  routes derive ownership from `X-Client-Id` (releasing another client's
  session rejected).
- Status codes: 404 unknown session/node/device, 409 rename conflict,
  422 contract validation (duplicate devices), happy-path 200/201 shapes.

### agent (no real USB, no real subprocess)
- `device_discoverer._classify`: composite-device priority resolution,
  Bluetooth gated on subclass/protocol, fallback GENERIC.
- `fingerprint`: stable hash, distinct inputs → distinct outputs.
- `UsbipController.bind/unbind/list_bound/check_available` with mocked
  `subprocess`: success, nonzero exit, timeout.
- API routes via ASGITransport: bind/unbind endpoints (controller mocked),
  health.
- `_detect_advertise_ip`: returns the UDP-trick IP; `ADVERTISE_IP` override
  wins.

### client (no network, no subprocess)
- `Config`: load/save/set/unset/resolution order (constructor > file >
  default) against a temp config dir.
- `usbip` helpers: `attach` parses vhci port from mocked `usbip` output;
  `_parse_port` edge cases; `detach`; error types on failure.
- `AllocatorClient`: every public method against httpx `MockTransport` —
  correct paths/headers (`X-Client-Id`), payload shapes, 409 → `NameConflict`,
  HTTP errors surface.
- `AllocatorSession`: context-manager happy path (allocate → attach via
  mocked usbip → detach + release on exit); attach failure → release still
  called (rollback).
- CLI: typer `CliRunner` smoke tests per command group (mocked client);
  duplicate/empty `--devices` rejected with friendly message.

### e2e (docker compose)
Composition: Postgres + manager image (existing `manager/Dockerfile`) + **stub
agent** — a small FastAPI app that registers itself with the manager,
heartbeats, syncs a configurable fake device inventory, and records
bind/unbind requests (always succeeding). Tests run on the host, driving the
**real client library** at the manager's published port:

1. Lifecycle: create session → ACTIVE → response carries correct node/bus-id
   coordinates → release → devices FREE again; stub agent saw bind then
   unbind.
2. Queue: second session wanting the same device is PENDING; releasing the
   first wakes it (global skip-FIFO observable end-to-end).
3. Freeze: frozen node excluded; unfreeze re-enables and kicks queue.
4. Validation: duplicate device names rejected at the API.

usbip `attach` (kernel side) is exercised only at client-unit level with
mocked subprocess — never in e2e.

## Runner

`run_tests.sh` at repo root: for each package, ensure `.venv` with dev extras,
run pytest; then e2e: `docker compose -f e2e/docker-compose.test.yml up -d
--build`, run `e2e/tests`, `down -v`. Any failure → nonzero exit, but later
suites still run (report all failures at the end).

## Implementation plan decomposition

One spec, three implementation plans, executed in this order:

1. **Plan A — pure units:** contract + agent + client tests (no shared infra,
   independently green).
2. **Plan B — manager:** testcontainers infrastructure, service tests, API
   tests.
3. **Plan C — e2e:** stub agent, compose file, lifecycle tests, `run_tests.sh`
   orchestrator (depends on A+B conventions existing).

## Out of scope

CI workflows, real usbip/hardware, load/performance tests, horizontal-scale
scenarios (single manager instance assumed, per HANDOFF).

## Risks

- testcontainers adds a docker dependency for manager tests — acceptable
  (docker already required for deploy; available locally).
- e2e stub agent must track the contract — it imports `allocator-contract`
  directly, so drift breaks loudly at build time, which is the desired
  behavior.
- Background-task tests (reaper/expiry) must inject thresholds via settings
  rather than sleeping — tests stay fast.
