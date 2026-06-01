from datetime import datetime

from pydantic import BaseModel, Field


class SessionDeviceAttachInfo(BaseModel):
    logical_name: str
    node_id: str
    node_hostname: str
    node_ip: str
    usbip_bus_id: str | None
    usbip_attach_command: str | None
    device_class: str

    model_config = {"from_attributes": True}


class SessionResponse(BaseModel):
    session_id: str
    client_id: str
    group_name: str
    status: str
    failure_reason: str | None
    devices: list[SessionDeviceAttachInfo]
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int


class SessionCreate(BaseModel):
    group_name: str
