"""What the operator keeps: users, their logins (encrypted tokens), sessions, API keys,
device logins for the CLI, secrets, plugin choices and the audit log.

Repos, PRs and runs are never stored: they come live from GitHub and GitLab with the user's token.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, scoped_session, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    identities: Mapped[list[Identity]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def settings(self) -> dict:
        try:
            return json.loads(self.settings_json or "{}")
        except ValueError:
            return {}

    @settings.setter
    def settings(self, value: dict) -> None:
        self.settings_json = json.dumps(value, sort_keys=True)

    def identity(self, provider: str) -> Identity | None:
        return next((i for i in self.identities if i.provider == provider), None)


class Identity(Base):
    """A login at GitHub or GitLab, with its OAuth token encrypted (Vault)."""
    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("provider", "host", "uid"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    host: Mapped[str] = mapped_column(String(200))
    uid: Mapped[str] = mapped_column(String(100))
    login: Mapped[str] = mapped_column(String(200))
    access_enc: Mapped[str] = mapped_column(Text, default="")
    refresh_enc: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scopes: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    user: Mapped[User] = relationship(back_populates="identities")


class Session(Base):
    """A browser session: the cookie holds a random value, the database only its SHA-256."""
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")


class ApiKey(Base):
    """An API key (bnbo_<prefix>_<secret>) for the CLI and TUI; stored as a SHA-256 hash."""
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    hash: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(10), default="read")  # read | write
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_ip: Mapped[str] = mapped_column(String(64), default="")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DeviceGrant(Base):
    """`bnb login <operator>`: the CLI shows a code, the user approves it in the browser, the CLI gets a key."""
    __tablename__ = "device_grants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_code: Mapped[str] = mapped_column(String(16), unique=True)
    client_name: Mapped[str] = mapped_column(String(100), default="bnb")
    scope: Mapped[str] = mapped_column(String(10), default="write")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    approved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    denied: Mapped[bool] = mapped_column(Boolean, default=False)
    key_enc: Mapped[str] = mapped_column(Text, default="")  # the new key until the CLI fetched it once
    last_poll: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    verifier_enc: Mapped[str] = mapped_column(Text)
    link_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    binding: Mapped[str] = mapped_column(String(64))  # SHA-256 of the browser's state cookie
    return_to: Mapped[str] = mapped_column(String(200), default="/")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Secret(Base):
    """A user's own secret for a plugin (e.g. a Hubra API token), encrypted."""
    __tablename__ = "secrets"
    __table_args__ = (UniqueConstraint("user_id", "name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    value_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Audit(Base):
    """Every change, who made it, how (web or which API key), and whether it was a dry run."""
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    via: Mapped[str] = mapped_column(String(40), default="web")
    ip: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(300), default="")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str] = mapped_column(Text, default="")


class Setting(Base):
    """Instance settings an admin changes at runtime (plugins switched off for everyone, ...)."""
    __tablename__ = "instance_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


db = scoped_session(sessionmaker(expire_on_commit=False))


def init(url: str):
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragma(conn, _rec):  # pragma: no cover - sqlite only
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    db.configure(bind=engine)
    Base.metadata.create_all(engine)
    return engine
