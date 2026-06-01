"""Async context manager for the full allocator session lifecycle.

Usage:
    async with AllocatorSession(client, "wifi_lab") as session:
        # devices are attached
        # session.devices contains attach info
        pass
    # on exit: usbip detach + DELETE /sessions
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from allocator_client.client import AllocatorClient
from allocator_client import usbip as usbip_helper

log = structlog.get_logger(__name__)


@dataclass
class AttachedDevice:
    logical_name: str
    node_ip: str
    usbip_bus_id: str | None
    device_class: str
    local_port: int = -1


@dataclass
class SessionInfo:
    session_id: str
    group_name: str
    status: str
    devices: list[AttachedDevice] = field(default_factory=list)


class AllocatorSession:
    """Context manager that allocates a group, attaches devices, and cleans up on exit."""

    def __init__(self, client: AllocatorClient, group_name: str) -> None:
        self._client = client
        self._group_name = group_name
        self._session: SessionInfo | None = None

    async def __aenter__(self) -> SessionInfo:
        data = await self._client.create_session(self._group_name)
        session_id = data["session_id"]
        status = data.get("status", "")

        if status == "FAILED":
            raise RuntimeError(
                f"Session {session_id} failed: {data.get('failure_reason', '')}"
            )
        if status != "ACTIVE":
            raise RuntimeError(f"Unexpected session status: {status}")

        attached_devices: list[AttachedDevice] = []
        for dev in data.get("devices", []):
            ad = AttachedDevice(
                logical_name=dev["logical_name"],
                node_ip=dev.get("node_ip", ""),
                usbip_bus_id=dev.get("usbip_bus_id"),
                device_class=dev.get("device_class", "GENERIC"),
            )
            if ad.node_ip and ad.usbip_bus_id:
                try:
                    port = usbip_helper.attach(ad.node_ip, ad.usbip_bus_id)
                    ad.local_port = port
                    log.info("device_attached", logical_name=ad.logical_name, port=port)
                except Exception as exc:
                    await self._cleanup(attached_devices)
                    await self._client.release_session(session_id)
                    raise RuntimeError(f"Failed to attach {ad.logical_name}: {exc}") from exc
            attached_devices.append(ad)

        self._session = SessionInfo(
            session_id=session_id,
            group_name=self._group_name,
            status="ACTIVE",
            devices=attached_devices,
        )
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self._session:
            return
        await self._cleanup(self._session.devices)
        try:
            await self._client.release_session(self._session.session_id)
            log.info("session_released", session_id=self._session.session_id)
        except Exception as exc:
            log.warning("session_release_failed", error=str(exc))

    async def _cleanup(self, devices: list[AttachedDevice]) -> None:
        for dev in reversed(devices):
            if dev.local_port >= 0:
                try:
                    usbip_helper.detach(dev.local_port)
                    log.info("device_detached", logical_name=dev.logical_name)
                except Exception as exc:
                    log.warning("device_detach_failed", logical_name=dev.logical_name, error=str(exc))
