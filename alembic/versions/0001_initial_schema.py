"""initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("github_id",       sa.Integer(),    nullable=True,  unique=True),
        sa.Column("username",        sa.String(100),  nullable=False, unique=True),
        sa.Column("email",           sa.String(255),  nullable=False),
        sa.Column("avatar_url",      sa.Text(),       nullable=True),
        sa.Column("hashed_password", sa.Text(),       nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at",      sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_users_id",        "users", ["id"])
    op.create_index("ix_users_username",  "users", ["username"])
    op.create_index("ix_users_email",     "users", ["email"])
    op.create_index("ix_users_github_id", "users", ["github_id"])

    # ── refresh_tokens ─────────────────────────────────────────────────────────
    op.create_table(
        "refresh_tokens",
        sa.Column("id",          postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id",     postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash",  sa.String(64),  nullable=False, unique=True),
        sa.Column("expires_at",  sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked",     sa.Boolean(),   nullable=False, default=False),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_refresh_tokens_user_id",    "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"])

    # ── skill_profiles ─────────────────────────────────────────────────────────
    op.create_table(
        "skill_profiles",
        sa.Column("id",               postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id",          postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("skill_name",       sa.String(100), nullable=False),
        sa.Column("score",            sa.Float(),     nullable=False, default=0.5),
        sa.Column("confidence",       sa.Float(),     nullable=False, default=0.0),
        sa.Column("difficulty_level", sa.Integer(),   nullable=False, default=1),
        sa.Column("last_assessed",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",       sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at",       sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "skill_name", name="uq_skill_profile_user_skill"),
    )
    op.create_index("ix_skill_profiles_user_id",    "skill_profiles", ["user_id"])
    op.create_index("ix_skill_profiles_skill_name", "skill_profiles", ["skill_name"])

    # ── assessment_sessions ────────────────────────────────────────────────────
    op.create_table(
        "assessment_sessions",
        sa.Column("id",              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id",         postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("skill_name",      sa.String(100), nullable=False),
        sa.Column("status",          sa.Enum("active", "completed", "abandoned", name="session_status"), nullable=False, default="active"),
        sa.Column("questions_asked", sa.Integer(),   nullable=False, default=0),
        sa.Column("final_score",     sa.Float(),     nullable=True),
        sa.Column("started_at",      sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at",    sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_assessment_sessions_user_id", "assessment_sessions", ["user_id"])
    op.create_index("ix_assessment_sessions_status",  "assessment_sessions", ["status"])

    # ── question_history ───────────────────────────────────────────────────────
    op.create_table(
        "question_history",
        sa.Column("id",            postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id",    postgresql.UUID(as_uuid=True), sa.ForeignKey("assessment_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_text", sa.Text(),    nullable=False),
        sa.Column("difficulty",    sa.Integer(), nullable=False),
        sa.Column("user_answer",   sa.Text(),    nullable=False),
        sa.Column("ai_grade",      sa.Float(),   nullable=False),
        sa.Column("ai_feedback",   sa.Text(),    nullable=False),
        sa.Column("answered_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_question_history_session_id", "question_history", ["session_id"])

    # ── collab_rooms ───────────────────────────────────────────────────────────
    op.create_table(
        "collab_rooms",
        sa.Column("id",         sa.String(8),    primary_key=True),
        sa.Column("name",       sa.String(100),  nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_collab_rooms_id",         "collab_rooms", ["id"])
    op.create_index("ix_collab_rooms_created_by", "collab_rooms", ["created_by"])

    # ── room_members ───────────────────────────────────────────────────────────
    op.create_table(
        "room_members",
        sa.Column("room_id",   sa.String(8),    sa.ForeignKey("collab_rooms.id", ondelete="CASCADE"),  primary_key=True),
        sa.Column("user_id",   postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("room_members")
    op.drop_table("collab_rooms")
    op.drop_table("question_history")
    op.drop_table("assessment_sessions")
    op.execute("DROP TYPE IF EXISTS session_status")
    op.drop_table("skill_profiles")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
