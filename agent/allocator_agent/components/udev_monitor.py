"""Monitors USB hotplug events via pyudev.

Runs a pyudev MonitorObserver in a background thread. On ADD/REMOVE events,
calls back into the InventoryManager (which is async). Uses asyncio.run_coroutine_threadsafe
to bridge the thread boundary.
"""

import asyncio
import threading
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from allocator_agent.components.inventory_manager import InventoryManager

log = structlog.get_logger(__name__)


class UdevMonitor:
    def __init__(self, inventory_manager: "InventoryManager") -> None:
        self._mgr = inventory_manager
        self._observer = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._debounce_timer: threading.Timer | None = None
        self._debounce_lock = threading.Lock()
        self._pending_events: list[tuple[str, object]] = []

    def start(self) -> None:
        try:
            import pyudev
        except ImportError:
            log.warning("pyudev_not_available", msg="USB hotplug monitoring disabled")
            return

        self._loop = asyncio.get_event_loop()
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by("usb")

        from pyudev import MonitorObserver
        self._observer = MonitorObserver(monitor, callback=self._on_udev_event)
        self._observer.daemon = True
        self._observer.start()
        log.info("udev_monitor_started")

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            log.info("udev_monitor_stopped")

    def _on_udev_event(self, action: str, device) -> None:
        if action not in ("add", "remove"):
            return
        # Debounce: batch rapid events (e.g. device enumerates multiple interfaces)
        with self._debounce_lock:
            self._pending_events.append((action, device))
            if self._debounce_timer:
                self._debounce_timer.cancel()
            from allocator_agent.config import settings
            delay = settings.udev_debounce_ms / 1000.0
            self._debounce_timer = threading.Timer(delay, self._flush_events)
            self._debounce_timer.start()

    def _flush_events(self) -> None:
        with self._debounce_lock:
            events = list(self._pending_events)
            self._pending_events.clear()

        for action, device in events:
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._dispatch(action, device), self._loop
                )

    async def _dispatch(self, action: str, device) -> None:
        from allocator_agent.components.device_discoverer import DeviceDiscoverer
        discoverer = DeviceDiscoverer()

        if action == "add":
            info = discoverer.inspect_udev_device(device)
            if info and info.is_valid():
                log.info("udev_device_added", bus_id=info.usbip_bus_id, fingerprint=info.fingerprint[:12])
                await self._mgr.handle_add(info)
                await self._mgr.push_sync_to_manager()

        elif action == "remove":
            bus_id = getattr(device, "sys_name", None)
            if bus_id:
                log.info("udev_device_removed", bus_id=bus_id)
                await self._mgr.handle_remove(bus_id)
                await self._mgr.push_sync_to_manager()
