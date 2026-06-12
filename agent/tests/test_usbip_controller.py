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
