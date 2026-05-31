from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Manager connection
    manager_url: str = "http://localhost:8000"
    agent_secret: str = "dev-agent-secret"
    agent_secret_header: str = "X-Agent-Secret"

    # This node's identity (set to empty string to auto-detect)
    node_name: str = ""
    agent_host: str = "0.0.0.0"
    agent_port: int = 5000

    # Inventory persistence
    inventory_db_path: str = str(Path.home() / ".allocator-agent" / "inventory.db")

    # Pre-configured device map (optional)
    device_map_path: str = "/etc/allocator-agent/device-map.yaml"

    # Heartbeat
    heartbeat_interval: int = 15        # seconds
    heartbeat_max_backoff: int = 300    # seconds

    # USB/IP
    usbip_bind_timeout: int = 10        # seconds
    usbipd_port: int = 3240

    # udev debounce
    udev_debounce_ms: int = 500

    # Logging
    log_level: str = "INFO"
    log_json: bool = True


settings = Settings()
