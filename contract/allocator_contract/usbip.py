from pydantic import BaseModel


class BindRequest(BaseModel):
    bus_id: str
    logical_name: str
    session_id: str


class UnbindRequest(BaseModel):
    bus_id: str
    logical_name: str
    session_id: str | None = None


class BindResponse(BaseModel):
    bound: bool
    bus_id: str


class UnbindResponse(BaseModel):
    unbound: bool
