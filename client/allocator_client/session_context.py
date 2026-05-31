"""Async context manager for a full allocator session lifecycle.

Usage:
    async with AllocatorSession(client, "wifi_lab") as session:
        # devices are attached and heartbeat is running
        # session.devices contains attach info
        pass
    # on exit: usbip detach + DELETE /sessions
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import structlog

from allocator_client.client import AllocatorClient
from allocator_client.heartbeat import HeartbeatThread
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
    lease_expires_at: str | None
    devices: list[AttachedDevice] = field(default_factory=list)


class AllocatorSession:
    """Context manager that allocates a group, attaches devices, and cleans up."""

    def __init__(
        self,
        client: AllocatorClient,
        group_name: str,
        lease_duration: int = 3600,
        heartbeat_interval: int = 30,
        attach_timeout: int = 60,
        poll_interval: float = 1.0,
    ) -> None:
        self._client = client
        self._group_name = group_name
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._attach_timeout = attach_timeout
        self._poll_interval = poll_interval
        self._session: SessionInfo | None = None
        self._heartbeat: HeartbeatThread | None = None

    async def __aenter__(self) -> SessionInfo:
        # 1. Request session from manager
        data = await self._client.create_session(
            self._group_name, lease_duration=self._lease_duration
        )
        session_id = data["session_id"]
        log.info("session_requested", session_id=session_id, group=self._group_name)

        # 2. Poll until ACTIVE (or FAILED)
        session_data = await self._wait_for_active(session_id)

        # 3. Build AttachedDevice list and run usbip attach for each device
        attached_devices: list[AttachedDevice] = []
        for dev in session_data.get("devices", []):
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
                    log.error("device_attach_failed", logical_name=ad.logical_name, error=str(exc))
                    # Cleanup already-attached devices and release session
                    await self._cleanup(attached_devices)
                    await self._client.release_session(session_id)
                    raise RuntimeError(f"Failed to attach {ad.logical_name}: {exc}") from exc
            attached_devices.append(ad)

        self._session = SessionInfo(
            session_id=session_id,
            group_name=self._group_name,
            status="ACTIVE",
            lease_expires_at=session_data.get("lease_expires_at"),
            devices=attached_devices,
        )

        # 4. Start background heartbeat
        self._heartbeat = HeartbeatThread(
            session_id=session_id,
            manager_url=self._client._base_url,
            api_key=self._client._api_key,
            interval=self._heartbeat_interval,
        )
        self._heartbeat.start()

        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self._session:
            return

        # Stop heartbeat first
        if self._heartbeat:
            self._heartbeat.stop()

        # Detach all devices
        await self._cleanup(self._session.devices)

        # Release session on manager
        try:
            await self._client.release_session(self._session.session_id)
            log.info("session_released", session_id=self._session.session_id)
        except Exception as exc:
            log.warning("session_release_failed", error=str(exc))

    async def _wait_for_active(self, session_id: str) -> dict:
        deadline = time.monotonic() + self._attach_timeout
        while time.monotonic() < deadline:
            data = await self._client.get_session(session_id)
            status = data.get("status", "")
            if status == "ACTIVE":
                return data
            if status in ("FAILED", "EXPIRED"):
                raise RuntimeError(
                    f"Session {session_id} ended in {status}: {data.get('failure_reason', '')}"
                )
            await asyncio.sleep(self._poll_interval)
        raise TimeoutError(f"Session {session_id} did not become ACTIVE within {self._attach_timeout}s")

    async def _cleanup(self, devices: list[AttachedDevice]) -> None:
        for dev in reversed(devices):
            if dev.local_port >= 0:
                try:
                    usbip_helper.detach(dev.local_port)
                    log.info("device_detached", logical_name=dev.logical_name, port=dev.local_port)
                except Exception as exc:
                    log.warning("device_detach_failed", logical_name=dev.logical_name, error=str(exc))
