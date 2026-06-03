import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.models.node import Node, NodeStatus
from allocator_manager.models.device import Device, DeviceClass, DeviceStatus
from allocator_manager.models.device_name import DeviceName
from allocator_manager.models.session_device import SessionDevice
from allocator_contract.node import (
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
        frozen=node.frozen,
        created_at=node.created_at,
    )


class NodeService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def register(self, payload: NodeRegisterPayload) -> NodeRegisterResponse:
        agent_url = f"http://{payload.ip_address}:{payload.agent_port}"
        node = (await self._db.execute(
            select(Node).where(Node.name == payload.name)
        )).scalar_one_or_none()
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

    async def heartbeat(self, node_id: str, payload: NodeHeartbeatPayload) -> NodeHeartbeatResponse:
        node = (await self._db.execute(
            select(Node).where(Node.id == uuid.UUID(node_id))
        )).scalar_one_or_none()
        if node:
            node.last_heartbeat = datetime.now(UTC)
            node.status = NodeStatus.ONLINE
            if payload.agent_version:
                node.agent_version = payload.agent_version
            node.updated_at = datetime.now(UTC)
            await self._db.commit()
        return NodeHeartbeatResponse(acknowledged=True)

    async def sync_devices(self, node_id: str, payload: DeviceSyncPayload) -> DeviceSyncResponse:
        node_uuid = uuid.UUID(node_id)
        new_count = updated_count = 0
        now = datetime.now(UTC)

        for dev_info in payload.devices:
            # Identity is the fingerprint, not the regenerated UUID or the
            # volatile auto-assigned logical_name. Matching here keeps a
            # physical device's row (and id) stable across reconnects, so
            # group_devices references survive a device refresh.
            device = (await self._db.execute(
                select(Device).where(
                    Device.fingerprint == dev_info.fingerprint,
                    Device.node_id == node_uuid,
                )
            )).scalar_one_or_none()

            if device:
                # Only runtime fields change; logical_name stays stable.
                device.usbip_bus_id = dev_info.usbip_bus_id
                device.updated_at = now
                updated_count += 1
            else:
                try:
                    device_class = DeviceClass(dev_info.device_class)
                except ValueError:
                    device_class = DeviceClass.GENERIC
                device = Device(
                    node_id=node_uuid,
                    logical_name=await self._resolve_name(
                        node_uuid, dev_info.fingerprint, device_class
                    ),
                    vendor_id=dev_info.vendor_id,
                    product_id=dev_info.product_id,
                    serial=dev_info.serial,
                    manufacturer=dev_info.manufacturer,
                    product_name=dev_info.product_name,
                    mac_address=dev_info.mac_address,
                    device_class=device_class,
                    usbip_bus_id=dev_info.usbip_bus_id,
                    fingerprint=dev_info.fingerprint,
                    status=DeviceStatus.FREE,
                )
                self._db.add(device)
                # Flush now (the session has autoflush off) so the next
                # device's name lookup sees this one — otherwise several new
                # devices of the same class would all resolve to e.g. wifi_0
                # and collide on uq_device_per_node_logical.
                await self._db.flush()
                new_count += 1

        removed_count = await self._prune_vanished(node_uuid, payload)

        await self._db.commit()
        log.info(
            "devices_synced",
            node_id=node_id,
            new=new_count,
            updated=updated_count,
            removed=removed_count,
        )
        # New/removed devices may make a queued session allocatable.
        if new_count or removed_count:
            from allocator_manager.tasks.queue_processor import kick_queue
            kick_queue()
        return DeviceSyncResponse(
            synced=len(payload.devices),
            new=new_count,
            updated=updated_count,
            removed=removed_count,
        )

    async def _resolve_name(
        self, node_uuid: uuid.UUID, fingerprint: str, device_class: DeviceClass
    ) -> str:
        """Pick the logical name for a newly-seen device on a node.

        A custom name remembered for this physical device (by fingerprint)
        follows it across nodes — applied if still free on this node. Otherwise
        the device gets a fresh per-node generic name.
        """
        mem = (await self._db.execute(
            select(DeviceName).where(DeviceName.fingerprint == fingerprint)
        )).scalar_one_or_none()
        if mem and await self._name_free_on_node(node_uuid, mem.name):
            return mem.name
        return await self._unique_logical_name(node_uuid, device_class)

    async def _name_free_on_node(self, node_uuid: uuid.UUID, name: str) -> bool:
        taken = (await self._db.execute(
            select(Device.id).where(
                Device.node_id == node_uuid, Device.logical_name == name
            ).limit(1)
        )).first()
        return taken is None

    async def _unique_logical_name(self, node_uuid: uuid.UUID, device_class: DeviceClass) -> str:
        from allocator_manager.services.naming import unique_logical_name
        return await unique_logical_name(self._db, node_uuid, device_class)

    async def _prune_vanished(self, node_uuid: uuid.UUID, payload: DeviceSyncPayload) -> int:
        """Delete FREE devices the agent no longer reports.

        A device is removed only if it is FREE and not referenced by ANY
        session_device row (the FK has no ON DELETE, so a lingering row from a
        released session would block the delete — we skip it rather than error).
        Groups reference names, not device rows, so they don't pin a device
        here. ALLOCATED / ERROR devices are left for the session-expiry and
        zombie-cleanup tasks.
        Identity is the fingerprint, matching the upsert above. The device's
        custom name (if any) survives in device_names for when it returns.
        """
        incoming = {d.fingerprint for d in payload.devices}
        conditions = [Device.node_id == node_uuid, Device.status == DeviceStatus.FREE]
        if incoming:
            conditions.append(Device.fingerprint.notin_(incoming))

        candidates = (await self._db.execute(select(Device).where(*conditions))).scalars().all()

        removed = 0
        for device in candidates:
            in_session = (await self._db.execute(
                select(SessionDevice.id).where(SessionDevice.device_id == device.id).limit(1)
            )).first()
            if in_session:
                continue
            await self._db.delete(device)
            removed += 1

        return removed

    async def list(self) -> NodeListResponse:
        nodes = (await self._db.execute(select(Node).order_by(Node.name))).scalars().all()
        return NodeListResponse(items=[_node_to_response(n) for n in nodes], total=len(nodes))

    async def get(self, node_id: str) -> NodeResponse | None:
        node = await self._resolve(node_id)
        return _node_to_response(node) if node else None

    async def _resolve(self, ident: str) -> Node | None:
        """Resolve a node by UUID or by unique name."""
        try:
            node_uuid = uuid.UUID(ident)
            return (await self._db.execute(
                select(Node).where(Node.id == node_uuid)
            )).scalar_one_or_none()
        except ValueError:
            return (await self._db.execute(
                select(Node).where(Node.name == ident)
            )).scalar_one_or_none()

    async def unfreeze(self, ident: str) -> NodeResponse:
        node = await self._resolve(ident)
        if not node:
            from fastapi import HTTPException, status as http_status
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Node not found")
        if node.frozen:
            node.frozen = False
            node.updated_at = datetime.now(UTC)
            await self._db.commit()
            log.info("node_unfrozen", node_id=str(node.id), node_name=node.name)
            # A newly-thawed node may satisfy queued sessions.
            from allocator_manager.tasks.queue_processor import kick_queue
            kick_queue()
        return _node_to_response(node)

    async def delete(self, node_id: str) -> None:
        node = (await self._db.execute(
            select(Node).where(Node.id == uuid.UUID(node_id))
        )).scalar_one_or_none()
        if node:
            node.status = NodeStatus.OFFLINE
            node.updated_at = datetime.now(UTC)
            await self._db.commit()
