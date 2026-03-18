"""add is_admin and is_blocked to users

Revision ID: 0003_admin_fields
Revises: 0002_bounties
Create Date: 2025-03-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_admin_fields"
down_revision = "0002_bounties"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("users", "is_blocked")
    op.drop_column("users", "is_admin")