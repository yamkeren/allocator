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
    created_at: datetime

    model_config = {"from_attributes": True}


class DeviceListResponse(BaseModel):
    items: list[DeviceResponse]
    total: int


class DeviceCreate(BaseModel):
    node_id: str
    logical_name: str = Field(..., pattern=r"^[a-z0-9_]+$", max_length=100)
    vendor_id: str
    product_id: str
    serial: str | None = None
    manufacturer: str | None = None
    product_name: str | None = None
    mac_address: str | None = None
    device_class: str = "GENERIC"
    usbip_bus_id: str | None = None
    fingerprint: str


class DevicePatch(BaseModel):
    device_class: str | None = None
    usbip_bus_id: str | None = None


class DeviceRename(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9_]+$", max_length=100)
    # If the name is taken on the node, force renames the holder to a generic
    # name; without force the request 409s so the client can prompt.
    force: bool = False
