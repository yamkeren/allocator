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
