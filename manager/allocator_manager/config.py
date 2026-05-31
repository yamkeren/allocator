from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://allocator:allocator@localhost:5432/allocator"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Security
    secret_key: str = "dev-secret-change-in-production"
    api_key_header: str = "X-API-Key"
    agent_secret_header: str = "X-Agent-Secret"
    agent_secret: str = "dev-agent-secret"

    # Session defaults
    default_lease_duration: int = 3600       # seconds
    heartbeat_timeout: int = 90              # seconds — session expired if no heartbeat
    node_offline_timeout: int = 90           # seconds — node marked OFFLINE
    bind_timeout: int = 60                   # seconds — BINDING phase timeout
    allocation_lock_retries: int = 3
    allocation_lock_retry_base_ms: int = 100

    # Background tasks
    heartbeat_reaper_interval: int = 30      # seconds
    session_expiry_interval: int = 60        # seconds
    zombie_cleanup_interval: int = 300       # seconds

    # HTTP client (manager → agent)
    agent_request_timeout: float = 10.0     # seconds
    agent_max_retries: int = 3
    agent_circuit_failure_threshold: int = 5
    agent_circuit_recovery_timeout: int = 60

    # Logging
    log_level: str = "INFO"
    log_json: bool = True

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Rate limiting
    rate_limit_session_create: str = "10/minute"


settings = Settings()
