"""CLI smoke tests with typer CliRunner. get_client is patched where each
command module imported it; no network.
"""

import json

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
