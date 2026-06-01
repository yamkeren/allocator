import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from allocator_manager.database import Base


class SessionDeviceStatus(str, enum.Enum):
    ALLOCATED = "ALLOCATED"
    RELEASED = "RELEASED"
    ERROR = "ERROR"


class SessionDevice(Base):
    __tablename__ = "session_devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id"), nullable=False
    )
    logical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    node_agent_url: Mapped[str] = mapped_column(Text, nullable=False)
    usbip_bus_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SessionDeviceStatus] = mapped_column(
        Enum(SessionDeviceStatus, name="sessiondevicestatus"),
        nullable=False,
        default=SessionDeviceStatus.ALLOCATED,
    )
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    session: Mapped["Session"] = relationship("Session", back_populates="session_devices")
    device: Mapped["Device"] = relationship("Device", back_populates="session_devices")

    __table_args__ = (
        UniqueConstraint("session_id", "device_id", name="uq_session_device"),
        Index("idx_session_devices_session_id", "session_id"),
        Index("idx_session_devices_device_id", "device_id"),
    )


from allocator_manager.models.session import Session  # noqa: E402, F401
from allocator_manager.models.device import Device  # noqa: E402, F401
