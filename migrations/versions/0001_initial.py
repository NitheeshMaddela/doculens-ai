"""Initial document, vector and job schema."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "owners",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("profile", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("indexed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "owner_id"),
        sa.UniqueConstraint("owner_id", "sha256"),
        sa.CheckConstraint("status IN ('queued','processing','indexed','failed','deleting')"),
    )
    op.create_index("ix_documents_owner_created", "documents", ["owner_id", "created_at", "id"])
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id", "owner_id"], ["documents.id", "documents.owner_id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("document_id", "page", "ordinal"),
        sa.CheckConstraint("page > 0 AND token_count > 0"),
    )
    op.create_index("ix_chunks_owner_document", "chunks", ["owner_id", "document_id"])
    op.create_table(
        "jobs",
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("lease_id", sa.Uuid()),
    )
    op.create_index("ix_jobs_available", "jobs", ["available_at", "document_id"])
    op.create_table(
        "worker_heartbeat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "quota",
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("owners.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("operation", sa.Text(), primary_key=True),
        sa.Column("bucket", sa.Integer(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    for table in ("quota", "worker_heartbeat", "jobs", "chunks", "documents", "owners"):
        op.drop_table(table)
