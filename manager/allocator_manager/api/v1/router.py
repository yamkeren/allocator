from fastapi import APIRouter

from allocator_manager.api.v1 import devices, nodes, sessions
from allocator_manager.api.v1.internal import heartbeat, node_agent

v1_router = APIRouter()
v1_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
v1_router.include_router(devices.router, prefix="/devices", tags=["devices"])
v1_router.include_router(nodes.router, prefix="/nodes", tags=["nodes"])

internal_router = APIRouter()
internal_router.include_router(node_agent.router, prefix="/nodes", tags=["internal-nodes"])
internal_router.include_router(heartbeat.router, prefix="/nodes", tags=["internal-heartbeat"])
