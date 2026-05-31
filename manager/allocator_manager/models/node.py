import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from allocator_manager.database import Base


class NodeStatus(str, enum.Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    hostname: Mapped[str] = mapped_column(Text, nullable=False)
    ip_address: Mapped[str] = mapped_column(INET, nullable=False)
    agent_port: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    agent_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[NodeStatus] = mapped_column(
        Enum(NodeStatus, name="nodestatus"), nullable=False, default=NodeStatus.OFFLINE
    )
    last_heartbeat: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    agent_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    devices: Mapped[list["Device"]] = relationship("Device", back_populates="node")

    __table_args__ = (
        Index("idx_nodes_status", "status"),
        Index("idx_nodes_last_heartbeat", "last_heartbeat"),
    )


# Avoid circular import
from allocator_manager.models.device import Device  # noqa: E402, F401
