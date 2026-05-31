from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["health"])
async def health() -> dict[str, Any]:
    from allocator_agent.components.usbip_controller import UsbipController
    usbip_available = await UsbipController.check_available()
    return {
        "status": "healthy",
        "version": "0.1.0",
        "usbip_available": usbip_available,
    }
