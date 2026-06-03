from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://allocator:allocator@localhost:5432/allocator"

    # Security
    # Clients are not authenticated; their identity is their hostname, sent here.
    client_id_header: str = "X-Client-Id"
    agent_secret_header: str = "X-Agent-Secret"
    agent_secret: str = "dev-agent-secret"

    # HTTP client (manager → agent)
    agent_request_timeout: float = 10.0

    # Background task intervals (seconds)
    node_offline_timeout: int = 180         # mark node OFFLINE after this many seconds without heartbeat
    session_max_age: int = 3600             # expire ACTIVE sessions not updated in this long
    heartbeat_reaper_interval: int = 120
    session_expiry_interval: int = 60
    zombie_cleanup_interval: int = 300
    queue_processor_interval: int = 15      # safety sweep for queued (PENDING) sessions

    # Logging
    log_level: str = "INFO"
    log_json: bool = True

    # Server
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
