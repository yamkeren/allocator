import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.database import get_db
from allocator_manager.services.overview import OverviewResponse, OverviewService

log = structlog.get_logger(__name__)
router = APIRouter()


@router.get("", response_model=OverviewResponse)
async def get_overview(
    db: AsyncSession = Depends(get_db),
) -> OverviewResponse:
    """Whole-system snapshot for the operator dashboard. Unauthenticated."""
    return await OverviewService(db).get_overview()
