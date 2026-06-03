import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from allocator_manager.database import Base


class DeviceName(Base):
    """Durable, portable name memory for a physical device.

    Keyed by fingerprint so a custom name follows the device across nodes and
    survives unplug/prune. Generic names (wifi_0…) are recomputed per node and
    are intentionally NOT recorded here — only operator-assigned custom names.
    """

    __tablename__ = "device_names"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (Index("idx_device_names_fingerprint", "fingerprint"),)
