"""JSON-file-backed client configuration.

Defaults live in code; the file at ``~/.config/allocator/config.json`` holds
only values you've explicitly set. ``set``/``unset`` persist immediately:

    cfg = Config()
    cfg.set("url", "http://localhost")   # written to the JSON file
    cfg.get("url")                              # -> the stored value (or default)
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "url": "http://localhost",                  # manager base URL
    "client_id": socket.gethostname(),                # identity; None -> this host's name
}


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "allocator" / "config.json"


class Config:
    """A tiny JSON config store. ``set``/``unset`` write to disk immediately."""

    KEYS = tuple(_DEFAULTS)

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else config_path()
        self._data: dict[str, Any] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                self._data = {}

    def get(self, key: str) -> Any:
        value = self._data.get(key, _DEFAULTS.get(key))
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    def unset(self, key: str) -> None:
        if key in self._data:
            del self._data[key]
            self._save()

    def resolved(self) -> dict[str, Any]:
        """The effective config: stored values merged over defaults."""
        return {key: self.get(key) for key in self.KEYS}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")
