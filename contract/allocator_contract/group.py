from datetime import datetime

from pydantic import BaseModel, Field


class GroupResponse(BaseModel):
    group_id: str
    name: str
    description: str | None
    device_count: int
    available: bool          # is there an eligible node that can satisfy the group right now
    active_sessions: int
    devices: list[str]       # logical names that make up the group
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
