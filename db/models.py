from datetime import date, datetime

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    Index, Integer, MetaData, String, UniqueConstraint,
)
from sqlalchemy import true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from db.base import utcnow

# Nama constraint eksplisit → migrasi Alembic (batch mode SQLite) stabil.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Source(Base):
    """Channel / thread Discord yang direkap. Disinkronkan dari appsettings.json."""
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("kind IN ('channel', 'thread')", name="kind"),
    )

    id:         Mapped[int]        = mapped_column(Integer, primary_key=True)
    discord_id: Mapped[int]        = mapped_column(BigInteger, unique=True)
    kind:       Mapped[str]        = mapped_column(String(16))
    name:       Mapped[str]        = mapped_column(String(200))
    team:       Mapped[str | None] = mapped_column(String(50), default=None)
    created_at: Mapped[datetime]   = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime]   = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    recaps: Mapped[list["Recap"]] = relationship(back_populates="source")

    def __repr__(self) -> str:
        return f"<Source {self.kind}:{self.discord_id} {self.name!r} team={self.team!r}>"


class Recap(Base):
    """Rekap 1 source untuk 1 tanggal WIB. Rekap ulang menimpa baris ini."""
    __tablename__ = "recaps"
    __table_args__ = (
        UniqueConstraint("source_id", "recap_date"),
        Index("ix_recaps_recap_date", "recap_date"),
    )

    id:                Mapped[int]      = mapped_column(Integer, primary_key=True)
    source_id:         Mapped[int]      = mapped_column(ForeignKey("sources.id"))
    recap_date:        Mapped[date]     = mapped_column(Date)
    revision_count:    Mapped[int]      = mapped_column(Integer, default=0, server_default="0")
    last_triggered_by: Mapped[str]      = mapped_column(String(100))
    created_at:        Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at:        Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    source:    Mapped[Source] = relationship(back_populates="recaps")
    entries:   Mapped[list["RecapEntry"]] = relationship(
        back_populates="recap", cascade="all, delete-orphan", passive_deletes=True,
        order_by="RecapEntry.display_name",
    )
    revisions: Mapped[list["RecapRevision"]] = relationship(
        back_populates="recap", cascade="all, delete-orphan", passive_deletes=True,
        order_by="RecapRevision.revision_no",
    )


class RecapEntry(Base):
    """Hasil per user per rekap (4 flag, sama dengan kolom Excel)."""
    __tablename__ = "recap_entries"
    __table_args__ = (
        UniqueConstraint("recap_id", "discord_user_id"),
    )

    id:              Mapped[int]  = mapped_column(Integer, primary_key=True)
    recap_id:        Mapped[int]  = mapped_column(ForeignKey("recaps.id", ondelete="CASCADE"))
    discord_user_id: Mapped[int]  = mapped_column(BigInteger)
    display_name:    Mapped[str]  = mapped_column(String(200))
    opening:         Mapped[bool] = mapped_column(Boolean, default=False)
    opening_late:    Mapped[bool] = mapped_column(Boolean, default=False)
    closing:         Mapped[bool] = mapped_column(Boolean, default=False)
    closing_late:    Mapped[bool] = mapped_column(Boolean, default=False)

    recap: Mapped[Recap] = relationship(back_populates="entries")


class RecapRevision(Base):
    """Snapshot versi lama sebelum rekap ditimpa."""
    __tablename__ = "recap_revisions"
    __table_args__ = (
        UniqueConstraint("recap_id", "revision_no"),
    )

    id:           Mapped[int]      = mapped_column(Integer, primary_key=True)
    recap_id:     Mapped[int]      = mapped_column(ForeignKey("recaps.id", ondelete="CASCADE"))
    revision_no:  Mapped[int]      = mapped_column(Integer)
    snapshot:     Mapped[dict]     = mapped_column(JsonType)
    triggered_by: Mapped[str]      = mapped_column(String(100))
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    recap: Mapped[Recap] = relationship(back_populates="revisions")


class AdminUser(Base):
    """Akun login panel. Dibuat lewat CLI, tidak ada halaman register."""
    __tablename__ = "admin_users"

    id:            Mapped[int]             = mapped_column(Integer, primary_key=True)
    username:      Mapped[str]             = mapped_column(String(64), unique=True)
    password_hash: Mapped[str]             = mapped_column(String(255))
    role:          Mapped[str]             = mapped_column(String(32), default="admin", server_default="admin")
    is_active:     Mapped[bool]            = mapped_column(Boolean, default=True, server_default=true())
    created_at:    Mapped[datetime]        = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
