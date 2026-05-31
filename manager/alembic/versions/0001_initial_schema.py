"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-28

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgcrypto for gen_random_uuid() — also enabled in postgres/init.sql
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Enum types
    nodestatus = postgresql.ENUM("ONLINE", "OFFLINE", "DEGRADED", name="nodestatus")
    nodestatus.create(op.get_bind())

    devicestatus = postgresql.ENUM(
        "FREE", "RESERVED", "BINDING", "BOUND", "ATTACHED", "ACTIVE", "RELEASING", "ERROR",
        name="devicestatus",
    )
    devicestatus.create(op.get_bind())

    deviceclass = postgresql.ENUM(
        "WIFI", "ETHERNET", "AUDIO", "HID", "SERIAL", "GENERIC",
        name="deviceclass",
    )
    deviceclass.create(op.get_bind())

    sessionstatus = postgresql.ENUM(
        "PENDING", "RESERVING", "BINDING", "ACTIVE", "RELEASING", "RELEASED", "FAILED", "EXPIRED",
        name="sessionstatus",
    )
    sessionstatus.create(op.get_bind())

    sessiondevicestatus = postgresql.ENUM(
        "RESERVED", "BINDING", "BOUND", "ATTACHED", "ACTIVE", "RELEASING", "RELEASED", "ERROR",
        name="sessiondevicestatus",
    )
    sessiondevicestatus.create(op.get_bind())

    # nodes
    op.create_table(
        "nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("hostname", sa.Text, nullable=False),
        sa.Column("ip_address", postgresql.INET, nullable=False),
        sa.Column("agent_port", sa.Integer, nullable=False, server_default="5000"),
        sa.Column("agent_url", sa.Text, nullable=False),
        sa.Column("status", sa.Enum("ONLINE", "OFFLINE", "DEGRADED", name="nodestatus"), nullable=False, server_default="OFFLINE"),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_version", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_nodes_status", "nodes", ["status"])
    op.create_index("idx_nodes_last_heartbeat", "nodes", ["last_heartbeat"])

    # devices
    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("logical_name", sa.String(255), nullable=False, unique=True),
        sa.Column("vendor_id", sa.String(4), nullable=False),
        sa.Column("product_id", sa.String(4), nullable=False),
        sa.Column("serial", sa.Text, nullable=True),
        sa.Column("manufacturer", sa.Text, nullable=True),
        sa.Column("product_name", sa.Text, nullable=True),
        sa.Column("mac_address", sa.Text, nullable=True),
        sa.Column("device_class", sa.Enum("WIFI", "ETHERNET", "AUDIO", "HID", "SERIAL", "GENERIC", name="deviceclass"), nullable=False, server_default="GENERIC"),
        sa.Column("status", sa.Enum("FREE", "RESERVED", "BINDING", "BOUND", "ATTACHED", "ACTIVE", "RELEASING", "ERROR", name="devicestatus"), nullable=False, server_default="FREE"),
        sa.Column("usbip_bus_id", sa.Text, nullable=True),
        sa.Column("fingerprint", sa.Text, nullable=False),
        sa.Column("extra_metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint("uq_device_per_node_logical", "devices", ["node_id", "logical_name"])
    op.create_index("idx_devices_status", "devices", ["status"])
    op.create_index("idx_devices_fingerprint", "devices", ["fingerprint"])
    op.create_index("idx_devices_vendor_product", "devices", ["vendor_id", "product_id"])

    # groups
    op.create_table(
        "groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # group_devices
    op.create_table(
        "group_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("logical_name", sa.String(255), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_unique_constraint("uq_group_device", "group_devices", ["group_id", "device_id"])
    op.create_index("idx_group_devices_group_id", "group_devices", ["group_id"])
    op.create_index("idx_group_devices_device_id", "group_devices", ["device_id"])

    # sessions
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("client_id", sa.Text, nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("group_name", sa.String(255), nullable=False),
        sa.Column("status", sa.Enum("PENDING", "RESERVING", "BINDING", "ACTIVE", "RELEASING", "RELEASED", "FAILED", "EXPIRED", name="sessionstatus"), nullable=False, server_default="PENDING"),
        sa.Column("lease_duration", sa.Integer, nullable=False, server_default="3600"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.Column("client_metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_sessions_status", "sessions", ["status"])
    op.create_index("idx_sessions_client_id", "sessions", ["client_id"])
    op.create_index("idx_sessions_group_id", "sessions", ["group_id"])
    op.execute(
        "CREATE INDEX idx_sessions_active_leases ON sessions (lease_expires_at) "
        "WHERE status = 'ACTIVE'"
    )

    # session_devices
    op.create_table(
        "session_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("logical_name", sa.String(255), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_agent_url", sa.Text, nullable=False),
        sa.Column("usbip_bus_id", sa.Text, nullable=True),
        sa.Column("client_attach_port", sa.Integer, nullable=True),
        sa.Column("status", sa.Enum("RESERVED", "BINDING", "BOUND", "ATTACHED", "ACTIVE", "RELEASING", "RELEASED", "ERROR", name="sessiondevicestatus"), nullable=False, server_default="RESERVED"),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_unique_constraint("uq_session_device", "session_devices", ["session_id", "device_id"])
    op.create_index("idx_session_devices_session_id", "session_devices", ["session_id"])
    op.create_index("idx_session_devices_device_id", "session_devices", ["device_id"])
    op.create_index("idx_session_devices_status", "session_devices", ["status"])

    # audit_log
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("old_status", sa.Text, nullable=True),
        sa.Column("new_status", sa.Text, nullable=True),
        sa.Column("actor", sa.Text, nullable=True),
        sa.Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_audit_log_entity", "audit_log", ["entity_type", "entity_id"])
    op.execute(
        "CREATE INDEX idx_audit_log_brin ON audit_log USING BRIN(created_at)"
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("session_devices")
    op.drop_table("sessions")
    op.drop_table("group_devices")
    op.drop_table("groups")
    op.drop_table("devices")
    op.drop_table("nodes")

    for enum_name in [
        "sessiondevicestatus", "sessionstatus", "deviceclass", "devicestatus", "nodestatus"
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
