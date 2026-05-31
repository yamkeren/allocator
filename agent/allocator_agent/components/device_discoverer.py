"""Scans /sys/bus/usb/devices/ to build a stable DeviceInfo for each USB device.

Stable identity fields: vendor_id, product_id, serial, mac_address
Runtime fields: usbip_bus_id (the sysfs bus path, e.g. "1-1.2")

MAC address resolution: if the USB device has a net/ subdirectory (i.e. it
presents as a network interface), read the MAC from sysfs.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_SYSFS_USB = Path("/sys/bus/usb/devices")


@dataclass
class DeviceInfo:
    vendor_id: str
    product_id: str
    usbip_bus_id: str           # sysfs path component (runtime only)
    serial: str | None = None
    manufacturer: str | None = None
    product_name: str | None = None
    mac_address: str | None = None
    device_class: str = "GENERIC"
    extra_metadata: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        key = f"{self.vendor_id}:{self.product_id}:{self.serial or ''}:{self.mac_address or ''}"
        return hashlib.sha256(key.encode()).hexdigest()

    def is_valid(self) -> bool:
        return bool(self.vendor_id and self.product_id)


def _read_sysfs(path: Path, attr: str) -> str | None:
    try:
        return (path / attr).read_text().strip() or None
    except (OSError, PermissionError):
        return None


def _detect_mac(device_path: Path) -> str | None:
    """Check if the USB device exposes a net interface; return its MAC if so."""
    net_dir = device_path / "net"
    if not net_dir.is_dir():
        # Some devices nest the interface deeper
        for child in device_path.iterdir():
            if child.is_dir():
                net_dir = child / "net"
                if net_dir.is_dir():
                    break
        else:
            return None

    for iface_dir in net_dir.iterdir():
        mac_path = Path("/sys/class/net") / iface_dir.name / "address"
        mac = _read_sysfs(mac_path.parent, "address")
        if mac and mac != "00:00:00:00:00:00":
            return mac
    return None


def _classify_device(vendor_id: str, product_id: str, device_path: Path) -> str:
    """Heuristic device classification from sysfs bDeviceClass and interface class."""
    # Check bDeviceClass
    bclass = _read_sysfs(device_path, "bDeviceClass")
    if bclass == "e0":
        return "GENERIC"  # Wireless controller (could be WiFi or BT — check interface)

    # Check for net interfaces → likely ETHERNET or WIFI
    if _detect_mac(device_path):
        # Distinguish WiFi from Ethernet by interface type
        net_dir = device_path / "net"
        if not net_dir.is_dir():
            for child in device_path.iterdir():
                if child.is_dir():
                    net_dir = child / "net"
                    if net_dir.is_dir():
                        break
        if net_dir.is_dir():
            for iface_dir in net_dir.iterdir():
                wireless_dir = Path("/sys/class/net") / iface_dir.name / "wireless"
                if wireless_dir.exists():
                    return "WIFI"
            return "ETHERNET"

    # Check interface class for HID (03), Audio (01), Serial (ff or CDC)
    for interface_dir in device_path.iterdir():
        if not interface_dir.is_dir():
            continue
        iclass = _read_sysfs(interface_dir, "bInterfaceClass")
        if iclass == "03":
            return "HID"
        if iclass == "01":
            return "AUDIO"
        if iclass in ("ff", "0a", "02"):
            return "SERIAL"

    return "GENERIC"


class DeviceDiscoverer:
    """Scans /sys/bus/usb/devices/ and returns a list of DeviceInfo objects."""

    async def scan(self) -> list[DeviceInfo]:
        """Return DeviceInfo for every non-hub USB device currently connected."""
        if not _SYSFS_USB.exists():
            log.warning("sysfs_usb_not_found", path=str(_SYSFS_USB))
            return []

        devices: list[DeviceInfo] = []
        for device_path in _SYSFS_USB.iterdir():
            info = self._inspect_path(device_path)
            if info and info.is_valid():
                devices.append(info)

        log.debug("scan_complete", device_count=len(devices))
        return devices

    def inspect_udev_device(self, udev_device) -> DeviceInfo | None:
        """Build a DeviceInfo from a pyudev device object (hotplug path)."""
        try:
            vendor_id = udev_device.get("ID_VENDOR_ID", "").lower()
            product_id = udev_device.get("ID_MODEL_ID", "").lower()
            if not vendor_id or not product_id:
                return None
            device_path = Path(udev_device.sys_path)
            return self._build_info(vendor_id, product_id, device_path)
        except Exception as exc:
            log.warning("udev_inspect_error", error=str(exc))
            return None

    def _inspect_path(self, device_path: Path) -> DeviceInfo | None:
        """Read sysfs attributes for one USB device path."""
        vendor_id = _read_sysfs(device_path, "idVendor")
        product_id = _read_sysfs(device_path, "idProduct")
        if not vendor_id or not product_id:
            return None
        # Skip root hubs (vendor 1d6b = Linux Foundation)
        if vendor_id == "1d6b":
            return None
        return self._build_info(vendor_id, product_id, device_path)

    def _build_info(self, vendor_id: str, product_id: str, device_path: Path) -> DeviceInfo:
        serial = _read_sysfs(device_path, "serial")
        manufacturer = _read_sysfs(device_path, "manufacturer")
        product_name = _read_sysfs(device_path, "product")
        mac_address = _detect_mac(device_path)
        device_class = _classify_device(vendor_id, product_id, device_path)
        bus_id = device_path.name  # e.g. "1-1.2"

        return DeviceInfo(
            vendor_id=vendor_id,
            product_id=product_id,
            usbip_bus_id=bus_id,
            serial=serial,
            manufacturer=manufacturer,
            product_name=product_name,
            mac_address=mac_address,
            device_class=device_class,
        )
