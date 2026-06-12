"""Round-trip drift guard: payloads must survive serialize -> deserialize
unchanged. This is the HANDOFF §8 'contract drift' check: if a field is
added/renamed with a default, a silently-lossy round trip would hide it.
"""

from datetime import UTC, datetime

import pytest

from allocator_contract.device import DeviceCreate, DeviceResponse
from allocator_contract.node import (
    DeviceInfoPayload,
    DeviceSyncPayload,
    NodeHeartbeatPayload,
    NodeRegisterPayload,
)
from allocator_contract.session import SessionDeviceAttachInfo, SessionResponse
from allocator_contract.usbip import BindRequest, UnbindRequest

SAMPLES = [
    DeviceInfoPayload(
        vendor_id="0bda", product_id="8812", serial="S1", manufacturer="Realtek",
        product_name="AC1200", mac_address="aa:bb:cc:dd:ee:ff",
        device_class="WIFI", usbip_bus_id="1-1.2", fingerprint="f" * 64,
    ),
    DeviceInfoPayload(vendor_id="1a2b", product_id="3c4d", fingerprint="0" * 64),
    DeviceSyncPayload(devices=[DeviceInfoPayload(vendor_id="1", product_id="2", fingerprint="ab")]),
    NodeRegisterPayload(name="lab-1", hostname="lab-1.local", ip_address="192.168.1.5"),
    NodeHeartbeatPayload(timestamp=datetime(2026, 6, 12, 10, 0, tzinfo=UTC)),
    BindRequest(bus_id="1-1.2", logical_name="wifi_0", session_id="s1"),
    UnbindRequest(bus_id="1-1.2", logical_name="wifi_0"),
    DeviceCreate(node_id="n1", logical_name="wifi_0", vendor_id="0bda",
                 product_id="8812", fingerprint="ff"),
    SessionResponse(
        session_id="s1", client_id="host-a", requested_devices=["wifi_0"],
        status="ACTIVE", node_name="lab-1", failure_reason=None,
        devices=[SessionDeviceAttachInfo(
            logical_name="wifi_0", node_id="n1", node_hostname="lab-1.local",
            node_ip="192.168.1.5", usbip_bus_id="1-1.2",
            usbip_attach_command="usbip attach -r 192.168.1.5 -b 1-1.2",
            device_class="WIFI",
        )],
        created_at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    ),
    DeviceResponse(
        device_id="d1", node_id="n1", logical_name="wifi_0", vendor_id="0bda",
        product_id="8812", serial=None, manufacturer=None, product_name=None,
        mac_address=None, device_class="WIFI", status="FREE",
        usbip_bus_id="1-1.2", created_at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
    ),
]


@pytest.mark.parametrize("model", SAMPLES, ids=lambda m: type(m).__name__)
def test_json_roundtrip_unchanged(model):
    restored = type(model).model_validate_json(model.model_dump_json())
    assert restored == model
    assert restored.model_dump() == model.model_dump()
