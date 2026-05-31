"""Local SQLite device inventory model."""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class LocalBase(DeclarativeBase):
    pass


class LocalDeviceStatus(str, enum.Enum):
    FREE = "FREE"
    PENDING_ONBOARDING = "PENDING_ONBOARDING"
    BOUND = "BOUND"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


class LocalDevice(LocalBase):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    logical_name: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    vendor_id: Mapped[str] = mapped_column(String(4), nullable=False)
    product_id: Mapped[str] = mapped_column(String(4), nullable=False)
    serial: Mapped[str | None] = mapped_column(Text, nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    mac_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_class: Mapped[str] = mapped_column(String(50), nullable=False, default="GENERIC")
    # Runtime field — refreshed on every scan
    usbip_bus_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[LocalDeviceStatus] = mapped_column(
        Enum(LocalDeviceStatus, name="localdevicestatus"),
        nullable=False,
        default=LocalDeviceStatus.PENDING_ONBOARDING,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
