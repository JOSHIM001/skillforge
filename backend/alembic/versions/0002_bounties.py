"""bounties schema

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-02 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── bounties ───────────────────────────────────────────────
    op.create_table(
        "bounties",
        sa.Column("id",            postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title",         sa.String(200),  nullable=False),
        sa.Column("description",   sa.Text(),        nullable=True),
        sa.Column("posted_by",     postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_ai_generated", sa.Boolean(),  nullable=False, default=False),
        sa.Column("required_skills",  postgresql.JSONB(), nullable=False),  # [{"skill": "Python", "min_score": 0.75}]
        sa.Column("reward_label",  sa.String(100),  nullable=True),         # e.g. "Interview Fast-Track", "500 Points"
        sa.Column("status",        sa.Enum("open", "closed", name="bounty_status"), nullable=False, default="open"),
        sa.Column("expires_at",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",    sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_bounties_status",   "bounties", ["status"])
    op.create_index("ix_bounties_posted_by","bounties", ["posted_by"])

    # ── bounty_claims ──────────────────────────────────────────
    op.create_table(
        "bounty_claims",
        sa.Column("id",          postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bounty_id",   postgresql.UUID(as_uuid=True), sa.ForeignKey("bounties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id",     postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id",    ondelete="CASCADE"), nullable=False),
        sa.Column("score",       sa.Float(),   nullable=False),   # composite score across required skills
        sa.Column("claimed_at",  sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("bounty_id", "user_id", name="uq_bounty_claim"),
    )
    op.create_index("ix_bounty_claims_bounty_id", "bounty_claims", ["bounty_id"])
    op.create_index("ix_bounty_claims_user_id",   "bounty_claims", ["user_id"])


def downgrade() -> None:
    op.drop_table("bounty_claims")
    op.drop_table("bounties")
    op.execute("DROP TYPE IF EXISTS bounty_status")