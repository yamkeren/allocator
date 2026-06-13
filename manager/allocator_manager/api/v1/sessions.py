import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_manager.middleware.auth import require_client
from allocator_contract.session import SessionCreate, SessionListResponse, SessionResponse
from allocator_manager.services.session import SessionService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    client_id: str = Depends(require_client),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    svc = SessionService(db)
    return await svc.create(client_id=client_id, request=body)


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    session_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    client_id: str = Depends(require_client),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    return await SessionService(db).list(
        client_id=client_id, status=session_status, limit=limit, offset=offset
    )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str = Path(...),
    client_id: str = Depends(require_client),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    session = await SessionService(db).get(session_id=session_id, client_id=client_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.post("/{session_id}/freeze", status_code=status.HTTP_204_NO_CONTENT)
async def freeze_session_node(
    session_id: str = Path(...),
    client_id: str = Depends(require_client),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Freeze the node this active session is using; it stays frozen after release."""
    await SessionService(db).freeze(session_id=session_id, client_id=client_id)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def release_session(
    session_id: str = Path(...),
    client_id: str = Depends(require_client),
    db: AsyncSession = Depends(get_db),
) -> None:
    await SessionService(db).release(session_id=session_id, client_id=client_id)


@router.post("/{session_id}/force-release", status_code=status.HTTP_204_NO_CONTENT)
async def force_release_session(
    session_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Operator release of any session, regardless of owner (dashboard). No auth."""
    await SessionService(db).force_release(session_id=session_id)
