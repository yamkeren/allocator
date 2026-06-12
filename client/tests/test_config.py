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
