import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from allocator_manager.database import Base


class DeviceStatus(str, enum.Enum):
    FREE = "FREE"
    ALLOCATED = "ALLOCATED"
    ERROR = "ERROR"


class DeviceClass(str, enum.Enum):
    WIFI = "WIFI"
    ETHERNET = "ETHERNET"
    AUDIO = "AUDIO"
    HID = "HID"
    SERIAL = "SERIAL"
    GENERIC = "GENERIC"


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nodes.id"), nullable=False
    )
    logical_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    vendor_id: Mapped[str] = mapped_column(String(4), nullable=False)
    product_id: Mapped[str] = mapped_column(String(4), nullable=False)
    serial: Mapped[str | None] = mapped_column(Text, nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    mac_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_class: Mapped[DeviceClass] = mapped_column(
        Enum(DeviceClass, name="deviceclass"), nullable=False, default=DeviceClass.GENERIC
    )
    status: Mapped[DeviceStatus] = mapped_column(
        Enum(DeviceStatus, name="devicestatus"), nullable=False, default=DeviceStatus.FREE
    )
    usbip_bus_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    node: Mapped["Node"] = relationship("Node", back_populates="devices")
    group_entries: Mapped[list["GroupDevice"]] = relationship("GroupDevice", back_populates="device")
    session_devices: Mapped[list["SessionDevice"]] = relationship(
        "SessionDevice", back_populates="device"
    )

    __table_args__ = (
        UniqueConstraint("node_id", "logical_name", name="uq_device_per_node_logical"),
        Index("idx_devices_status", "status"),
        Index("idx_devices_vendor_product", "vendor_id", "product_id"),
    )


from allocator_manager.models.node import Node  # noqa: E402, F401
from allocator_manager.models.group import GroupDevice  # noqa: E402, F401
from allocator_manager.models.session_device import SessionDevice  # noqa: E402, F401
