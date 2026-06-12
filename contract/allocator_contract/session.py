from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


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
    requested_devices: list[str]  # logical names the client asked for
    status: str                  # PENDING | ACTIVE | RELEASED | FAILED
    node_name: str | None        # the node this session was allocated on (None while PENDING)
    failure_reason: str | None
    devices: list[SessionDeviceAttachInfo]
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int


class SessionCreate(BaseModel):
    # Ad-hoc list of logical device names to allocate together on one node.
    devices: list[Annotated[str, Field(pattern=r"^[a-z0-9_]+$", max_length=100)]] = Field(
        ..., min_length=1
    )
    node: str | None = None      # optional node name to pin the session to

    @field_validator("devices")
    @classmethod
    def _reject_duplicates(cls, v: list[str]) -> list[str]:
        # A duplicate name would reserve and usbip-bind the same device twice.
        seen: set[str] = set()
        dupes = sorted({d for d in v if d in seen or seen.add(d)})
        if dupes:
            raise ValueError(f"duplicate device names: {', '.join(dupes)}")
        return v
