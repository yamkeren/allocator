import structlog
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from allocator_manager.config import settings

log = structlog.get_logger(__name__)

_api_key_scheme = APIKeyHeader(name=settings.api_key_header, auto_error=False)
_agent_secret_scheme = APIKeyHeader(name=settings.agent_secret_header, auto_error=False)


async def require_api_key(api_key: str | None = Security(_api_key_scheme)) -> str:
    """Dependency for client-facing endpoints. Returns the client identifier."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    # TODO Phase 7: look up hashed key in clients table; for MVP accept any non-empty key
    # and use it as the client_id directly.
    return api_key


async def require_agent_secret(
    secret: str | None = Security(_agent_secret_scheme),
) -> None:
    """Dependency for internal endpoints called by node agents."""
    if secret != settings.agent_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent secret",
        )
