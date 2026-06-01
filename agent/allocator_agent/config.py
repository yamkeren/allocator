from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Manager connection
    manager_url: str = "http://localhost:8000"
    agent_secret: str = "dev-agent-secret"
    agent_secret_header: str = "X-Agent-Secret"

    # This node's identity (defaults to hostname if empty)
    node_name: str = ""
    agent_host: str = "0.0.0.0"
    agent_port: int = 5000

    # USB/IP
    usbip_bind_timeout: int = 10
    usbipd_port: int = 3240

    # Heartbeat
    heartbeat_interval: int = 15

    # Logging
    log_level: str = "INFO"
    log_json: bool = True


settings = Settings()
