from datetime import datetime

from pydantic import BaseModel, Field


class GroupDeviceEntry(BaseModel):
    logical_name: str
    device_id: str
    node_id: str
    device_class: str

    model_config = {"from_attributes": True}


class GroupResponse(BaseModel):
    group_id: str
    name: str
    description: str | None
    device_count: int
    available: bool
    active_sessions: int
    devices: list[GroupDeviceEntry]
    created_at: datetime

    model_config = {"from_attributes": True}


class GroupListResponse(BaseModel):
    items: list[GroupResponse]
    total: int


class GroupCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-z0-9_-]+$", max_length=100)
    description: str | None = None
    devices: list[str] = Field(..., min_length=1)


class GroupUpdate(BaseModel):
    description: str | None = None
    devices: list[str] | None = None
