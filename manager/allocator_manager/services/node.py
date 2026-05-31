import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.models.device import Device, DeviceClass, DeviceStatus
from allocator_manager.models.node import Node, NodeStatus
from allocator_manager.schemas.node import (
    DeviceEventPayload,
    DeviceSyncPayload,
    DeviceSyncResponse,
    NodeHeartbeatPayload,
    NodeHeartbeatResponse,
    NodeListResponse,
    NodeRegisterPayload,
    NodeRegisterResponse,
    NodeResponse,
)

log = structlog.get_logger(__name__)


def _node_to_response(node: Node) -> NodeResponse:
    return NodeResponse(
        node_id=str(node.id),
        name=node.name,
        hostname=node.hostname,
        ip_address=str(node.ip_address),
        agent_port=node.agent_port,
        agent_url=node.agent_url,
        status=node.status.value,
        last_heartbeat=node.last_heartbeat,
        agent_version=node.agent_version,
        created_at=node.created_at,
    )


class NodeService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def register(self, payload: NodeRegisterPayload) -> NodeRegisterResponse:
        agent_url = f"http://{payload.ip_address}:{payload.agent_port}"
        result = await self._db.execute(
            select(Node).where(Node.name == payload.name, Node.deleted_at.is_(None))
        )
        node = result.scalar_one_or_none()
        now = datetime.now(UTC)

        if node:
            node.hostname = payload.hostname
            node.ip_address = payload.ip_address
            node.agent_port = payload.agent_port
            node.agent_url = agent_url
            node.agent_version = payload.agent_version
            node.status = NodeStatus.ONLINE
            node.last_heartbeat = now
            node.updated_at = now
        else:
            node = Node(
                name=payload.name,
                hostname=payload.hostname,
                ip_address=payload.ip_address,
                agent_port=payload.agent_port,
                agent_url=agent_url,
                agent_version=payload.agent_version,
                status=NodeStatus.ONLINE,
                last_heartbeat=now,
            )
            self._db.add(node)

        await self._db.commit()
        await self._db.refresh(node)
        log.info("node_registered", node_id=str(node.id), name=node.name)
        return NodeRegisterResponse(node_id=str(node.id), registered=True)

    async def heartbeat(
        self, node_id: str, payload: NodeHeartbeatPayload
    ) -> NodeHeartbeatResponse:
        result = await self._db.execute(
            select(Node).where(Node.id == uuid.UUID(node_id), Node.deleted_at.is_(None))
        )
        node = result.scalar_one_or_none()
        if node:
            node.last_heartbeat = datetime.now(UTC)
            node.status = NodeStatus.ONLINE
            if payload.agent_version:
                node.agent_version = payload.agent_version
            node.updated_at = datetime.now(UTC)
            await self._db.commit()
        return NodeHeartbeatResponse(acknowledged=True)

    async def sync_devices(
        self, node_id: str, payload: DeviceSyncPayload
    ) -> DeviceSyncResponse:
        node_uuid = uuid.UUID(node_id)
        new_count = updated_count = 0

        for dev_info in payload.devices:
            result = await self._db.execute(
                select(Device).where(
                    Device.logical_name == dev_info.logical_name,
                    Device.node_id == node_uuid,
                    Device.deleted_at.is_(None),
                )
            )
            device = result.scalar_one_or_none()
            now = datetime.now(UTC)

            if device:
                # Update runtime fields only
                device.usbip_bus_id = dev_info.usbip_bus_id
                device.updated_at = now
                updated_count += 1
            else:
                device_class = DeviceClass(dev_info.device_class) if dev_info.device_class in DeviceClass.__members__ else DeviceClass.GENERIC
                device = Device(
                    node_id=node_uuid,
                    logical_name=dev_info.logical_name,
                    vendor_id=dev_info.vendor_id,
                    product_id=dev_info.product_id,
                    serial=dev_info.serial,
                    manufacturer=dev_info.manufacturer,
                    product_name=dev_info.product_name,
                    mac_address=dev_info.mac_address,
                    device_class=device_class,
                    usbip_bus_id=dev_info.usbip_bus_id,
                    fingerprint=dev_info.fingerprint,
                    extra_metadata=dev_info.extra_metadata,
                    status=DeviceStatus.FREE,
                )
                self._db.add(device)
                new_count += 1

        await self._db.commit()
        synced = len(payload.devices)
        log.info("devices_synced", node_id=node_id, synced=synced, new=new_count, updated=updated_count)
        return DeviceSyncResponse(synced=synced, new=new_count, updated=updated_count, removed=0)

    async def handle_device_event(self, node_id: str, payload: DeviceEventPayload) -> None:
        if payload.event == "removed":
            result = await self._db.execute(
                select(Device).where(
                    Device.logical_name == payload.device.logical_name,
                    Device.node_id == uuid.UUID(node_id),
                    Device.deleted_at.is_(None),
                )
            )
            device = result.scalar_one_or_none()
            if device and device.status == DeviceStatus.FREE:
                # Safe to note disconnection; if BOUND, session manager handles it
                log.info("device_removed_event", logical_name=payload.device.logical_name)
        elif payload.event == "added":
            await self.sync_devices(
                node_id,
                DeviceSyncPayload(devices=[payload.device]),
            )

    async def list(self) -> NodeListResponse:
        result = await self._db.execute(
            select(Node).where(Node.deleted_at.is_(None)).order_by(Node.name)
        )
        nodes = result.scalars().all()
        return NodeListResponse(
            items=[_node_to_response(n) for n in nodes],
            total=len(nodes),
        )

    async def get(self, node_id: str) -> NodeResponse | None:
        result = await self._db.execute(
            select(Node).where(Node.id == uuid.UUID(node_id), Node.deleted_at.is_(None))
        )
        node = result.scalar_one_or_none()
        return _node_to_response(node) if node else None

    async def deregister(self, node_id: str) -> None:
        now = datetime.now(UTC)
        await self._db.execute(
            update(Node)
            .where(Node.id == uuid.UUID(node_id))
            .values(deleted_at=now, status=NodeStatus.OFFLINE, updated_at=now)
        )
        await self._db.commit()
