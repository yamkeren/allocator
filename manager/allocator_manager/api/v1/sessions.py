import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_manager.middleware.auth import require_api_key
from allocator_manager.schemas.session import SessionCreate, SessionListResponse, SessionResponse
from allocator_manager.services.session import SessionService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    client_id: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    svc = SessionService(db)
    return await svc.create(client_id=client_id, request=body)


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    session_status: str | None = None,
    group_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
    client_id: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    return await SessionService(db).list(
        client_id=client_id, status=session_status, group_name=group_name, limit=limit, offset=offset
    )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str = Path(...),
    client_id: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    session = await SessionService(db).get(session_id=session_id, client_id=client_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def release_session(
    session_id: str = Path(...),
    client_id: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> None:
    await SessionService(db).release(session_id=session_id, client_id=client_id)
