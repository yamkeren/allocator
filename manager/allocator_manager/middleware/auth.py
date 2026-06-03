import structlog
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from allocator_manager.config import settings

log = structlog.get_logger(__name__)

_client_id_scheme = APIKeyHeader(
    name=settings.client_id_header, scheme_name="ClientId", auto_error=False
)
_agent_secret_scheme = APIKeyHeader(
    name=settings.agent_secret_header, scheme_name="AgentSecret", auto_error=False
)


async def require_client(client_id: str | None = Security(_client_id_scheme)) -> str:
    """Client identity for session-scoped endpoints.

    There is no client authentication: the identity is simply the client host's
    name, sent as the X-Client-Id header. It scopes session ownership only.
    """
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing client identifier ({settings.client_id_header})",
        )
    return client_id


async def require_agent_secret(
    secret: str | None = Security(_agent_secret_scheme),
) -> None:
    """Dependency for internal endpoints called by node agents."""
    if secret != settings.agent_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent secret",
        )
