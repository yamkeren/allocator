"""Scans /sys/bus/usb/devices/ and produces device dicts ready for manager sync.

Auto logical name assignment:
  Devices are sorted by (device_class, vendor_id, product_id, serial or '')
  and assigned names like wifi_0, hid_1, serial_2 etc.
  Deterministic within a boot — same physical device always gets the same name
  because sysfs enumeration order is stable per boot.
"""

import hashlib
import re
import subprocess
from pathlib import Path

import structlog
from allocator_contract.node import DeviceInfoPayload

log = structlog.get_logger(__name__)

_SYSFS_USB = Path("/sys/bus/usb/devices")

# Matches both `usbip list -l` ("- busid 1-1 (...)") and parsable ("busid=1-1#...")
_BUSID_RE = re.compile(r"busid[ =](\S+?)[ #(]")


def usbip_bindable_busids() -> set[str] | None:
    """Busids that usbip can actually export, via `usbip list -l`.

    Returns None if usbip is unavailable or errors, so callers can decide
    whether to fall back. Many devices in /sys/bus/usb/devices have no usbip
    driver support and never appear here.
    """
    try:
        result = subprocess.run(
            ["usbip", "list", "-p", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("usbip_list_failed", error=str(exc))
        return None
    if result.returncode != 0:
        log.warning("usbip_list_nonzero", code=result.returncode, stderr=result.stderr.strip())
        return None

    busids = {m.group(1) for line in result.stdout.splitlines() for m in [_BUSID_RE.search(line)] if m}
    log.debug("usbip_bindable", count=len(busids))
    return busids


def _read(path: Path, attr: str) -> str | None:
    try:
        val = (path / attr).read_text().strip()
        return val or None
    except OSError:
        return None


def _detect_network(device_path: Path) -> tuple[str | None, str | None]:
    """Return (mac_address, 'WIFI'|'ETHERNET'|None) if device has a net interface."""
    # Some devices expose net/ directly; others nest it under an interface dir
    candidates = [device_path / "net"]
    for child in device_path.iterdir():
        if child.is_dir() and not child.name.startswith("."):
            candidates.append(child / "net")

    for net_dir in candidates:
        if not net_dir.is_dir():
            continue
        for iface_dir in net_dir.iterdir():
            iface = iface_dir.name
            mac = _read(Path("/sys/class/net") / iface, "address")
            if not mac or mac == "00:00:00:00:00:00":
                continue
            is_wifi = (Path("/sys/class/net") / iface / "wireless").exists()
            return mac, "WIFI" if is_wifi else "ETHERNET"
    return None, None


# USB base class code (bInterfaceClass / bDeviceClass) → our DeviceClass bucket.
# https://www.usb.org/defined-class-codes
_USB_CLASS_MAP = {
    "01": "AUDIO",         # Audio
    "02": "SERIAL",        # CDC Communications (modems; net handled separately)
    "03": "HID",           # Human Interface Device
    "06": "IMAGE",         # Still Imaging (PTP cameras, scanners)
    "07": "PRINTER",       # Printer
    "08": "MASS_STORAGE",  # Mass Storage
    "0a": "SERIAL",        # CDC-Data
    "0b": "SMARTCARD",     # Smart Card
    "0e": "VIDEO",         # Video (UVC webcams)
    "e0": "BLUETOOTH",     # Wireless Controller (BT: subclass 01 / proto 01)
    "ff": "SERIAL",        # Vendor-specific — usually serial adapters
}

# When a composite device exposes several interfaces, prefer the most specific.
_CLASS_PRIORITY = {
    "VIDEO": 90,
    "MASS_STORAGE": 85,
    "SMARTCARD": 80,
    "PRINTER": 75,
    "IMAGE": 70,
    "BLUETOOTH": 65,
    "AUDIO": 60,
    "HID": 50,
    "SERIAL": 40,
    "GENERIC": 0,
}


def _interface_classes(device_path: Path) -> list[tuple[str, str | None, str | None]]:
    """Return (class, subclass, protocol) for every interface under a device."""
    out = []
    for child in device_path.iterdir():
        if not child.is_dir():
            continue
        iclass = _read(child, "bInterfaceClass")
        if iclass:
            out.append((
                iclass.lower(),
                _read(child, "bInterfaceSubClass"),
                _read(child, "bInterfaceProtocol"),
            ))
    return out


def _map_class(iclass: str, isub: str | None, iproto: str | None) -> str | None:
    """Map one (class, subclass, protocol) triple to a DeviceClass, or None."""
    if iclass == "e0":
        # Wireless Controller: only subclass 01 / protocol 01 is Bluetooth.
        return "BLUETOOTH" if (isub == "01" and iproto == "01") else None
    return _USB_CLASS_MAP.get(iclass)


def _classify(device_path: Path) -> str:
    # Network interfaces are the strongest signal: an actual net/ device tells
    # us WIFI vs ETHERNET regardless of how the USB class is declared.
    _, net_class = _detect_network(device_path)
    if net_class:
        return net_class

    # Device-level class (often 00 "defer to interface" or ef "composite") plus
    # every interface, all run through the same mapping.
    triples = [(
        (_read(device_path, "bDeviceClass") or "").lower(),
        _read(device_path, "bDeviceSubClass"),
        _read(device_path, "bDeviceProtocol"),
    )]
    triples.extend(_interface_classes(device_path))

    candidates = [c for (ic, isub, ip) in triples if (c := _map_class(ic, isub, ip))]
    if not candidates:
        return "GENERIC"
    return max(candidates, key=lambda c: _CLASS_PRIORITY.get(c, 0))


def _fingerprint(vendor_id: str, product_id: str, serial: str | None, mac: str | None) -> str:
    key = f"{vendor_id}:{product_id}:{serial or ''}:{mac or ''}"
    return hashlib.sha256(key.encode()).hexdigest()


def scan_usb_devices() -> list[DeviceInfoPayload]:
    """Return the bindable USB devices as shared-contract DeviceInfoPayload models."""
    if not _SYSFS_USB.exists():
        log.warning("sysfs_usb_not_found", path=str(_SYSFS_USB))
        return []

    # Only devices usbip can actually export are useful to us. If usbip is
    # unavailable (None), fall back to including everything rather than
    # silently reporting an empty inventory.
    bindable = usbip_bindable_busids()

    raw = []
    for device_path in _SYSFS_USB.iterdir():
        vendor_id = _read(device_path, "idVendor")
        product_id = _read(device_path, "idProduct")
        if not vendor_id or not product_id:
            continue
        if vendor_id == "1d6b":  # Linux Foundation root hubs — skip
            continue
        if bindable is not None and device_path.name not in bindable:
            continue  # no usbip support for this device

        serial = _read(device_path, "serial")
        manufacturer = _read(device_path, "manufacturer")
        product_name = _read(device_path, "product")
        mac, _ = _detect_network(device_path)
        device_class = _classify(device_path)
        bus_id = device_path.name  # e.g. "1-1.2" — runtime field only

        raw.append({
            "vendor_id": vendor_id,
            "product_id": product_id,
            "serial": serial,
            "manufacturer": manufacturer,
            "product_name": product_name,
            "mac_address": mac,
            "device_class": device_class,
            "usbip_bus_id": bus_id,
            "fingerprint": _fingerprint(vendor_id, product_id, serial, mac),
        })

    # The manager owns naming (per-node, with portable custom names), so the
    # agent only reports identity + fingerprint. A stable order keeps logs and
    # generic-name assignment deterministic across scans.
    raw.sort(key=lambda d: (
        d["device_class"],
        d["vendor_id"],
        d["product_id"],
        d["serial"] or "",
    ))

    log.debug("usb_scan_complete", count=len(raw))
    return [DeviceInfoPayload(**d) for d in raw]
