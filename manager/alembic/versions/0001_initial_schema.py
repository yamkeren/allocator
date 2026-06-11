"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-01

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Reusable shorthand: reference an already-created enum type without trying to CREATE it.
def _enum(name):
    return postgresql.ENUM(name=name, create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Create enum types via raw SQL using exception-safe DO blocks.
    # This is idempotent and runs inside Alembic's transaction, so it rolls
    # back cleanly on any subsequent failure.
    op.execute("""
        DO $$ BEGIN CREATE TYPE nodestatus AS ENUM ('ONLINE', 'OFFLINE');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN CREATE TYPE devicestatus AS ENUM ('FREE', 'ALLOCATED', 'ERROR');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN CREATE TYPE deviceclass AS ENUM ('WIFI', 'ETHERNET', 'BLUETOOTH', 'AUDIO', 'VIDEO', 'HID', 'MASS_STORAGE', 'PRINTER', 'IMAGE', 'SMARTCARD', 'SERIAL', 'GENERIC');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN CREATE TYPE sessionstatus AS ENUM ('PENDING', 'ACTIVE', 'RELEASED', 'FAILED');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN CREATE TYPE sessiondevicestatus AS ENUM ('ALLOCATED', 'RELEASED', 'ERROR');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)

    op.create_table(
        "nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("hostname", sa.Text, nullable=False),
        sa.Column("ip_address", postgresql.INET, nullable=False),
        sa.Column("agent_port", sa.Integer, nullable=False, server_default="5000"),
        sa.Column("agent_url", sa.Text, nullable=False),
        sa.Column("status", _enum("nodestatus"), nullable=False, server_default="OFFLINE"),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_version", sa.Text, nullable=True),
        sa.Column("frozen", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_nodes_status", "nodes", ["status"])

    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("nodes.id"), nullable=False),
        sa.Column("logical_name", sa.String(255), nullable=False),
        sa.Column("vendor_id", sa.String(4), nullable=False),
        sa.Column("product_id", sa.String(4), nullable=False),
        sa.Column("serial", sa.Text, nullable=True),
        sa.Column("manufacturer", sa.Text, nullable=True),
        sa.Column("product_name", sa.Text, nullable=True),
        sa.Column("mac_address", sa.Text, nullable=True),
        sa.Column("device_class", _enum("deviceclass"), nullable=False, server_default="GENERIC"),
        sa.Column("status", _enum("devicestatus"), nullable=False, server_default="FREE"),
        sa.Column("usbip_bus_id", sa.Text, nullable=True),
        sa.Column("fingerprint", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_unique_constraint("uq_device_per_node_logical", "devices", ["node_id", "logical_name"])
    op.create_unique_constraint("uq_device_per_node_fingerprint", "devices", ["node_id", "fingerprint"])
    op.create_index("idx_devices_status", "devices", ["status"])
    op.create_index("idx_devices_vendor_product", "devices", ["vendor_id", "product_id"])
    op.create_index("idx_devices_fingerprint", "devices", ["fingerprint"])

    op.create_table(
        "device_names",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("fingerprint", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_device_names_fingerprint", "device_names", ["fingerprint"])

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("client_id", sa.Text, nullable=False),
        sa.Column("requested_devices", postgresql.ARRAY(sa.String(255)), nullable=False, server_default="{}"),
        sa.Column("status", _enum("sessionstatus"), nullable=False, server_default="PENDING"),
        sa.Column("requested_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_sessions_status", "sessions", ["status"])
    op.create_index("idx_sessions_client_id", "sessions", ["client_id"])

    op.create_table(
        "session_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("logical_name", sa.String(255), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_agent_url", sa.Text, nullable=False),
        sa.Column("usbip_bus_id", sa.Text, nullable=True),
        sa.Column("status", _enum("sessiondevicestatus"), nullable=False, server_default="ALLOCATED"),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_unique_constraint("uq_session_device", "session_devices", ["session_id", "device_id"])
    op.create_index("idx_session_devices_session_id", "session_devices", ["session_id"])
    op.create_index("idx_session_devices_device_id", "session_devices", ["device_id"])


def downgrade() -> None:
    op.drop_table("session_devices")
    op.drop_table("sessions")
    op.drop_table("device_names")
    op.drop_table("devices")
    op.drop_table("nodes")

    for name in ["sessiondevicestatus", "sessionstatus", "deviceclass", "devicestatus", "nodestatus"]:
        op.execute(f"DROP TYPE IF EXISTS {name}")
