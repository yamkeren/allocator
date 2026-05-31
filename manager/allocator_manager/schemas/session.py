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
    status: str

    model_config = {"from_attributes": True}


class SessionResponse(BaseModel):
    session_id: str
    client_id: str
    group_name: str
    status: str
    lease_duration: int
    lease_expires_at: datetime | None
    last_heartbeat: datetime | None
    failure_reason: str | None
    devices: list[SessionDeviceAttachInfo]
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int


class SessionCreate(BaseModel):
    group_name: str
    lease_duration: int = Field(default=3600, ge=60, le=86400)
    metadata: dict = Field(default_factory=dict)


class SessionHeartbeatResponse(BaseModel):
    session_id: str
    lease_expires_at: datetime | None
    status: str
