"""Store detected vehicle trips.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trips",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("started_at", sa.String(length=40), nullable=False),
        sa.Column("ended_at", sa.String(length=40), nullable=False),
        sa.Column("start_odometer_km", sa.Float(), nullable=False),
        sa.Column("end_odometer_km", sa.Float(), nullable=False),
        sa.Column("start_level_pct", sa.Float(), nullable=True),
        sa.Column("end_level_pct", sa.Float(), nullable=True),
        sa.Column("max_speed_kmh", sa.Float(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["vin"], ["vehicles.vin"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_trips_vin_started_at", "trips", ["vin", "started_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_trips_vin_started_at", table_name="trips")
    op.drop_table("trips")
