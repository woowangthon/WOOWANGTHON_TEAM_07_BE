"""add OAuth accounts, one-time flows, sessions and user favorites"""
from alembic import op
import sqlalchemy as sa

revision = "d96e20b4a701"
down_revision = "c4a91f27e6b3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("display_name", sa.String(30), nullable=False),
        sa.Column("created_at", sa.String(20), nullable=False),
        sa.Column("updated_at", sa.String(20), nullable=False),
        sa.CheckConstraint("length(display_name) BETWEEN 1 AND 30", name="ck_user_name"))
    op.create_table("auth_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_auth_identity"),
        sa.CheckConstraint("provider IN ('google', 'kakao')", name="ck_identity_provider"))
    op.create_index("ix_auth_identities_user_id", "auth_identities", ["user_id"])
    op.create_table("auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.String(20), nullable=False))
    op.create_index("ix_auth_sessions_user_expiry", "auth_sessions", ["user_id", "expires_at"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table("oauth_flows",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("verifier", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.String(20), nullable=False))
    op.create_index("ix_oauth_flows_expires_at", "oauth_flows", ["expires_at"])
    op.create_table("user_favorites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("station_id", sa.Integer(), sa.ForeignKey("stations.station_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("place_name", sa.String(64), nullable=False),
        sa.Column("alias", sa.String(20), nullable=True),
        sa.Column("created_at", sa.String(20), nullable=False),
        sa.Column("updated_at", sa.String(20), nullable=False),
        sa.UniqueConstraint("user_id", "station_id", name="uq_user_favorite_station"),
        sa.CheckConstraint("length(place_name) BETWEEN 1 AND 64", name="ck_favorite_place"),
        sa.CheckConstraint("alias IS NULL OR length(alias) <= 20", name="ck_favorite_alias"))
    op.create_index("ix_user_favorites_order", "user_favorites", ["user_id", "created_at", "id"])


def downgrade():
    for name in ("user_favorites", "oauth_flows", "auth_sessions", "auth_identities", "users"):
        op.drop_table(name)
