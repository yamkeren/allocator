import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from allocator_manager.database import Base


class NodeStatus(str, enum.Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    hostname: Mapped[str] = mapped_column(Text, nullable=False)
    ip_address: Mapped[str] = mapped_column(INET, nullable=False)
    agent_port: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    agent_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[NodeStatus] = mapped_column(
        Enum(NodeStatus, name="nodestatus"), nullable=False, default=NodeStatus.OFFLINE
    )
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agent_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When frozen, the node is excluded from all new session allocation
    # (auto-pick and explicit pin) until a client unfreezes it. Set by a client
    # from within an active session; persists after the session ends.
    frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    devices: Mapped[list["Device"]] = relationship("Device", back_populates="node")

    __table_args__ = (Index("idx_nodes_status", "status"),)


from allocator_manager.models.device import Device  # noqa: E402, F401
