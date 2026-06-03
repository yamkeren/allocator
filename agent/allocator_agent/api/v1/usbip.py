import structlog
from fastapi import APIRouter, HTTPException, status

from allocator_contract.usbip import BindRequest, BindResponse, UnbindRequest, UnbindResponse
from allocator_agent.components.usbip_controller import UsbipController

log = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/bind", response_model=BindResponse)
async def bind_device(body: BindRequest) -> BindResponse:
    controller = UsbipController()
    try:
        result = await controller.bind(body.bus_id)
    except Exception as exc:
        log.error("bind_failed", bus_id=body.bus_id, logical_name=body.logical_name, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"usbip bind failed: {exc}",
        )
    log.info("device_bound", bus_id=body.bus_id, logical_name=body.logical_name)
    return BindResponse(bound=True, bus_id=result.bus_id)


@router.post("/unbind", response_model=UnbindResponse)
async def unbind_device(body: UnbindRequest) -> UnbindResponse:
    controller = UsbipController()
    try:
        await controller.unbind(body.bus_id)
    except Exception as exc:
        log.error("unbind_failed", bus_id=body.bus_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"usbip unbind failed: {exc}",
        )
    log.info("device_unbound", bus_id=body.bus_id)
    return UnbindResponse(unbound=True)
