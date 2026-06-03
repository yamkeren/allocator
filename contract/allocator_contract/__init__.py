"""Shared API contract for the allocator system.

Single source of truth for the request/response models exchanged between the
manager, node agents, and clients. Pydantic-only — no fastapi/sqlalchemy.
"""

from allocator_contract.device import (
    DeviceCreate,
    DeviceListResponse,
    DevicePatch,
    DeviceRename,
    DeviceResponse,
)
from allocator_contract.group import (
    GroupCreate,
    GroupListResponse,
    GroupResponse,
    GroupUpdate,
)
from allocator_contract.node import (
    DeviceInfoPayload,
    DeviceSyncPayload,
    DeviceSyncResponse,
    NodeHeartbeatPayload,
    NodeHeartbeatResponse,
    NodeListResponse,
    NodeRegisterPayload,
    NodeRegisterResponse,
    NodeResponse,
)
from allocator_contract.session import (
    SessionCreate,
    SessionDeviceAttachInfo,
    SessionListResponse,
    SessionResponse,
)
from allocator_contract.usbip import (
    BindRequest,
    BindResponse,
    UnbindRequest,
    UnbindResponse,
)

__all__ = [
    "DeviceCreate",
    "DeviceListResponse",
    "DevicePatch",
    "DeviceRename",
    "DeviceResponse",
    "GroupCreate",
    "GroupListResponse",
    "GroupResponse",
    "GroupUpdate",
    "DeviceInfoPayload",
    "DeviceSyncPayload",
    "DeviceSyncResponse",
    "NodeHeartbeatPayload",
    "NodeHeartbeatResponse",
    "NodeListResponse",
    "NodeRegisterPayload",
    "NodeRegisterResponse",
    "NodeResponse",
    "SessionCreate",
    "SessionDeviceAttachInfo",
    "SessionListResponse",
    "SessionResponse",
    "BindRequest",
    "BindResponse",
    "UnbindRequest",
    "UnbindResponse",
]
