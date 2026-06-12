# Test Suite Plan A — Pure Units (contract, agent, client)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unit tests for the three infra-free packages: contract validation/round-trip, agent device classification + usbip controller + API routes, client config/usbip/library/session-context/CLI.

**Architecture:** Each package gets its own `.venv` with a `dev` extra and pytest (`asyncio_mode="auto"` everywhere). No network, no real subprocess, no database: subprocess is monkeypatched, HTTP uses httpx `MockTransport` (client) / `ASGITransport` (agent). One deliberate testability seam: `AllocatorClient` gains an optional `transport` constructor arg.

**Tech Stack:** Python 3.12, pytest, pytest-asyncio, httpx, typer CliRunner, pydantic v2.

**Spec:** `docs/superpowers/specs/2026-06-12-system-test-suite-design.md` (Plan A of three).

---

## Background for the implementer

- Working dir is `/home/yam/code/allocator`. Git working tree must stay clean apart from your task's files — `git add` exact paths only, never `-A`/`.` (untracked `graphify-out/` must never be committed).
- `agent/pyproject.toml` and `client/pyproject.toml` already have `dev` extras and `[tool.pytest.ini_options]` with `asyncio_mode="auto"`, `testpaths=["tests"]`. `contract/pyproject.toml` has neither — Task 1 adds them.
- `agent/tests/__init__.py`, `client/tests/__init__.py` already exist (empty). `contract/tests/` does not exist.
- Venv pattern (same as manager's): `python3 -m venv .venv && .venv/bin/pip install -e <deps...> -e ".[dev]"`.
- All file paths below are relative to the repo root.

## File structure

```
contract/pyproject.toml          # modify: add dev extra + pytest ini
contract/tests/__init__.py       # create (empty)
contract/tests/test_session_create.py
contract/tests/test_roundtrip.py
agent/tests/test_device_discoverer.py
agent/tests/test_usbip_controller.py
agent/tests/test_api_usbip.py
agent/tests/test_advertise_ip.py
client/allocator_client/client.py  # modify: optional transport arg (testability seam)
client/tests/test_config.py
client/tests/test_usbip.py
client/tests/test_client.py
client/tests/test_session_context.py
client/tests/test_cli.py
```

---

### Task 1: Contract test environment + validation tests

**Files:**
- Modify: `contract/pyproject.toml`
- Create: `contract/tests/__init__.py` (empty)
- Test: `contract/tests/test_session_create.py`

- [ ] **Step 1: Add dev extra + pytest config to `contract/pyproject.toml`**

Append after the `[project]` dependencies block (before `[tool.hatch...]`):

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.2",
]
```

And at the end of the file:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

(No pytest-asyncio: contract models are sync.)

- [ ] **Step 2: Create venv**

```bash
cd /home/yam/code/allocator/contract
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Expected: ends `Successfully installed ... allocator-contract ... pytest ...`. Verify `.venv` not in `git status --porcelain` (repo `.gitignore` covers it after Task 1 of the FIFO plan verified manager's; if `contract/.venv` appears, add `**/.venv/` to root `.gitignore` and include that file in this task's commit).

- [ ] **Step 3: Write `contract/tests/test_session_create.py`** (create empty `contract/tests/__init__.py` first)

```python
"""SessionCreate validation: the API-level guard for session requests."""

import pytest
from pydantic import ValidationError

from allocator_contract.session import SessionCreate


def test_accepts_unique_names():
    sc = SessionCreate(devices=["wifi_0", "hid_1"])
    assert sc.devices == ["wifi_0", "hid_1"]
    assert sc.node is None


def test_rejects_duplicates_and_names_them():
    with pytest.raises(ValidationError) as exc:
        SessionCreate(devices=["a", "b", "a", "c", "c"])
    msg = str(exc.value)
    assert "duplicate device names" in msg
    assert "a" in msg and "c" in msg


def test_rejects_empty_list():
    with pytest.raises(ValidationError):
        SessionCreate(devices=[])


@pytest.mark.parametrize("bad", ["WiFi_0", "wifi-0", "wifi 0", "wifi/0", ""])
def test_rejects_invalid_name_pattern(bad):
    with pytest.raises(ValidationError):
        SessionCreate(devices=[bad])


def test_rejects_overlong_name():
    with pytest.raises(ValidationError):
        SessionCreate(devices=["x" * 101])


def test_node_pin_optional():
    sc = SessionCreate(devices=["wifi_0"], node="lab-1")
    assert sc.node == "lab-1"
```

- [ ] **Step 4: Run**

```bash
cd /home/yam/code/allocator/contract && .venv/bin/pytest tests/ -v
```

Expected: all pass (these test already-shipped behavior; this is characterization, not TDD red).

- [ ] **Step 5: Commit**

```bash
cd /home/yam/code/allocator
git add contract/pyproject.toml contract/tests/__init__.py contract/tests/test_session_create.py
git commit -m "test(contract): SessionCreate validation suite + dev extra"
```

### Task 2: Contract round-trip drift guard

**Files:**
- Test: `contract/tests/test_roundtrip.py`

- [ ] **Step 1: Write the test**

```python
"""Round-trip drift guard: payloads must survive serialize -> deserialize
unchanged. This is the HANDOFF §8 'contract drift' check: if a field is
added/renamed with a default, a silently-lossy round trip would hide it.
"""

from datetime import UTC, datetime

import pytest

from allocator_contract.device import DeviceCreate, DeviceResponse
from allocator_contract.node import (
    DeviceInfoPayload,
    DeviceSyncPayload,
    NodeHeartbeatPayload,
    NodeRegisterPayload,
)
from allocator_contract.session import SessionDeviceAttachInfo, SessionResponse
from allocator_contract.usbip import BindRequest, UnbindRequest

SAMPLES = [
    DeviceInfoPayload(
        vendor_id="0bda", product_id="8812", serial="S1", manufacturer="Realtek",
        product_name="AC1200", mac_address="aa:bb:cc:dd:ee:ff",
        device_class="WIFI", usbip_bus_id="1-1.2", fingerprint="f" * 64,
    ),
    DeviceInfoPayload(vendor_id="1a2b", product_id="3c4d", fingerprint="0" * 64),
    DeviceSyncPayload(devices=[DeviceInfoPayload(vendor_id="1", product_id="2", fingerprint="ab")]),
    NodeRegisterPayload(name="lab-1", hostname="lab-1.local", ip_address="192.168.1.5"),
    NodeHeartbeatPayload(timestamp=datetime(2026, 6, 12, 10, 0, tzinfo=UTC)),
    BindRequest(bus_id="1-1.2", logical_name="wifi_0", session_id="s1"),
    UnbindRequest(bus_id="1-1.2", logical_name="wifi_0"),
    DeviceCreate(node_id="n1", logical_name="wifi_0", vendor_id="0bda",
                 product_id="8812", fingerprint="ff"),
    SessionResponse(
        session_id="s1", client_id="host-a", requested_devices=["wifi_0"],
        status="ACTIVE", node_name="lab-1", failure_reason=None,
        devices=[SessionDeviceAttachInfo(
            logical_name="wifi_0", node_id="n1", node_hostname="lab-1.local",
            node_ip="192.168.1.5", usbip_bus_id="1-1.2",
            usbip_attach_command="usbip attach -r 192.168.1.5 -b 1-1.2",
            device_class="WIFI",
        )],
        created_at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    ),
    DeviceResponse(
        device_id="d1", node_id="n1", logical_name="wifi_0", vendor_id="0bda",
        product_id="8812", serial=None, manufacturer=None, product_name=None,
        mac_address=None, device_class="WIFI", status="FREE",
        usbip_bus_id="1-1.2", created_at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    ),
]


@pytest.mark.parametrize("model", SAMPLES, ids=lambda m: type(m).__name__)
def test_json_roundtrip_unchanged(model):
    restored = type(model).model_validate_json(model.model_dump_json())
    assert restored == model
    assert restored.model_dump() == model.model_dump()
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/contract && .venv/bin/pytest tests/test_roundtrip.py -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add contract/tests/test_roundtrip.py
git commit -m "test(contract): JSON round-trip drift guard for shared payloads"
```

### Task 3: Agent device discoverer tests

**Files:**
- Test: `agent/tests/test_device_discoverer.py`

The functions under test read sysfs attribute files relative to a device dir, so a `tmp_path` directory with the right small files IS a fake device. `_detect_network` finds nothing in `tmp_path` (no `net/` subdir) unless we create one — the WIFI/ETHERNET branch needs `/sys/class/net/<iface>` which we cannot fake without patching; we patch `Path` lookups via monkeypatch on the module's `_read` only for that one test.

- [ ] **Step 1: Create venv**

```bash
cd /home/yam/code/allocator/agent
python3 -m venv .venv
.venv/bin/pip install -e ../contract -e ".[dev]"
```

- [ ] **Step 2: Write `agent/tests/test_device_discoverer.py`**

```python
"""Classification, fingerprint, and bindable-busid parsing — all on fake sysfs
trees in tmp_path; no real USB, no real subprocess.
"""

import subprocess
from pathlib import Path

import pytest

from allocator_agent.components import device_discoverer as dd


def make_device(tmp_path: Path, *, device_class: str = "00",
                interfaces: list[tuple[str, str | None, str | None]] = ()) -> Path:
    """Fake sysfs device dir: bDeviceClass + one subdir per interface."""
    dev = tmp_path / "1-1"
    dev.mkdir()
    (dev / "bDeviceClass").write_text(device_class + "\n")
    for i, (iclass, isub, iproto) in enumerate(interfaces):
        iface = dev / f"1-1:1.{i}"
        iface.mkdir()
        (iface / "bInterfaceClass").write_text(iclass + "\n")
        if isub is not None:
            (iface / "bInterfaceSubClass").write_text(isub + "\n")
        if iproto is not None:
            (iface / "bInterfaceProtocol").write_text(iproto + "\n")
    return dev


# _classify

def test_classify_plain_hid(tmp_path):
    dev = make_device(tmp_path, interfaces=[("03", "01", "01")])
    assert dd._classify(dev) == "HID"


def test_classify_composite_prefers_higher_priority(tmp_path):
    # Webcam with a HID control interface: VIDEO (90) must beat HID (50).
    dev = make_device(tmp_path, interfaces=[("03", None, None), ("0e", None, None)])
    assert dd._classify(dev) == "VIDEO"


def test_classify_bluetooth_requires_subclass_and_protocol(tmp_path):
    dev = make_device(tmp_path, interfaces=[("e0", "01", "01")])
    assert dd._classify(dev) == "BLUETOOTH"


def test_classify_e0_without_bt_subclass_is_not_bluetooth(tmp_path):
    dev = make_device(tmp_path, interfaces=[("e0", "02", "01")])
    assert dd._classify(dev) == "GENERIC"


def test_classify_no_known_classes_is_generic(tmp_path):
    dev = make_device(tmp_path)
    assert dd._classify(dev) == "GENERIC"


def test_classify_device_level_class_used_when_no_interfaces(tmp_path):
    dev = make_device(tmp_path, device_class="08")
    assert dd._classify(dev) == "MASS_STORAGE"


# _map_class

@pytest.mark.parametrize("triple,expected", [
    (("03", None, None), "HID"),
    (("e0", "01", "01"), "BLUETOOTH"),
    (("e0", "01", "02"), None),
    (("ff", None, None), "SERIAL"),
    (("zz", None, None), None),
])
def test_map_class(triple, expected):
    assert dd._map_class(*triple) == expected


# _fingerprint

def test_fingerprint_stable_and_distinct():
    a = dd._fingerprint("0bda", "8812", "S1", None)
    assert a == dd._fingerprint("0bda", "8812", "S1", None)
    assert len(a) == 64
    assert a != dd._fingerprint("0bda", "8812", "S2", None)
    assert a != dd._fingerprint("0bda", "8812", "S1", "aa:bb:cc:dd:ee:ff")


# usbip_bindable_busids

def _cp(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess(args=["usbip"], returncode=rc,
                                       stdout=stdout, stderr=stderr)


def test_bindable_busids_parses_both_formats(monkeypatch):
    out = " - busid 1-1 (0bda:8812)\nbusid=2-1.4#usbid=1234:5678#\n"
    monkeypatch.setattr(dd.subprocess, "run", lambda *a, **k: _cp(stdout=out))
    assert dd.usbip_bindable_busids() == {"1-1", "2-1.4"}


def test_bindable_busids_nonzero_returns_none(monkeypatch):
    monkeypatch.setattr(dd.subprocess, "run", lambda *a, **k: _cp(rc=1, stderr="boom"))
    assert dd.usbip_bindable_busids() is None


def test_bindable_busids_oserror_returns_none(monkeypatch):
    def boom(*a, **k):
        raise OSError("usbip missing")
    monkeypatch.setattr(dd.subprocess, "run", boom)
    assert dd.usbip_bindable_busids() is None
```

- [ ] **Step 3: Run**

```bash
cd /home/yam/code/allocator/agent && .venv/bin/pytest tests/test_device_discoverer.py -v
```

Expected: all pass. If `test_classify_e0_without_bt_subclass_is_not_bluetooth` fails, read the actual `_map_class` — do not change production code without reporting.

- [ ] **Step 4: Commit**

```bash
cd /home/yam/code/allocator
git add agent/tests/test_device_discoverer.py
git commit -m "test(agent): device classification, fingerprint, bindable-busid parsing"
```

### Task 4: Agent usbip controller tests

**Files:**
- Test: `agent/tests/test_usbip_controller.py`

- [ ] **Step 1: Write the test**

```python
"""UsbipController against a monkeypatched _run / fake subprocess."""

import asyncio

import pytest

from allocator_agent.components.usbip_controller import (
    UsbipController,
    UsbipError,
    UsbipTimeoutError,
)


def patch_run(monkeypatch, stdout="", stderr="", rc=0):
    calls = []

    async def fake_run(*args, timeout=10):
        calls.append(args)
        return stdout, stderr, rc

    monkeypatch.setattr(UsbipController, "_run", staticmethod(fake_run))
    return calls


async def test_bind_success(monkeypatch):
    calls = patch_run(monkeypatch)
    result = await UsbipController().bind("1-1.2")
    assert result.bound and result.bus_id == "1-1.2"
    assert calls == [("usbip", "bind", "-b", "1-1.2")]


async def test_bind_failure_raises_with_stderr(monkeypatch):
    patch_run(monkeypatch, stderr="error: device not found", rc=1)
    with pytest.raises(UsbipError, match="device not found"):
        await UsbipController().bind("9-9")


async def test_unbind_not_bound_is_tolerated(monkeypatch):
    patch_run(monkeypatch, stderr="device is not bound", rc=1)
    result = await UsbipController().unbind("1-1.2")
    assert result.unbound


async def test_unbind_real_failure_raises(monkeypatch):
    patch_run(monkeypatch, stderr="permission denied", rc=1)
    with pytest.raises(UsbipError, match="permission denied"):
        await UsbipController().unbind("1-1.2")


async def test_list_bound_parses_busids(monkeypatch):
    out = "Local USB devices\n - busid 1-1.2 (0bda:8812)\n - busid 2-1 (1234:5678)\n"
    patch_run(monkeypatch, stdout=out)
    assert await UsbipController().list_bound() == ["1-1.2", "2-1"]


async def test_list_bound_nonzero_returns_empty(monkeypatch):
    patch_run(monkeypatch, rc=1)
    assert await UsbipController().list_bound() == []


# _run itself: timeout and missing binary, via fake create_subprocess_exec

async def test_run_timeout_raises(monkeypatch):
    class SlowProc:
        returncode = None
        async def communicate(self):
            await asyncio.sleep(5)

    async def fake_exec(*args, **kwargs):
        return SlowProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(UsbipTimeoutError):
        await UsbipController._run("usbip", "bind", "-b", "1-1", timeout=0)


async def test_run_missing_binary_raises(monkeypatch):
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(UsbipError, match="not found in PATH"):
        await UsbipController._run("usbip", "version")
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/agent && .venv/bin/pytest tests/test_usbip_controller.py -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add agent/tests/test_usbip_controller.py
git commit -m "test(agent): usbip controller bind/unbind/list/_run error paths"
```

### Task 5: Agent API route tests

**Files:**
- Test: `agent/tests/test_api_usbip.py`

- [ ] **Step 1: Write the test**

```python
"""Agent FastAPI routes via ASGITransport — controller monkeypatched.

ASGITransport does not run the lifespan, so no registration/heartbeat
side effects happen.
"""

import httpx
import pytest

from allocator_agent.components.usbip_controller import BindResult, UsbipController, UsbipError
from allocator_agent.main import create_app


@pytest.fixture
async def api():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        yield client


async def test_health(api):
    resp = await api.get("/api/v1/health")
    assert resp.status_code == 200


async def test_bind_success(api, monkeypatch):
    async def ok(self, bus_id):
        return BindResult(bus_id=bus_id)
    monkeypatch.setattr(UsbipController, "bind", ok)
    resp = await api.post("/api/v1/usbip/bind", json={
        "bus_id": "1-1.2", "logical_name": "wifi_0", "session_id": "s1",
    })
    assert resp.status_code == 200
    assert resp.json() == {"bound": True, "bus_id": "1-1.2"}


async def test_bind_failure_maps_to_500(api, monkeypatch):
    async def boom(self, bus_id):
        raise UsbipError("bind exploded")
    monkeypatch.setattr(UsbipController, "bind", boom)
    resp = await api.post("/api/v1/usbip/bind", json={
        "bus_id": "1-1.2", "logical_name": "wifi_0", "session_id": "s1",
    })
    assert resp.status_code == 500
    assert "bind exploded" in resp.json()["detail"]


async def test_unbind_success(api, monkeypatch):
    async def ok(self, bus_id):
        from allocator_agent.components.usbip_controller import UnbindResult
        return UnbindResult()
    monkeypatch.setattr(UsbipController, "unbind", ok)
    resp = await api.post("/api/v1/usbip/unbind", json={
        "bus_id": "1-1.2", "logical_name": "wifi_0",
    })
    assert resp.status_code == 200
    assert resp.json() == {"unbound": True}


async def test_bind_validation_error_is_422(api):
    resp = await api.post("/api/v1/usbip/bind", json={"bus_id": "1-1.2"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/agent && .venv/bin/pytest tests/test_api_usbip.py -v
```

Expected: all pass. If the health route path differs, check `agent/allocator_agent/api/v1/router.py` for the actual prefix and fix the TEST (report it), not the app.

- [ ] **Step 3: Write `agent/tests/test_advertise_ip.py`**

```python
"""_detect_advertise_ip: explicit setting wins; otherwise the UDP-connect
trick reports the primary outbound IP (no packets sent).
"""

from allocator_agent import main as agent_main


def test_advertise_ip_setting_wins(monkeypatch):
    monkeypatch.setattr(agent_main.settings, "advertise_ip", "10.9.8.7")
    assert agent_main._detect_advertise_ip() == "10.9.8.7"


def test_advertise_ip_udp_trick(monkeypatch):
    monkeypatch.setattr(agent_main.settings, "advertise_ip", "")

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def connect(self, addr):
            assert addr == ("8.8.8.8", 80)

        def getsockname(self):
            return ("192.168.1.42", 0)

    monkeypatch.setattr(agent_main.socket, "socket", lambda *a, **k: FakeSock())
    assert agent_main._detect_advertise_ip() == "192.168.1.42"
```

- [ ] **Step 4: Run**

```bash
cd /home/yam/code/allocator/agent && .venv/bin/pytest tests/test_advertise_ip.py -v
```

Expected: both pass.

- [ ] **Step 5: Commit**

```bash
cd /home/yam/code/allocator
git add agent/tests/test_api_usbip.py agent/tests/test_advertise_ip.py
git commit -m "test(agent): usbip + health routes via ASGI transport, advertise-ip detection"
```

### Task 6: Client config + usbip helper tests

**Files:**
- Test: `client/tests/test_config.py`
- Test: `client/tests/test_usbip.py`

- [ ] **Step 1: Create venv**

```bash
cd /home/yam/code/allocator/client
python3 -m venv .venv
.venv/bin/pip install -e ../contract -e ".[dev]"
```

- [ ] **Step 2: Write `client/tests/test_config.py`**

```python
"""Config: JSON-file store with code defaults; set/unset persist immediately."""

import json

from allocator_client.config import Config, config_path


def test_defaults_when_no_file(tmp_path):
    cfg = Config(tmp_path / "config.json")
    assert cfg.get("url") == "http://localhost"
    assert cfg.get("client_id")  # hostname default, non-empty


def test_set_persists_to_disk(tmp_path):
    p = tmp_path / "config.json"
    Config(p).set("url", "http://allocator.local")
    assert json.loads(p.read_text()) == {"url": "http://allocator.local"}
    assert Config(p).get("url") == "http://allocator.local"


def test_unset_restores_default(tmp_path):
    p = tmp_path / "config.json"
    cfg = Config(p)
    cfg.set("url", "http://x")
    cfg.unset("url")
    assert cfg.get("url") == "http://localhost"
    assert json.loads(p.read_text()) == {}


def test_resolved_merges_over_defaults(tmp_path):
    cfg = Config(tmp_path / "config.json")
    cfg.set("url", "http://y")
    resolved = cfg.resolved()
    assert resolved["url"] == "http://y"
    assert set(resolved) == set(Config.KEYS)


def test_corrupt_file_treated_as_empty(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json")
    assert Config(p).get("url") == "http://localhost"


def test_config_path_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "allocator" / "config.json"
```

- [ ] **Step 3: Write `client/tests/test_usbip.py`**

```python
"""Client usbip wrappers with monkeypatched subprocess.run."""

import subprocess

import pytest

from allocator_client import usbip


def _cp(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess(args=["usbip"], returncode=rc,
                                       stdout=stdout, stderr=stderr)


def test_attach_parses_port(monkeypatch):
    seen = {}
    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _cp(stdout="usbip: info: using port 3 (0x0003)\n")
    monkeypatch.setattr(usbip.subprocess, "run", fake_run)
    assert usbip.attach("192.168.1.5", "1-1.2") == 3
    assert seen["cmd"] == ["usbip", "attach", "-r", "192.168.1.5", "-b", "1-1.2"]


def test_attach_sudo_prefixes(monkeypatch):
    seen = {}
    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _cp(stdout="using port 0")
    monkeypatch.setattr(usbip.subprocess, "run", fake_run)
    usbip.attach("10.0.0.1", "2-1", sudo=True)
    assert seen["cmd"][0] == "sudo"


def test_attach_nonzero_raises(monkeypatch):
    monkeypatch.setattr(usbip.subprocess, "run",
                        lambda cmd, **k: _cp(rc=1, stderr="attach failed"))
    with pytest.raises(usbip.UsbipAttachError, match="attach failed"):
        usbip.attach("10.0.0.1", "2-1")


def test_attach_timeout_raises(monkeypatch):
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 15)
    monkeypatch.setattr(usbip.subprocess, "run", boom)
    with pytest.raises(usbip.UsbipAttachError, match="timed out"):
        usbip.attach("10.0.0.1", "2-1")


def test_detach_port_not_in_use_tolerated(monkeypatch):
    monkeypatch.setattr(usbip.subprocess, "run",
                        lambda cmd, **k: _cp(rc=1, stderr="error: port 3 is not in use"))
    usbip.detach(3)  # must not raise


def test_detach_real_failure_raises(monkeypatch):
    monkeypatch.setattr(usbip.subprocess, "run",
                        lambda cmd, **k: _cp(rc=1, stderr="permission denied"))
    with pytest.raises(usbip.UsbipDetachError, match="permission denied"):
        usbip.detach(3)


@pytest.mark.parametrize("output,expected", [
    ("usbip: info: using port 0 (0x0000)", 0),
    ("noise\nusing port 12 tail", 12),
    ("no port here", -1),
])
def test_parse_port(output, expected):
    assert usbip._parse_port(output) == expected
```

- [ ] **Step 4: Run**

```bash
cd /home/yam/code/allocator/client && .venv/bin/pytest tests/test_config.py tests/test_usbip.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd /home/yam/code/allocator
git add client/tests/test_config.py client/tests/test_usbip.py
git commit -m "test(client): config store and usbip wrappers"
```

### Task 7: AllocatorClient transport seam + library tests

**Files:**
- Modify: `client/allocator_client/client.py:41-59` (constructor)
- Test: `client/tests/test_client.py`

- [ ] **Step 1: Write the failing test first (TDD — the seam doesn't exist yet)**

`client/tests/test_client.py`:

```python
"""AllocatorClient against httpx MockTransport: paths, headers, parsing,
NameConflict mapping. No network.
"""

import json

import httpx
import pytest

from allocator_client.client import AllocatorClient, NameConflict
from allocator_client.config import Config

NOW = "2026-06-12T10:00:00Z"

SESSION_JSON = {
    "session_id": "s1", "client_id": "host-a", "requested_devices": ["wifi_0"],
    "status": "ACTIVE", "node_name": "lab-1", "failure_reason": None,
    "devices": [{
        "logical_name": "wifi_0", "node_id": "n1", "node_hostname": "lab-1.local",
        "node_ip": "192.168.1.5", "usbip_bus_id": "1-1.2",
        "usbip_attach_command": "usbip attach -r 192.168.1.5 -b 1-1.2",
        "device_class": "WIFI",
    }],
    "created_at": NOW,
}

DEVICE_JSON = {
    "device_id": "d1", "node_id": "n1", "logical_name": "wifi_0",
    "vendor_id": "0bda", "product_id": "8812", "serial": None,
    "manufacturer": None, "product_name": None, "mac_address": None,
    "device_class": "WIFI", "status": "FREE", "usbip_bus_id": "1-1.2",
    "created_at": NOW,
}


def make_client(handler, **kwargs) -> AllocatorClient:
    cfg = Config(path="/nonexistent/never-written.json")
    return AllocatorClient(
        manager_url="http://mgr", client_id="host-a", config=cfg,
        transport=httpx.MockTransport(handler), **kwargs,
    )


def test_create_session_posts_and_parses():
    seen = {}
    def handler(request):
        seen["path"] = request.url.path
        seen["client_id"] = request.headers["X-Client-Id"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=SESSION_JSON)

    with make_client(handler) as client:
        s = client.create_session(["wifi_0"])
    assert seen["path"] == "/api/v1/sessions"
    assert seen["client_id"] == "host-a"
    assert seen["body"] == {"devices": ["wifi_0"], "node": None}
    assert s.status == "ACTIVE" and s.devices[0].usbip_bus_id == "1-1.2"


def test_release_session_deletes():
    seen = {}
    def handler(request):
        seen["method_path"] = (request.method, request.url.path)
        return httpx.Response(204)

    with make_client(handler) as client:
        client.release_session("s1")
    assert seen["method_path"] == ("DELETE", "/api/v1/sessions/s1")


def test_list_sessions_passes_status_param():
    def handler(request):
        assert request.url.params["session_status"] == "ACTIVE"
        return httpx.Response(200, json={"items": [SESSION_JSON], "total": 1})

    with make_client(handler) as client:
        out = client.list_sessions(status="ACTIVE")
    assert out.total == 1


def test_rename_conflict_raises_nameconflict():
    def handler(request):
        return httpx.Response(409, json={"detail": {
            "error": "name_conflict", "name": "cam", "node": "lab-1",
            "holder_logical_name": "video_0",
        }})

    with make_client(handler) as client:
        with pytest.raises(NameConflict) as exc:
            client.rename_device("lab-1", "video_1", "cam")
    assert exc.value.holder == "video_0"


def test_rename_success_parses_device():
    def handler(request):
        body = json.loads(request.content)
        assert body == {"name": "cam", "force": True}
        return httpx.Response(200, json=DEVICE_JSON)

    with make_client(handler) as client:
        d = client.rename_device("lab-1", "wifi_0", "cam", force=True)
    assert d.logical_name == "wifi_0"


def test_http_error_surfaces():
    def handler(request):
        return httpx.Response(500, json={"detail": "boom"})

    with make_client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.get_session("s1")


def test_unfreeze_node_path():
    def handler(request):
        assert (request.method, request.url.path) == ("POST", "/api/v1/nodes/lab-1/unfreeze")
        return httpx.Response(200, json={
            "node_id": "n1", "name": "lab-1", "hostname": "lab-1.local",
            "ip_address": "192.168.1.5", "agent_port": 5000,
            "agent_url": "http://192.168.1.5:5000", "status": "ONLINE",
            "last_heartbeat": NOW, "agent_version": None, "frozen": False,
            "created_at": NOW,
        })

    with make_client(handler) as client:
        n = client.unfreeze_node("lab-1")
    assert n.frozen is False
```

- [ ] **Step 2: Run — expect TypeError (no `transport` kwarg yet)**

```bash
cd /home/yam/code/allocator/client && .venv/bin/pytest tests/test_client.py -v 2>&1 | tail -5
```

Expected: errors mentioning `unexpected keyword argument 'transport'`.

- [ ] **Step 3: Add the seam to `client/allocator_client/client.py`**

Change the constructor signature and the `httpx.Client(...)` call (lines ~41-59) to:

```python
    def __init__(
        self,
        manager_url: str | None = None,
        client_id: str | None = None,
        timeout: float = 30.0,
        config: Config | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # Values come from the JSON config file; args override them. `config`
        # is exposed so callers can read/persist values:
        #   client.config.set("url", "http://allocator.local")
        # `transport` exists for tests (httpx.MockTransport).
        self.config = config or Config()
        self._base_url = (manager_url or self.config.get("url")).rstrip("/")
        self._client_id = client_id or self.config.get("client_id")
        # There is no client authentication — identity is just the hostname.
        self._http = httpx.Client(
            base_url=self._base_url,
            headers={"X-Client-Id": self._client_id},
            timeout=timeout,
            transport=transport,
        )
```

(`transport=None` is httpx's default — production behavior unchanged.)

- [ ] **Step 4: Run — all pass**

```bash
cd /home/yam/code/allocator/client && .venv/bin/pytest tests/test_client.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/yam/code/allocator
git add client/allocator_client/client.py client/tests/test_client.py
git commit -m "test(client): AllocatorClient suite via MockTransport (adds transport seam)"
```

### Task 8: AllocatorSession context-manager tests

**Files:**
- Test: `client/tests/test_session_context.py`

- [ ] **Step 1: Write the test**

```python
"""AllocatorSession lifecycle: attach on enter, detach+release on exit,
rollback when an attach fails mid-way. Client and usbip are fakes.
"""

import pytest

from allocator_client import session_context
from allocator_client.session_context import AllocatorSession

NOW = "2026-06-12T10:00:00Z"


def _session_response(status="ACTIVE", n_devices=2, failure_reason=None):
    from allocator_contract.session import SessionDeviceAttachInfo, SessionResponse
    devices = [
        SessionDeviceAttachInfo(
            logical_name=f"dev_{i}", node_id="n1", node_hostname="lab-1.local",
            node_ip="192.168.1.5", usbip_bus_id=f"1-1.{i}",
            usbip_attach_command=None, device_class="GENERIC",
        )
        for i in range(n_devices)
    ]
    return SessionResponse(
        session_id="s1", client_id="host-a",
        requested_devices=[d.logical_name for d in devices],
        status=status, node_name="lab-1", failure_reason=failure_reason,
        devices=devices, created_at=NOW,
    )


class FakeClient:
    def __init__(self, response):
        self._response = response
        self.released = []

    def create_session(self, devices, node=None):
        return self._response

    def release_session(self, session_id):
        self.released.append(session_id)


@pytest.fixture
def usbip_log(monkeypatch):
    log = {"attached": [], "detached": []}
    ports = iter(range(10))

    def fake_attach(node_ip, bus_id, timeout=15, *, sudo=False):
        log["attached"].append(bus_id)
        return next(ports)

    def fake_detach(port, timeout=10, *, sudo=False):
        log["detached"].append(port)

    monkeypatch.setattr(session_context.usbip_helper, "attach", fake_attach)
    monkeypatch.setattr(session_context.usbip_helper, "detach", fake_detach)
    return log


def test_happy_path_attaches_then_detaches_and_releases(usbip_log):
    client = FakeClient(_session_response())
    with AllocatorSession(client, ["dev_0", "dev_1"]) as info:
        assert [d.local_port for d in info.devices] == [0, 1]
        assert usbip_log["attached"] == ["1-1.0", "1-1.1"]
    assert usbip_log["detached"] == [1, 0]  # reverse order
    assert client.released == ["s1"]


def test_attach_failure_rolls_back_and_releases(usbip_log, monkeypatch):
    calls = {"n": 0}

    def flaky_attach(node_ip, bus_id, timeout=15, *, sudo=False):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("vhci full")
        usbip_log["attached"].append(bus_id)
        return 7

    monkeypatch.setattr(session_context.usbip_helper, "attach", flaky_attach)
    client = FakeClient(_session_response())
    with pytest.raises(RuntimeError, match="Failed to attach dev_1"):
        with AllocatorSession(client, ["dev_0", "dev_1"]):
            pass
    assert usbip_log["detached"] == [7]      # first device detached again
    assert client.released == ["s1"]          # session released on rollback


def test_non_active_status_raises(usbip_log):
    client = FakeClient(_session_response(status="PENDING"))
    with pytest.raises(RuntimeError, match="Unexpected session status"):
        with AllocatorSession(client, ["dev_0"]):
            pass
    assert client.released == []  # nothing attached -> enter failed before session set


def test_failed_status_raises_with_reason(usbip_log):
    client = FakeClient(_session_response(status="FAILED", failure_reason="no node"))
    with pytest.raises(RuntimeError, match="no node"):
        with AllocatorSession(client, ["dev_0"]):
            pass
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/client && .venv/bin/pytest tests/test_session_context.py -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
cd /home/yam/code/allocator
git add client/tests/test_session_context.py
git commit -m "test(client): AllocatorSession lifecycle and attach rollback"
```

### Task 9: CLI tests

**Files:**
- Test: `client/tests/test_cli.py`

- [ ] **Step 1: Write the test**

```python
"""CLI smoke tests with typer CliRunner. get_client is patched where each
command module imported it; no network.
"""

import json
from contextlib import contextmanager

import pytest
from typer.testing import CliRunner

from allocator_client.cli.main import app

runner = CliRunner()

NOW = "2026-06-12T10:00:00Z"


class FakeSession:
    session_id = "s1"
    status = "ACTIVE"
    node_name = "lab-1"
    devices = []
    failure_reason = None
    requested_devices = ["wifi_0"]
    client_id = "host-a"
    created_at = NOW


class FakeClient:
    def __init__(self):
        self.calls = []

    def create_session(self, devices, node=None):
        self.calls.append(("create_session", devices, node))
        return FakeSession()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_client(monkeypatch):
    fc = FakeClient()
    import allocator_client.cli.commands.session as session_cmd
    monkeypatch.setattr(session_cmd, "get_client", lambda: fc)
    return fc


def test_session_create_happy(fake_client):
    result = runner.invoke(app, ["session", "create", "--devices", "wifi_0, hid_1"])
    assert result.exit_code == 0, result.output
    assert "s1" in result.output
    assert fake_client.calls == [("create_session", ["wifi_0", "hid_1"], None)]


def test_session_create_rejects_duplicates(fake_client):
    result = runner.invoke(app, ["session", "create", "--devices", "a,a,b"])
    assert result.exit_code != 0
    assert "duplicate device names" in result.output
    assert fake_client.calls == []


def test_session_create_rejects_empty(fake_client):
    result = runner.invoke(app, ["session", "create", "--devices", " , "])
    assert result.exit_code != 0
    assert "at least one device" in result.output


def test_config_set_and_show(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = runner.invoke(app, ["config", "set", "url", "http://allocator.local"])
    assert result.exit_code == 0, result.output
    stored = json.loads((tmp_path / "allocator" / "config.json").read_text())
    assert stored == {"url": "http://allocator.local"}


def test_no_args_shows_help():
    result = runner.invoke(app, [])
    assert "session" in result.output and "device" in result.output
```

- [ ] **Step 2: Run**

```bash
cd /home/yam/code/allocator/client && .venv/bin/pytest tests/test_cli.py -v
```

Expected: all pass. If `config set` syntax differs (check `client/allocator_client/cli/commands/config.py` for the actual command signature), fix the TEST to match the real CLI and report the difference.

- [ ] **Step 3: Run the full Plan A suite**

```bash
cd /home/yam/code/allocator/contract && .venv/bin/pytest tests/ -q | tail -1
cd /home/yam/code/allocator/agent && .venv/bin/pytest tests/ -q | tail -1
cd /home/yam/code/allocator/client && .venv/bin/pytest tests/ -q | tail -1
```

Expected: three `N passed` lines, zero failures.

- [ ] **Step 4: Commit**

```bash
cd /home/yam/code/allocator
git add client/tests/test_cli.py
git commit -m "test(client): CLI smoke tests via CliRunner"
```

---

## Verification (whole plan)

All three package suites green (commands in Task 9 Step 3). The only production-code diff in this plan is the `transport` kwarg in `client/allocator_client/client.py` — `git diff <plan-start>..HEAD -- '*/allocator_*'` must show nothing else outside `tests/`.
