import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_manager.middleware.auth import require_agent_secret
from allocator_manager.schemas.node import NodeRegisterPayload, NodeRegisterResponse
from allocator_manager.services.node import NodeService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.post(
    "/register",
    response_model=NodeRegisterResponse,
    dependencies=[Depends(require_agent_secret)],
)
async def register_node(
    body: NodeRegisterPayload,
    db: AsyncSession = Depends(get_db),
) -> NodeRegisterResponse:
    return await NodeService(db).register(body)
