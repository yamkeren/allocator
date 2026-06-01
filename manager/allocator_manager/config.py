from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://allocator:allocator@localhost:5432/allocator"

    # Security
    api_key_header: str = "X-API-Key"
    agent_secret_header: str = "X-Agent-Secret"
    agent_secret: str = "dev-agent-secret"

    # HTTP client (manager → agent)
    agent_request_timeout: float = 10.0

    # Logging
    log_level: str = "INFO"
    log_json: bool = True

    # Server
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
