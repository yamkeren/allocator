from fastapi import APIRouter

from allocator_agent.api.v1 import health, usbip

v1_router = APIRouter()
v1_router.include_router(health.router, tags=["health"])
v1_router.include_router(usbip.router, prefix="/usbip", tags=["usbip"])
