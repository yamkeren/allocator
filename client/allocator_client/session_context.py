"""Context manager for the full allocator session lifecycle.

Usage:
    with AllocatorClient(url) as client:
        with AllocatorSession(client, "wifi_lab") as session:
            # devices are usbip-attached; session.devices has the attach info
            ...
        # on exit: usbip detach + DELETE /sessions
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from allocator_client import usbip as usbip_helper
from allocator_client.client import AllocatorClient

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
    """Allocates a group, usbip-attaches its devices, and cleans up on exit."""

    def __init__(self, client: AllocatorClient, group_name: str) -> None:
        self._client = client
        self._group_name = group_name
        self._session: SessionInfo | None = None

    def __enter__(self) -> SessionInfo:
        data = self._client.create_session(self._group_name)
        if data.status == "FAILED":
            raise RuntimeError(f"Session {data.session_id} failed: {data.failure_reason or ''}")
        if data.status != "ACTIVE":
            raise RuntimeError(f"Unexpected session status: {data.status}")

        attached: list[AttachedDevice] = []
        for dev in data.devices:
            ad = AttachedDevice(
                logical_name=dev.logical_name,
                node_ip=dev.node_ip,
                usbip_bus_id=dev.usbip_bus_id,
                device_class=dev.device_class,
            )
            if ad.node_ip and ad.usbip_bus_id:
                try:
                    ad.local_port = usbip_helper.attach(ad.node_ip, ad.usbip_bus_id)
                    log.info("device_attached", logical_name=ad.logical_name, port=ad.local_port)
                except Exception as exc:
                    self._cleanup(attached)
                    self._client.release_session(data.session_id)
                    raise RuntimeError(f"Failed to attach {ad.logical_name}: {exc}") from exc
            attached.append(ad)

        self._session = SessionInfo(
            session_id=data.session_id,
            group_name=self._group_name,
            status="ACTIVE",
            devices=attached,
        )
        return self._session

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self._session:
            return
        self._cleanup(self._session.devices)
        try:
            self._client.release_session(self._session.session_id)
            log.info("session_released", session_id=self._session.session_id)
        except Exception as exc:
            log.warning("session_release_failed", error=str(exc))

    def _cleanup(self, devices: list[AttachedDevice]) -> None:
        for dev in reversed(devices):
            if dev.local_port >= 0:
                try:
                    usbip_helper.detach(dev.local_port)
                    log.info("device_detached", logical_name=dev.logical_name)
                except Exception as exc:
                    log.warning("device_detach_failed", logical_name=dev.logical_name, error=str(exc))
