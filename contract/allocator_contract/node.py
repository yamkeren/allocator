from datetime import datetime

from pydantic import BaseModel


class NodeResponse(BaseModel):
    node_id: str
    name: str
    hostname: str
    ip_address: str
    agent_port: int
    agent_url: str
    status: str
    last_heartbeat: datetime | None
    agent_version: str | None
    frozen: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class NodeListResponse(BaseModel):
    items: list[NodeResponse]
    total: int


class NodeRegisterPayload(BaseModel):
    name: str
    hostname: str
    ip_address: str
    agent_port: int = 5000
    agent_version: str | None = None


class NodeRegisterResponse(BaseModel):
    node_id: str
    registered: bool


class NodeHeartbeatPayload(BaseModel):
    timestamp: datetime
    agent_version: str | None = None


class NodeHeartbeatResponse(BaseModel):
    acknowledged: bool


class DeviceInfoPayload(BaseModel):
    vendor_id: str
    product_id: str
    serial: str | None = None
    manufacturer: str | None = None
    product_name: str | None = None
    mac_address: str | None = None
    device_class: str = "GENERIC"
    usbip_bus_id: str | None = None
    fingerprint: str


class DeviceSyncPayload(BaseModel):
    devices: list[DeviceInfoPayload]


class DeviceSyncResponse(BaseModel):
    synced: int
    new: int
    updated: int
    removed: int = 0
