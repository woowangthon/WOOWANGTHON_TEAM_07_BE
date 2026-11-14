"""소셜 계정·서버 세션·즐겨찾기. 제공자 토큰과 이메일은 저장하지 않는다."""
from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("length(display_name) BETWEEN 1 AND 30", name="ck_user_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[str] = mapped_column(String(20))
    updated_at: Mapped[str] = mapped_column(String(20))


class AuthIdentity(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_auth_identity"),
        CheckConstraint("provider IN ('google', 'kakao')", name="ck_identity_provider"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(10))
    provider_subject: Mapped[str] = mapped_column(String(255))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_expiry", "user_id", "expires_at"),)
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(10))
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[str] = mapped_column(String(20), index=True)


class OAuthFlow(Base):
    __tablename__ = "oauth_flows"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(10))
    verifier: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[str] = mapped_column(String(20), index=True)


class UserFavorite(Base):
    __tablename__ = "user_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "station_id", name="uq_user_favorite_station"),
        CheckConstraint("length(place_name) BETWEEN 1 AND 64", name="ck_favorite_place"),
        CheckConstraint("alias IS NULL OR length(alias) <= 20", name="ck_favorite_alias"),
        Index("ix_user_favorites_order", "user_id", "created_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.station_id", ondelete="RESTRICT"))
    place_name: Mapped[str] = mapped_column(String(64))
    alias: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[str] = mapped_column(String(20))
    updated_at: Mapped[str] = mapped_column(String(20))
