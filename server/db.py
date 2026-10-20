import datetime
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import DATABASE_URL


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Avatar(Base):
    __tablename__ = "avatars"

    id: Mapped[int] = mapped_column(primary_key=True)
    nick: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    cid: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    country: Mapped[str | None] = mapped_column(String(16), nullable=True)
    registered: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tagline: Mapped[str | None] = mapped_column(String(240), nullable=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    availability: Mapped[str | None] = mapped_column(String(24), nullable=True)
    room_rid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    room_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    room_privacy: Mapped[str | None] = mapped_column(String(24), nullable=True)
    look_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    look_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    look_image: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_seen: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_online_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_offline_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_suspected_offline: Mapped[bool] = mapped_column(Boolean, default=False)
    total_events: Mapped[int] = mapped_column(Integer, default=0)

    eventos: Mapped[list["StatusEvent"]] = relationship(back_populates="avatar", cascade="all, delete-orphan")
    looks: Mapped[list["Look"]] = relationship(back_populates="avatar", cascade="all, delete-orphan")
    watches: Mapped[list["Watch"]] = relationship(back_populates="avatar", cascade="all, delete-orphan")


class StatusEvent(Base):
    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    avatar_id: Mapped[int] = mapped_column(ForeignKey("avatars.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    room_rid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    room_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    avatar: Mapped[Avatar] = relationship(back_populates="eventos")


class Look(Base):
    __tablename__ = "looks"
    __table_args__ = (UniqueConstraint("avatar_id", "signature", name="uq_look_avatar_sig"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    avatar_id: Mapped[int] = mapped_column(ForeignKey("avatars.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    signature: Mapped[str] = mapped_column(String(64))
    caption: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    items: Mapped[int | None] = mapped_column(Integer, nullable=True)

    avatar: Mapped[Avatar] = relationship(back_populates="looks")


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    rid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_seen: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    last_snapshot_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    snapshots: Mapped[list["RoomSnapshot"]] = relationship(back_populates="room", cascade="all, delete-orphan")


class RoomSnapshot(Base):
    __tablename__ = "room_snapshots"
    __table_args__ = (UniqueConstraint("room_id", "ts", name="uq_room_ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    room: Mapped[Room] = relationship(back_populates="snapshots")


class Watch(Base):
    __tablename__ = "watches"
    __table_args__ = (UniqueConstraint("device", "avatar_id", name="uq_watch_device_avatar"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device: Mapped[str] = mapped_column(String(64), index=True)
    avatar_id: Mapped[int] = mapped_column(ForeignKey("avatars.id", ondelete="CASCADE"), index=True)
    notificar: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)

    avatar: Mapped[Avatar] = relationship(back_populates="watches")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    device: Mapped[str] = mapped_column(String(64), index=True)
    avatar_id: Mapped[int | None] = mapped_column(ForeignKey("avatars.id", ondelete="SET NULL"), nullable=True, index=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    device: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(24), default="generico")
    url: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    events: Mapped[list | None] = mapped_column(JSON, default=list)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)


engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {},
)


if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _configurar_sqlite(conexao, _registro):
        cursor = conexao.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_db():
    Base.metadata.create_all(engine)


@contextmanager
def sessao() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
