"""Add daily summaries for retained reading history.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "readings_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("vin", sa.String(length=17), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("odometer_km", sa.Float(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["vin"], ["vehicles.vin"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vin", "day", name="uq_readings_daily_vin_day"),
    )


def downgrade() -> None:
    op.drop_table("readings_daily")
