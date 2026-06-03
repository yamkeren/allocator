"""Shared device-naming helpers.

Names are unique per node. Generic names are `{class}_{index}` with the lowest
free index on that node.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from allocator_manager.models.device import Device, DeviceClass


async def unique_logical_name(
    db: AsyncSession, node_id, device_class: DeviceClass
) -> str:
    prefix = device_class.value.lower() + "_"
    names = (await db.execute(
        select(Device.logical_name).where(Device.node_id == node_id)
    )).scalars().all()
    used = {
        int(name[len(prefix):])
        for name in names
        if name.startswith(prefix) and name[len(prefix):].isdigit()
    }
    n = 0
    while n in used:
        n += 1
    return f"{prefix}{n}"
