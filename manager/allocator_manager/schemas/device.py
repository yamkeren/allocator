from datetime import datetime

from pydantic import BaseModel, Field


class DeviceResponse(BaseModel):
    device_id: str
    node_id: str
    logical_name: str
    vendor_id: str
    product_id: str
    serial: str | None
    manufacturer: str | None
    product_name: str | None
    mac_address: str | None
    device_class: str
    status: str
    usbip_bus_id: str | None
    fingerprint: str
    extra_metadata: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class DeviceListResponse(BaseModel):
    items: list[DeviceResponse]
    total: int


class DevicePatch(BaseModel):
    device_class: str | None = None
    description: str | None = None
    extra_metadata: dict | None = None
