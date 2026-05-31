"""Operator onboarding workflow for newly discovered USB devices.

Three modes:
1. CLI (interactive): `allocator-agent onboard`
2. Pre-config file: /etc/allocator-agent/device-map.yaml
3. API: POST /api/v1/devices/{bus_id}/assign (future)
"""

import re
from pathlib import Path

import structlog

from allocator_agent.components.inventory_manager import InventoryManager

log = structlog.get_logger(__name__)

LOGICAL_NAME_RE = re.compile(r"^[a-z0-9_]+$")
DEVICE_CLASSES = ["WIFI", "ETHERNET", "AUDIO", "HID", "SERIAL", "GENERIC"]


async def run_interactive_onboarding() -> int:
    """Interactively prompt operator for each PENDING_ONBOARDING device.

    Returns number of devices successfully onboarded.
    """
    mgr = await InventoryManager.get_instance()
    pending = await mgr.list_pending_onboarding()

    if not pending:
        print("No devices pending onboarding.")
        return 0

    onboarded = 0
    for device in pending:
        print(f"\n{'='*60}")
        print("New USB device detected:")
        print(f"  Vendor ID    : {device.vendor_id}")
        print(f"  Product ID   : {device.product_id}")
        print(f"  Serial       : {device.serial or '(none)'}")
        print(f"  Manufacturer : {device.manufacturer or '(unknown)'}")
        print(f"  Product      : {device.product_name or '(unknown)'}")
        print(f"  MAC Address  : {device.mac_address or '(N/A)'}")
        print(f"  USB Bus ID   : {device.usbip_bus_id}")
        print(f"  Fingerprint  : {device.fingerprint[:16]}...")

        while True:
            name = input("\nEnter logical name (e.g., wifi_adapter_1) [skip]: ").strip()
            if not name:
                print("Skipped.")
                break
            if not LOGICAL_NAME_RE.match(name):
                print("Invalid name. Use only lowercase letters, digits, and underscores.")
                continue

            class_input = input(
                f"Device class [{'/'.join(DEVICE_CLASSES)}] [GENERIC]: "
            ).strip().upper() or "GENERIC"
            if class_input not in DEVICE_CLASSES:
                class_input = "GENERIC"

            try:
                await mgr.assign_logical_name(
                    bus_id=device.usbip_bus_id or "",
                    logical_name=name,
                    device_class=class_input,
                )
                print(f"Assigned: {name} ({class_input})")
                onboarded += 1
                break
            except ValueError as exc:
                print(f"Error: {exc}")

    print(f"\nOnboarding complete: {onboarded}/{len(pending)} devices assigned.")
    await mgr.push_sync_to_manager()
    return onboarded


async def apply_device_map(map_path: str) -> int:
    """Apply a YAML pre-config file mapping fingerprints to logical names.

    File format:
      devices:
        - fingerprint: "sha256hex..."
          logical_name: "wifi_adapter_1"
          device_class: WIFI
        - fingerprint: "sha256hex..."
          logical_name: "usb_eth_1"
          device_class: ETHERNET
    """
    from pathlib import Path as P
    path = P(map_path)
    if not path.exists():
        return 0

    try:
        import yaml  # type: ignore
        with open(path) as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        log.warning("device_map_load_failed", path=map_path, error=str(exc))
        return 0

    mgr = await InventoryManager.get_instance()
    pending = await mgr.list_pending_onboarding()
    pending_by_fp = {d.fingerprint: d for d in pending}

    assigned = 0
    for entry in data.get("devices", []):
        fp = entry.get("fingerprint", "")
        name = entry.get("logical_name", "")
        cls = entry.get("device_class", "GENERIC")
        device = pending_by_fp.get(fp)
        if device and name:
            try:
                await mgr.assign_logical_name(
                    bus_id=device.usbip_bus_id or "",
                    logical_name=name,
                    device_class=cls,
                )
                assigned += 1
                log.info("device_map_applied", logical_name=name, fingerprint=fp[:12])
            except ValueError as exc:
                log.warning("device_map_assign_failed", name=name, error=str(exc))

    return assigned
