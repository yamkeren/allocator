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
