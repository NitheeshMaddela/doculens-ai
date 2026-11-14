from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.config import DIMENSIONS, settings


class Base(DeclarativeBase):
    pass


class Owner(Base):
    __tablename__ = "owners"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("id", "owner_id"),
        UniqueConstraint("owner_id", "sha256"),
        Index("ix_documents_owner_created", "owner_id", "created_at", "id"),
        CheckConstraint("status IN ('queued','processing','indexed','failed','deleting')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("owners.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int]
    sha256: Mapped[str] = mapped_column(Text)
    page_count: Mapped[int | None]
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "owner_id"], ["documents.id", "documents.owner_id"], ondelete="CASCADE"
        ),
        UniqueConstraint("document_id", "page", "ordinal"),
        Index("ix_chunks_owner_document", "owner_id", "document_id"),
        CheckConstraint("page > 0 AND token_count > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    page: Mapped[int]
    ordinal: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int]
    embedding: Mapped[list[float]] = mapped_column(Vector(DIMENSIONS))


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_available", "available_at", "document_id"),)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    lease_id: Mapped[UUID | None]


class Heartbeat(Base):
    __tablename__ = "worker_heartbeat"
    id: Mapped[int] = mapped_column(primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Quota(Base):
    __tablename__ = "quota"
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), primary_key=True
    )
    operation: Mapped[str] = mapped_column(Text, primary_key=True)
    bucket: Mapped[int] = mapped_column(primary_key=True)
    count: Mapped[int]


@lru_cache
def engine() -> Engine:
    return create_engine(settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def database() -> Iterator[Session]:
    with Session(engine(), expire_on_commit=False) as session:
        yield session
