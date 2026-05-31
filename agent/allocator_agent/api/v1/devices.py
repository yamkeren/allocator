import structlog
from fastapi import APIRouter, HTTPException, status

log = structlog.get_logger(__name__)
router = APIRouter()


@router.get("")
async def list_devices() -> dict:
    from allocator_agent.components.inventory_manager import InventoryManager
    mgr = await InventoryManager.get_instance()
    devices = await mgr.list_all()
    return {"devices": [d.__dict__ for d in devices], "total": len(devices)}


@router.get("/{logical_name}/status")
async def device_status(logical_name: str) -> dict:
    from allocator_agent.components.inventory_manager import InventoryManager
    mgr = await InventoryManager.get_instance()
    device = await mgr.get_by_logical_name(logical_name)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return {"logical_name": device.logical_name, "status": device.status, "usbip_bus_id": device.usbip_bus_id}


@router.post("/discover")
async def trigger_discover() -> dict:
    from allocator_agent.components.device_discoverer import DeviceDiscoverer
    from allocator_agent.components.inventory_manager import InventoryManager
    discoverer = DeviceDiscoverer()
    devices = await discoverer.scan()
    mgr = await InventoryManager.get_instance()
    for device_info in devices:
        await mgr.handle_add(device_info)
    return {"discovered": len(devices)}
