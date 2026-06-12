"""unique_logical_name lowest-index, rename conflict/force, and custom-name
portability across nodes via the device_names memory table.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from allocator_manager.models.device import DeviceClass
from allocator_manager.models.device_name import DeviceName
from allocator_manager.services.device import DeviceService
from allocator_manager.services.naming import unique_logical_name
from allocator_contract.node import DeviceInfoPayload, DeviceSyncPayload


async def test_unique_name_fills_lowest_free_index(db, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fp0")
    await make_device(node, "wifi_2", fingerprint="fp2")  # gap at wifi_1
    name = await unique_logical_name(db, node.id, DeviceClass.WIFI)
    assert name == "wifi_1"


async def test_rename_conflict_raises_409(db, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fpw")
    await make_device(node, "video_0", device_class=DeviceClass.VIDEO, fingerprint="fpv")
    with pytest.raises(HTTPException) as exc:
        await DeviceService(db).rename(node.name, "video_0", "wifi_0", force=False)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "name_conflict"
    assert exc.value.detail["holder_logical_name"] == "wifi_0"


async def test_rename_force_displaces_holder(db, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fpw")
    await make_device(node, "video_0", device_class=DeviceClass.VIDEO, fingerprint="fpv")
    # force-rename video_0 -> wifi_0: the old wifi_0 holder gets a generic name
    result = await DeviceService(db).rename(node.name, "video_0", "wifi_0", force=True)
    assert result.logical_name == "wifi_0"
    # the displaced holder no longer owns wifi_0
    holder = (await DeviceService(db).get(node.name, "wifi_1"))
    assert holder is not None  # displaced to wifi_1 (lowest free wifi index)


async def test_custom_name_is_remembered(db, make_node, make_device):
    node = await make_node()
    await make_device(node, "wifi_0", fingerprint="fp-cam")
    await DeviceService(db).rename(node.name, "wifi_0", "cam")
    mem = (await db.execute(select(DeviceName).where(DeviceName.fingerprint == "fp-cam"))).scalar_one()
    assert mem.name == "cam"


async def test_custom_name_follows_device_to_new_node(db, make_node):
    from allocator_manager.services.node import NodeService
    n1 = await make_node(name="n1", ip="10.0.0.1")
    n2 = await make_node(name="n2", ip="10.0.0.2")

    # Device with fingerprint fp-roam first appears on n1 and is named "cam".
    sync = DeviceSyncPayload(devices=[DeviceInfoPayload(
        vendor_id="0bda", product_id="8812", device_class="WIFI",
        usbip_bus_id="1-1", fingerprint="fp-roam",
    )])
    await NodeService(db).sync_devices(str(n1.id), sync)
    await DeviceService(db).rename("n1", "wifi_0", "cam")

    # Same physical device (same fingerprint) now appears on n2.
    await NodeService(db).sync_devices(str(n2.id), sync)
    moved = await DeviceService(db).get("n2", "cam")
    assert moved is not None
    assert moved.node_id == str(n2.id)
