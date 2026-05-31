"""Local SQLite inventory for the node agent.

Persists device identity and status across restarts. Uses SQLAlchemy async
with aiosqlite driver.

Also handles the onboarding workflow: when a new device is seen (unknown
fingerprint or identical fingerprint to an existing device with no serial),
the device is placed in PENDING_ONBOARDING status until the operator
assigns it a logical name.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from allocator_agent.models.device import LocalBase, LocalDevice, LocalDeviceStatus

if TYPE_CHECKING:
    from allocator_agent.components.device_discoverer import DeviceInfo

log = structlog.get_logger(__name__)

_instance: InventoryManager | None = None


class InventoryManager:
    """Singleton — one per agent process."""

    def __init__(self, db_path: str) -> None:
        db_url = f"sqlite+aiosqlite:///{db_path}"
        self._engine = create_async_engine(db_url, echo=False)
        self._session_factory = async_sessionmaker(
            bind=self._engine, expire_on_commit=False
        )
        self._manager_url: str | None = None
        self._node_id: str | None = None

    @classmethod
    async def get_instance(cls) -> InventoryManager:
        global _instance
        if _instance is None:
            from allocator_agent.config import settings
            Path(settings.inventory_db_path).parent.mkdir(parents=True, exist_ok=True)
            _instance = cls(db_path=settings.inventory_db_path)
        return _instance

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        async with self._engine.begin() as conn:
            await conn.run_sync(LocalBase.metadata.create_all)
        log.info("inventory_db_initialized")

    def set_manager_coords(self, manager_url: str, node_id: str) -> None:
        self._manager_url = manager_url
        self._node_id = node_id

    # ------------------------------------------------------------------
    # Device CRUD
    # ------------------------------------------------------------------

    async def handle_add(self, device_info: DeviceInfo) -> LocalDevice:
        async with self._session_factory() as db:
            # Look for existing record by fingerprint AND bus_id match
            existing = await self._find_by_fingerprint_and_bus(
                db, device_info.fingerprint, device_info.usbip_bus_id
            )
            now = datetime.now(UTC)

            if existing:
                # Known device returning — just update runtime fields
                existing.usbip_bus_id = device_info.usbip_bus_id
                existing.updated_at = now
                if existing.status == LocalDeviceStatus.DISCONNECTED:
                    existing.status = LocalDeviceStatus.FREE
                db.add(existing)
                await db.commit()
                return existing

            # Check for fingerprint collision (identical hardware, no serial)
            collision = await self._find_any_by_fingerprint(db, device_info.fingerprint)
            if collision and collision.usbip_bus_id != device_info.usbip_bus_id:
                log.warning(
                    "fingerprint_collision",
                    fingerprint=device_info.fingerprint[:12],
                    existing_name=collision.logical_name,
                    msg="Duplicate hardware detected — manual disambiguation required",
                )

            # New device — create record in PENDING_ONBOARDING
            device = LocalDevice(
                vendor_id=device_info.vendor_id,
                product_id=device_info.product_id,
                serial=device_info.serial,
                manufacturer=device_info.manufacturer,
                product_name=device_info.product_name,
                mac_address=device_info.mac_address,
                device_class=device_info.device_class,
                usbip_bus_id=device_info.usbip_bus_id,
                fingerprint=device_info.fingerprint,
                status=LocalDeviceStatus.PENDING_ONBOARDING,
            )
            db.add(device)
            await db.commit()
            await db.refresh(device)
            log.info(
                "new_device_pending_onboarding",
                vendor_id=device_info.vendor_id,
                product_id=device_info.product_id,
                bus_id=device_info.usbip_bus_id,
            )
            return device

    async def assign_logical_name(
        self,
        bus_id: str,
        logical_name: str,
        device_class: str = "GENERIC",
    ) -> LocalDevice:
        """Operator assigns a logical name to a PENDING_ONBOARDING device."""
        async with self._session_factory() as db:
            # Check name uniqueness
            name_check = await db.execute(
                select(LocalDevice).where(LocalDevice.logical_name == logical_name)
            )
            if name_check.scalar_one_or_none():
                raise ValueError(f"Logical name {logical_name!r} is already in use")

            result = await db.execute(
                select(LocalDevice).where(
                    LocalDevice.usbip_bus_id == bus_id,
                    LocalDevice.status == LocalDeviceStatus.PENDING_ONBOARDING,
                )
            )
            device = result.scalar_one_or_none()
            if not device:
                raise ValueError(f"No PENDING_ONBOARDING device at bus_id {bus_id!r}")

            device.logical_name = logical_name
            device.device_class = device_class
            device.status = LocalDeviceStatus.FREE
            device.updated_at = datetime.now(UTC)
            db.add(device)
            await db.commit()
            await db.refresh(device)
            log.info("device_onboarded", logical_name=logical_name, bus_id=bus_id)
            return device

    async def handle_remove(self, bus_id: str) -> None:
        async with self._session_factory() as db:
            result = await db.execute(
                select(LocalDevice).where(LocalDevice.usbip_bus_id == bus_id)
            )
            device = result.scalar_one_or_none()
            if device:
                device.status = LocalDeviceStatus.DISCONNECTED
                device.updated_at = datetime.now(UTC)
                db.add(device)
                await db.commit()
                log.info("device_disconnected", bus_id=bus_id, logical_name=device.logical_name)

    async def list_all(self) -> list[LocalDevice]:
        async with self._session_factory() as db:
            result = await db.execute(select(LocalDevice))
            return result.scalars().all()

    async def list_onboarded(self) -> list[LocalDevice]:
        """Return only devices with a logical_name (ready for use)."""
        async with self._session_factory() as db:
            result = await db.execute(
                select(LocalDevice).where(LocalDevice.logical_name.is_not(None))
            )
            return result.scalars().all()

    async def list_pending_onboarding(self) -> list[LocalDevice]:
        async with self._session_factory() as db:
            result = await db.execute(
                select(LocalDevice).where(
                    LocalDevice.status == LocalDeviceStatus.PENDING_ONBOARDING
                )
            )
            return result.scalars().all()

    async def get_by_logical_name(self, logical_name: str) -> LocalDevice | None:
        async with self._session_factory() as db:
            result = await db.execute(
                select(LocalDevice).where(LocalDevice.logical_name == logical_name)
            )
            return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Manager sync
    # ------------------------------------------------------------------

    async def push_sync_to_manager(self) -> None:
        """Push full onboarded device list to manager after a hotplug event."""
        if not self._manager_url or not self._node_id:
            return
        devices = await self.list_onboarded()
        if not devices:
            return

        import httpx
        from allocator_agent.config import settings

        payload = {
            "devices": [
                {
                    "logical_name": d.logical_name,
                    "vendor_id": d.vendor_id,
                    "product_id": d.product_id,
                    "serial": d.serial,
                    "manufacturer": d.manufacturer,
                    "product_name": d.product_name,
                    "mac_address": d.mac_address,
                    "device_class": d.device_class,
                    "usbip_bus_id": d.usbip_bus_id,
                    "fingerprint": d.fingerprint,
                    "extra_metadata": {},
                }
                for d in devices
            ]
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{self._manager_url}/internal/v1/nodes/{self._node_id}/devices/sync",
                    json=payload,
                    headers={settings.agent_secret_header: settings.agent_secret},
                )
                resp.raise_for_status()
        except Exception as exc:
            log.warning("manager_sync_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _find_by_fingerprint_and_bus(
        self, db: AsyncSession, fingerprint: str, bus_id: str
    ) -> LocalDevice | None:
        result = await db.execute(
            select(LocalDevice).where(
                LocalDevice.fingerprint == fingerprint,
                LocalDevice.usbip_bus_id == bus_id,
            )
        )
        return result.scalar_one_or_none()

    async def _find_any_by_fingerprint(
        self, db: AsyncSession, fingerprint: str
    ) -> LocalDevice | None:
        result = await db.execute(
            select(LocalDevice).where(LocalDevice.fingerprint == fingerprint)
        )
        return result.scalar_one_or_none()
