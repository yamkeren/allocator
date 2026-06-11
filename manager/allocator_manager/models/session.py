import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from allocator_manager.database import Base


class SessionStatus(str, enum.Enum):
    PENDING = "PENDING"  # queued: no eligible node yet, waiting for one to free up
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    FAILED = "FAILED"


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Ordered list of logical names the client requested. Re-resolved to devices
    # on a node at allocation time (and on every queue retry while PENDING).
    requested_devices: Mapped[list[str]] = mapped_column(ARRAY(String(255)), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="sessionstatus"),
        nullable=False,
        default=SessionStatus.PENDING,
    )
    # Optional node pin requested by the client; None means auto-pick.
    requested_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # The node this session was actually allocated on (set when it goes ACTIVE).
    node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session_devices: Mapped[list["SessionDevice"]] = relationship(
        "SessionDevice", back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_sessions_status", "status"),
        Index("idx_sessions_client_id", "client_id"),
    )


from allocator_manager.models.session_device import SessionDevice  # noqa: E402, F401
