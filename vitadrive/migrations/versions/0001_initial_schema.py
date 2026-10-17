"""Create the initial VitaDrive database schema.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("api_key_hash", sa.String(length=64), nullable=True),
        sa.Column("data", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("vin"),
    )
    op.create_index("ix_vehicles_owner_id", "vehicles", ["owner_id"], unique=False)
    op.create_index("ix_vehicles_api_key_hash", "vehicles", ["api_key_hash"], unique=False)
    op.create_table(
        "readings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("timestamp", sa.String(length=40), nullable=False),
        sa.Column("odometer_km", sa.Float(), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["vin"], ["vehicles.vin"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_readings_vin_ts", "readings", ["vin", "timestamp"], unique=False)
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("timestamp", sa.String(length=40), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["vin"], ["vehicles.vin"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_alerts_vin_ts", "alerts", ["vin", "timestamp"], unique=False)
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("item", sa.String(length=64), nullable=False),
        sa.Column("performed_on", sa.String(length=10), nullable=False),
        sa.Column("odometer_km", sa.Float(), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["vin"], ["vehicles.vin"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "fuel_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("filled_on", sa.String(length=10), nullable=False),
        sa.Column("odometer_km", sa.Float(), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["vin"], ["vehicles.vin"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fuel_logs_vin", "fuel_logs", ["vin"], unique=False)
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("endpoint", sa.String(length=2000), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint"),
    )
    op.create_index("ix_push_subscriptions_owner_id", "push_subscriptions", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_owner_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
    op.drop_index("ix_fuel_logs_vin", table_name="fuel_logs")
    op.drop_table("fuel_logs")
    op.drop_table("services")
    op.drop_index("idx_alerts_vin_ts", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("idx_readings_vin_ts", table_name="readings")
    op.drop_table("readings")
    op.drop_index("ix_vehicles_api_key_hash", table_name="vehicles")
    op.drop_index("ix_vehicles_owner_id", table_name="vehicles")
    op.drop_table("vehicles")
